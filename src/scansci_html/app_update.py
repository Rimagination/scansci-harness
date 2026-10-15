"""Version checks and verified Windows desktop updates for ScanSci."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from .update_blockmap import (
    DifferentialDownloadUnavailable,
    download_differential,
    load_blockmap,
)
from .manifest_trust import verify_manifest_signature
from .release_transport import open_release_url, validate_release_url


APP_VERSION = "0.4.2"
UPDATE_MANIFEST_ENV = "SCANSCI_UPDATE_MANIFEST_URL"
RELEASE_PUBLIC_KEY_ENV = "SCANSCI_RELEASE_PUBLIC_KEY"
DEFAULT_UPDATE_MANIFEST_URL = "https://github.com/Rimagination/scansci-harness/releases/latest/download/stable.json"
_USER_AGENT = f"ScanSci/{APP_VERSION} Windows"
_MAX_MANIFEST_BYTES = 1_000_000
_MAX_UPDATE_DOWNLOAD_BYTES = 2 * 1024**3
_MAX_BLOCKMAP_BYTES = 64 * 1024**2
_DEFAULT_RELEASE_NOTES = [
    {
        "title": "安装与更新",
        "items": [
            "本地 AI 运行时会自动切换到经过校验的兼容下载通道。",
            "自动检查更新偏好改为应用设置持久化，不再受随机本地端口影响。",
            "更新替换或新版本启动失败时自动恢复旧版本，并保留错误日志。",
            "正式发布会验证完整包、差分 blockmap、运行时分片与安装签名。",
        ],
    },
    {
        "title": "对话与 Harness",
        "items": [
            "保留多轮上下文、稳定短标题、可拖动队列与最后一轮编辑重做。",
            "模型输出会跟随用户当前输入语言。",
        ],
    },
]


class AppUpdateService:
    """Read a release manifest and prepare a verified self-update."""

    def __init__(
        self,
        *,
        manifest_url: str | None = None,
        current_version: str | None = None,
        updates_root: str | Path | None = None,
        release_public_key: str | None = None,
    ) -> None:
        build: dict[str, Any] = {}
        if current_version is None or manifest_url is None or release_public_key is None:
            # build-info.json is written into every packaged candidate and is
            # the runtime identity already exposed by /api/health.  Reading it
            # here prevents the updater/About page from reporting a stale
            # source constant when the packaged version has advanced.
            from .build_info import current_build_info

            build = current_build_info()
        if current_version is None:
            current_version = str(build.get("version", "")).strip() or APP_VERSION
        self.current_version = str(current_version).strip() or APP_VERSION
        configured_manifest = (
            manifest_url
            if manifest_url is not None
            else os.getenv(UPDATE_MANIFEST_ENV, "") or build.get("update_manifest_url", "")
        )
        # A packaged desktop must always have an update channel. Older builds
        # did not persist this URL in build-info.json, so keep a compiled
        # fallback for those installs as well as for newly built packages.
        if manifest_url is None and not str(configured_manifest or "").strip() and getattr(sys, "frozen", False):
            configured_manifest = DEFAULT_UPDATE_MANIFEST_URL
        self.manifest_url = str(configured_manifest or "").strip()
        if release_public_key is None:
            release_public_key = os.getenv(RELEASE_PUBLIC_KEY_ENV, "") or build.get("release_public_key", "")
        self.release_public_key = str(release_public_key or "").strip()
        local_root = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        self.updates_root = Path(updates_root or Path(local_root) / "ScanSciPi" / "updates").resolve()
        self._manifest: dict[str, Any] | None = None
        self._manifest_allows_local_files = False
        self._status = self._current_status()

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def check(self) -> dict[str, Any]:
        if not self.manifest_url:
            self._status = self._current_status(checked=True)
            return self.status()
        if getattr(sys, "frozen", False) and not self.release_public_key:
            self._status = {
                **self._current_status(checked=True),
                "state": "error",
                "message": "此安装包缺少更新签名公钥；请从官方 Release 手动下载新版本。",
                "error": "missing_release_public_key",
            }
            return self.status()
        try:
            self._manifest_allows_local_files = urlparse(self.manifest_url).scheme.casefold() == "file"
            manifest = self._read_manifest(self.manifest_url)
            self._manifest = manifest
            self._status = self._status_from_manifest(manifest)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._status = {
                **self._current_status(checked=True),
                "state": "error",
                "message": "暂时无法检查更新",
                "error": str(error),
            }
        return self.status()

    def install(self, *, relaunch_args: list[str] | None = None) -> dict[str, Any]:
        """Download, verify and schedule a Windows onedir replacement."""

        status = self._status
        if not status.get("available"):
            raise RuntimeError("当前没有可安装的 ScanSci 更新。")
        if not getattr(sys, "frozen", False):
            raise RuntimeError("源码预览不会覆盖安装目录，请在 ScanSci 桌面应用中更新。")
        if sys.platform != "win32":
            raise RuntimeError("当前自动更新器仅支持 Windows 桌面版。")

        package = self._validated_windows_package(
            self._manifest or {},
            allow_file=self._manifest_allows_local_files,
        )
        package_url = str(package.get("url", "")).strip()
        expected_sha256 = str(package.get("sha256", "")).strip().lower()
        expected_size = int(package["size"])

        version = str(status.get("latest_version", "update"))
        update_dir = self.updates_root / _safe_segment(version)
        update_dir.mkdir(parents=True, exist_ok=True)
        archive = update_dir / "ScanSci-update.zip"
        blockmap_path = update_dir / "ScanSci-update.zip.blockmap"
        update_result: dict[str, Any] = {
            "used_differential": False,
            "bytes_downloaded": 0,
            "total_size": 0,
        }
        blockmap = self._prepare_blockmap(package, blockmap_path)
        if blockmap is not None:
            cached_base = self._cached_base_package()
            if cached_base is not None:
                old_archive, old_blockmap_path = cached_base
                try:
                    update_result = download_differential(
                        old_archive,
                        load_blockmap(old_blockmap_path),
                        package_url,
                        blockmap,
                        archive,
                    )
                except (DifferentialDownloadUnavailable, OSError, ValueError):
                    archive.unlink(missing_ok=True)
        if not update_result["used_differential"]:
            self._download(
                package_url,
                archive,
                expected_size=expected_size,
                allow_file=self._manifest_allows_local_files,
            )
            update_result = {
                "used_differential": False,
                "bytes_downloaded": archive.stat().st_size,
                "total_size": archive.stat().st_size,
            }
        if archive.stat().st_size != expected_size:
            archive.unlink(missing_ok=True)
            raise RuntimeError("更新包大小与清单不一致，已取消安装。")
        actual_sha256 = _sha256(archive)
        if actual_sha256 != expected_sha256:
            archive.unlink(missing_ok=True)
            raise RuntimeError("更新包校验失败，已取消安装。")
        _validate_release_archive(archive)

        executable = Path(sys.executable).resolve()
        install_dir = executable.parent
        helper = update_dir / "apply-update.ps1"
        arguments_path = update_dir / "relaunch-args.json"
        ready_token = uuid4().hex
        arguments_path.write_text(json.dumps(list(relaunch_args or []), ensure_ascii=False), encoding="utf-8")
        helper.write_text(_UPDATER_SCRIPT, encoding="utf-8-sig")

        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(helper),
            "-ParentPid",
            str(os.getpid()),
            "-Archive",
            str(archive),
            "-InstallDir",
            str(install_dir),
            "-ExecutableName",
            executable.name,
            "-RelaunchArgsPath",
            str(arguments_path),
            "-TargetVersion",
            version,
            "-ReadyToken",
            ready_token,
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(command, creationflags=creation_flags, close_fds=True)  # noqa: S603 - fixed local helper
        return {
            **status,
            "state": "restarting",
            "message": "更新已验证，ScanSci 即将重新启动",
            "archive": str(archive),
            "update_mode": "differential" if update_result["used_differential"] else "full",
            "bytes_downloaded": int(update_result["bytes_downloaded"]),
            "total_size": int(update_result["total_size"]),
        }

    def _current_status(self, *, checked: bool = False) -> dict[str, Any]:
        return {
            "state": "idle" if not self.manifest_url else "current",
            "available": False,
            "current_version": self.current_version,
            "latest_version": self.current_version,
            "release_title": f"ScanSci {self.current_version}",
            "published_at": "",
            "release_notes": _DEFAULT_RELEASE_NOTES,
            "can_install": False,
            "channel": "稳定版",
            "message": f"当前版本 v{self.current_version}" if not self.manifest_url else "当前已是最新版本",
            "checked_at": _now_iso() if checked else "",
            "update_mode": "full",
            "blockmap_size": 0,
        }

    def _status_from_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        latest = str(manifest.get("version", "")).strip()
        if not latest:
            raise ValueError("更新清单缺少 version。")
        available = _version_key(latest) > _version_key(self.current_version)
        package = self._windows_package(manifest)
        if available:
            package = self._validated_windows_package(
                manifest,
                allow_file=self._manifest_allows_local_files,
            )
        notes = _normalise_release_notes(manifest.get("notes")) or _DEFAULT_RELEASE_NOTES
        blockmap = self._blockmap_info(package, allow_file=self._manifest_allows_local_files)
        return {
            "state": "available" if available else "current",
            "available": available,
            "current_version": self.current_version,
            "latest_version": latest if available else self.current_version,
            "release_title": str(manifest.get("title", "")).strip() or f"ScanSci {latest}",
            "published_at": str(manifest.get("published_at", "")).strip(),
            "release_notes": notes,
            "can_install": bool(available and package.get("url") and package.get("sha256")),
            "channel": str(manifest.get("channel", "稳定版")).strip() or "稳定版",
            "message": "有可用更新" if available else "当前已是最新版本",
            "checked_at": _now_iso(),
            "update_mode": "differential-capable" if blockmap else "full",
            "blockmap_size": int(blockmap.get("size", 0) or 0) if blockmap else 0,
        }

    @staticmethod
    def _windows_package(manifest: dict[str, Any]) -> dict[str, Any]:
        package = manifest.get("windows", {})
        return package if isinstance(package, dict) else {}

    @staticmethod
    def _validated_windows_package(manifest: dict[str, Any], *, allow_file: bool) -> dict[str, Any]:
        package = AppUpdateService._windows_package(manifest)
        url = str(package.get("url", "")).strip()
        checksum = str(package.get("sha256", "")).strip().lower()
        size = package.get("size")
        if (
            not url
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or size > _MAX_UPDATE_DOWNLOAD_BYTES
        ):
            raise ValueError("更新清单缺少有效的 Windows 安装包地址、大小或 SHA256。")
        _validate_update_url(url, allow_file=allow_file)
        return {**package, "url": url, "sha256": checksum, "size": size}

    @staticmethod
    def _blockmap_info(package: dict[str, Any], *, allow_file: bool = False) -> dict[str, Any] | None:
        blockmap = package.get("blockmap")
        if not isinstance(blockmap, dict):
            return None
        url = str(blockmap.get("url", "")).strip()
        checksum = str(blockmap.get("sha256", "")).strip().lower()
        size = blockmap.get("size")
        if (
            not url
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or size > _MAX_BLOCKMAP_BYTES
        ):
            return None
        try:
            _validate_update_url(url, allow_file=allow_file)
        except ValueError:
            return None
        return {"url": url, "sha256": checksum, "size": size}

    def _prepare_blockmap(self, package: dict[str, Any], destination: Path) -> dict[str, Any] | None:
        blockmap_info = self._blockmap_info(package, allow_file=self._manifest_allows_local_files)
        if blockmap_info is None:
            destination.unlink(missing_ok=True)
            return None
        try:
            self._download_verified(
                str(blockmap_info["url"]),
                destination,
                str(blockmap_info["sha256"]),
                expected_size=int(blockmap_info["size"]),
                allow_file=self._manifest_allows_local_files,
            )
            blockmap = load_blockmap(destination)
            if (
                int(blockmap["size"]) != int(package.get("size", 0) or 0)
                or str(blockmap["sha256"]).casefold() != str(package.get("sha256", "")).casefold()
            ):
                raise ValueError("更新 blockmap 与完整包元数据不一致。")
            return blockmap
        except (OSError, ValueError, RuntimeError):
            destination.unlink(missing_ok=True)
            return None

    def _cached_base_package(self) -> tuple[Path, Path] | None:
        base_dir = self.updates_root / _safe_segment(self.current_version)
        archive = base_dir / "ScanSci-update.zip"
        blockmap = base_dir / "ScanSci-update.zip.blockmap"
        if archive.is_file() and blockmap.is_file():
            return archive, blockmap
        return None

    def _read_manifest(self, url: str) -> dict[str, Any]:
        allow_file = urlparse(url).scheme.casefold() == "file"
        _validate_update_url(url, allow_file=allow_file)
        request = Request(url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT})
        with open_release_url(request, timeout=8, allow_file=allow_file, opener=urlopen) as response:
            payload = response.read(_MAX_MANIFEST_BYTES + 1)
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise ValueError("更新清单过大。")
        parsed = json.loads(payload.decode("utf-8-sig"))
        if not isinstance(parsed, dict):
            raise ValueError("更新清单必须是 JSON 对象。")
        schema_version = parsed.get("schema_version")
        signature_required = bool(self.release_public_key) and not allow_file
        if schema_version is not None and schema_version != 1:
            raise ValueError("更新清单 schema_version 必须为 1。")
        if signature_required and schema_version != 1:
            raise ValueError("已签名更新清单 schema_version 必须为 1。")
        verify_manifest_signature(parsed, self.release_public_key, required=signature_required)
        return parsed

    @staticmethod
    def _download(url: str, destination: Path, *, expected_size: int, allow_file: bool) -> None:
        if isinstance(expected_size, bool) or expected_size <= 0 or expected_size > _MAX_UPDATE_DOWNLOAD_BYTES:
            raise ValueError("更新资源大小超出允许范围。")
        _validate_update_url(url, allow_file=allow_file)
        request = Request(url, headers={"Accept-Encoding": "identity", "User-Agent": _USER_AGENT})
        temporary = destination.with_name(destination.name + ".download")
        temporary.unlink(missing_ok=True)
        downloaded = 0
        try:
            with open_release_url(request, timeout=90, allow_file=allow_file, opener=urlopen) as response, temporary.open("wb") as output:
                content_length = str(response.headers.get("Content-Length", "") or "").strip()
                if content_length:
                    if not content_length.isdigit() or int(content_length) != expected_size:
                        raise ValueError("更新资源响应大小与清单不一致。")
                while chunk := response.read(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > expected_size:
                        raise ValueError("更新资源超过清单声明大小。")
                    output.write(chunk)
            if downloaded != expected_size:
                raise ValueError("更新资源下载不完整。")
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _download_verified(
        self,
        url: str,
        destination: Path,
        expected_sha256: str,
        *,
        expected_size: int,
        allow_file: bool,
    ) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise RuntimeError("更新清单缺少有效的 blockmap SHA256。")
        if expected_size > _MAX_BLOCKMAP_BYTES:
            raise RuntimeError("更新 blockmap 超过允许大小。")
        self._download(url, destination, expected_size=expected_size, allow_file=allow_file)
        if _sha256(destination) != expected_sha256:
            destination.unlink(missing_ok=True)
            raise RuntimeError("更新 blockmap 校验失败。")


def _normalise_release_notes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sections: list[dict[str, Any]] = []
    for raw in value[:8]:
        if not isinstance(raw, dict):
            continue
        items = [str(item).strip() for item in raw.get("items", []) if str(item).strip()][:16]
        if items:
            sections.append({"title": str(raw.get("title", "更新内容")).strip() or "更新内容", "items": items})
    return sections


def _validate_update_url(url: str, *, allow_file: bool = False) -> None:
    validate_release_url(url, allow_file=allow_file)


def _version_key(value: str) -> tuple[tuple[int, ...], int, str]:
    clean = value.strip().lstrip("vV")
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:[-+]?([0-9A-Za-z.-]+))?", clean)
    if not match:
        raise ValueError(f"无效版本号：{value}")
    number_parts = [int(part) for part in match.group(1).split(".")]
    while len(number_parts) > 1 and number_parts[-1] == 0:
        number_parts.pop()
    numbers = tuple(number_parts)
    prerelease = match.group(2) or ""
    return numbers, 1 if not prerelease else 0, prerelease


def _safe_segment(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip(".-") or uuid4().hex


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_release_archive(path: Path) -> None:
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > 200_000 or sum(info.file_size for info in infos) > 8 * 1024**3:
                raise ValueError("更新包内容为空或解压规模异常。")
            names: list[PurePosixPath] = []
            seen: set[str] = set()
            for info in infos:
                raw = info.filename.replace("\\", "/").rstrip("/")
                name = PurePosixPath(raw)
                if (
                    not raw
                    or "\x00" in raw
                    or name.is_absolute()
                    or any(part in {"", ".", ".."} or ":" in part for part in name.parts)
                ):
                    raise ValueError("更新包包含不安全路径。")
                normal = name.as_posix().casefold()
                if normal in seen:
                    raise ValueError("更新包包含重复路径。")
                seen.add(normal)
                if ((info.external_attr >> 16) & 0o170000) == 0o120000 or info.flag_bits & 0x1:
                    raise ValueError("更新包不能包含符号链接或加密文件。")
                names.append(name)
            executables = [name for name in names if name.name.casefold() == "scansci.exe"]
            if len(executables) != 1:
                raise ValueError("更新包中没有 ScanSci.exe。")
            executable = executables[0]
            if len(executable.parts) > 2:
                raise ValueError("更新包的 ScanSci.exe 不在发行根目录。")
    except BadZipFile as error:
        raise ValueError("更新包不是有效的 ZIP 文件。") from error


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


_UPDATER_SCRIPT = r'''param(
  [Parameter(Mandatory = $true)][int]$ParentPid,
  [Parameter(Mandatory = $true)][string]$Archive,
  [Parameter(Mandatory = $true)][string]$InstallDir,
  [Parameter(Mandatory = $true)][string]$ExecutableName,
  [Parameter(Mandatory = $true)][string]$RelaunchArgsPath,
  [Parameter(Mandatory = $true)][string]$TargetVersion,
  [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{32}$')][string]$ReadyToken
)
$ErrorActionPreference = "Stop"
Wait-Process -Id $ParentPid -Timeout 180 -ErrorAction SilentlyContinue
$staging = Join-Path ([IO.Path]::GetTempPath()) ("ScanSci-update-" + [guid]::NewGuid().ToString("N"))
$relaunchArgs = @(Get-Content -Raw -LiteralPath $RelaunchArgsPath | ConvertFrom-Json)
$quotedArgs = (($relaunchArgs | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' ')
$stamp = (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$backup = $InstallDir + ".previous-" + $stamp
$failed = $InstallDir + ".failed-" + $stamp
$errorLog = Join-Path (Split-Path -Parent $RelaunchArgsPath) "apply-update-error.log"
$readyFile = Join-Path (Split-Path -Parent $RelaunchArgsPath) ("startup-ready-" + $stamp + ".json")
$installMoved = $false

function Start-ScanSci([string]$Root, [bool]$RequireReady) {
  if ($RequireReady) {
    Remove-Item -LiteralPath $readyFile -Force -ErrorAction SilentlyContinue
    $env:SCANSCI_UPDATE_READY_FILE = $readyFile
    $env:SCANSCI_UPDATE_READY_TOKEN = $ReadyToken
    $env:SCANSCI_UPDATE_EXPECTED_VERSION = $TargetVersion
  }
  else {
    Remove-Item Env:\SCANSCI_UPDATE_READY_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:\SCANSCI_UPDATE_READY_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:\SCANSCI_UPDATE_EXPECTED_VERSION -ErrorAction SilentlyContinue
  }
  $start = @{
    FilePath = (Join-Path $Root $ExecutableName)
    WorkingDirectory = $Root
    WindowStyle = "Hidden"
    PassThru = $true
  }
  if ($quotedArgs) { $start.ArgumentList = $quotedArgs }
  $process = Start-Process @start
  $deadline = (Get-Date).AddSeconds($(if ($RequireReady) { 45 } else { 2 }))
  do {
    if ($process.HasExited) {
      throw "$ExecutableName exited during startup with code $($process.ExitCode)"
    }
    if ($RequireReady -and (Test-Path -LiteralPath $readyFile -PathType Leaf)) {
      try {
        $ready = Get-Content -Raw -LiteralPath $readyFile | ConvertFrom-Json
        if (
          [string]$ready.token -ceq $ReadyToken -and
          [string]$ready.version -ceq $TargetVersion -and
          [int]$ready.pid -eq $process.Id
        ) {
          $stableUntil = (Get-Date).AddMilliseconds(2500)
          do {
            if ($process.HasExited) {
              throw "$ExecutableName exited after startup confirmation with code $($process.ExitCode)"
            }
            Start-Sleep -Milliseconds 250
          } while ((Get-Date) -lt $stableUntil)
          return
        }
      }
      catch {
        if ($process.HasExited) { throw }
      }
    }
    if (-not $RequireReady -and (Get-Date) -ge $deadline) {
      return
    }
    Start-Sleep -Milliseconds 250
  } while ((Get-Date) -lt $deadline)
  throw "$ExecutableName did not confirm startup within 45 seconds"
}

try {
  New-Item -ItemType Directory -Path $staging | Out-Null
  Expand-Archive -LiteralPath $Archive -DestinationPath $staging -Force
  $payload = $staging
  $children = @(Get-ChildItem -LiteralPath $staging)
  if ($children.Count -eq 1 -and $children[0].PSIsContainer -and (Test-Path -LiteralPath (Join-Path $children[0].FullName $ExecutableName))) {
    $payload = $children[0].FullName
  }
  if (-not (Test-Path -LiteralPath (Join-Path $payload $ExecutableName) -PathType Leaf)) {
    throw "The update package does not contain $ExecutableName"
  }
  Move-Item -LiteralPath $InstallDir -Destination $backup
  $installMoved = $true
  Move-Item -LiteralPath $payload -Destination $InstallDir
  if (-not (Test-Path -LiteralPath (Join-Path $InstallDir $ExecutableName) -PathType Leaf)) {
    throw "The installed update does not contain $ExecutableName"
  }
  Start-ScanSci $InstallDir $true
}
catch {
  $failure = $_
  $rollbackError = ""
  if ($installMoved) {
    try {
      if (Test-Path -LiteralPath $InstallDir) {
        Move-Item -LiteralPath $InstallDir -Destination $failed
      }
      if (Test-Path -LiteralPath $backup) {
        Move-Item -LiteralPath $backup -Destination $InstallDir
        Start-ScanSci $InstallDir $false
      }
    }
    catch {
      $rollbackError = $_ | Out-String
    }
  }
  @(
    "ScanSci update failed at $(Get-Date -Format o)",
    "Backup: $backup",
    "Failed candidate: $failed",
    "Update error:",
    ($failure | Out-String),
    "Rollback error:",
    $rollbackError
  ) | Set-Content -LiteralPath $errorLog -Encoding UTF8
  throw $failure
}
finally {
  if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
  }
  Remove-Item -LiteralPath $readyFile -Force -ErrorAction SilentlyContinue
}
'''

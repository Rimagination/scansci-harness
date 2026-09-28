"""Create an atomic, checksummed archive for ScanSci's local runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def archive_component(
    package_dir: Path,
    output_dir: Path,
    version: str,
    package_url: str = "",
    notice_file: Path | None = None,
    part_size_mb: int = 0,
) -> tuple[Path, Path]:
    executable = package_dir / "ScanSciLocalRuntime.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Local runtime executable is missing: {executable}")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"ScanSciLocalRuntime-{version}.zip"
    temporary = archive.with_suffix(".zip.tmp")
    temporary.unlink(missing_ok=True)
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=1, allowZip64=True) as bundle:
        for source in sorted(path for path in package_dir.rglob("*") if path.is_file()):
            relative = source.relative_to(package_dir)
            bundle.write(source, (Path(package_dir.name) / relative).as_posix())
        if notice_file is not None:
            if not notice_file.is_file():
                raise FileNotFoundError(f"Runtime notice file is missing: {notice_file}")
            bundle.write(
                notice_file,
                (Path(package_dir.name) / "THIRD_PARTY_NOTICES.md").as_posix(),
            )
    temporary.replace(archive)

    archive_sha256 = sha256(archive)
    windows: dict[str, object] = {
        "sha256": archive_sha256,
        "size": archive.stat().st_size,
        "diagnostics": {
            "args": ["--diagnose-output", "{output}"],
            "timeout_seconds": 180,
        },
    }
    if part_size_mb > 0:
        part_size = int(part_size_mb) * 1024 * 1024
        parts: list[dict[str, object]] = []
        with archive.open("rb") as source:
            index = 1
            while chunk := source.read(part_size):
                part = output_dir / f"{archive.name}.part{index:03d}"
                part_tmp = part.with_suffix(part.suffix + ".tmp")
                part_tmp.write_bytes(chunk)
                part_tmp.replace(part)
                if package_url:
                    base_url = package_url.rstrip("/")
                    url = (
                        f"{package_url}.part{index:03d}"
                        if package_url.lower().endswith(".zip")
                        else f"{base_url}/{part.name}"
                    )
                else:
                    url = part.resolve().as_uri()
                parts.append(
                    {
                        "url": url,
                        "sha256": sha256(part),
                        "size": part.stat().st_size,
                    }
                )
                index += 1
        windows["parts"] = parts
    else:
        windows["url"] = package_url or archive.resolve().as_uri()

    manifest = output_dir / "local-transformers.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "local-transformers",
                "version": version,
                "windows": windows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return archive, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--package-url", default="")
    parser.add_argument("--notice-file", type=Path)
    parser.add_argument("--part-size-mb", type=int, default=0)
    args = parser.parse_args()
    archive, manifest = archive_component(
        args.package_dir,
        args.output_dir,
        args.version,
        args.package_url,
        args.notice_file,
        args.part_size_mb,
    )
    print(archive)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

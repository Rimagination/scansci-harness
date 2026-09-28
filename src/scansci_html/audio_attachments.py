"""Private audio attachments used by ScanSci conversations.

Audio files are user inputs, not library sources.  They are kept beside the
workspace and are only opened by the local speech-to-text runtime for the
request that attached them.  The UI replays them from history, so files are
not deleted after transcription; instead every persist call prunes files
older than the retention window (or beyond the retained count) so the
directory cannot grow without bound.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
import re
import time
import uuid
from typing import Any


_ATTACHMENTS_DIR = ".scansci-audio-attachments"
_MAX_AUDIO_FILES = 4
_MAX_AUDIO_BYTES = 50 * 1024 * 1024
_MAX_TOTAL_BYTES = 100 * 1024 * 1024
_RETENTION_DAYS = 30
_MAX_RETAINED_FILES = 200
_AUDIO_TYPES = {
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/aac": ".aac",
    "audio/webm": ".webm",
}
_DATA_URL = re.compile(
    r"^data:(audio/[a-zA-Z0-9.+-]+)(?:;[a-zA-Z0-9!#$&^_.+-]+=[a-zA-Z0-9!#$&^_.+:-]+)*;base64,([A-Za-z0-9+/=]+)$",
    re.IGNORECASE,
)
_ATTACHMENT_ID = re.compile(r"^audio-[a-f0-9]{32}$")


def persist_audio_attachments(workspace: str | Path, audio: object) -> list[dict[str, Any]]:
    """Validate audio data URLs and persist them under the local workspace."""

    if audio in (None, ""):
        return []
    if not isinstance(audio, list):
        raise ValueError("audio must be a list")
    if len(audio) > _MAX_AUDIO_FILES:
        raise ValueError(f"一次最多可以发送 {_MAX_AUDIO_FILES} 个音频文件")

    root = _attachments_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    total = 0
    for index, item in enumerate(audio, start=1):
        if not isinstance(item, dict):
            raise ValueError("音频附件格式无效")
        attachment_id = str(item.get("id", "")).strip()
        if attachment_id and not str(item.get("data_url", "")).strip():
            path, mime_type = audio_attachment_asset(workspace, attachment_id)
            raw = path.read_bytes()
            reused_id = attachment_id
        else:
            mime_type, raw = _decode_data_url(str(item.get("data_url", "")))
            reused_id = ""
        if len(raw) > _MAX_AUDIO_BYTES:
            raise ValueError("单个音频文件不能超过 50 MB")
        total += len(raw)
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("本次音频附件总大小不能超过 100 MB")
        attachment_id = reused_id or f"audio-{uuid.uuid4().hex}"
        if not reused_id:
            target = root / f"{attachment_id}{_AUDIO_TYPES[mime_type]}"
            target.write_bytes(raw)
        name = _display_name(str(item.get("name", "")), index, _AUDIO_TYPES[mime_type])
        records.append(
            {
                "id": attachment_id,
                "name": name,
                "mime_type": _canonical_mime_type(mime_type),
                "size": len(raw),
                "audio_url": f"/api/audio-attachments/{attachment_id}",
            }
        )
    _prune_attachments(root)
    return records


def _prune_attachments(root: Path) -> None:
    """Bound disk usage: drop files past the retention window or the cap.

    Runs opportunistically on every persist so no background scheduler is
    needed.  The UI replays audio from history, so old-but-recent files stay;
    only files older than the retention window (or beyond the newest kept
    count) are removed.
    """

    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    try:
        candidates = [path for path in root.glob("audio-*") if path.is_file()]
    except OSError:
        return
    cutoff = time.time() - _RETENTION_DAYS * 86400
    stale = {path for path in candidates if mtime(path) < cutoff}
    candidates.sort(key=mtime, reverse=True)
    overflow = set(candidates[_MAX_RETAINED_FILES:])
    for path in stale | overflow:
        try:
            path.unlink()
        except OSError:
            pass


def audio_attachment_asset(workspace: str | Path, attachment_id: str) -> tuple[Path, str]:
    """Resolve a stored audio file without allowing workspace traversal."""

    if not _ATTACHMENT_ID.fullmatch(str(attachment_id or "")):
        raise FileNotFoundError("音频附件不存在")
    root = _attachments_root(workspace)
    matches = [path for path in root.glob(f"{attachment_id}.*") if path.is_file()]
    if len(matches) != 1:
        raise FileNotFoundError("音频附件不存在")
    path = matches[0].resolve()
    if root.resolve() not in path.parents:
        raise FileNotFoundError("音频附件不存在")
    mime_type = next((kind for kind, extension in _AUDIO_TYPES.items() if path.suffix.lower() == extension), "")
    if not mime_type:
        raise FileNotFoundError("音频附件不存在")
    # Prefer the canonical media type in responses even when the browser sent
    # audio/x-wav or audio/mp3.
    return path, _canonical_mime_type(mime_type)


def _canonical_mime_type(mime_type: str) -> str:
    canonical = {
        "audio/wave": "audio/wav",
        "audio/x-wav": "audio/wav",
        "audio/mp3": "audio/mpeg",
        "audio/x-m4a": "audio/mp4",
    }
    return canonical.get(mime_type, mime_type)


def _attachments_root(workspace: str | Path) -> Path:
    return Path(workspace).resolve().parent / _ATTACHMENTS_DIR


def _decode_data_url(value: str) -> tuple[str, bytes]:
    match = _DATA_URL.fullmatch(value.strip())
    if match is None:
        raise ValueError("仅支持 WAV、MP3、M4A、FLAC、OGG、AAC 或 WebM 音频")
    mime_type = match.group(1).lower()
    if mime_type not in _AUDIO_TYPES:
        raise ValueError("仅支持 WAV、MP3、M4A、FLAC、OGG、AAC 或 WebM 音频")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("音频数据损坏") from error
    if not raw:
        raise ValueError("音频内容为空")
    return mime_type, raw


def _display_name(value: str, index: int, extension: str) -> str:
    clean = " ".join(value.replace("\x00", "").split()).strip()
    if clean:
        return clean[:120]
    return f"音频 {index}{extension}"

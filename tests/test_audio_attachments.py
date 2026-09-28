import base64
from pathlib import Path

import pytest

from scansci_html.audio_attachments import audio_attachment_asset, persist_audio_attachments


def _data_url(mime: str = "audio/wave", payload: bytes = b"RIFF-sample") -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def test_audio_attachment_is_persisted_and_resolved_with_canonical_mime(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    records = persist_audio_attachments(
        workspace,
        [{"name": "访谈.wav", "mime_type": "audio/wave", "data_url": _data_url()}],
    )

    assert records[0]["name"] == "访谈.wav"
    path, mime_type = audio_attachment_asset(workspace, records[0]["id"])
    assert path.is_file()
    assert path.read_bytes() == b"RIFF-sample"
    assert mime_type == "audio/wav"


def test_browser_recording_accepts_webm_opus_codec_parameter(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    records = persist_audio_attachments(
        workspace,
        [
            {
                "name": "recording.webm",
                "mime_type": "audio/webm;codecs=opus",
                "data_url": _data_url("audio/webm;codecs=opus", b"webm-opus-sample"),
            }
        ],
    )

    path, mime_type = audio_attachment_asset(workspace, records[0]["id"])
    assert path.suffix == ".webm"
    assert path.read_bytes() == b"webm-opus-sample"
    assert mime_type == "audio/webm"


def test_history_audio_metadata_reuses_the_existing_local_attachment(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    original = persist_audio_attachments(
        workspace,
        [{"name": "访谈.wav", "data_url": _data_url()}],
    )[0]

    retried = persist_audio_attachments(
        workspace,
        [{"id": original["id"], "name": original["name"], "mime_type": original["mime_type"]}],
    )

    assert retried == [original]


def test_audio_attachment_rejects_non_audio_and_traversal(tmp_path: Path):
    with pytest.raises(ValueError, match="仅支持"):
        persist_audio_attachments(
            tmp_path / "workspace.sqlite",
            [{"name": "not-image.png", "data_url": _data_url("image/png")}],
        )

    with pytest.raises(FileNotFoundError):
        audio_attachment_asset(tmp_path / "workspace.sqlite", "audio-../secret")

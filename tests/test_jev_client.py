import io
import json
from urllib.error import HTTPError

import pytest

from scansci_html import jev_client


class _Response:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def test_ask_jev_uses_the_official_typed_evaluation_request(monkeypatch):
    calls = []

    def opener(req, *, timeout):
        calls.append((req, timeout, json.loads(req.data.decode("utf-8"))))
        return _Response({"model": "jev-latest", "answers": {"decision": {"type": "choice", "choice": "yes"}}})

    result = jev_client.ask_jev(
        api_key="private-key",
        state={"task": "connection"},
        questions={"decision": {"type": "choice", "instructions": "Choose yes."}},
        opener=opener,
    )

    assert result["model"] == "jev-latest"
    assert len(calls) == 1
    req, timeout, body = calls[0]
    assert req.full_url == jev_client.DEFAULT_ENDPOINT
    assert req.method == "POST"
    assert req.get_header("Authorization") == "Bearer private-key"
    assert req.get_header("Content-type") == "application/json"
    assert timeout == jev_client.DEFAULT_TIMEOUT_SECONDS
    assert body == {
        "state": {"task": "connection"},
        "model": "jev-latest",
        "questions": {"decision": {"type": "choice", "instructions": "Choose yes."}},
    }


def test_ask_jev_retries_rate_limits_without_exposing_key(monkeypatch):
    calls = []
    delays = []

    def opener(req, *, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise HTTPError(req.full_url, 429, "rate limited", {}, io.BytesIO())
        return _Response({"answers": {"check": {"type": "noul", "noul": 1}}})

    result = jev_client.ask_jev(
        api_key="private-key",
        state="check",
        questions={"check": {"type": "noul", "instructions": "Check."}},
        opener=opener,
        sleeper=delays.append,
    )

    assert result["answers"]["check"]["type"] == "noul"
    assert len(calls) == 2
    assert delays == [0.5]


def test_ask_jev_rejects_non_https_endpoint_without_network_call():
    with pytest.raises(jev_client.JEVError, match="HTTPS"):
        jev_client.ask_jev(
            api_key="private-key",
            endpoint="http://example.invalid/v1/systemone",
            state="check",
            questions={"check": {"type": "noul", "instructions": "Check."}},
        )

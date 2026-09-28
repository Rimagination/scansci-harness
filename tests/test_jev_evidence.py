from __future__ import annotations

import json

from scansci_html import jev_evidence


def _hits() -> list[dict[str, object]]:
    return [
        {
            "evidence_id": "evidence-a",
            "paper": "Paper A",
            "section": "Results",
            "matched_text": "The intervention changed the measured outcome.",
            "context_before": "Participants were randomized.",
            "context_after": "The effect was measured at week 8.",
            "html_anchor": "results-p1-s0001",
            "score": 0.92,
        },
        {
            "evidence_id": "evidence-b",
            "paper": "Paper B",
            "section": "Discussion",
            "matched_text": "The treatment increased cortical activity in language regions.",
            "context_before": "The imaging analysis was prespecified.",
            "context_after": "The finding was stronger in the treatment group.",
            "html_anchor": "discussion-p2-s0003",
            "score": 0.51,
        },
    ]


def test_jev_reorders_bounded_candidates_and_returns_a_coverage_signal(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(jev_evidence, "get_provider_api_key", lambda *_args: "private-key")

    def ask(**kwargs):
        calls.append(kwargs)
        return {
            "answers": {
                "supports_0": {"type": "noul", "noul": 0.2},
                "supports_1": {"type": "noul", "noul": 0.9},
                "coverage_sufficient": {"type": "noul", "noul": 0.8},
            }
        }

    monkeypatch.setattr(jev_evidence, "ask_jev", ask)
    result = jev_evidence.review_evidence_candidates(
        workspace=str(tmp_path / "workspace.sqlite"),
        provider={"id": "jev", "enabled": True, "base_url": "https://api.typesafe.ai/v1/systemone"},
        question="Which passage directly answers the treatment question?",
        hits=_hits(),
    )

    assert [item["evidence_id"] for item in result["hits"]] == ["evidence-b", "evidence-a"]
    assert result["hits"][0]["jev_support_probability"] == 0.9
    assert result["review"]["status"] == "applied"
    assert result["review"]["coverage_probability"] == 0.8
    assert result["review"]["coverage_signal_only"] is True
    state = calls[0]["state"]
    assert state["question"].startswith("Which passage")
    assert state["candidates"][0]["matched_text"] == _hits()[0]["matched_text"]
    assert "html_anchor" not in json.dumps(state, ensure_ascii=False)
    assert "private-key" not in json.dumps(result, ensure_ascii=False)


def test_jev_disabled_keeps_local_order_without_network_call(monkeypatch, tmp_path) -> None:
    def fail(**_kwargs):
        raise AssertionError("disabled JEV must not be called")

    monkeypatch.setattr(jev_evidence, "ask_jev", fail)
    result = jev_evidence.review_evidence_candidates(
        workspace=str(tmp_path / "workspace.sqlite"),
        provider={"id": "jev", "enabled": False},
        question="Question",
        hits=_hits(),
    )

    assert [item["evidence_id"] for item in result["hits"]] == ["evidence-a", "evidence-b"]
    assert result["review"]["status"] == "not_run"
    assert result["review"]["reason"] == "provider_disabled"


def test_jev_failure_degrades_to_local_order(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(jev_evidence, "get_provider_api_key", lambda *_args: "private-key")
    monkeypatch.setattr(
        jev_evidence,
        "ask_jev",
        lambda **_kwargs: (_ for _ in ()).throw(
            jev_evidence.JEVError("upstream secret", status_code=503, retryable=True)
        ),
    )
    result = jev_evidence.review_evidence_candidates(
        workspace=str(tmp_path / "workspace.sqlite"),
        provider={"id": "jev", "enabled": True},
        question="Question",
        hits=_hits(),
    )

    assert [item["evidence_id"] for item in result["hits"]] == ["evidence-a", "evidence-b"]
    assert result["review"]["status"] == "degraded"
    assert result["review"]["reason"] == "jev_unavailable"
    assert result["review"]["http_status"] == 503
    assert "upstream secret" not in json.dumps(result, ensure_ascii=False)

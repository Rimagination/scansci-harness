from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scansci_html.builtin_skills import builtin_skill_asset_path, default_skill_records


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "scansci-evidence"


def test_public_evidence_plugin_manifest_and_skill_are_complete() -> None:
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "scansci-evidence"
    assert manifest["license"] == "MIT"
    assert manifest["skills"] == "./skills/"
    assert (PLUGIN / "skills" / "evidence-retrieval" / "SKILL.md").is_file()
    assert (PLUGIN / "skills" / "evidence-retrieval" / "references" / "host-tool-contract.md").is_file()
    assert (PLUGIN / "scripts" / "normalize_evidence.py").is_file()


def test_evidence_retrieval_is_shipped_as_a_builtin_skill() -> None:
    records = {item["id"]: item for item in default_skill_records()}
    assert records["evidence-retrieval"]["path"] == "builtin:evidence-retrieval"
    asset = builtin_skill_asset_path("evidence-retrieval")
    assert (asset / "SKILL.md").is_file()
    assert "search_local_evidence" in (asset / "SKILL.md").read_text(encoding="utf-8")
    assert (PLUGIN / "skills" / "evidence-retrieval" / "SKILL.md").read_text(encoding="utf-8") == (
        asset / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert (PLUGIN / "skills" / "evidence-retrieval" / "references" / "host-tool-contract.md").read_text(
        encoding="utf-8"
    ) == (asset / "references" / "host-tool-contract.md").read_text(encoding="utf-8")


def test_normalize_evidence_script_emits_safe_envelope() -> None:
    payload = {
        "query": "Which result is supported?",
        "hits": [
            {
                "evidence_id": "paper-1.s0001",
                "doc_id": "paper-1",
                "paper": "Paper 1",
                "section": "Results",
                "html_anchor": "results-p1-s0001",
                "matched_text": "The measured result was higher.",
                "score": 0.83,
                "jev_support_probability": 0.91,
            }
        ],
        "jev_review": {
            "status": "applied",
            "coverage_probability": 0.9,
            "api_key": "must-not-leak",
        },
    }
    completed = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "normalize_evidence.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["schema_version"] == "scansci.evidence-envelope.v1"
    assert result["status"] == "ready"
    assert result["hits"][0]["evidence_id"] == "paper-1.s0001"
    assert result["review"]["status"] == "applied"
    assert "api_key" not in completed.stdout


def test_normalize_evidence_script_rejects_unanchored_candidates() -> None:
    completed = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "normalize_evidence.py")],
        input=json.dumps({"query": "q", "hits": [{"matched_text": "text"}]}),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "evidence_id" in completed.stderr


def test_normalize_evidence_script_rejects_sensitive_text_and_preserves_degraded_status() -> None:
    payload = {
        "status": "degraded",
        "query": "safe question",
        "hits": [
            {
                "evidence_id": "paper-1.s0001",
                "matched_text": "A normal evidence passage.",
            }
        ],
        "jev_review": {"status": "not_run", "candidate_count": 1, "reviewed_count": 0},
    }
    completed = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "normalize_evidence.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "degraded"

    secret_payload = {
        "query": "api_key=do-not-emit",
        "hits": [],
    }
    rejected = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "normalize_evidence.py")],
        input=json.dumps(secret_payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 2
    assert "do-not-emit" not in rejected.stderr


def test_normalize_evidence_script_rejects_invalid_review_counts() -> None:
    payload = {
        "query": "q",
        "hits": [],
        "jev_review": {"status": "applied", "candidate_count": 1, "reviewed_count": 2},
    }
    completed = subprocess.run(
        [sys.executable, str(PLUGIN / "scripts" / "normalize_evidence.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "reviewed_count" in completed.stderr

"""Run stable, local integration contracts for ScanSci internal beta.

This is deliberately network-independent.  It verifies the product's
decisions and user-visible contracts using a temporary workspace, while the
separate provider and source tests cover real external integrations.  The
script is suitable for CI and for a pre-release source gate.
"""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from typing import Any, Callable
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scansci_html.app_settings import save_settings  # noqa: E402
from scansci_html.evidence_store import index_evidence_library  # noqa: E402
from scansci_html.research_agent import _structured_recovery  # noqa: E402
from scansci_html.webapp import NotebookWebApp, create_notebook_server  # noqa: E402
from scansci_html.workspace import sync_sources_from_evidence_store  # noqa: E402


def _payload(response: Any) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


def _configure_local_evidence(workspace: Path) -> None:
    save_settings(
        workspace,
        {
            "active_model": {"provider_id": "local-evidence", "model_id": "evidence-retrieval"},
            "providers": [{
                "id": "local-evidence",
                "name": "Local evidence engine",
                "kind": "local",
                "models": [{"id": "evidence-retrieval", "name": "Evidence retrieval"}],
            }],
            "model_roles": {
                "reasoning": "provider:local-evidence:evidence-retrieval",
                "writing": "provider:local-evidence:evidence-retrieval",
                "retrieval": "local:builtin-evidence",
                "embedding": "local:builtin-evidence",
                "reranking": "local:builtin-evidence",
                "vision": "",
                "slides": "provider:local-evidence:evidence-retrieval",
            },
        },
    )


def _temporary_app(root: Path) -> tuple[NotebookWebApp, Path, Path]:
    library = root / "library"
    library.mkdir()
    (library / "evidence.html").write_text(
        """
        <article class="paper" data-doi="10.1234/internal-beta">
          <h1>Internal Beta Evidence Paper</h1>
          <h2>Results</h2>
          <p id="result">Galunisertib reduced regulatory T cells after treatment.</p>
        </article>
        """,
        encoding="utf-8",
    )
    evidence = root / "evidence.sqlite"
    index_evidence_library(library, db_path=evidence, inject_evidence_html=True, min_sentence_length=10)
    workspace = root / "workspace.sqlite"
    sync_sources_from_evidence_store(workspace, evidence, notebook_id="internal-beta")
    _configure_local_evidence(workspace)
    return NotebookWebApp(workspace=workspace, evidence_db=evidence), workspace, evidence


def _health_contract(workspace: Path) -> dict[str, Any]:
    # Keep this endpoint check independent of the evidence answer's SQLite
    # handles.  It verifies the serving runtime, not indexing persistence.
    server = create_notebook_server(workspace=workspace, evidence_db=workspace.with_name("health-evidence.sqlite"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/health", timeout=3) as response:
            health = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        # BaseHTTPRequestHandler closes synchronously, but the server retains
        # its app closure until the object becomes unreachable.  Releasing it
        # here makes this contract deterministic on Windows file locking.
        server = None
        thread = None
        gc.collect()
    expected_package = (SOURCE_ROOT / "scansci_html").resolve()
    assert health["status"] == "ok"
    assert health["runtime_kind"] == "source"
    assert Path(health["package_root"]).resolve() == expected_package
    return {"runtime_kind": health["runtime_kind"], "package_root": health["package_root"]}


def _check_evidence_answer(app: NotebookWebApp) -> dict[str, Any]:
    answer = _payload(
        app.dispatch(
            "POST",
            "/api/ask",
            json.dumps({"question": "What did Galunisertib reduce?", "idempotency_key": "internal-beta-evidence-answer-1", "retrieval_quality": "balanced"}).encode("utf-8"),
        )
    )
    reader = dict(answer["reader_answer"])
    assert answer["citation_verification"]["passed"] is True
    assert int(reader["citation_count"]) >= 1
    return {"citation_count": reader["citation_count"]}


def _check_public_search_route(app: NotebookWebApp) -> dict[str, Any]:
    route = _payload(
        app.dispatch(
            "POST",
            "/api/task-routing/preview",
            json.dumps({"question": "联网检索 2023 年以来 RAG factuality evaluation 的关键论文"}).encode("utf-8"),
        )
    )
    assert route["workflow_type"] == "academic_search"
    assert route["scope"] == "public_academic"
    return {"workflow_type": route["workflow_type"], "scope": route["scope"]}


def _check_download_timeout_recovery() -> dict[str, Any]:
    recovery = _structured_recovery(RuntimeError("scansci-pdf timed out after 60s"), stage_key="search")
    assert recovery["code"] == "stage_timeout"
    assert recovery["retryable"] is True
    assert any(action.get("id") == "retry" for action in recovery["actions"])
    return {"recovery_code": recovery["code"], "retryable": recovery["retryable"]}


def _check_ppt_templates(app: NotebookWebApp) -> dict[str, Any]:
    catalog = _payload(app.dispatch("GET", "/api/slides/templates"))
    templates = list(catalog.get("templates", []) or [])
    assert templates
    return {"template_count": len(templates)}


def run_contracts() -> dict[str, Any]:
    """Execute the five release-blocking internal-beta user contracts."""

    report: dict[str, Any] = {"schema_version": 1, "status": "passed", "checks": []}
    with TemporaryDirectory(prefix="scansci-internal-beta-") as raw_root:
        app, workspace, evidence = _temporary_app(Path(raw_root))
        checks: list[tuple[str, Callable[[], dict[str, Any]]]] = [
            ("runtime-provenance", lambda: _health_contract(workspace)),
            ("evidence-answer-citations", lambda: _check_evidence_answer(app)),
            ("public-academic-search-routing", lambda: _check_public_search_route(app)),
            ("download-timeout-recovery", _check_download_timeout_recovery),
            ("ppt-template-catalog", lambda: _check_ppt_templates(app)),
        ]
        for check_id, check in checks:
            try:
                details = check()
                report["checks"].append({"id": check_id, "status": "passed", "details": details})
            except Exception as error:  # retain all evidence for release debugging
                report["checks"].append({"id": check_id, "status": "failed", "error": f"{type(error).__name__}: {error}"})
                report["status"] = "failed"
                break
        # Evidence accessors may cache SQLite statements; release their owning
        # app before TemporaryDirectory removes the test workspace on Windows.
        checks.clear()
        del app
        gc.collect()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify ScanSci internal-beta product contracts.")
    parser.add_argument("--output", required=True, help="Path for the JSON verification report.")
    args = parser.parse_args(argv)
    report = run_contracts()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

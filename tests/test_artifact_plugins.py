from __future__ import annotations

from pathlib import Path

import pytest

from scansci_html.app_settings import load_settings
from scansci_html.artifact_plugins import (
    compile_latex_artifact,
    create_document_artifact,
    create_pdf_artifact,
    create_presentation_artifact,
    create_spreadsheet_artifact,
    find_tectonic,
    plugin_runtime_statuses,
)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace.sqlite"
    workspace.touch()
    return workspace


def test_builtin_office_plugins_are_merged_into_existing_settings(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    settings = load_settings(workspace)
    plugins = {item["id"]: item for item in settings["plugins"]}

    assert {"zotero", "documents", "pdf", "spreadsheets", "presentations", "latex"} <= set(plugins)
    assert all(plugins[plugin_id]["builtin"] for plugin_id in plugins)
    assert all(plugins[plugin_id]["runtime"]["status"] for plugin_id in plugins)


def test_office_plugins_create_verified_editable_artifacts(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    document = create_document_artifact(
        {"title": "研究记录", "content": "# 结论\n- 第一项\n- 第二项"}, workspace=workspace
    )
    pdf = create_pdf_artifact(
        {"title": "证据摘要", "content": "这是一份由 ScanSci 生成并校验的 PDF。"}, workspace=workspace
    )
    spreadsheet = create_spreadsheet_artifact(
        {"title": "实验数据", "columns": ["样本", "数值"], "rows": [["A", 1], ["B", "=2*2"]]},
        workspace=workspace,
    )
    presentation = create_presentation_artifact(
        {"title": "研究汇报", "slides": [{"title": "问题", "bullets": ["背景", "假设"]}]},
        workspace=workspace,
    )

    for artifact in (document, pdf, spreadsheet, presentation):
        path = Path(artifact["file_path"])
        assert artifact["ok"] is True
        assert path.is_file()
        assert path.stat().st_size > 0
    assert spreadsheet["rows"] == 2
    assert presentation["slides"] == 2


def test_presentation_renders_model_bullets_without_markdown_markers(tmp_path: Path) -> None:
    from pptx import Presentation

    artifact = create_presentation_artifact(
        {
            "title": "Plot A",
            "slides": [{
                "title": "Results",
                "bullets": ["**Tree Count:** 12 trees\n  * **Evidence ID:** paper.s0001"],
            }],
        },
        workspace=_workspace(tmp_path),
    )

    paragraphs = Presentation(artifact["file_path"]).slides[1].placeholders[1].text_frame.paragraphs
    assert [paragraph.text for paragraph in paragraphs] == [
        "Tree Count: 12 trees", "Evidence ID: paper.s0001",
    ]
    assert paragraphs[1].level == 1
    assert paragraphs[0].runs[0].font.bold is True


def test_latex_plugin_compiles_with_detected_runtime(tmp_path: Path) -> None:
    if find_tectonic() is None:
        pytest.skip("Tectonic is not installed on this test machine")
    workspace = _workspace(tmp_path)
    artifact = compile_latex_artifact(
        {
            "title": "LaTeX smoke test",
            "source": r"\documentclass{article}\begin{document}ScanSci LaTeX\end{document}",
        },
        workspace=workspace,
    )
    assert artifact["runtime"] == "tectonic"
    assert Path(artifact["file_path"]).is_file()


def test_find_tectonic_does_not_probe_codex_plugin_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    import scansci_html.artifact_plugins as artifact_plugins

    class _NoManagedTectonic:
        def executable(self) -> None:
            return None

    monkeypatch.setattr(artifact_plugins, "default_tectonic_component", lambda: _NoManagedTectonic())
    monkeypatch.setattr(artifact_plugins.shutil, "which", lambda _name: None)

    def _unexpected_home() -> Path:
        raise AssertionError("find_tectonic must not inspect the Codex plugin cache")

    monkeypatch.setattr(Path, "home", staticmethod(_unexpected_home))
    assert artifact_plugins.find_tectonic() is None


def test_plugin_runtime_statuses_cover_every_builtin() -> None:
    statuses = plugin_runtime_statuses()
    assert set(statuses) == {"zotero", "documents", "pdf", "spreadsheets", "presentations", "latex"}

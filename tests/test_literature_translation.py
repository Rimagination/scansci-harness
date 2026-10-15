import base64
import json
from pathlib import Path
import threading
import time

import pymupdf
import pytest
import requests

from scansci_html.literature_translation import _TranslationFormatError, _model_translate, _native_blocks, _numbers_changed, _ollama_translate, _preserve_source_block, _translate_chunks, bind_translation_library, register_translation, translate_pdf, translation_root
from scansci_html.workspace import load_workspace_summary, translation_folder_files
from scansci_html.webapp import NotebookWebApp


def test_translated_pdf_is_local_and_linked_in_translation_library(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    source = tmp_path / "paper.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_textbox(
            pymupdf.Rect(50, 50, 500, 300),
            "Scientific methods improve reproducibility. The experiment measured water quality. " * 4,
        )
        document.save(source)

    output = translation_root(workspace) / "example" / "translated.pdf"
    result = translate_pdf(
        source,
        output,
        workspace=workspace,
        translator=lambda _text, _language: "科学方法提高研究的可重复性。该实验测量了水质并记录了各项指标。" * 4,
    )
    with pymupdf.open(output) as translated:
        assert translated.page_count == 1
        assert "科学方法" in translated[0].get_text()
        assert translated[0].get_images(), "可见中文应固定为图像，避免不同 PDF 预览器显示方框"
    saved = register_translation(
        workspace,
        output,
        run_id="run-example",
        source_name=source.name,
        target_language="简体中文",
        result=result,
    )
    notebook = load_workspace_summary(workspace, notebook_id="translation-library")["notebooks"][0]
    assert notebook["metadata"]["library_kind"] == "translation"
    assert notebook["root_path"] == str(translation_root(workspace))
    assert notebook["translation_files"][0]["id"] == saved["file_id"]
    assert notebook["counts"]["sources"] == 0
    assert output.with_suffix(".translation.json").is_file()
    assert source.is_file()


def test_translation_pins_formula_without_chinese_characters(tmp_path: Path):
    source = tmp_path / "formula.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_textbox(
            pymupdf.Rect(40, 40, 400, 150),
            "Equation for the Shannon diversity index is shown below.",
        )
        document.save(source)

    output = tmp_path / "formula-translation.pdf"
    translate_pdf(
        source, output, workspace=tmp_path / "workspace.sqlite",
        translator=lambda _text, _language: "H = -∑(P_i ln P_i)",
    )
    with pymupdf.open(output) as document:
        assert "H = -∑(P_i ln P_i)" in document[0].get_text()
        assert document[0].get_images(), "纯公式译文也需要可见字形图层"


def test_translation_keeps_title_and_section_typography(tmp_path: Path):
    source = tmp_path / "paper.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(50, 40, 500, 90), "Layout Preservation Translation Trial", fontsize=17)
        page.insert_textbox(pymupdf.Rect(50, 120, 500, 145), "Abstract", fontsize=10)
        page.insert_textbox(pymupdf.Rect(50, 155, 500, 250), "We sampled plants in the treatment group.", fontsize=10)
        document.save(source)

    output = tmp_path / "translated.pdf"
    translations = {
        "Layout Preservation Translation Trial": "科研版式翻译试验",
        "Abstract": "摘要",
        "We sampled plants in the treatment group.": "我们采集了处理组植物。",
    }
    translate_pdf(source, output, workspace=tmp_path / "workspace.sqlite", translator=lambda text, _language: translations[text])
    with pymupdf.open(output) as document:
        spans = [span for block in document[0].get_text("dict")["blocks"] if "lines" in block for line in block["lines"] for span in line["spans"]]
        title, heading, body = (next(span for span in spans if label in span["text"]) for label in translations.values())
        assert title["size"] > heading["size"] > body["size"]
        assert document[0].search_for("科研版式翻译试验")
        assert document[0].search_for("我们采集了处理组植物")


def test_translation_library_links_pdfs_added_to_selected_folder(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    parent = tmp_path / "selected"
    parent.mkdir()
    bound = bind_translation_library(workspace, parent)
    root = Path(bound["root_path"])
    assert root == parent / "翻译库"
    (root / "manual.pdf").write_bytes(b"%PDF-1.4\n")
    notebook = load_workspace_summary(workspace, notebook_id="translation-library")["notebooks"][0]
    assert notebook["translation_files"][0]["title"] == "manual"
    assert translation_root(workspace) == root


def test_translation_preserves_blank_page_before_text(tmp_path: Path):
    source = tmp_path / "paper.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.new_page().insert_text((50, 80), "Experimental method")
        document.save(source)
    output = translation_root(tmp_path / "workspace.sqlite") / "translated.pdf"
    result = translate_pdf(source, output, workspace=tmp_path / "workspace.sqlite", translator=lambda _text, _language: "实验方法")
    with pymupdf.open(output) as document:
        assert document.page_count == 2
        assert not document[0].get_text().strip()
        assert "实验方法" in document[1].get_text()
    assert any("第 1 页" in warning for warning in result["warnings"])


def test_translation_splits_long_body_block_and_preserves_references(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    source = tmp_path / "paper.pdf"
    long_body = "The treatment improved soil properties and plant diversity. " * 55
    with pymupdf.open() as document:
        body = document.new_page(width=900, height=1200)
        assert body.insert_textbox(pymupdf.Rect(40, 40, 860, 1000), long_body, fontsize=9) >= 0
        references = document.new_page()
        references.insert_text((50, 80), "References")
        references.insert_text((50, 150), "Smith, J. Original research title. DOI: 10.1000/test.")
        document.new_page().insert_text((50, 80), "Jones, A. Another original title.")
        document.save(source)

    with pymupdf.open(source) as document:
        blocks = _native_blocks(document[0])
        assert len(blocks) == 1
        assert len(blocks[0][1]) > 2200

    calls = []
    output = translation_root(workspace) / "translated.pdf"
    result = translate_pdf(
        source, output, workspace=workspace,
        translator=lambda text, _language: (calls.append(text) or "处理提高了土壤质量和植物多样性。"),
    )
    assert len(calls) > len(blocks)
    assert all(len(text) <= 2200 for text in calls)
    assert all(text.endswith(".") for text in calls)
    assert result["pages"] == 3
    assert result["blocks"] == 1
    assert any("参考文献保留原文" in warning for warning in result["warnings"])
    with pymupdf.open(output) as document:
        assert "处理提高了土壤质量" in document[0].get_text()
        assert "Smith, J." in document[1].get_text()
        assert "Jones, A." in document[2].get_text()


def test_translation_retries_one_empty_model_response(monkeypatch: pytest.MonkeyPatch):
    calls = []

    def flaky_model(*_args, **_kwargs):
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("The model returned an empty response")
        return "译文"

    monkeypatch.setattr("scansci_html.llm.complete_chat_text", flaky_model)
    route = {"provider_kind": "local", "base_url": "http://127.0.0.1:11434/v1", "api_key": "local", "model_id": "qwen3.5:4b", "provider_id": "local-test"}
    assert _model_translate("Source text", "简体中文", route) == "译文"
    assert len(calls) == 2


def test_translation_keeps_table_values_and_source_identifiers_unchanged():
    assert _preserve_source_block("S\nNS")
    assert _preserve_source_block("pH\n7.900 ± 0.046a\n7.913 ± 0.061a\nTm (°C)\n23.044 ± 0.471b")
    assert _preserve_source_block("Land 2023, 12, 367\n4 of 14")
    assert _preserve_source_block("doi.org/10.3390/land12020367")
    assert _preserve_source_block("creativecommons.org/licenses/by/")
    assert not _preserve_source_block("distributed\nunder\nthe\nterms\nand")
    assert not _preserve_source_block("The results were significant.\nSee https://example.org/data for details.")
    assert not _preserve_source_block("No significant difference was observed between S and NS (p < 0.05).")


def test_first_page_publication_sidebar_stays_original(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    source = tmp_path / "paper.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((35, 200), "Received: 4 January 2023")
        assert page.insert_textbox(
            pymupdf.Rect(166, 100, 560, 300),
            "The experiment measured solar radiation and soil temperature. " * 6,
        ) >= 0
        document.save(source)
    calls = []
    output = translation_root(workspace) / "translated.pdf"
    translate_pdf(source, output, workspace=workspace, translator=lambda text, _language: (calls.append(text) or "实验测量了太阳辐射和土壤温度。"))
    assert len(calls) == 1 and "Received" not in calls[0]
    with pymupdf.open(output) as document:
        assert "Received: 4 January 2023" in document[0].get_text()
        assert "实验测量" in document[0].get_text()


def test_local_ollama_translation_disables_thinking_and_keeps_one_block(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": json.dumps({"translation": "第一段。"})}}

    def post(session, url, **kwargs):
        captured.update(url=url, trust_env=session.trust_env, **kwargs)
        return Response()

    monkeypatch.setenv("HTTP_PROXY", "http://example-proxy.invalid:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setattr("scansci_html.literature_translation.requests.Session.post", post)
    route = {"base_url": "http://127.0.0.1:11434/v1", "model_id": "qwen3.5:4b"}
    assert _ollama_translate("First.", "简体中文", route) == "第一段。"
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["trust_env"] is False
    assert captured["json"]["think"] is False
    assert captured["json"]["format"]["properties"]["translation"]["type"] == "string"


def test_local_model_interruption_reports_resume(monkeypatch: pytest.MonkeyPatch):
    def stopped(_session, *_args, **_kwargs):
        raise requests.ConnectionError("runner stopped")

    monkeypatch.setattr("scansci_html.literature_translation.requests.Session.post", stopped)
    route = {"base_url": "http://127.0.0.1:11434/v1", "model_id": "qwen3.5:4b"}
    with pytest.raises(RuntimeError, match="断点继续"):
        _ollama_translate("First.", "简体中文", route)


def test_invalid_local_format_falls_back_to_compatible_endpoint(monkeypatch: pytest.MonkeyPatch):
    import scansci_html.literature_translation as translation

    calls = []

    def invalid_native(*_args):
        calls.append("native")
        raise _TranslationFormatError("invalid")

    monkeypatch.setattr(translation, "_ollama_translate", invalid_native)
    monkeypatch.setattr(translation, "_model_translate", lambda *_args: (calls.append("compatible") or "译文。"))
    route = {"provider": {"runtime": "ollama"}, "base_url": "http://127.0.0.1:11434/v1", "model_id": "qwen3.5:4b"}
    values, hits, requests_made, _seconds = _translate_chunks(["First."], "简体中文", route, None, None)
    assert values == ["译文。"]
    assert (hits, requests_made) == (0, 2)
    assert calls == ["native", "compatible"]


def test_cancel_stops_before_next_model_request(monkeypatch: pytest.MonkeyPatch):
    import scansci_html.literature_translation as translation

    calls = []
    monkeypatch.setattr(translation, "_ollama_translate", lambda text, *_args: (calls.append(text) or "译文。"))
    route = {"provider": {"runtime": "ollama"}, "base_url": "http://127.0.0.1:11434/v1", "model_id": "qwen3.5:4b"}
    with pytest.raises(InterruptedError, match="已取消"):
        _translate_chunks(
            ["First.", "Second."], "简体中文", route, None, None,
            cancelled=lambda: bool(calls),
        )
    assert calls == ["First."]


def test_cancel_before_save_leaves_no_pdf(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    source = tmp_path / "paper.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 80), "Scientific finding.")
        document.save(source)
    output = translation_root(workspace) / "translated.pdf"
    finished_model_call = False

    def translate(_text, _language):
        nonlocal finished_model_call
        finished_model_call = True
        return "科研发现。"

    with pytest.raises(InterruptedError, match="已取消"):
        translate_pdf(source, output, workspace=workspace, translator=translate, cancelled=lambda: finished_model_call)
    assert not output.exists()


def test_rotated_page_uses_unrotated_text_coordinates(tmp_path: Path):
    workspace = tmp_path / "workspace.sqlite"
    source = tmp_path / "rotated.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((50, 750), "Scientific finding on a rotated page.")
        page.set_rotation(90)
        document.save(source)
    output = translation_root(workspace) / "translated.pdf"
    translate_pdf(source, output, workspace=workspace, translator=lambda *_args: "旋转页上的科研发现。")
    with pymupdf.open(output) as document:
        assert "旋转页上的科研发现" in document[0].get_text()


def test_numeric_audit_detects_changed_comparison_and_sign():
    assert _numbers_changed("p < 0.05 and change -5.0", "p > 0.05 and change 5.0")
    assert not _numbers_changed("p < 0.05 and change -5.0", "p<0.05 and change −5.0")


def test_translation_resumes_finished_blocks_from_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import scansci_html.literature_translation as translation

    workspace = tmp_path / "workspace.sqlite"
    source = tmp_path / "paper.pdf"
    with pymupdf.open() as document:
        first = document.new_page()
        first.insert_text((50, 80), "First scientific finding.")
        first.insert_text((50, 160), "Second scientific finding.")
        second = document.new_page()
        second.insert_text((50, 80), "Third scientific finding.")
        document.save(source)
    route = {
        "provider": {"runtime": "ollama"}, "provider_id": "local-test",
        "model_id": "qwen3.5:4b", "base_url": "http://127.0.0.1:11434/v1",
    }
    monkeypatch.setattr(translation, "selected_translation_model", lambda *_args: route)
    calls = []
    fail_second_page = True

    def translate_one(text, _language, _route):
        nonlocal fail_second_page
        calls.append(text)
        if len(calls) == 3 and fail_second_page:
            fail_second_page = False
            raise RuntimeError("model runner stopped")
        return "科研译文。"

    monkeypatch.setattr(translation, "_ollama_translate", translate_one)
    output = translation_root(workspace) / "translated.pdf"
    with pytest.raises(RuntimeError, match="model runner stopped"):
        translate_pdf(source, output, workspace=workspace)
    assert len(calls) == 3
    assert not output.exists()

    result = translate_pdf(source, output, workspace=workspace)
    assert len(calls) == 4 and calls[-1] == "Third scientific finding."
    assert result["cache_hits"] == 2
    assert result["model_requests"] == 1
    with pymupdf.open(output) as document:
        assert document.page_count == 2
        assert "科研译文" in document[0].get_text()
        assert "科研译文" in document[1].get_text()


def test_translation_cache_changes_with_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import scansci_html.literature_translation as translation

    workspace = tmp_path / "workspace.sqlite"
    source = tmp_path / "paper.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 80), "Scientific finding.")
        document.save(source)
    route = {
        "provider": {"runtime": "ollama"}, "provider_id": "local-test",
        "model_id": "qwen3.5:4b", "base_url": "http://127.0.0.1:11434/v1",
    }
    monkeypatch.setattr(translation, "selected_translation_model", lambda *_args: route)
    calls = []
    monkeypatch.setattr(translation, "_ollama_translate", lambda _text, *_args: (calls.append(route["model_id"]) or "科研译文。"))
    output = translation_root(workspace) / "translated.pdf"
    translate_pdf(source, output, workspace=workspace)
    route["model_id"] = "qwen3.5:9b"
    translate_pdf(source, output, workspace=workspace)
    route["model_id"] = "qwen3.5:4b"
    route["base_url"] = "http://127.0.0.1:11435/v1"
    translate_pdf(source, output, workspace=workspace)
    assert calls == ["qwen3.5:4b", "qwen3.5:9b", "qwen3.5:4b"]


def test_translation_rejects_unregistered_output_path(tmp_path: Path):
    foreign_pdf = tmp_path / "foreign.pdf"
    foreign_pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ValueError, match="翻译产物目录"):
        register_translation(
            tmp_path / "workspace.sqlite",
            foreign_pdf,
            run_id="run-example",
            source_name="paper.pdf",
            target_language="简体中文",
        )


def test_translation_api_saves_pdf_to_selected_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import scansci_html.webapp as webapp

    real_translate = translate_pdf
    monkeypatch.setattr(webapp, "translate_pdf", lambda source, destination, **kwargs: real_translate(
        source, destination, translator=lambda _text, _language: "这是一份中文译文。", **kwargs
    ))
    app = NotebookWebApp(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    selected = tmp_path / "selected"
    selected.mkdir()

    def post(route: str, payload: dict):
        response = app.dispatch("POST", route, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        return response.status, json.loads(response.body)

    status, bound = post("/api/translations/library", {"parent_path": str(selected)})
    assert status == 201
    assert Path(bound["root_path"]) == selected / "翻译库"
    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 500, 200), "Scientific results were reproducible.")
        document.save(source)
    status, started = post("/api/translations", {
        "filename": source.name,
        "data_url": "data:application/pdf;base64," + base64.b64encode(source.read_bytes()).decode("ascii"),
        "target_language": "简体中文",
    })
    assert status == 202
    run_id = started["run_id"]
    for _ in range(100):
        run = json.loads(app.dispatch("GET", f"/api/translations/{run_id}").body)
        if run["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert run["status"] == "completed", run.get("error_message")
    result = app.dispatch("GET", f"/api/translations/{run_id}/pdf")
    assert result.status == 200 and result.body.startswith(b"%PDF-")
    notebook = load_workspace_summary(app.workspace, notebook_id="translation-library")["notebooks"][0]
    assert len(notebook["translation_files"]) == 1
    file_id = notebook["translation_files"][0]["id"]
    assert app.dispatch("GET", f"/api/translations/files/{file_id}").body == result.body
    assert app.dispatch("GET", "/api/translations/files/not-a-linked-file").status == 404


def test_translation_api_can_cancel_without_rebinding_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import scansci_html.webapp as webapp

    entered = threading.Event()

    def wait_for_cancel(_source, _destination, **kwargs):
        entered.set()
        for _ in range(200):
            if kwargs["cancelled"]():
                raise InterruptedError("cancelled")
            time.sleep(0.01)
        raise AssertionError("cancel request was not delivered")

    monkeypatch.setattr(webapp, "translate_pdf", wait_for_cancel)
    app = NotebookWebApp(workspace=tmp_path / "workspace.sqlite", evidence_db=tmp_path / "evidence.sqlite")
    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 80), "English source")
        document.save(source)

    def post(route: str, payload: dict):
        response = app.dispatch("POST", route, json.dumps(payload).encode("utf-8"))
        return response.status, json.loads(response.body)

    _, started = post("/api/translations", {
        "filename": source.name,
        "data_url": "data:application/pdf;base64," + base64.b64encode(source.read_bytes()).decode("ascii"),
    })
    assert entered.wait(3)
    assert post("/api/translations/library", {"parent_path": str(tmp_path)})[0] == 400
    assert post(f"/api/translations/{started['run_id']}/cancel", {})[0] == 200
    for _ in range(100):
        run = json.loads(app.dispatch("GET", f"/api/translations/{started['run_id']}").body)
        if run["status"] == "cancelled":
            break
        time.sleep(0.02)
    assert run["status"] == "cancelled"
    assert not translation_folder_files(translation_root(app.workspace))

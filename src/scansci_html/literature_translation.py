"""Host-owned first slice of local PDF translation and durable library storage."""

from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable

import pymupdf
import requests

from .app_settings import load_settings
from .ollama_runtime import is_local_ollama_url, ollama_base_url
from .vision_routing import _tesseract_environment, _tesseract_language_ids, _tesseract_path, select_text_route, tesseract_status
from .workspace import initialize_notebook, load_workspace_summary, translation_folder_files


TRANSLATION_NOTEBOOK_ID = "translation-library"
_NUMBERS = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?%?")
_COMPARISONS = re.compile(r"(?:<=|>=|<|>|≤|≥)\s*[-−]?\d+(?:[.,]\d+)?%?")
_NEGATIVE_NUMBERS = re.compile(r"(?<![A-Za-z0-9])[-−]\d+(?:[.,]\d+)?%?")
_MAX_BLOCK_CHARS = 2200
_CACHE_VERSION = 2
_FONT_CANDIDATES = (
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "STSONG.TTF",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "NotoSerifSC-VF.ttf",
    Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc"),
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "simsun.ttc",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "STXIHEI.TTF",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "NotoSansSC-VF.ttf",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "simhei.ttf",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc",
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
)
_HEADING_FONT_CANDIDATES = (
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "STZHONGS.TTF",
    Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSerifCJK-Bold.ttc"),
)
_HEADING_LABELS = frozenset({
    "abstract", "introduction", "methods", "materials and methods", "results",
    "discussion", "results and discussion", "conclusion", "conclusions",
})


class _TranslationFormatError(RuntimeError):
    """The local model replied without a usable translation."""


def translation_root(workspace: str | Path) -> Path:
    summary = load_workspace_summary(workspace, notebook_id=TRANSLATION_NOTEBOOK_ID)
    notebooks = summary.get("notebooks", [])
    return Path(notebooks[0]["root_path"]).resolve() if notebooks else Path(workspace).resolve().parent / "翻译库"


def bind_translation_library(workspace: str | Path, parent_path: str | Path = "") -> dict[str, Any]:
    """Create the user-selected 翻译库 folder once and link it as a knowledge source."""

    parent = Path(parent_path).expanduser().resolve() if str(parent_path).strip() else Path(workspace).resolve().parent
    if not parent.is_dir():
        raise ValueError("请选择已存在的保存位置")
    root = parent if parent.name == "翻译库" else parent / "翻译库"
    root.mkdir(exist_ok=True)
    initialize_notebook(
        workspace,
        notebook_id=TRANSLATION_NOTEBOOK_ID,
        title="翻译库",
        root_path=root,
        metadata={"library_kind": "translation", "created_by": "literature_translation"},
    )
    return {"notebook_id": TRANSLATION_NOTEBOOK_ID, "root_path": str(root), "files": translation_folder_files(root)}


def _font_file(*, heading: bool = False) -> Path:
    candidates = (*_HEADING_FONT_CANDIDATES, *_FONT_CANDIDATES) if heading else _FONT_CANDIDATES
    font = next((path for path in candidates if path.is_file()), None)
    if font is None:
        raise RuntimeError("未找到可嵌入 PDF 的中文字体，请安装微软雅黑或 Noto Sans CJK")
    return font


def selected_translation_model(workspace: str | Path, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    current = settings or load_settings(workspace)
    selected = dict(current.get("active_model", {}) or {})
    route = select_text_route(
        workspace,
        current,
        active_provider_id=str(selected.get("provider_id", "")),
        active_model_id=str(selected.get("model_id", "")),
    )
    if not route or (route["provider_id"], route["model_id"]) != (
        str(selected.get("provider_id", "")), str(selected.get("model_id", ""))
    ):
        raise RuntimeError("当前主力模型未就绪，请在模型服务中选择可用模型")
    if route["provider_id"] == "local-huggingface":
        from .local_transformers_runtime import ensure_local_transformers_runtime

        route["base_url"] = ensure_local_transformers_runtime(route["model_id"])
    return route


def _model_translate(text: str, target_language: str, route: dict[str, Any]) -> str:
    from .llm import complete_chat_text

    for attempt in range(2):
        try:
            answer = complete_chat_text(
                route["provider_kind"],
                base_url=route["base_url"],
                api_key=route["api_key"],
                model=route["model_id"],
                provider_id=route["provider_id"],
                messages=[
                    {"role": "system", "content": "你是科研文献翻译员。只输出译文，保持数字、单位、公式、文献引用和专有名词准确，不补充解释。"},
                    {"role": "user", "content": f"把下面文字翻译为{target_language}：\n\n{text}"},
                ],
                temperature=0.1,
                max_tokens=2048,
                timeout=180,
            )
            return re.sub(r"^<think>.*?</think>\s*", "", str(answer), flags=re.S).strip()
        except RuntimeError as error:
            if str(error) != "The model returned an empty response":
                raise
            if attempt:
                raise RuntimeError("模型连续两次返回空译文，请检查模型运行状态") from error
    raise AssertionError("unreachable")


def _uses_local_ollama(route: dict[str, Any]) -> bool:
    provider = dict(route.get("provider", {}) or {})
    return (
        (provider.get("runtime") == "ollama" or provider.get("logo") == "ollama")
        and is_local_ollama_url(str(route.get("base_url", "")))
    )


def _ollama_translate(text: str, target_language: str, route: dict[str, Any]) -> str:
    """Keep each PDF block aligned while avoiding local reasoning-token overhead."""

    schema = {
        "type": "object",
        "properties": {"translation": {"type": "string"}},
        "required": ["translation"],
    }
    try:
        with requests.Session() as client:
            client.trust_env = False  # Loopback PDF text must never follow HTTP_PROXY or netrc.
            response = client.post(
                f"{ollama_base_url(str(route['base_url']))}/api/chat",
                json={
                    "model": route["model_id"],
                    "messages": [
                        {"role": "system", "content": "你是科研文献翻译员。只翻译当前片段，只输出 JSON。数字、单位、公式、引用和专有名词保持准确，不补充解释。"},
                        {"role": "user", "content": f"把以下文献片段翻译为{target_language}：\n{text}"},
                    ],
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "options": {"temperature": 0.1, "num_predict": 2048},
                },
                timeout=180,
            )
            response.raise_for_status()
            response_body = response.json()
    except requests.RequestException as error:
        raise RuntimeError("本地模型服务中断；已完成的译文已保存，再次提交同一 PDF 可从断点继续") from error
    try:
        value = json.loads(response_body["message"]["content"])["translation"]
    except (KeyError, TypeError, ValueError) as error:
        raise _TranslationFormatError("本地模型未返回可解析的译文") from error
    if not isinstance(value, str) or not value.strip():
        raise _TranslationFormatError("本地模型返回空译文")
    return value.strip()


def _translation_cache(source: Path, workspace: str | Path, route: dict[str, Any], language: str) -> tuple[Path, dict[str, Any]]:
    with source.open("rb") as source_file:
        source_hash = hashlib.file_digest(source_file, "sha256").hexdigest()
    identity = {
        "version": _CACHE_VERSION,
        "source_sha256": source_hash,
        "provider_id": str(route["provider_id"]),
        "model_id": str(route["model_id"]),
        "endpoint_sha256": hashlib.sha256(str(route["base_url"]).strip().rstrip("/").encode("utf-8")).hexdigest(),
        "target_language": language,
    }
    filename = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() + ".json"
    path = Path(workspace).resolve().parent / ".scansci-translation-cache" / filename
    if not path.is_file():
        return path, {**identity, "translations": {}}
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("翻译断点数据损坏，请检查本地缓存文件") from error
    if (
        not isinstance(cached, dict)
        or any(cached.get(key) != value for key, value in identity.items())
        or not isinstance(cached.get("translations"), dict)
        or any(not isinstance(value, str) or not value for value in cached["translations"].values())
    ):
        raise RuntimeError("翻译断点数据与原文或模型不匹配")
    return path, cached


def _save_translation_cache(path: Path, cached: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".json.partial")
    # ponytail: one active translation per app owns this cache; add a cross-process
    # lock if multiple ScanSci instances must translate the same PDF concurrently.
    # ponytail: whole-file checkpoints are cheap for papers; use a journal or SQLite for multi-hundred-page batches.
    pending.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)


def _translate_chunks(
    texts: list[str], language: str, route: dict[str, Any],
    cache_path: Path | None, cache: dict[str, Any] | None,
    on_progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[list[str], int, int, float]:
    translations = dict(cache["translations"]) if cache is not None else {}
    keys = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
    result = [translations.get(key, "") for key in keys]
    missing = [index for index, value in enumerate(result) if not value]
    cache_hits = len(texts) - len(missing)
    requests_made = 0
    model_seconds = 0.0
    use_local_ollama = _uses_local_ollama(route)

    for index in missing:
        if cancelled and cancelled():
            raise InterruptedError("文献翻译已取消")
        started = time.monotonic()
        try:
            value = _ollama_translate(texts[index], language, route) if use_local_ollama else _model_translate(texts[index], language, route)
        except _TranslationFormatError:
            model_seconds += time.monotonic() - started
            requests_made += 1
            started = time.monotonic()
            value = _model_translate(texts[index], language, route)
        model_seconds += time.monotonic() - started
        requests_made += 1
        result[index] = value
        translations[keys[index]] = value
        if cache is not None and cache_path is not None:
            cache["translations"] = translations
            _save_translation_cache(cache_path, cache)
        if on_progress:
            on_progress(sum(bool(item) for item in result), len(texts))
    return result, cache_hits, requests_made, model_seconds


def _native_blocks(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    blocks = []
    for row in page.get_text("blocks"):
        if len(row) < 7 or row[6] != 0:
            continue
        text = str(row[4]).strip()
        if text:
            blocks.append((pymupdf.Rect(row[:4]), text))
    return blocks


def _block_role(text: str, rect: pymupdf.Rect, *, page_index: int, first_body: pymupdf.Rect, page_height: float) -> str:
    label = " ".join(text.split())
    if page_index == 0 and rect == first_body and rect.y0 < page_height * 0.2 and 12 <= len(label) <= 180:
        return "title"
    if "\n" not in text.strip() and len(label) <= 90 and (
        label.casefold() in _HEADING_LABELS or re.fullmatch(r"\d+(?:\.\d+)*\.\s+[^.;:]+", label)
    ):
        return "heading"
    return "body"


def _translation_chunks(text: str) -> list[str]:
    if len(text) <= _MAX_BLOCK_CHARS:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > _MAX_BLOCK_CHARS:
        window = remaining[:_MAX_BLOCK_CHARS]
        sentence_ends = [match.end() for match in re.finditer(r"[.!?]\s+", window) if match.end() >= _MAX_BLOCK_CHARS // 2]
        cut = sentence_ends[-1] if sentence_ends else window.rfind("\n") + 1
        if cut <= 0:
            raise RuntimeError("PDF 单段文字过长，无法可靠切分翻译")
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _preserve_source_block(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if re.fullmatch(r"(?:https?://)?[\w.-]+\.(?:org|com|net|edu|gov)/\S+", text.strip(), re.I):
        return True
    if len(text) < 80 and re.search(r"\b\d+\s+of\s+\d+\b", text):
        return True
    if len(lines) > 1 and all(re.fullmatch(r"[A-Z]{1,3}", line) for line in lines):
        return True
    return len(lines) >= 4 and sum(character.isdigit() for character in text) / max(1, len(text)) >= 0.25


def _numbers_changed(source: str, translated: str) -> bool:
    normalize = lambda values: Counter(re.sub(r"\s+", "", value).replace("−", "-") for value in values)
    return any(
        normalize(pattern.findall(source)) != normalize(pattern.findall(translated))
        for pattern in (_NUMBERS, _COMPARISONS, _NEGATIVE_NUMBERS)
    )


def _scanned_blocks(page: pymupdf.Page, settings: dict[str, Any]) -> list[tuple[pymupdf.Rect, str]]:
    ocr = dict(dict(settings.get("document_processing", {}) or {}).get("ocr", {}) or {})
    if not ocr.get("enabled", True):
        raise RuntimeError("此页没有可提取文字，当前 OCR 已关闭")
    if str(ocr.get("provider", "tesseract")) != "tesseract":
        raise RuntimeError("当前 OCR 提供者尚不能返回页块坐标；请在文档处理设置中选择 Tesseract")
    languages = list(ocr.get("languages", ["en"]) or ["en"])
    status = tesseract_status(languages)
    if not status.get("available") or not status.get("requested_supported"):
        raise RuntimeError(str(status.get("message", "Tesseract OCR 不可用")))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    result = subprocess.run(
        [_tesseract_path(), "stdin", "stdout", "-l", "+".join(_tesseract_language_ids(languages)), "--psm", "3", "-c", "tessedit_create_tsv=1"],
        input=pix.tobytes("png"),
        capture_output=True,
        timeout=90,
        check=False,
        env=_tesseract_environment(),
    )
    if result.returncode != 0:
        raise RuntimeError("Tesseract OCR 识别失败")
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in csv.DictReader(StringIO(result.stdout.decode("utf-8-sig", errors="replace")), delimiter="\t"):
        if row.get("level") == "5" and str(row.get("text", "")).strip():
            grouped.setdefault((row.get("block_num", ""), row.get("par_num", "")), []).append(row)
    blocks = []
    for words in grouped.values():
        left = min(int(word["left"]) for word in words) / 2
        top = min(int(word["top"]) for word in words) / 2
        right = max(int(word["left"]) + int(word["width"]) for word in words) / 2
        bottom = max(int(word["top"]) + int(word["height"]) for word in words) / 2
        text = " ".join(str(word["text"]).strip() for word in words)
        blocks.append((pymupdf.Rect(left, top, right, bottom), text))
    return blocks


def _pin_visible_glyphs(page: pymupdf.Page, boxes: list[pymupdf.Rect]) -> None:
    """Overlay rendered glyphs while leaving the PDF text searchable and selectable."""

    rotation = page.rotation
    if rotation:
        page.set_rotation(0)
    try:
        images = [
            (box, page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=box, alpha=False).tobytes("png"))
            for box in boxes
        ]
        for box, image in images:
            page.insert_image(box, stream=image, overlay=True)
    finally:
        if rotation:
            page.set_rotation(rotation)


def translate_pdf(
    source: str | Path,
    destination: str | Path,
    *,
    workspace: str | Path,
    target_language: str = "简体中文",
    translator: Callable[[str, str], str] | None = None,
    progress: Callable[[float, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Translate positioned blocks while retaining the source page artwork."""

    original = Path(source).resolve()
    target = Path(destination).resolve()
    if not original.is_file() or original.suffix.lower() != ".pdf":
        raise ValueError("请选择有效的 PDF 文件")
    settings = load_settings(workspace)
    route = {} if translator is not None else selected_translation_model(workspace, settings)
    translate = translator or (lambda text, language: _model_translate(text, language, route))
    cache_path, cache = _translation_cache(original, workspace, route, target_language) if route else (None, None)
    body_font = _font_file()
    heading_font = _font_file(heading=True)
    document = pymupdf.open(original)
    if document.is_encrypted or not document.page_count:
        raise ValueError("PDF 已加密或没有页面")
    warnings: list[str] = []
    translated_count = 0
    cache_hits = 0
    model_requests = 0
    model_seconds = 0.0
    ocr_pages = 0
    references_started = False
    try:
        for page_index, page in enumerate(document):
            blocks = _native_blocks(page)
            if not blocks:
                if page.get_contents():
                    blocks = _scanned_blocks(page, settings)
                    ocr_pages += 1
            if not blocks:
                warnings.append(f"第 {page_index + 1} 页未识别到文字，已保留原页")
            page_width = page.rect.width
            wide_body_starts = [rect.x0 for rect, _ in blocks if
                rect.x0 >= 0.25 * page_width and rect.width >= 0.55 * page_width
            ] if page_index == 0 else []
            body_left = min(wide_body_starts) if wide_body_starts else 0
            page_sources = []
            for block_index, (rect, source_text) in enumerate(blocks, start=1):
                if cancelled and cancelled():
                    raise InterruptedError("文献翻译已取消")
                if source_text.strip().casefold() in {"references", "bibliography"}:
                    references_started = True
                    warnings.append("参考文献保留原文，以保留作者、题名和 DOI")
                if references_started:
                    continue
                if body_left and rect.x1 < body_left and rect.width < 0.28 * page_width:
                    warnings.append("首页窄栏中的引用和出版元数据保留原文")
                    continue
                if _preserve_source_block(source_text):
                    warnings.append("页眉、来源标识和表格数据保留原文")
                    continue
                page_sources.append((block_index, rect, source_text, _translation_chunks(source_text)))
            page_chunks = [chunk for _, _, _, chunks in page_sources for chunk in chunks]
            if route:
                page_translations, hits, calls, seconds = _translate_chunks(
                    page_chunks, target_language, route, cache_path, cache,
                    on_progress=(lambda done, total: progress(page_index + 0.9 * done / total, document.page_count)) if progress else None,
                    cancelled=cancelled,
                )
                cache_hits += hits
                model_requests += calls
                model_seconds += seconds
            else:
                page_translations = [translate(chunk, target_language).strip() for chunk in page_chunks]
            translated_iter = iter(page_translations)
            pending = []
            for block_index, rect, source_text, chunks in page_sources:
                if cancelled and cancelled():
                    raise InterruptedError("文献翻译已取消")
                translated_chunks = [next(translated_iter) for _ in chunks]
                if not all(translated_chunks):
                    raise RuntimeError(f"第 {page_index + 1} 页模型返回空译文")
                translated = "\n".join(translated_chunks)
                if _numbers_changed(source_text, translated):
                    warnings.append(f"第 {page_index + 1} 页数字或百分比需人工核对")
                role = _block_role(source_text, rect, page_index=page_index, first_body=page_sources[0][1], page_height=page.rect.height)
                pending.append((rect, translated, role))
                translated_count += 1
                if progress:
                    progress(page_index + block_index / len(blocks), document.page_count)
            for rect, _, _ in pending:
                page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions(images=0, graphics=0, text=0)
            unrotated_bounds = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
            glyph_boxes = []
            for rect, translated, role in pending:
                bottom = rect.y1 + 6
                if role == "title":
                    next_top = min((other.y0 for _, other, _, _ in page_sources if other.y0 > rect.y1 + 1), default=unrotated_bounds.y1)
                    bottom = max(bottom, min(rect.y1 + 28, next_top - 3))
                elif role == "heading":
                    next_top = min((other.y0 for _, other, _, _ in page_sources if other.y0 > rect.y1 + 1 and other.x0 < rect.x1 and other.x1 > rect.x0), default=unrotated_bounds.y1)
                    bottom = max(bottom, min(rect.y1 + 12, next_top + 4))
                box = pymupdf.Rect(rect.x0, rect.y0, min(unrotated_bounds.x1, rect.x1 + 8), min(unrotated_bounds.y1, bottom))
                font = heading_font if role != "body" else body_font
                fontname = "ScanSciCJKHeading" if role != "body" else "ScanSciCJKBody"
                sizes = (18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4) if role == "title" else (
                    (13, 12, 11, 10, 9, 8, 7, 6, 5, 4) if role == "heading" else (11, 10, 9, 8, 7, 6, 5, 4)
                )
                rendered = False
                for size in sizes:
                    if page.insert_textbox(box, translated, fontname=fontname, fontfile=str(font), fontsize=size, lineheight=1.05) >= 0:
                        rendered = True
                        break
                if not rendered:
                    raise RuntimeError(f"第 {page_index + 1} 页译文超出原版面，未保存不完整 PDF")
                glyph_boxes.append(box)
            # Keep selectable text, but make visible CJK independent of the reader's font engine.
            _pin_visible_glyphs(page, glyph_boxes)
            if progress:
                progress(page_index + 1, document.page_count)
        if cancelled and cancelled():
            raise InterruptedError("文献翻译已取消")
        if hasattr(document, "subset_fonts"):
            document.subset_fonts()
        target.parent.mkdir(parents=True, exist_ok=True)
        pending_file = target.with_suffix(".pdf.partial")
        try:
            document.save(pending_file, garbage=4, deflate=True)
            with pymupdf.open(pending_file) as check:
                if check.page_count != document.page_count or not translated_count or not any(page.get_text().strip() for page in check):
                    raise RuntimeError("译文 PDF 完整性检查失败")
            pending_file.replace(target)
        finally:
            pending_file.unlink(missing_ok=True)
    finally:
        document.close()
    return {
        "pages": page_index + 1,
        "blocks": translated_count,
        "ocr_backend": "tesseract" if ocr_pages else "native-pdf-text",
        "ocr_pages": ocr_pages,
        "model_provider_id": str(route.get("provider_id", "test-translator")),
        "model_id": str(route.get("model_id", "test-translator")),
        "cache_hits": cache_hits,
        "model_requests": model_requests,
        "model_seconds": round(model_seconds, 1),
        "warnings": sorted(set(warnings)),
    }


def register_translation(
    workspace: str | Path,
    pdf_path: str | Path,
    *,
    run_id: str,
    source_name: str,
    target_language: str,
    source_id: str = "",
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save provenance beside a finished PDF; the linked folder supplies the library view."""

    root = translation_root(workspace).resolve()
    pdf = Path(pdf_path).resolve()
    if not pdf.is_file() or root not in pdf.parents:
        raise ValueError("译文必须位于当前工作区的翻译产物目录")
    if not load_workspace_summary(workspace, notebook_id=TRANSLATION_NOTEBOOK_ID).get("notebooks"):
        bind_translation_library(workspace, root)
    title = f"{Path(source_name).stem}（{target_language}）"
    metadata = {
        "translation_of": source_id,
        "translation_run_id": run_id,
        "target_language": target_language,
        "pdf_path": str(pdf),
        "source_name": Path(source_name).name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **{key: value for key, value in dict(result or {}).items() if key in {"ocr_backend", "model_provider_id", "model_id", "warnings"}},
    }
    sidecar = pdf.with_suffix(".translation.json")
    pending = sidecar.with_suffix(".translation.json.partial")
    pending.write_text(json.dumps({"title": title, **metadata}, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(sidecar)
    item = next((item for item in translation_folder_files(root) if item["path"] == str(pdf)), None)
    if item is None:
        raise RuntimeError("译文已保存，但翻译库没有找到该 PDF")
    return {"notebook_id": TRANSLATION_NOTEBOOK_ID, "file_id": item["id"], "title": title, "pdf_path": str(pdf), "metadata": metadata}

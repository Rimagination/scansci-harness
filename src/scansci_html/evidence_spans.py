from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import blake2b
import re
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag

from .resolver import safe_identifier_part


@dataclass(frozen=True)
class EvidenceSpan:
    doc_id: str
    evidence_id: str
    title: str
    doi: str | None
    source_url: str
    publication_year: int | None
    html_path: str
    html_anchor: str
    section: str
    section_kind: str
    block_id: str
    block_type: str
    sentence_index: int
    char_start: int
    char_end: int
    text: str
    # These fields make the structural path explicit instead of forcing
    # downstream callers to reverse-engineer it from a display label.  They
    # intentionally have defaults so existing benchmark fixtures can keep
    # constructing EvidenceSpan objects without a migration flag day.
    section_id: str = ""
    parent_section_id: str = ""
    section_path: str = ""
    section_level: int = 0
    source_locator: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _SentenceFragment:
    text: str
    span: EvidenceSpan | None


@dataclass(frozen=True)
class _BlockAnnotation:
    tag: Tag
    fragments: tuple[_SentenceFragment, ...]


def extract_evidence_spans(
    html_text: str,
    *,
    html_path: str | Path,
    min_sentence_length: int = 40,
) -> list[EvidenceSpan]:
    _, spans, _ = _scan_evidence_spans(
        html_text,
        html_path=html_path,
        min_sentence_length=min_sentence_length,
    )
    return spans


def write_evidence_html(
    html_path: str | Path,
    *,
    output_path: str | Path | None = None,
    min_sentence_length: int = 40,
    document_id: str = "",
) -> list[EvidenceSpan]:
    source = Path(html_path)
    output = Path(output_path) if output_path else source.with_name(f"{source.stem}.evidence.html")
    if source.resolve() == output.resolve():
        raise ValueError("output_path must not overwrite the source HTML file")

    soup, spans, annotations = _scan_evidence_spans(
        source.read_text(encoding="utf-8"),
        html_path=source,
        min_sentence_length=min_sentence_length,
        document_id=document_id,
    )
    _ensure_evidence_styles(soup)
    for annotation in annotations:
        if annotation.tag.name == "tr":
            span = next((fragment.span for fragment in annotation.fragments if fragment.span is not None), None)
            if span is not None:
                annotation.tag["id"] = span.html_anchor
                annotation.tag["data-evidence-id"] = span.evidence_id
                annotation.tag["data-section-kind"] = span.section_kind
                annotation.tag["data-evidence-short-id"] = _short_evidence_id(span.evidence_id)
                annotation.tag["title"] = f"{span.evidence_id} ({span.section_kind})"
                _add_css_class(annotation.tag, "scansci-evidence-row")
            continue
        annotation.tag.clear()
        for index, fragment in enumerate(annotation.fragments):
            if index:
                annotation.tag.append(NavigableString(" "))
            if fragment.span is None:
                annotation.tag.append(NavigableString(fragment.text))
                continue
            span_tag = soup.new_tag("span")
            span_tag["id"] = fragment.span.html_anchor
            span_tag["data-evidence-id"] = fragment.span.evidence_id
            span_tag["data-section-kind"] = fragment.span.section_kind
            span_tag["data-evidence-short-id"] = _short_evidence_id(fragment.span.evidence_id)
            span_tag["class"] = "scansci-evidence-span"
            span_tag["title"] = f"{fragment.span.evidence_id} ({fragment.span.section_kind})"
            span_tag.string = fragment.text
            annotation.tag.append(span_tag)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(str(soup), encoding="utf-8")
    return spans


def evidence_html_path_for(html_path: str | Path) -> Path:
    path = Path(html_path)
    return path.with_name(f"{path.stem}.evidence.html")


def _ensure_evidence_styles(soup: BeautifulSoup) -> None:
    if soup.select_one("style[data-scansci-evidence-style]"):
        return
    if not isinstance(soup.head, Tag):
        html = soup.html
        if not isinstance(html, Tag):
            html = soup.new_tag("html")
            for child in list(soup.contents):
                html.append(child.extract())
            soup.append(html)
        head = soup.new_tag("head")
        body = soup.body
        if isinstance(body, Tag):
            body.insert_before(head)
        else:
            html.insert(0, head)
    head = soup.head
    if not isinstance(head, Tag):
        return
    style = soup.new_tag("style")
    style["data-scansci-evidence-style"] = "true"
    style.string = """
    [data-evidence-id] { scroll-margin-top: 6rem; }
    [data-evidence-id]:target {
      background: #79f2a6;
      color: #14351f;
      outline: none;
      box-shadow: none;
      border-radius: 0;
    }
    """
    head.append(style)


def _short_evidence_id(evidence_id: str) -> str:
    suffix = str(evidence_id).rsplit(".", 1)[-1].strip()
    return suffix or evidence_id


def _add_css_class(tag: Tag, class_name: str) -> None:
    raw_classes = tag.get("class", [])
    if isinstance(raw_classes, str):
        classes = raw_classes.split()
    else:
        classes = [str(value) for value in raw_classes]
    if class_name not in classes:
        classes.append(class_name)
    tag["class"] = classes


def _scan_evidence_spans(
    html_text: str,
    *,
    html_path: str | Path,
    min_sentence_length: int,
    document_id: str = "",
) -> tuple[BeautifulSoup, list[EvidenceSpan], list[_BlockAnnotation]]:
    soup = BeautifulSoup(str(html_text or ""), "lxml")
    article = _article_root(soup)
    content_root = _article_content_root(article)
    title = _document_title(article, soup)
    doi = _attr(article, "data-doi")
    source_url = _attr(article, "data-source-url")
    publication_year = _publication_year(article, soup)
    doc_id = str(document_id or "").strip() or safe_identifier_part(doi or source_url or Path(html_path).stem)
    normalized_path = Path(html_path).as_posix()

    spans: list[EvidenceSpan] = []
    annotations: list[_BlockAnnotation] = []
    current_section = ""
    current_section_kind = "abstract" if content_root is not article else ""
    section_stack: list[tuple[int, str, str]] = []
    block_ordinal = 0
    for tag in content_root.descendants:
        if not isinstance(tag, Tag):
            continue
        if tag.name and re.fullmatch(r"h[1-6]", tag.name):
            heading_text = _normalized_text(tag)
            if heading_text:
                level = int(tag.name[1])
                while section_stack and section_stack[-1][0] >= level:
                    section_stack.pop()
                direct_kind = _section_kind(heading_text)
                current_section_kind = _inherited_section_kind(direct_kind, section_stack)
                section_stack.append((level, heading_text, current_section_kind))
            current_section = heading_text
            continue
        block_type = _block_type(tag)
        if not block_type or _inside_same_block(tag):
            continue

        block_text = _normalized_text(tag)
        if not block_text:
            continue
        block_ordinal += 1
        block_anchor = _attr(tag, "id") or f"evidence-block-{block_ordinal:04d}"
        section_kind = current_section_kind or _section_kind(current_section)
        if _is_non_evidence_section(section_kind):
            continue
        section, section_path, section_level, section_id, parent_section_id = _section_metadata(
            doc_id,
            section_stack,
            fallback=current_section,
        )
        fragments: list[_SentenceFragment] = []
        local_sentence_index = 0
        sentence_offsets = (
            ((block_text, 0, len(block_text)),)
            if block_type in {"caption", "table_row"}
            else tuple(_sentence_offsets(block_text))
        )
        for sentence_text, char_start, char_end in sentence_offsets:
            local_sentence_index += 1
            if len(sentence_text) < int(min_sentence_length):
                fragments.append(_SentenceFragment(sentence_text, None))
                continue
            sentence_index = len(spans) + 1
            html_anchor = block_anchor if block_type == "table_row" else f"{block_anchor}-s{local_sentence_index:04d}"
            span = EvidenceSpan(
                doc_id=doc_id,
                evidence_id=f"{doc_id}.s{sentence_index:04d}",
                title=title,
                doi=doi,
                source_url=source_url,
                publication_year=publication_year,
                html_path=normalized_path,
                html_anchor=html_anchor,
                section=section,
                section_kind=section_kind,
                block_id=f"{doc_id}:{block_anchor}",
                block_type=block_type,
                sentence_index=sentence_index,
                char_start=char_start,
                char_end=char_end,
                text=sentence_text,
                section_id=section_id,
                parent_section_id=parent_section_id,
                section_path=section_path,
                section_level=section_level,
                source_locator=_source_locator(section_path, html_anchor=html_anchor),
            )
            spans.append(span)
            fragments.append(_SentenceFragment(sentence_text, span))
        if fragments:
            annotations.append(_BlockAnnotation(tag, tuple(fragments)))
    return soup, spans, annotations


def _sentence_offsets(text: str) -> Iterable[tuple[str, int, int]]:
    """Yield traceable, bounded evidence fragments from one source block.

    This is deliberately rule based: an evidence fragment must remain an exact
    substring of the parsed source block.  Earlier code only split English
    full stops followed by an uppercase token, which left Chinese paragraphs
    and transcript pages as multi-thousand-character "sentences".  Here we
    handle Chinese punctuation, ordinary English punctuation, and timestamped
    transcripts without involving an LLM.
    """

    value = str(text or "")
    if not value:
        return
    timestamped = tuple(_timestamp_group_offsets(value))
    if timestamped:
        yield from timestamped
        return

    boundaries: list[int] = []
    for index, character in enumerate(value):
        if _is_sentence_boundary(value, index, character):
            end = _consume_closing_punctuation(value, index + 1)
            if not boundaries or end > boundaries[-1]:
                boundaries.append(end)

    start = 0
    for end in [*boundaries, len(value)]:
        if end <= start:
            continue
        yield from _bounded_fragment_offsets(value, start, end)
        start = end


def _timestamp_group_offsets(text: str, *, target_length: int = 480) -> Iterable[tuple[str, int, int]]:
    """Group timestamped transcript cues into retrieval-sized original spans."""

    markers = list(re.finditer(r"\[(?:\d+(?:\.\d+)?)\s*-\s*(?:\d+(?:\.\d+)?)\]", text))
    if len(markers) < 3:
        return ()
    pieces = [
        (marker.start(), markers[index + 1].start() if index + 1 < len(markers) else len(text))
        for index, marker in enumerate(markers)
    ]
    groups: list[tuple[int, int]] = []
    group_start, group_end = pieces[0]
    for piece_start, piece_end in pieces[1:]:
        if group_end - group_start >= target_length:
            groups.append((group_start, group_end))
            group_start = piece_start
        group_end = piece_end
    groups.append((group_start, group_end))
    if len(groups) > 1 and groups[-1][1] - groups[-1][0] < target_length // 3:
        previous_start, _ = groups[-2]
        groups[-2] = (previous_start, groups[-1][1])
        groups.pop()
    return tuple(_trimmed_fragment(text, start, end) for start, end in groups if text[start:end].strip())


def _is_sentence_boundary(text: str, index: int, character: str) -> bool:
    if character in "。！？；!?":
        return True
    if character != ".":
        return False
    if _decimal_period(text, index) or _abbreviation_period(text, index):
        return False
    if index + 1 < len(text) and not text[index + 1].isspace() and not _is_cjk(text[index + 1]):
        # A period inside a DOI, URL, version number, or abbreviation has no
        # following whitespace.  Do not turn `10.1000/ABC.Def` into two
        # fabricated evidence fragments merely because the next token starts
        # with a capital letter.
        return False
    next_index = index + 1
    while next_index < len(text) and text[next_index].isspace():
        next_index += 1
    if next_index >= len(text):
        return True
    next_character = text[next_index]
    return bool(next_character.isupper() or next_character.isdigit() or _is_cjk(next_character))


def _decimal_period(text: str, index: int) -> bool:
    before = index - 1
    after = index + 1
    while before >= 0 and text[before].isspace():
        before -= 1
    while after < len(text) and text[after].isspace():
        after += 1
    return before >= 0 and after < len(text) and text[before].isdigit() and text[after].isdigit()


def _abbreviation_period(text: str, index: int) -> bool:
    prefix = text[max(0, index - 12) : index + 1].lower()
    return bool(re.search(r"(?:figs?|eqs?|dr|prof|mr|mrs|ms|vs|etc|al|e\.g|i\.e)\.$", prefix))


def _consume_closing_punctuation(text: str, index: int) -> int:
    cursor = index
    while cursor < len(text) and text[cursor] in "”’\"')】」』":
        cursor += 1
    return cursor


def _bounded_fragment_offsets(
    text: str,
    start: int,
    end: int,
    *,
    max_length: int = 900,
) -> Iterable[tuple[str, int, int]]:
    """Fallback split for malformed/OCR text that lacks sentence punctuation."""

    cursor = start
    while end - cursor > max_length:
        window_end = min(end, cursor + max_length)
        boundary = _best_soft_boundary(text, cursor, window_end)
        if boundary <= cursor:
            boundary = window_end
        yield _trimmed_fragment(text, cursor, boundary)
        cursor = boundary
    if cursor < end:
        yield _trimmed_fragment(text, cursor, end)


def _best_soft_boundary(text: str, start: int, end: int) -> int:
    lower_bound = start + max(160, (end - start) // 2)
    for index in range(end - 1, lower_bound - 1, -1):
        if text[index] in "，,、:：;；\n":
            return index + 1
    for index in range(end - 1, lower_bound - 1, -1):
        if text[index].isspace():
            return index + 1
    return end


def _trimmed_fragment(text: str, start: int, end: int) -> tuple[str, int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return text[start:end], start, end


def _is_cjk(character: str) -> bool:
    return "\u3400" <= character <= "\u9fff" or "\uf900" <= character <= "\ufaff"


def _inside_decimal_fragment(text: str, match: re.Match[str]) -> bool:
    punctuation_index = match.start() - 1
    if punctuation_index < 0 or text[punctuation_index] != ".":
        return False
    previous_index = punctuation_index - 1
    while previous_index >= 0 and text[previous_index].isspace():
        previous_index -= 1
    next_index = match.end()
    while next_index < len(text) and text[next_index].isspace():
        next_index += 1
    if previous_index < 0 or next_index >= len(text):
        return False
    return text[previous_index].isdigit() and text[next_index].isdigit()


def _protect_abbreviations(text: str) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}
    protected = text
    for index, abbreviation in enumerate(("Fig.", "Figs.", "Eq.", "Eqs.", "Dr.", "Prof.", "et al.")):
        token = f"__ABBR_{index}__"
        if abbreviation in protected:
            protected = protected.replace(abbreviation, token)
            replacements[token] = abbreviation
    return protected, replacements


def _restore_abbreviations(text: str, replacements: dict[str, str]) -> str:
    restored = text
    for token, abbreviation in replacements.items():
        restored = restored.replace(token, abbreviation)
    return restored


def _article_root(soup: BeautifulSoup) -> Tag:
    for selector in ("article.paper", "article", "body"):
        tag = soup.select_one(selector)
        if isinstance(tag, Tag):
            return tag
    return soup


def _article_content_root(article: Tag) -> Tag:
    for selector in (
        "#article__content article[lang]",
        "main article[lang]",
        "article[lang]",
    ):
        tag = article.select_one(selector)
        if isinstance(tag, Tag):
            return tag
    return article


def _document_title(article: Tag, soup: BeautifulSoup) -> str:
    h1 = article.find("h1")
    if isinstance(h1, Tag):
        text = _normalized_text(h1)
        if text:
            return text
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return "Untitled article"


def _publication_year(article: Tag, soup: BeautifulSoup) -> int | None:
    for attr_name in (
        "data-publication-year",
        "data-published-year",
        "data-pub-year",
        "data-year",
        "data-publication-date",
        "data-published",
    ):
        year = _year_from_text(_attr(article, attr_name))
        if year is not None:
            return year
    for meta_name in (
        "citation_publication_date",
        "citation_online_date",
        "citation_date",
        "citation_year",
        "dc.date",
        "dc.Date",
        "prism.publicationDate",
        "prism.publicationdate",
        "article:published_time",
        "date",
    ):
        tag = soup.find("meta", attrs={"name": meta_name}) or soup.find("meta", attrs={"property": meta_name})
        if isinstance(tag, Tag):
            year = _year_from_text(_attr(tag, "content"))
            if year is not None:
                return year
    for time_tag in soup.find_all("time"):
        if not isinstance(time_tag, Tag):
            continue
        year = _year_from_text(_attr(time_tag, "datetime") or _normalized_text(time_tag))
        if year is not None:
            return year
    return None


def _year_from_text(value: str) -> int | None:
    match = re.search(r"\b(1[6-9]\d{2}|20\d{2}|21\d{2}|22\d{2})\b", value)
    if not match:
        return None
    return int(match.group(1))


def _block_type(tag: Tag) -> str:
    if tag.name == "p":
        return "paragraph"
    if tag.name in {"figcaption", "caption"}:
        return "caption"
    if tag.name == "tr":
        return "table_row"
    return ""


def _inside_same_block(tag: Tag) -> bool:
    parent = tag.parent
    while isinstance(parent, Tag):
        if _block_type(parent):
            return True
        parent = parent.parent
    return False


def _section_kind(section: str) -> str:
    value = _normalized_section_heading(section)
    if not value:
        return ""
    if "reference" in value or "bibliograph" in value or value in {"参考文献", "參考文獻"}:
        return "references"
    if "author" in value and ("affiliation" in value or "information" in value or "contribution" in value):
        return "authors"
    if value in {"affiliations", "authors and affiliations"}:
        return "authors"
    if "contribution" in value:
        return "contributions"
    if "acknowledgement" in value or "acknowledgment" in value or value in {"致谢", "致謝"}:
        return "acknowledgements"
    if "funding" in value:
        return "funding"
    if "data availability" in value or "availability of data" in value:
        return "data_availability"
    if "competing interest" in value or "conflict of interest" in value or "ethics declaration" in value:
        return "declarations"
    if "rights and permissions" in value or "cite this article" in value or "about this article" in value:
        return "article_metadata"
    if "supplementary information" in value or "supporting information" in value:
        return "supplementary"
    if "source data" in value:
        return "source_data"
    if "abstract" in value or value in {"摘要", "中文摘要", "英文摘要"}:
        return "abstract"
    if "method" in value or "materials" in value or value in {"方法", "研究方法", "实验方法", "實驗方法", "材料与方法", "材料與方法"}:
        return "methods"
    if value == "main" or "result" in value or "finding" in value or value in {"结果", "結果", "研究结果", "研究結果"}:
        return "results"
    if "discussion" in value or value in {"讨论", "討論"}:
        return "discussion"
    if "conclusion" in value or value in {"结论", "結論"}:
        return "conclusion"
    if "introduction" in value or value == "intro" or value in {"引言", "前言", "介绍", "介紹"}:
        return "introduction"
    return "other"


def _normalized_section_heading(section: str) -> str:
    value = section.strip().lower()
    return re.sub(r"^\s*(?:\d+(?:\.\d+)*|[ivxlcdm]+)(?:[\).:-]|\s+)\s*", "", value)


def _section_metadata(
    doc_id: str,
    section_stack: list[tuple[int, str, str]],
    *,
    fallback: str,
) -> tuple[str, str, int, str, str]:
    """Return a stable, hierarchical section identity for a source block."""

    titles = [title.strip() for _, title, _ in section_stack if title.strip()]
    if not titles:
        titles = [str(fallback or "正文").strip() or "正文"]
    section = titles[-1]
    section_path = " / ".join(titles)
    section_level = int(section_stack[-1][0]) if section_stack else 0
    section_id = _section_id(doc_id, section_path)
    parent_section_id = _section_id(doc_id, " / ".join(titles[:-1])) if len(titles) > 1 else ""
    return section, section_path, section_level, section_id, parent_section_id


def _section_id(doc_id: str, section_path: str) -> str:
    digest = re.sub(r"[^a-z0-9]+", "-", section_path.lower()).strip("-")[:56] or "root"
    fingerprint = blake2b(section_path.encode("utf-8"), digest_size=5).hexdigest()
    return f"{doc_id}.sec.{digest}-{fingerprint}"


def _source_locator(section_path: str, *, html_anchor: str) -> str:
    page_match = re.search(r"(?:第\s*)?(\d+)\s*页|\bpage\s*(\d+)\b", section_path, flags=re.IGNORECASE)
    if page_match:
        return f"page:{next(value for value in page_match.groups() if value)}"
    return f"anchor:{html_anchor}" if html_anchor else ""


def _inherited_section_kind(direct_kind: str, section_stack: list[tuple[int, str, str]]) -> str:
    if direct_kind and direct_kind != "other":
        return direct_kind
    for _, _, parent_kind in reversed(section_stack):
        if parent_kind in _CONTENT_SECTION_KINDS:
            return parent_kind
        if _is_non_evidence_section(parent_kind):
            return parent_kind
    return direct_kind


def _is_non_evidence_section(section_kind: str) -> bool:
    return section_kind in _NON_EVIDENCE_SECTION_KINDS


def _attr(tag: Tag, name: str) -> str:
    value = tag.get(name, "")
    return str(value).strip() if value else ""


def _normalized_text(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()


_CONTENT_SECTION_KINDS = {
    "abstract",
    "introduction",
    "methods",
    "results",
    "discussion",
    "conclusion",
}

_NON_EVIDENCE_SECTION_KINDS = {
    "acknowledgements",
    "article_metadata",
    "authors",
    "contributions",
    "data_availability",
    "declarations",
    "funding",
    "references",
    "source_data",
    "supplementary",
}

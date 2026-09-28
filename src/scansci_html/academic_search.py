"""Federated academic discovery with explicit provenance and local reranking.

Search results produced here are bibliographic discovery leads.  They only
become citable ScanSci evidence after lawful full text has been acquired and
indexed by the notebook evidence pipeline.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Protocol, Sequence
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests

from .embeddings import cosine_similarity, embed_query
from .text_tokenization import lexical_tokens


DEFAULT_PROVIDER_NAMES = (
    "openalex",
    "semantic-scholar",
    "crossref",
    "pubmed",
    "europe-pmc",
    "arxiv",
    "openreview",
    "dblp",
)

# Public scholarly APIs have very different anonymous-use limits.  A small
# provider-local cadence prevents a multi-query plan (or two simultaneous UI
# searches) from turning a transient 429 into a guaranteed failed source.
# This is deliberately process-local: ScanSci is a single-user desktop app,
# and no cross-user rate-limiting service is needed here.
_PROVIDER_MIN_INTERVAL_SECONDS = {
    "semantic-scholar": 1.0,
    "dblp": 0.35,
}
_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
_PROVIDER_SCHEDULE_LOCK = threading.RLock()
_PROVIDER_NEXT_REQUEST_AT: dict[str, float] = {}


def _wait_for_provider_slot(provider_name: str) -> None:
    """Respect a small process-local cadence before querying one provider."""

    interval = max(0.0, float(_PROVIDER_MIN_INTERVAL_SECONDS.get(provider_name, 0.0)))
    if interval <= 0:
        return
    while True:
        with _PROVIDER_SCHEDULE_LOCK:
            now = time.monotonic()
            allowed_at = _PROVIDER_NEXT_REQUEST_AT.get(provider_name, now)
            if allowed_at <= now:
                _PROVIDER_NEXT_REQUEST_AT[provider_name] = now + interval
                return
            delay = allowed_at - now
        time.sleep(delay)


def _provider_retry_delay(response: Any, attempt: int) -> float:
    """Use Retry-After when present, otherwise a short bounded backoff."""

    headers = getattr(response, "headers", {}) or {}
    retry_after = ""
    try:
        retry_after = str(headers.get("Retry-After", "")).strip()
    except AttributeError:
        retry_after = ""
    try:
        return max(0.0, min(8.0, float(retry_after)))
    except (TypeError, ValueError):
        return min(4.0, 0.5 * (2**attempt))


def _extend_provider_cooldown(provider_name: str, delay: float) -> None:
    if delay <= 0:
        return
    with _PROVIDER_SCHEDULE_LOCK:
        _PROVIDER_NEXT_REQUEST_AT[provider_name] = max(
            _PROVIDER_NEXT_REQUEST_AT.get(provider_name, 0.0),
            time.monotonic() + delay,
        )


def _academic_get(provider_name: str, session: Any, url: str, **kwargs: Any) -> Any:
    """Perform a bounded, provider-aware GET against a public academic API."""

    last_response: Any | None = None
    for attempt in range(3):
        _wait_for_provider_slot(provider_name)
        response = session.get(url, **kwargs)
        last_response = response
        try:
            status = int(getattr(response, "status_code", 200))
        except (TypeError, ValueError):
            status = 200
        if status not in _RETRYABLE_HTTP_STATUS or attempt == 2:
            response.raise_for_status()
            return response
        delay = _provider_retry_delay(response, attempt)
        _extend_provider_cooldown(provider_name, delay)
        time.sleep(delay)

    # The loop always returns or raises.  Keep an explicit guard so mocked
    # sessions cannot silently return an unusable value.
    if last_response is None:
        raise RuntimeError(f"{provider_name} did not return a response")
    last_response.raise_for_status()
    return last_response


@dataclass
class AcademicPaper:
    """Canonical bibliographic record used across all discovery providers."""

    title: str
    source: str
    source_id: str = ""
    abstract: str = ""
    doi: str = ""
    year: int | None = None
    venue: str = ""
    authors: list[str] = field(default_factory=list)
    url: str = ""
    oa_url: str = ""
    citation_count: int = 0
    publication_types: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    arxiv_id: str = ""
    provider_rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AcademicSearchProvider(Protocol):
    source_name: str

    def search(self, query: str, *, limit: int = 10, year_from: int | None = None) -> list[AcademicPaper]:
        ...


class OpenAlexAcademicProvider:
    source_name = "openalex"

    def __init__(self, *, timeout: float = 25.0, session: Any | None = None) -> None:
        self.timeout = float(timeout)
        self.session = session or requests.Session()

    def search(self, query: str, *, limit: int = 10, year_from: int | None = None) -> list[AcademicPaper]:
        params: dict[str, Any] = {"search": query, "per-page": int(limit)}
        if year_from:
            params["filter"] = f"from_publication_date:{int(year_from)}-01-01"
        response = _academic_get(self.source_name, self.session, "https://api.openalex.org/works", params=params, timeout=self.timeout)
        response.raise_for_status()
        return [_openalex_paper_from_record(item, rank=rank) for rank, item in enumerate(response.json().get("results", []), start=1)]


def search_openalex_author_works(
    author_name: str,
    *,
    limit: int = 20,
    sort: str = "relevance",
    year_from: int | None = None,
    year_to: int | None = None,
    timeout: float = 25.0,
    session: Any | None = None,
) -> dict[str, Any]:
    """Resolve an author once, then retrieve that author's actual OpenAlex works.

    Searching an author name as ordinary paper text is not an author search: it
    mixes homonyms, citations *about* the author, and unrelated titles.  This
    path uses OpenAlex's author registry first and makes the chosen author ID
    explicit for callers and the UI.
    """

    clean_name = _clean_space(str(author_name or ""))
    if not clean_name:
        raise ValueError("author name is required")
    client = session or requests.Session()
    author_response = _academic_get(
        "openalex",
        client,
        "https://api.openalex.org/authors",
        params={"search": clean_name, "per-page": 10},
        timeout=float(timeout),
    )
    author_response.raise_for_status()
    candidates = [dict(item) for item in list(author_response.json().get("results", []) or []) if isinstance(item, dict)]
    if not candidates:
        raise LookupError(f"OpenAlex could not resolve author: {clean_name}")

    normalized_name = _normalized_author_name(clean_name)
    exact = [item for item in candidates if _normalized_author_name(item.get("display_name")) == normalized_name]
    ranked_candidates = exact or candidates
    ranked_candidates.sort(
        key=lambda item: (
            -int(item.get("works_count", 0) or 0),
            -int(item.get("cited_by_count", 0) or 0),
            str(item.get("display_name", "")),
        )
    )
    selected = ranked_candidates[0]
    author_id = str(selected.get("id", "")).rstrip("/").rsplit("/", 1)[-1]
    if not author_id:
        raise LookupError(f"OpenAlex returned an author without an ID: {clean_name}")

    filters = [f"authorships.author.id:{author_id}"]
    if year_from is not None:
        filters.append(f"from_publication_date:{int(year_from)}-01-01")
    if year_to is not None:
        filters.append(f"to_publication_date:{int(year_to)}-12-31")
    work_params: dict[str, Any] = {
        "filter": ",".join(filters),
        "per-page": max(1, min(50, int(limit or 20))),
    }
    sort_value = {
        "cited_by_count": "cited_by_count:desc",
        "publication_date": "publication_date:desc",
    }.get(str(sort or "").strip())
    if sort_value:
        work_params["sort"] = sort_value
    work_response = _academic_get(
        "openalex",
        client,
        "https://api.openalex.org/works",
        params=work_params,
        timeout=float(timeout),
    )
    work_response.raise_for_status()
    work_payload = dict(work_response.json() or {})
    records = [dict(item) for item in list(work_payload.get("results", []) or []) if isinstance(item, dict)]
    items: list[dict[str, Any]] = []
    for rank, record in enumerate(records, start=1):
        paper = _openalex_paper_from_record(record, rank=rank).to_dict()
        paper["is_oa"] = bool(dict(record.get("open_access") or {}).get("is_oa"))
        items.append(paper)
    return {
        "author": clean_name,
        "author_resolution": {
            "author_id": author_id,
            "display_name": str(selected.get("display_name") or clean_name),
            "works_count": int(selected.get("works_count", 0) or 0),
            "cited_by_count": int(selected.get("cited_by_count", 0) or 0),
            "match": "exact_name" if exact else "best_ranked_candidate",
        },
        "items": items,
        "count": len(items),
        "source": "openalex_author_works",
    }


def _openalex_paper_from_record(item: dict[str, Any], *, rank: int) -> AcademicPaper:
    primary = dict(item.get("primary_location") or {})
    source = dict(primary.get("source") or {})
    best_oa = dict(item.get("best_oa_location") or {})
    return AcademicPaper(
        title=str(item.get("title", "")).strip(),
        source="openalex",
        source_id=str(item.get("id", "")),
        abstract=_openalex_abstract(item.get("abstract_inverted_index")),
        doi=_normalize_doi(item.get("doi")),
        year=_int_or_none(item.get("publication_year")),
        venue=str(source.get("display_name", "")),
        authors=[
            str(dict(dict(authorship).get("author") or {}).get("display_name", "")).strip()
            for authorship in list(item.get("authorships") or [])[:20]
            if str(dict(dict(authorship).get("author") or {}).get("display_name", "")).strip()
        ],
        url=str(primary.get("landing_page_url") or item.get("id") or ""),
        oa_url=str(best_oa.get("pdf_url") or best_oa.get("landing_page_url") or ""),
        citation_count=int(item.get("cited_by_count", 0) or 0),
        publication_types=[str(item.get("type", ""))] if item.get("type") else [],
        keywords=[str(topic.get("display_name", "")) for topic in list(item.get("topics") or [])[:8]],
        provider_rank=rank,
    )


class CrossrefAcademicProvider:
    source_name = "crossref"

    def __init__(self, *, timeout: float = 25.0, session: Any | None = None) -> None:
        self.timeout = float(timeout)
        self.session = session or requests.Session()

    def search(self, query: str, *, limit: int = 10, year_from: int | None = None) -> list[AcademicPaper]:
        params: dict[str, Any] = {"query.bibliographic": query, "rows": int(limit)}
        if year_from:
            params["filter"] = f"from-pub-date:{int(year_from)}-01-01"
        response = _academic_get(self.source_name, self.session, "https://api.crossref.org/works", params=params, timeout=self.timeout)
        response.raise_for_status()
        papers: list[AcademicPaper] = []
        for rank, item in enumerate(response.json().get("message", {}).get("items", []), start=1):
            links = [dict(link) for link in list(item.get("link") or []) if isinstance(link, dict)]
            authors = [_crossref_author(author) for author in list(item.get("author") or [])[:20]]
            pdf_url = next(
                (str(link.get("URL", "")) for link in links if "pdf" in str(link.get("content-type", "")).lower()),
                "",
            )
            papers.append(
                AcademicPaper(
                    title=_first(item.get("title")),
                    source=self.source_name,
                    source_id=str(item.get("DOI", "")),
                    abstract=_clean_markup(str(item.get("abstract", ""))),
                    doi=_normalize_doi(item.get("DOI")),
                    year=_crossref_year(item),
                    venue=_first(item.get("container-title")),
                    authors=[author for author in authors if author],
                    url=str(item.get("URL", "")),
                    oa_url=pdf_url,
                    citation_count=int(item.get("is-referenced-by-count", 0) or 0),
                    publication_types=[str(item.get("type", ""))] if item.get("type") else [],
                    provider_rank=rank,
                )
            )
        return papers


class SemanticScholarAcademicProvider:
    source_name = "semantic-scholar"

    def __init__(self, *, timeout: float = 25.0, session: Any | None = None, api_key: str = "") -> None:
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self.api_key = str(api_key).strip()

    def search(self, query: str, *, limit: int = 10, year_from: int | None = None) -> list[AcademicPaper]:
        params: dict[str, Any] = {
            "query": query,
            "limit": int(limit),
            "fields": "title,abstract,year,venue,url,authors,externalIds,citationCount,openAccessPdf,publicationTypes,fieldsOfStudy",
        }
        if year_from:
            params["year"] = f"{int(year_from)}-"
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        response = _academic_get(
            self.source_name,
            self.session,
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params=params,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        papers: list[AcademicPaper] = []
        for rank, item in enumerate(response.json().get("data", []), start=1):
            external = dict(item.get("externalIds") or {})
            open_pdf = dict(item.get("openAccessPdf") or {})
            papers.append(
                AcademicPaper(
                    title=str(item.get("title", "")).strip(),
                    source=self.source_name,
                    source_id=str(item.get("paperId", "")),
                    abstract=str(item.get("abstract") or "").strip(),
                    doi=_normalize_doi(external.get("DOI")),
                    year=_int_or_none(item.get("year")),
                    venue=str(item.get("venue", "")),
                    authors=[str(dict(author).get("name", "")).strip() for author in list(item.get("authors") or [])[:20]],
                    url=str(item.get("url", "")),
                    oa_url=str(open_pdf.get("url", "")),
                    citation_count=int(item.get("citationCount", 0) or 0),
                    publication_types=[str(value) for value in list(item.get("publicationTypes") or [])],
                    keywords=[str(value) for value in list(item.get("fieldsOfStudy") or [])],
                    arxiv_id=str(external.get("ArXiv", "")),
                    provider_rank=rank,
                )
            )
        return papers


class PubMedAcademicProvider:
    source_name = "pubmed"

    def __init__(self, *, timeout: float = 25.0, session: Any | None = None) -> None:
        self.timeout = float(timeout)
        self.session = session or requests.Session()

    def search(self, query: str, *, limit: int = 10, year_from: int | None = None) -> list[AcademicPaper]:
        term = query if not year_from else f"({query}) AND ({int(year_from)}:3000[dp])"
        search_response = _academic_get(
            self.source_name,
            self.session,
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": term, "retmode": "json", "retmax": int(limit)},
            timeout=self.timeout,
        )
        search_response.raise_for_status()
        ids = [str(value) for value in search_response.json().get("esearchresult", {}).get("idlist", [])]
        if not ids:
            return []
        fetch_response = _academic_get(
            self.source_name,
            self.session,
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
            timeout=self.timeout,
        )
        fetch_response.raise_for_status()
        root = ET.fromstring(fetch_response.content)
        by_id: dict[str, AcademicPaper] = {}
        for article in root.findall(".//PubmedArticle"):
            pmid = _xml_text(article.find(".//PMID"))
            article_node = article.find(".//Article")
            journal_node = article.find(".//Journal")
            if article_node is None:
                continue
            doi = ""
            for identifier in article.findall(".//ArticleId"):
                if str(identifier.attrib.get("IdType", "")).lower() == "doi":
                    doi = _normalize_doi(_xml_text(identifier))
                    break
            abstract = " ".join(_xml_text(node) for node in article_node.findall(".//AbstractText") if _xml_text(node))
            authors = []
            for author in article_node.findall(".//Author")[:20]:
                name = " ".join(filter(None, [_xml_text(author.find("ForeName")), _xml_text(author.find("LastName"))]))
                if name:
                    authors.append(name)
            by_id[pmid] = AcademicPaper(
                title=_xml_text(article_node.find("ArticleTitle")),
                source=self.source_name,
                source_id=pmid,
                abstract=abstract,
                doi=doi,
                year=_pubmed_year(article),
                venue=_xml_text(journal_node.find("Title")) if journal_node is not None else "",
                authors=authors,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                publication_types=[_xml_text(node) for node in article_node.findall(".//PublicationType")],
            )
        papers = [by_id[pmid] for pmid in ids if pmid in by_id]
        for rank, paper in enumerate(papers, start=1):
            paper.provider_rank = rank
        return papers


class EuropePmcAcademicProvider:
    source_name = "europe-pmc"

    def __init__(self, *, timeout: float = 25.0, session: Any | None = None) -> None:
        self.timeout = float(timeout)
        self.session = session or requests.Session()

    def search(self, query: str, *, limit: int = 10, year_from: int | None = None) -> list[AcademicPaper]:
        scoped = query if not year_from else f"({query}) AND FIRST_PDATE:[{int(year_from)}-01-01 TO 3000-12-31]"
        response = _academic_get(
            self.source_name,
            self.session,
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": scoped, "pageSize": int(limit), "format": "json", "resultType": "core"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        papers: list[AcademicPaper] = []
        for rank, item in enumerate(response.json().get("resultList", {}).get("result", []), start=1):
            fulltexts = list(dict(item.get("fullTextUrlList") or {}).get("fullTextUrl") or [])
            oa_url = next((str(dict(value).get("url", "")) for value in fulltexts if dict(value).get("url")), "")
            source_id = str(item.get("pmcid") or item.get("pmid") or item.get("id") or "")
            papers.append(
                AcademicPaper(
                    title=str(item.get("title", "")).strip(),
                    source=self.source_name,
                    source_id=source_id,
                    abstract=str(item.get("abstractText", "")).strip(),
                    doi=_normalize_doi(item.get("doi")),
                    year=_int_or_none(item.get("pubYear")),
                    venue=str(item.get("journalTitle", "")),
                    authors=[part.strip() for part in str(item.get("authorString", "")).split(",") if part.strip()][:20],
                    url=f"https://europepmc.org/article/{quote(str(item.get('source', 'MED')))}/{quote(source_id)}" if source_id else "",
                    oa_url=oa_url,
                    citation_count=int(item.get("citedByCount", 0) or 0),
                    publication_types=[str(item.get("pubType", ""))] if item.get("pubType") else [],
                    provider_rank=rank,
                )
            )
        return papers


class ArxivAcademicProvider:
    source_name = "arxiv"
    _NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

    def __init__(self, *, timeout: float = 25.0, session: Any | None = None) -> None:
        self.timeout = float(timeout)
        self.session = session or requests.Session()

    def search(self, query: str, *, limit: int = 10, year_from: int | None = None) -> list[AcademicPaper]:
        response = _academic_get(
            self.source_name,
            self.session,
            "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "start": 0, "max_results": int(limit), "sortBy": "relevance"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        papers: list[AcademicPaper] = []
        for entry in root.findall("atom:entry", self._NS):
            published = _xml_text(entry.find("atom:published", self._NS))
            year = _year_from_text(published)
            if year_from and year and year < int(year_from):
                continue
            entry_url = _xml_text(entry.find("atom:id", self._NS))
            arxiv_id = entry_url.rstrip("/").rsplit("/", 1)[-1]
            doi = _xml_text(entry.find("arxiv:doi", self._NS))
            pdf_url = ""
            for link in entry.findall("atom:link", self._NS):
                if str(link.attrib.get("type", "")).lower() == "application/pdf":
                    pdf_url = str(link.attrib.get("href", ""))
                    break
            papers.append(
                AcademicPaper(
                    title=_clean_space(_xml_text(entry.find("atom:title", self._NS))),
                    source=self.source_name,
                    source_id=arxiv_id,
                    abstract=_clean_space(_xml_text(entry.find("atom:summary", self._NS))),
                    doi=_normalize_doi(doi),
                    year=year,
                    venue="arXiv",
                    authors=[_xml_text(node.find("atom:name", self._NS)) for node in entry.findall("atom:author", self._NS)],
                    url=entry_url,
                    oa_url=pdf_url,
                    publication_types=["preprint"],
                    keywords=[
                        str(node.attrib.get("term", "")).strip()
                        for node in entry.findall("atom:category", self._NS)
                        if str(node.attrib.get("term", "")).strip()
                    ],
                    arxiv_id=arxiv_id,
                    provider_rank=len(papers) + 1,
                )
            )
        return papers


class OpenReviewAcademicProvider:
    """Search public OpenReview forum notes through the API v2 search endpoint.

    OpenReview records are discovery leads.  ``source=forum`` deliberately
    excludes reviews and comments, while the venue field remains explicit so
    that an accepted paper is not silently conflated with a submission.
    """

    source_name = "openreview"

    def __init__(self, *, timeout: float = 25.0, session: Any | None = None) -> None:
        self.timeout = float(timeout)
        self.session = session or requests.Session()

    def search(self, query: str, *, limit: int = 10, year_from: int | None = None) -> list[AcademicPaper]:
        response = _academic_get(
            self.source_name,
            self.session,
            "https://api2.openreview.net/notes/search",
            params={
                "query": query,
                "content": "all",
                "source": "forum",
                "limit": int(limit),
                "sort": "tmdate:desc",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        papers: list[AcademicPaper] = []
        for item in list(response.json().get("notes", []) or []):
            content = dict(item.get("content") or {})
            note_id = str(item.get("id") or item.get("forum") or "").strip()
            venue_id = str(_openreview_value(content.get("venueid")) or "").strip()
            venue = str(_openreview_value(content.get("venue")) or venue_id).strip()
            year = _openreview_year(item, venue_id or venue)
            if year_from and year and year < int(year_from):
                continue
            authors = _openreview_value(content.get("authors")) or []
            if isinstance(authors, str):
                authors = [authors]
            keywords = _openreview_value(content.get("keywords")) or []
            if isinstance(keywords, str):
                keywords = [part.strip() for part in keywords.split(",") if part.strip()]
            title = _clean_space(str(_openreview_value(content.get("title")) or ""))
            abstract = _clean_space(str(_openreview_value(content.get("abstract")) or ""))
            if not title:
                continue
            papers.append(
                AcademicPaper(
                    title=title,
                    source=self.source_name,
                    source_id=note_id,
                    abstract=abstract,
                    year=year,
                    venue=venue,
                    authors=[str(value).strip() for value in list(authors)[:20] if str(value).strip()],
                    url=f"https://openreview.net/forum?id={quote(note_id)}" if note_id else "",
                    oa_url=f"https://openreview.net/pdf?id={quote(note_id)}" if note_id else "",
                    publication_types=["conference-submission"],
                    keywords=[str(value).strip() for value in list(keywords)[:12] if str(value).strip()],
                    provider_rank=len(papers) + 1,
                )
            )
        return papers


class DblpAcademicProvider:
    """Search the public DBLP publication API without treating metadata as evidence."""

    source_name = "dblp"

    def __init__(self, *, timeout: float = 25.0, session: Any | None = None) -> None:
        self.timeout = float(timeout)
        self.session = session or requests.Session()

    def search(self, query: str, *, limit: int = 10, year_from: int | None = None) -> list[AcademicPaper]:
        response = _academic_get(
            self.source_name,
            self.session,
            "https://dblp.org/search/publ/api",
            params={"q": query, "format": "json", "h": int(limit), "f": 0},
            headers={"User-Agent": "ScanSci-Harness/1.0 (academic discovery)"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        hits = dict(response.json().get("result", {}).get("hits", {}) or {})
        papers: list[AcademicPaper] = []
        for hit in list(hits.get("hit", []) or []):
            info = dict(dict(hit).get("info") or {})
            year = _int_or_none(info.get("year"))
            if year_from and year and year < int(year_from):
                continue
            authors_value = dict(info.get("authors") or {}).get("author", [])
            if isinstance(authors_value, (str, dict)):
                authors_value = [authors_value]
            authors = [
                str(dict(value).get("text", "") if isinstance(value, dict) else value).strip()
                for value in list(authors_value)[:20]
            ]
            doi = _normalize_doi(info.get("doi"))
            url = _first_url(info.get("ee")) or str(info.get("url") or "")
            papers.append(
                AcademicPaper(
                    title=_clean_markup(str(info.get("title") or "")),
                    source=self.source_name,
                    source_id=str(info.get("key") or dict(hit).get("@id") or ""),
                    doi=doi,
                    year=year,
                    venue=str(info.get("venue") or ""),
                    authors=[value for value in authors if value],
                    url=url,
                    citation_count=0,
                    publication_types=[str(info.get("type") or "publication")],
                    provider_rank=len(papers) + 1,
                )
            )
        return papers


def build_academic_provider(name: str, *, timeout: float = 25.0) -> AcademicSearchProvider:
    normalized = str(name or "").strip().lower().replace("_", "-")
    if normalized == "openalex":
        return OpenAlexAcademicProvider(timeout=timeout)
    if normalized == "crossref":
        return CrossrefAcademicProvider(timeout=timeout)
    if normalized in {"semantic-scholar", "semanticscholar", "s2"}:
        return SemanticScholarAcademicProvider(timeout=timeout)
    if normalized == "pubmed":
        return PubMedAcademicProvider(timeout=timeout)
    if normalized in {"europe-pmc", "europepmc"}:
        return EuropePmcAcademicProvider(timeout=timeout)
    if normalized == "arxiv":
        return ArxivAcademicProvider(timeout=timeout)
    if normalized in {"openreview", "open-review"}:
        return OpenReviewAcademicProvider(timeout=timeout)
    if normalized == "dblp":
        return DblpAcademicProvider(timeout=timeout)
    raise ValueError(f"Unsupported academic search provider: {name}")


class FederatedAcademicSearch:
    """Run independent scholarly APIs concurrently and fuse their rankings."""

    def __init__(
        self,
        *,
        providers: Sequence[AcademicSearchProvider] | None = None,
        embedding_provider: Any | None = None,
        reranker: Any | None = None,
        max_workers: int = 8,
    ) -> None:
        self.providers = list(providers or [build_academic_provider(name) for name in DEFAULT_PROVIDER_NAMES])
        self.embedding_provider = embedding_provider
        self.reranker = reranker
        self.max_workers = max(1, int(max_workers))

    def search(
        self,
        query: str,
        *,
        query_variants: Sequence[str] | None = None,
        required_terms: Sequence[str] | None = None,
        limit: int = 20,
        per_source: int = 12,
        year_from: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        clean = _clean_space(query)
        if not clean:
            raise ValueError("query is required")
        result_limit = max(1, min(100, int(limit)))
        source_limit = max(1, min(50, int(per_source)))
        variants = _unique_strings(query_variants or [])[:3]
        if not variants:
            variants = [clean]
        # ``query_variants`` is the host-approved outbound plan.  Do not add
        # the display topic ahead of it: doing so used to consume one of the
        # three slots and silently drop a benchmark/fact-checking alias.  The
        # display topic remains in the artifact, while each provider receives
        # only concise, reviewable metadata queries.
        variant_limit = source_limit if len(variants) == 1 else min(50, max(3, math.ceil(source_limit / len(variants)) + 1))
        cancelled = cancel_requested or (lambda: False)
        started = datetime.now(timezone.utc)
        provider_results: dict[str, list[AcademicPaper]] = {}
        provider_queries: dict[str, list[str]] = {}
        errors: dict[str, str] = {}
        def collect_provider_results(query_batch: Sequence[str]) -> None:
            executor = ThreadPoolExecutor(
                max_workers=max(1, min(self.max_workers, len(self.providers) * len(query_batch)))
            )
            futures = {
                executor.submit(provider.search, variant, limit=variant_limit, year_from=year_from): (provider, variant)
                for provider in self.providers
                for variant in query_batch
            }
            pending = set(futures)
            abandoned = False
            try:
                while pending:
                    if cancelled():
                        for future in futures:
                            future.cancel()
                        abandoned = True
                        raise InterruptedError("academic search cancelled")
                    completed, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in completed:
                        provider, variant = futures[future]
                        try:
                            papers = future.result()
                            provider_results.setdefault(provider.source_name, []).extend(papers)
                            provider_queries.setdefault(provider.source_name, []).append(variant)
                        except Exception as error:  # one provider must not collapse federation
                            previous = errors.get(provider.source_name, "")
                            message = f"{type(error).__name__}: {error}"[:500]
                            errors[provider.source_name] = message if not previous else f"{previous}; {message}"[:500]
            finally:
                executor.shutdown(wait=not abandoned, cancel_futures=abandoned)

        collect_provider_results(variants)
        merged = _merge_provider_results(provider_results)
        expansion_variants = _zero_result_query_expansions(clean, variants) if not merged else []
        if expansion_variants:
            # A zero-result search is still a useful state, but a concise
            # deterministic relaxation often recovers records when a user
            # pasted a title suffix, acronym, or over-specific phrase.  Keep
            # this to one bounded retry and expose it in the artifact.
            collect_provider_results(expansion_variants)
            merged = _merge_provider_results(provider_results)
        searched_variants = _unique_strings([*variants, *expansion_variants])
        diagnostics: dict[str, Any] = {}
        ranked = self._rank(" ".join(searched_variants), merged, diagnostics=diagnostics)
        accepted, quality_gate = _apply_topic_quality_gate(ranked, required_terms)
        elapsed = datetime.now(timezone.utc) - started
        return {
            "query": clean,
            "query_variants": searched_variants,
            "query_expansions": expansion_variants,
            "zero_result_expanded": bool(expansion_variants),
            "items": accepted[:result_limit],
            "count": min(result_limit, len(accepted)),
            "candidate_count": sum(len(items) for items in provider_results.values()),
            "deduplicated_count": len(merged),
            "providers_requested": [provider.source_name for provider in self.providers],
            "providers_succeeded": sorted(provider_results),
            "provider_counts": {name: len(items) for name, items in sorted(provider_results.items())},
            "provider_queries": {name: _unique_strings(items) for name, items in sorted(provider_queries.items())},
            "provider_errors": errors,
            "ranking": diagnostics,
            "quality_gate": quality_gate,
            "year_from": year_from,
            "latency_ms": round(elapsed.total_seconds() * 1000),
            "evidence_status": "discovery_leads",
            "evidence_notice": "Bibliographic results are not citable evidence until lawful full text is indexed and verified.",
        }

    def _rank(self, query: str, merged: list[dict[str, Any]], *, diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
        if not merged:
            return []
        query_terms = set(_content_terms(query))
        max_citations = max(int(item.get("citation_count", 0) or 0) for item in merged)
        for item in merged:
            text_terms = set(_content_terms(f"{item.get('title', '')} {item.get('abstract', '')} {' '.join(item.get('keywords', []))}"))
            overlap = len(query_terms & text_terms) / max(1, len(query_terms))
            citation = math.log1p(int(item.get("citation_count", 0) or 0)) / max(1.0, math.log1p(max_citations))
            rrf = float(item.pop("_rrf", 0.0))
            source_agreement = min(1.0, max(0, len(item.get("sources", [])) - 1) / 3)
            item["score_breakdown"] = {
                "rrf": round(rrf, 6),
                "lexical": round(overlap, 6),
                "citation": round(citation, 6),
                "source_agreement": round(source_agreement, 6),
                "dense": 0.0,
                "reranker": 0.0,
            }
            item["score"] = rrf * 5.0 + overlap * 1.4 + citation * 0.12 + source_agreement * 0.3

        if self.embedding_provider is not None:
            try:
                query_vector = embed_query(self.embedding_provider, query)
                documents = [_candidate_text(item) for item in merged]
                vectors = self.embedding_provider.embed_texts(documents)
                for item, vector in zip(merged, vectors):
                    dense = max(0.0, cosine_similarity(query_vector, [float(value) for value in vector]))
                    item["score_breakdown"]["dense"] = round(dense, 6)
                    item["score"] += dense * 0.8
                diagnostics["embedding"] = getattr(self.embedding_provider, "cache_key", type(self.embedding_provider).__name__)
            except Exception as error:
                diagnostics["embedding_error"] = f"{type(error).__name__}: {error}"[:500]

        merged.sort(key=lambda item: (-float(item.get("score", 0.0)), -len(item.get("sources", [])), str(item.get("title", ""))))
        if self.reranker is not None:
            try:
                rerank_input = [
                    {**item, "text": str(item.get("abstract", "")), "section": str(item.get("venue", ""))}
                    for item in merged[: min(60, len(merged))]
                ]
                reranked = self.reranker.rerank(query, rerank_input)
                rerank_order = {_paper_key(item): index for index, item in enumerate(reranked)}
                for item in merged:
                    index = rerank_order.get(_paper_key(item))
                    if index is None:
                        continue
                    rerank_score = 1.0 / (index + 1)
                    item["score_breakdown"]["reranker"] = round(rerank_score, 6)
                    item["score"] += rerank_score * 0.65
                diagnostics["reranker"] = type(self.reranker).__name__
            except Exception as error:
                diagnostics["reranker_error"] = f"{type(error).__name__}: {error}"[:500]
        for item in merged:
            item["score"] = round(float(item.get("score", 0.0)), 6)
            item["discovery_only"] = True
        merged.sort(key=lambda item: (-float(item.get("score", 0.0)), -len(item.get("sources", [])), str(item.get("title", ""))))
        return merged


def search_academic_papers(
    query: str,
    *,
    query_variants: Sequence[str] | None = None,
    required_terms: Sequence[str] | None = None,
    limit: int = 20,
    per_source: int = 12,
    provider_names: Sequence[str] | None = None,
    year_from: int | None = None,
    embedding_provider: Any | None = None,
    reranker: Any | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    selected = [str(name) for name in list(provider_names or DEFAULT_PROVIDER_NAMES) if str(name).strip()]
    providers = [build_academic_provider(name) for name in selected]
    return FederatedAcademicSearch(
        providers=providers,
        embedding_provider=embedding_provider,
        reranker=reranker,
    ).search(
        query,
        query_variants=query_variants,
        required_terms=required_terms,
        limit=limit,
        per_source=per_source,
        year_from=year_from,
        cancel_requested=cancel_requested,
    )


def _merge_provider_results(provider_results: dict[str, list[AcademicPaper]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    title_aliases: dict[str, str] = {}
    priority = {name: index for index, name in enumerate(DEFAULT_PROVIDER_NAMES)}
    ordered_sources = sorted(provider_results.items(), key=lambda value: (priority.get(value[0], 999), value[0]))
    for source_name, papers in ordered_sources:
        for fallback_rank, paper in enumerate(papers, start=1):
            raw = paper.to_dict()
            title_key = _normalize_title(paper.title)
            doi_key = _normalize_doi(paper.doi)
            key = title_aliases.get(title_key, "") if title_key else ""
            if not key:
                key = f"doi:{doi_key}" if doi_key else f"title:{title_key}:{paper.year or ''}"
            if title_key:
                title_aliases[title_key] = key
            rank = int(paper.provider_rank or fallback_rank)
            if key not in merged:
                merged[key] = {
                    **raw,
                    "doi": doi_key,
                    "sources": [source_name],
                    "source_records": [{"source": source_name, "source_id": paper.source_id, "url": paper.url, "rank": rank}],
                    "_rrf": 1.0 / (60 + rank),
                }
                continue
            current = merged[key]
            if source_name not in current["sources"]:
                current["sources"].append(source_name)
            current["source_records"].append(
                {"source": source_name, "source_id": paper.source_id, "url": paper.url, "rank": rank}
            )
            current["_rrf"] = float(current.get("_rrf", 0.0)) + 1.0 / (60 + rank)
            if len(str(raw.get("abstract", ""))) > len(str(current.get("abstract", ""))):
                current["abstract"] = raw["abstract"]
            if len(list(raw.get("authors", []))) > len(list(current.get("authors", []))):
                current["authors"] = raw["authors"]
            for field_name in ("doi", "year", "venue", "url", "oa_url", "arxiv_id"):
                if not current.get(field_name) and raw.get(field_name):
                    current[field_name] = raw[field_name]
            current["citation_count"] = max(int(current.get("citation_count", 0) or 0), int(raw.get("citation_count", 0) or 0))
            current["publication_types"] = _unique_strings([*current.get("publication_types", []), *raw.get("publication_types", [])])
            current["keywords"] = _unique_strings([*current.get("keywords", []), *raw.get("keywords", [])])
    return list(merged.values())


def _paper_key(item: dict[str, Any]) -> str:
    doi = _normalize_doi(item.get("doi"))
    if doi:
        return f"doi:{doi}"
    return f"title:{_normalize_title(str(item.get('title', '')))}:{item.get('year') or ''}"


def _normalized_author_name(value: Any) -> str:
    return " ".join(re.findall(r"[\w]+", _clean_space(str(value or "")).casefold()))


def _candidate_text(item: dict[str, Any]) -> str:
    return _clean_space(
        " ".join(
            [
                str(item.get("title", "")),
                str(item.get("abstract", "")),
                str(item.get("venue", "")),
                " ".join(str(value) for value in list(item.get("keywords", []) or [])),
            ]
        )
    )


def _apply_topic_quality_gate(
    ranked: list[dict[str, Any]],
    required_terms: Sequence[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reject metadata that only matched an instruction's generic wording.

    This is deliberately a host-side acceptance gate, after ranking.  A model
    may propose aliases, but it cannot promote a cancer or writing paper merely
    because it was returned by a broad provider.
    """

    terms = _unique_strings(required_terms or [])[:6]
    if not terms:
        return ranked, {
            "status": "not_requested",
            "required_terms": [],
            "accepted_count": len(ranked),
            "rejected_count": 0,
            "reason": "未提供主题概念；保留调用方的原始排序。",
        }
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in ranked:
        match = _topic_match(item, terms)
        item["topic_match"] = match
        if bool(match["accepted"]):
            accepted.append(item)
        else:
            rejected.append(item)
    status = "passed" if accepted else "insufficient" if ranked else "no_candidates"
    reason = (
        "候选记录已通过主题相关性准入。"
        if accepted
        else "候选记录未包含足够的主题概念；已阻止把不相关结果作为检索答案交付。"
        if ranked
        else "各学术来源没有返回可排序的候选记录。"
    )
    return accepted, {
        "status": status,
        "required_terms": terms,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "candidate_count": len(ranked),
        "reason": reason,
        "rejected_preview": [
            {
                "title": str(item.get("title", "")),
                "score": float(dict(item.get("topic_match", {}) or {}).get("score", 0.0)),
            }
            for item in rejected[:5]
        ],
    }


def _topic_match(item: dict[str, Any], terms: Sequence[str]) -> dict[str, Any]:
    title = str(item.get("title", ""))
    document = _candidate_text(item)
    title_terms = set(_content_terms(title))
    document_terms = set(_content_terms(document))
    compact_document = _normalized_match_text(document)
    best_term = ""
    best_score = 0.0
    match_type = "none"
    for term in terms:
        compact_term = _normalized_match_text(term)
        term_tokens = set(_content_terms(term))
        score = 0.0
        kind = "none"
        if compact_term and compact_term in compact_document:
            score, kind = 1.0, "phrase"
        elif term_tokens:
            document_coverage = len(term_tokens & document_terms) / len(term_tokens)
            title_coverage = len(term_tokens & title_terms) / len(term_tokens)
            score = max(document_coverage, title_coverage * 0.92)
            kind = "term_coverage"
        if score > best_score:
            best_term, best_score, match_type = term, score, kind
    # A full phrase is ideal.  For concise English labels, two of three core
    # words in title/abstract are useful enough to keep a candidate for review.
    accepted = match_type == "phrase" or best_score >= 0.67
    return {
        "accepted": accepted,
        "matched_term": best_term,
        "score": round(best_score, 4),
        "match_type": match_type,
    }


def _normalized_match_text(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _content_terms(value: str) -> list[str]:
    return [token for token in lexical_tokens(value) if len(token) > 1 and token not in _STOPWORDS]


def _normalize_title(value: str) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _normalize_doi(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned.strip().rstrip(".,;)")


def _openreview_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _openreview_year(item: dict[str, Any], venue: str) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", str(venue))
    if match:
        return int(match.group(0))
    for key in ("pdate", "cdate", "tcdate", "tmdate"):
        value = _int_or_none(item.get(key))
        if value:
            try:
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc).year
            except (OverflowError, OSError, ValueError):
                continue
    return None


def _first_url(value: Any) -> str:
    if isinstance(value, list):
        return next((str(item).strip() for item in value if str(item).strip()), "")
    return str(value or "").strip()


def _openalex_abstract(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for token, indices in value.items():
        for index in list(indices or []):
            try:
                positions.append((int(index), str(token)))
            except (TypeError, ValueError):
                continue
    return _clean_space(" ".join(token for _, token in sorted(positions)))


def _crossref_year(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        parts = dict(item.get(key) or {}).get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            return _int_or_none(parts[0][0])
    return None


def _crossref_author(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return _clean_space(" ".join(filter(None, [str(value.get("given", "")), str(value.get("family", ""))])))


def _pubmed_year(article: ET.Element) -> int | None:
    for path in (".//ArticleDate/Year", ".//PubDate/Year", ".//DateCompleted/Year", ".//PubDate/MedlineDate"):
        year = _year_from_text(_xml_text(article.find(path)))
        if year:
            return year
    return None


def _year_from_text(value: str) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", str(value))
    return int(match.group(0)) if match else None


def _xml_text(node: ET.Element | None) -> str:
    return _clean_space("".join(node.itertext())) if node is not None else ""


def _clean_markup(value: str) -> str:
    return _clean_space(re.sub(r"<[^>]+>", " ", str(value)))


def _clean_space(value: str) -> str:
    return " ".join(str(value or "").split())


def _first(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0] if value else "").strip()
    return str(value or "").strip()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _zero_result_query_expansions(query: str, attempted: Sequence[str]) -> list[str]:
    """Build at most two transparent relaxations after a true zero result."""

    clean = _clean_space(query)
    if not clean or re.search(r"(?:10\.\d{4,9}/|\barxiv\s*:\s*|\b\d{4}\.\d{4,5})", clean, re.IGNORECASE):
        return []
    attempted_keys = {str(item).casefold() for item in attempted}
    candidates: list[str] = []
    without_boilerplate = re.sub(
        r"(?:please\s+)?(?:find|search|retrieve|look\s+for|papers?|articles?|研究|论文|文献|检索|搜索|查找)",
        " ",
        clean,
        flags=re.IGNORECASE,
    )
    compact = _clean_space(without_boilerplate).strip(" -:：,，;；")
    if compact and compact.casefold() != clean.casefold() and len(_content_terms(compact)) >= 2:
        candidates.append(compact)

    replacements = {
        "rag": "retrieval augmented generation",
        "factuality": "faithfulness",
        "groundedness": "faithfulness",
        "photovoltaic": "solar photovoltaic",
        "machine learning": "artificial intelligence",
    }
    expanded = clean
    for source, target in replacements.items():
        expanded = re.sub(rf"\b{re.escape(source)}\b", target, expanded, flags=re.IGNORECASE)
    expanded = _clean_space(expanded)
    if expanded.casefold() != clean.casefold() and len(_content_terms(expanded)) >= 2:
        candidates.append(expanded)
    return [item for item in _unique_strings(candidates) if item.casefold() not in attempted_keys][:2]


def _unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean.casefold() in seen:
            continue
        seen.add(clean.casefold())
        result.append(clean)
    return result


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "of", "on", "or",
    "that", "the", "to", "was", "were", "what", "which", "with", "研究", "论文", "文献", "关于", "以及",
}

from __future__ import annotations

from collections import Counter
import importlib
import re
from typing import Any, Callable, Protocol

import requests

from .local_retrieval_runtime import (
    model_device,
    move_model_to_device,
    resolve_retrieval_device,
    retrieval_inference_lock,
)
from .text_tokenization import lexical_tokens


class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ...


DEFAULT_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
DEFAULT_JINA_RERANKER_MODEL = "jinaai/jina-reranker-v3"
DEFAULT_QWEN3_RERANKER_MODEL = "Qwen/Qwen3-Reranker-0.6B"
DEFAULT_QWEN3_RERANK_INSTRUCTION = (
    "Given a scientific research question, retrieve passages that provide direct, "
    "verifiable evidence for answering the question. Author-only fragments, "
    "bibliography entries, paper titles, and navigation text are not evidence"
)


class LexicalReranker:
    """Deterministic reranker used until a cross-encoder provider is configured."""

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query_terms = _query_terms(query)
        reranked: list[dict[str, Any]] = []
        for candidate in candidates:
            hit = dict(candidate)
            matched_terms = _matched_terms(hit, query_terms)
            lexical_score = _lexical_score(hit, query_terms)
            phrase_bonus = _phrase_bonus(str(hit.get("text", "")), query_terms)
            dense_score = float(hit.get("dense_score", 0.0))
            fts_score = float(hit.get("fts_score", 0.0))
            context_score = float(hit.get("context_score", 0.0))
            tag_score = max(0.0, float(hit.get("tag_score", 0.0)))
            hit["matched_terms"] = matched_terms
            hit["tag_bonus"] = round(min(0.8, tag_score * 0.35), 6)
            hit["score"] = round(
                lexical_score + phrase_bonus + dense_score + fts_score + context_score + hit["tag_bonus"],
                6,
            )
            reranked.append(hit)
        reranked.sort(key=lambda hit: (-float(hit["score"]), str(hit.get("evidence_id", ""))))
        return reranked


class HybridPrefilterReranker(LexicalReranker):
    """Fuse dense, FTS, and lexical routes before an expensive neural reranker.

    Exact duplicate passages are removed here so repeated imports cannot consume
    most of the neural candidate budget. Reciprocal-rank fusion also prevents a
    cross-lingual dense hit from being discarded merely because the query and
    passage use different languages.
    """

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        lexical = super().rerank(query, candidates)
        dense = sorted(
            candidates,
            key=lambda hit: (
                -float(hit.get("dense_score", 0.0)),
                str(hit.get("evidence_id", "")),
            ),
        )
        fts = sorted(
            candidates,
            key=lambda hit: (
                -float(hit.get("fts_score", 0.0)),
                str(hit.get("evidence_id", "")),
            ),
        )
        lexical_rank = _rank_by_evidence_id(lexical)
        dense_rank = _rank_by_evidence_id(dense, positive_field="dense_score")
        fts_rank = _rank_by_evidence_id(fts, positive_field="fts_score")
        tag = sorted(
            candidates,
            key=lambda hit: (
                -float(hit.get("tag_score", 0.0)),
                str(hit.get("evidence_id", "")),
            ),
        )
        tag_rank = _rank_by_evidence_id(tag, positive_field="tag_score")
        best_by_text: dict[str, dict[str, Any]] = {}
        for candidate in lexical:
            evidence_id = str(candidate.get("evidence_id", ""))
            score = (
                _rrf(dense_rank.get(evidence_id), weight=1.4)
                + _rrf(fts_rank.get(evidence_id), weight=0.8)
                + _rrf(lexical_rank.get(evidence_id), weight=0.45)
                + _rrf(tag_rank.get(evidence_id), weight=0.35)
                + max(0.0, float(candidate.get("dense_score", 0.0))) * 0.08
                + max(0.0, float(candidate.get("fts_score", 0.0))) * 0.02
                + min(0.03, max(0.0, float(candidate.get("tag_score", 0.0))) * 0.01)
                + min(0.02, max(0.0, float(candidate.get("context_score", 0.0))) * 0.02)
            )
            hit = dict(candidate)
            hit["hybrid_prefilter_score"] = round(score, 8)
            hit["score"] = round(score, 8)
            routes = [str(route) for route in hit.get("routes", []) or []]
            if "hybrid-prefilter" not in routes:
                routes.append("hybrid-prefilter")
            hit["routes"] = routes
            text_key = re.sub(
                r"\s+",
                " ",
                str(hit.get("rerank_context_text", "") or hit.get("text", "")).strip(),
            ).casefold()
            dedupe_key = text_key or evidence_id
            previous = best_by_text.get(dedupe_key)
            if previous is None or float(hit["score"]) > float(previous["score"]):
                best_by_text[dedupe_key] = hit
        reranked = list(best_by_text.values())
        reranked.sort(key=lambda hit: (-float(hit["score"]), str(hit.get("evidence_id", ""))))
        return reranked


class CrossEncoderReranker:
    def __init__(
        self,
        *,
        model_name: str = DEFAULT_CROSS_ENCODER_MODEL,
        model: Any | None = None,
        batch_size: int = 32,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = resolve_retrieval_device(device)
        self.model = (
            model
            if model is not None
            else _load_cross_encoder_model(model_name, device=self.device)
        )
        if model is not None:
            self.model = move_model_to_device(self.model, self.device)
        self.device = model_device(self.model) or self.device
        self.batch_size = int(batch_size)

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pairs = [(query, _candidate_text(candidate)) for candidate in candidates]
        with retrieval_inference_lock():
            raw_scores = self.model.predict(pairs, batch_size=self.batch_size)
        scores = [float(score) for score in raw_scores]
        query_terms = _query_terms(query)
        reranked: list[dict[str, Any]] = []
        for candidate, score in zip(candidates, scores):
            hit = dict(candidate)
            hit["matched_terms"] = _matched_terms(hit, query_terms)
            hit["cross_encoder_score"] = round(score, 6)
            hit["score"] = round(score + _neural_tag_bonus(hit), 6)
            routes = [str(route) for route in hit.get("routes", []) or []]
            if "cross-encoder" not in routes:
                routes.append("cross-encoder")
            hit["routes"] = routes
            reranked.append(hit)
        reranked.sort(key=lambda hit: (-float(hit["score"]), str(hit.get("evidence_id", ""))))
        return reranked


class JinaReranker:
    """Reranker for Jina models that expose the official model.rerank API."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_JINA_RERANKER_MODEL,
        model: Any | None = None,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = resolve_retrieval_device(device)
        self.model = (
            model
            if model is not None
            else _load_jina_reranker_model(model_name, device=self.device)
        )
        if model is not None:
            self.model = move_model_to_device(self.model, self.device)
        self.device = model_device(self.model) or self.device

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        documents = [_candidate_text(candidate) for candidate in candidates]
        with retrieval_inference_lock():
            raw_results = self.model.rerank(query, documents, top_n=len(documents))
        scores_by_index: dict[int, float] = {
            int(result["index"]): float(result["relevance_score"]) for result in raw_results
        }
        query_terms = _query_terms(query)
        reranked: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            score = scores_by_index.get(index, float("-inf"))
            hit = dict(candidate)
            hit["matched_terms"] = _matched_terms(hit, query_terms)
            hit["jina_score"] = round(score, 6)
            hit["score"] = round(score + _neural_tag_bonus(hit), 6)
            routes = [str(route) for route in hit.get("routes", []) or []]
            if "jina-reranker" not in routes:
                routes.append("jina-reranker")
            hit["routes"] = routes
            reranked.append(hit)
        reranked.sort(key=lambda hit: (-float(hit["score"]), str(hit.get("evidence_id", ""))))
        return reranked


DEFAULT_SILICONFLOW_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"


class SiliconFlowReranker:
    """Remote reranker using SiliconFlow's ``/v1/rerank`` endpoint.

    The remote service is an optional precision stage.  A failed request is
    deliberately converted into a lexical rerank so a provider timeout,
    missing network, or rate limit cannot abort the surrounding evidence
    workflow.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.siliconflow.cn/v1",
        api_key: str,
        model_name: str = DEFAULT_SILICONFLOW_RERANKER_MODEL,
        timeout: float = 30.0,
        session: Any | None = None,
        fallback: Reranker | None = None,
        instruction: str = "",
        authorization_check: Callable[[], None] | None = None,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.model_name = str(model_name or DEFAULT_SILICONFLOW_RERANKER_MODEL).strip()
        self.timeout = max(1.0, float(timeout))
        if not self.base_url:
            raise ValueError("SiliconFlow reranker requires a base_url")
        if not self.api_key:
            raise ValueError("SiliconFlow reranker requires an API key")
        if not self.model_name:
            raise ValueError("SiliconFlow reranker requires a model name")
        self.session = session or requests.Session()
        self.authorization_check = authorization_check
        self.fallback = fallback or LexicalReranker()
        self.instruction = str(instruction or "").strip()
        if not self.instruction and self.model_name.casefold().startswith("qwen/qwen3-reranker"):
            self.instruction = DEFAULT_QWEN3_RERANK_INSTRUCTION
        # Do not include the API key in any identity used by caches or logs.
        self.cache_key = f"siliconflow:{self.base_url}:{self.model_name}"
        self.device = "remote"
        self.last_error = ""

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        documents = [_candidate_text(candidate) for candidate in candidates]
        try:
            payload = {
                "model": self.model_name,
                "query": str(query or ""),
                "documents": documents,
                "top_n": len(documents),
                "return_documents": False,
            }
            if self.instruction:
                payload["instruction"] = self.instruction
            if self.authorization_check:
                self.authorization_check()
            response = self.session.post(
                f"{self.base_url}/rerank",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
                allow_redirects=False,
            )
            if 300 <= getattr(response, "status_code", 200) < 400:
                raise RuntimeError("Reranker provider redirected the request")
            response.raise_for_status()
            payload = response.json()
            raw_results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(raw_results, list):
                raise RuntimeError("SiliconFlow reranker returned no results")
            scores_by_index: dict[int, float] = {}
            for result in raw_results:
                if not isinstance(result, dict):
                    continue
                try:
                    index = int(result["index"])
                    score = float(result["relevance_score"])
                except (KeyError, TypeError, ValueError):
                    continue
                if 0 <= index < len(candidates):
                    scores_by_index[index] = score
            if len(scores_by_index) != len(candidates):
                raise RuntimeError(
                    f"SiliconFlow reranker returned {len(scores_by_index)} scores for "
                    f"{len(candidates)} documents"
                )
            self.last_error = ""
        except Exception as error:  # remote provider boundary
            self.last_error = f"{type(error).__name__}: {str(error)[:240]}"
            return self._fallback_rerank(query, candidates)

        query_terms = _query_terms(query)
        reranked: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            remote_score = scores_by_index[index]
            hit = dict(candidate)
            hit["matched_terms"] = _matched_terms(hit, query_terms)
            hit["siliconflow_score"] = round(remote_score, 6)
            hit["score"] = round(remote_score + _neural_tag_bonus(hit), 6)
            routes = [str(route) for route in hit.get("routes", []) or []]
            if "siliconflow-reranker" not in routes:
                routes.append("siliconflow-reranker")
            hit["routes"] = routes
            reranked.append(hit)
        reranked.sort(key=lambda hit: (-float(hit["score"]), str(hit.get("evidence_id", ""))))
        return reranked

    def _fallback_rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        reranked = self.fallback.rerank(query, candidates)
        for hit in reranked:
            routes = [str(route) for route in hit.get("routes", []) or []]
            if "siliconflow-fallback" not in routes:
                routes.append("siliconflow-fallback")
            hit["routes"] = routes
        return reranked


class Qwen3Reranker:
    """Instruction-aware Qwen3 reranker using the model's yes/no logits.

    Qwen3-Reranker is a causal language model rather than a conventional
    sequence classifier.  Some sentence-transformers versions can load it via
    ``CrossEncoder`` but return constant zero scores.  This adapter follows the
    model card's native scoring protocol so a successful load is also a real
    ranking pass.
    """

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_QWEN3_RERANKER_MODEL,
        tokenizer: Any | None = None,
        model: Any | None = None,
        batch_size: int = 4,
        max_length: int = 2048,
        instruction: str = DEFAULT_QWEN3_RERANK_INSTRUCTION,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.batch_size = max(1, int(batch_size))
        self.max_length = max(256, int(max_length))
        self.instruction = str(instruction or DEFAULT_QWEN3_RERANK_INSTRUCTION).strip()
        self.device = resolve_retrieval_device(device)
        if tokenizer is None or model is None:
            tokenizer, model = _load_qwen3_reranker_model(model_name, device=self.device)
        self.tokenizer = tokenizer
        self.model = move_model_to_device(model, self.device)
        self.device = model_device(self.model) or self.device
        _validate_qwen3_reranker_model(self.model)
        self._false_token_id = self._token_id("no")
        self._true_token_id = self._token_id("yes")
        prefix = (
            '<|im_start|>system\nJudge whether the Document meets the requirements based on '
            'the Query and the Instruct provided. Note that the answer can only be "yes" or '
            '"no".<|im_end|>\n<|im_start|>user\n'
        )
        suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
        self._prefix_tokens = self.tokenizer.encode(prefix, add_special_tokens=False)
        self._suffix_tokens = self.tokenizer.encode(suffix, add_special_tokens=False)

    def _token_id(self, token: str) -> int:
        token_id = self.tokenizer.convert_tokens_to_ids(token)
        if token_id is None or int(token_id) < 0:
            ids = self.tokenizer(token, add_special_tokens=False).input_ids
            if not ids:
                raise RuntimeError(f"Qwen3 reranker tokenizer has no token for {token!r}")
            token_id = ids[0]
        return int(token_id)

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not candidates:
            return []
        documents = [_candidate_text(candidate) for candidate in candidates]
        scores: list[float] = []
        for start in range(0, len(documents), self.batch_size):
            scores.extend(self._score_batch(query, documents[start : start + self.batch_size]))
        query_terms = _query_terms(query)
        reranked: list[dict[str, Any]] = []
        for candidate, score in zip(candidates, scores):
            hit = dict(candidate)
            hit["matched_terms"] = _matched_terms(hit, query_terms)
            quality = _evidence_quality_multiplier(_candidate_text(hit))
            adjusted_score = float(score) * quality
            hit["qwen3_raw_score"] = round(float(score), 6)
            hit["evidence_quality"] = round(quality, 6)
            hit["qwen3_score"] = round(adjusted_score, 6)
            hit["score"] = round(adjusted_score + _neural_tag_bonus(hit), 6)
            routes = [str(route) for route in hit.get("routes", []) or []]
            if "qwen3-reranker" not in routes:
                routes.append("qwen3-reranker")
            hit["routes"] = routes
            reranked.append(hit)
        reranked.sort(key=lambda hit: (-float(hit["score"]), str(hit.get("evidence_id", ""))))
        return reranked

    def _score_batch(self, query: str, documents: list[str]) -> list[float]:
        try:
            torch = importlib.import_module("torch")
        except ImportError as error:  # pragma: no cover - optional dependency
            raise RuntimeError("torch is required for the Qwen3 reranker") from error
        pairs = [
            f"<Instruct>: {self.instruction}\n<Query>: {query}\n<Document>: {document}"
            for document in documents
        ]
        available = max(32, self.max_length - len(self._prefix_tokens) - len(self._suffix_tokens))
        inputs = self.tokenizer(
            pairs,
            padding=False,
            truncation="longest_first",
            return_attention_mask=False,
            max_length=available,
        )
        input_ids = [
            self._prefix_tokens + list(token_ids) + self._suffix_tokens
            for token_ids in inputs["input_ids"]
        ]
        padded = self.tokenizer.pad(
            {"input_ids": input_ids},
            padding=True,
            return_tensors="pt",
        )
        device = getattr(self.model, "device", None)
        if device is not None:
            padded = {key: value.to(device) for key, value in padded.items()}
        with retrieval_inference_lock(), torch.inference_mode():
            final_logits = self.model(**padded).logits[:, -1, :]
            yes_logits = final_logits[:, self._true_token_id]
            no_logits = final_logits[:, self._false_token_id]
            probabilities = torch.softmax(torch.stack([no_logits, yes_logits], dim=1), dim=1)[:, 1]
        return [float(score) for score in probabilities.detach().cpu().tolist()]


class CascadeReranker:
    """Run rerankers in stages, optionally trimming between expensive stages."""

    def __init__(self, stages: list[tuple[Reranker, int | None]]) -> None:
        if not stages:
            raise ValueError("CascadeReranker requires at least one stage")
        self.stages = stages

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        current = list(candidates)
        for stage_index, (reranker, keep_top) in enumerate(self.stages, start=1):
            try:
                current = reranker.rerank(query, current)
            except Exception as error:
                # Retrieval is a serving path: an exhausted local accelerator
                # must not turn a grounded answer/review into a failed run.
                # Keep the already-ranked bounded candidate set and make the
                # degradation explicit in routes.  Other programming/config
                # errors still propagate so they cannot be hidden as a quality
                # fallback.
                message = str(error).casefold()
                recoverable = any(
                    marker in message
                    for marker in (
                        "cuda error",
                        "out of memory",
                        "cuda out of memory",
                        "cudamemoryerror",
                        "acceleratorerror",
                    )
                )
                if not recoverable:
                    raise
                for hit in current:
                    routes = [str(route) for route in hit.get("routes", []) or []]
                    if "reranker-fallback" not in routes:
                        routes.append("reranker-fallback")
                    hit["routes"] = routes
                try:
                    torch = importlib.import_module("torch")
                    cuda = getattr(torch, "cuda", None)
                    if cuda is not None and bool(cuda.is_available()):
                        cuda.empty_cache()
                except Exception:
                    pass
            for hit in current:
                routes = [str(route) for route in hit.get("routes", []) or []]
                route_name = f"cascade-stage-{stage_index}"
                if route_name not in routes:
                    routes.append(route_name)
                hit["routes"] = routes
            if keep_top is not None and keep_top > 0:
                current = current[:keep_top]
        return current


def build_reranker(
    provider: str,
    *,
    model_name: str = "",
    model: Any | None = None,
    batch_size: int = 32,
    max_length: int = 2048,
    instruction: str = "",
    device: str | None = None,
    base_url: str = "",
    api_key: str = "",
    timeout: float = 30.0,
    session: Any | None = None,
    authorization_check: Callable[[], None] | None = None,
) -> Reranker:
    name = (provider or "local").strip().lower()
    if name in {"local", "lexical"}:
        return LexicalReranker()
    if name in {"cross-encoder", "cross_encoder", "sentence-transformers"}:
        return CrossEncoderReranker(
            model_name=model_name or DEFAULT_CROSS_ENCODER_MODEL,
            model=model,
            batch_size=batch_size,
            device=device,
        )
    if name == "jina":
        return JinaReranker(
            model_name=model_name or DEFAULT_JINA_RERANKER_MODEL,
            model=model,
            device=device,
        )
    if name in {"siliconflow", "silicon-flow"}:
        return SiliconFlowReranker(
            base_url=base_url or "https://api.siliconflow.cn/v1",
            api_key=api_key,
            model_name=model_name or DEFAULT_SILICONFLOW_RERANKER_MODEL,
            timeout=timeout,
            session=session,
            instruction=instruction,
            authorization_check=authorization_check,
        )
    if name in {"qwen3", "qwen3-reranker", "qwen-reranker"}:
        return Qwen3Reranker(
            model_name=model_name or DEFAULT_QWEN3_RERANKER_MODEL,
            tokenizer=None,
            model=model,
            batch_size=batch_size,
            max_length=max_length,
            instruction=instruction or DEFAULT_QWEN3_RERANK_INSTRUCTION,
            device=device,
        )
    raise ValueError(f"Unsupported reranker provider: {provider}")


def _load_cross_encoder_model(model_name: str, *, device: str = "cpu") -> Any:
    try:
        module = importlib.import_module("sentence_transformers.cross_encoder.model")
        CrossEncoder = getattr(module, "CrossEncoder")
    except ImportError as error:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "sentence-transformers is required for --reranker cross-encoder; "
            "install sentence-transformers or use --reranker local"
        ) from error
    return CrossEncoder(model_name, device=device)


def _load_jina_reranker_model(model_name: str, *, device: str = "cpu") -> Any:
    try:
        module = importlib.import_module("transformers.models.auto.modeling_auto")
        AutoModel = getattr(module, "AutoModel")
    except ImportError as error:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "transformers is required for --reranker jina; install transformers or use --reranker local"
        ) from error
    model = AutoModel.from_pretrained(model_name, trust_remote_code=True, torch_dtype="auto")
    return move_model_to_device(model, device)


def _resolve_qwen3_model_path(model_name: str) -> str:
    """Resolve a ScanSci-downloaded model snapshot before falling back to HF cache.

    ``download_snapshot`` writes ModelScope/HuggingFace snapshots under
    ``model_root()/models--<owner>--<name>/snapshots/<revision>``.  HuggingFace's
    ``from_pretrained(local_files_only=True)`` never looks there, so a model the
    user just downloaded from the model market would fail to load.  Resolve the
    snapshot directory ourselves and pass the concrete path instead.
    """

    try:
        from .local_model_market import discover_model_roots, _hub_roots
    except ImportError:  # pragma: no cover - local_model_market is bundled
        return model_name
    expected = "models--" + str(model_name).replace("/", "--")
    for root in discover_model_roots():
        for hub_root in _hub_roots(root):
            if not hub_root.is_dir():
                continue
            folder = hub_root / expected
            snapshots = folder / "snapshots"
            if not snapshots.is_dir():
                continue
            candidates = sorted(
                (item for item in snapshots.iterdir() if item.is_dir()),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if candidates and any(item.suffix.lower() in {".safetensors", ".bin", ".pt"} for item in candidates[0].iterdir()):
                return str(candidates[0])
    return model_name


def _load_qwen3_reranker_model(model_name: str, *, device: str = "cpu") -> tuple[Any, Any]:
    try:
        auto_models = importlib.import_module("transformers.models.auto.modeling_auto")
        auto_tokenizers = importlib.import_module("transformers.models.auto.tokenization_auto")
        AutoModelForCausalLM = getattr(auto_models, "AutoModelForCausalLM")
        AutoTokenizer = getattr(auto_tokenizers, "AutoTokenizer")
    except ImportError as error:  # pragma: no cover - optional dependency
        raise RuntimeError("transformers is required for the Qwen3 reranker") from error
    resolved_path = _resolve_qwen3_model_path(model_name)
    tokenizer = AutoTokenizer.from_pretrained(resolved_path, padding_side="left", local_files_only=True)
    load_kwargs: dict[str, Any] = {"local_files_only": True}
    if device.startswith("cuda"):
        load_kwargs["torch_dtype"] = "auto"
        # For the 4B variant, 4-bit quantisation keeps the model under 4 GB VRAM
        # with negligible quality loss (<1 % nDCG) compared to full precision.
        if "4b" in model_name.casefold():
            try:
                bnb_config = importlib.import_module("transformers").BitsAndBytesConfig
                load_kwargs["quantization_config"] = bnb_config(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype="float16",
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_quant_type="nf4",
                )
            except (ImportError, AttributeError):
                # bitsandbytes not available; fall through to standard loading
                pass
    model = AutoModelForCausalLM.from_pretrained(resolved_path, **load_kwargs).eval()
    model = move_model_to_device(model, device).eval()
    return tokenizer, model


def _validate_qwen3_reranker_model(model: Any) -> None:
    """Reject incomplete/corrupt snapshots before they can emit flat scores."""

    try:
        final_norm = model.model.norm.weight
        maximum = float(final_norm.detach().float().abs().max().cpu())
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeError("Qwen3 reranker model is missing its final normalization weights") from error
    if maximum <= 0.0:
        raise RuntimeError(
            "Qwen3 reranker weights failed integrity validation: final normalization weights are all zero"
        )


def _candidate_text(candidate: dict[str, Any]) -> str:
    parts = [
        str(candidate.get("title", "")).strip(),
        str(candidate.get("section", "")).strip(),
        str(candidate.get("rerank_context_text", "") or candidate.get("text", "")).strip(),
    ]
    tags = ", ".join(str(tag).strip() for tag in list(candidate.get("tags", []) or []) if str(tag).strip())
    if tags:
        parts.append("Tags: " + tags)
    return " ".join(part for part in parts if part)


def _neural_tag_bonus(candidate: dict[str, Any]) -> float:
    """Keep tag metadata as a small tie-breaker after neural reranking."""

    return min(0.08, max(0.0, float(candidate.get("tag_score", 0.0))) * 0.02)


def _rank_by_evidence_id(
    candidates: list[dict[str, Any]],
    *,
    positive_field: str = "",
) -> dict[str, int]:
    output: dict[str, int] = {}
    rank = 0
    for candidate in candidates:
        if positive_field and float(candidate.get(positive_field, 0.0)) <= 0.0:
            continue
        evidence_id = str(candidate.get("evidence_id", ""))
        if not evidence_id or evidence_id in output:
            continue
        rank += 1
        output[evidence_id] = rank
    return output


def _rrf(rank: int | None, *, weight: float) -> float:
    return 0.0 if rank is None else float(weight) / (60.0 + float(rank))


def _evidence_quality_multiplier(text: str) -> float:
    """Downweight title/reference fragments without penalizing concise claims."""

    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return 0.0
    lowered = f" {cleaned.casefold()} "
    predicates = (
        " is ",
        " are ",
        " was ",
        " were ",
        " found ",
        " show ",
        " shows ",
        " showed ",
        " suggest ",
        " suggests ",
        " increased ",
        " decreased ",
        " affected ",
        " resulted ",
        " 表明",
        " 显示",
        " 发现",
        " 提高",
        " 降低",
        " 影响",
        " 导致",
    )
    if any(predicate in lowered for predicate in predicates):
        return 1.0
    length = len(cleaned)
    if length < 24:
        return 0.2
    if length < 70:
        return 0.45
    if length < 140:
        return 0.7
    if length < 200:
        return 0.9
    return 1.0


def _lexical_score(row: dict[str, Any], query_terms: list[str]) -> float:
    text_counts = Counter(_tokens(str(row.get("text", ""))))
    title_counts = Counter(_tokens(str(row.get("title", ""))))
    section_counts = Counter(_tokens(str(row.get("section", ""))))
    score = 0.0
    for term in query_terms:
        score += text_counts.get(term, 0)
        score += title_counts.get(term, 0) * 0.4
        score += section_counts.get(term, 0) * 0.25
    return score


def _matched_terms(row: dict[str, Any], query_terms: list[str]) -> list[str]:
    haystack = set(
        _tokens(
            " ".join(
                [
                    str(row.get("text", "")),
                    str(row.get("title", "")),
                    str(row.get("section", "")),
                    " ".join(str(tag) for tag in list(row.get("tags", []) or [])),
                ]
            )
        )
    )
    return [term for term in query_terms if term in haystack]


def _phrase_bonus(text: str, query_terms: list[str]) -> float:
    tokens = _tokens(text)
    if len(query_terms) < 2 or len(tokens) < 2:
        return 0.0
    adjacent_pairs = set(zip(tokens, tokens[1:]))
    bonus = 0.0
    for pair in zip(query_terms, query_terms[1:]):
        if pair in adjacent_pairs:
            bonus += 0.5
    return bonus


def _query_terms(query: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for term in _tokens(query):
        if term in STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _tokens(value: str) -> list[str]:
    return lexical_tokens(value)


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "what",
    "which",
    "with",
}

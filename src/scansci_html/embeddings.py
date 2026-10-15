from __future__ import annotations

from hashlib import blake2b
import importlib
import math
import os
import re
from typing import Any, Callable
from typing import Protocol

import requests

from .local_retrieval_runtime import (
    model_device,
    resolve_retrieval_device,
    retrieval_inference_lock,
)
from .text_tokenization import lexical_tokens

_MAX_REMOTE_EMBED_TEXTS = 10_000
_MAX_REMOTE_EMBED_TOTAL_CHARS = 2_000_000
_MAX_REMOTE_EMBED_TEXT_CHARS = 100_000
_MAX_REMOTE_EMBED_BATCH_ITEMS = 128
_MAX_REMOTE_EMBED_BATCH_CHARS = 500_000
DEFAULT_SILICONFLOW_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"
DEFAULT_SILICONFLOW_EMBEDDING_DIMENSIONS = 4096


def _default_siliconflow_embedding_dimensions(model: str) -> int:
    """Return a safe default only for models with a documented projection."""

    if str(model or "").strip().casefold() == DEFAULT_SILICONFLOW_EMBEDDING_MODEL.casefold():
        return DEFAULT_SILICONFLOW_EMBEDDING_DIMENSIONS
    # Existing SiliconFlow entries such as BAAI/bge-m3 should use the model's
    # native output size.  Sending an undocumented ``dimensions`` field can
    # make an otherwise compatible endpoint reject the request.
    return 0


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


def embed_query(provider: EmbeddingProvider, query: str) -> list[float]:
    query_embedder = getattr(provider, "embed_query", None)
    if callable(query_embedder):
        return [float(value) for value in query_embedder(query)]
    return [float(value) for value in provider.embed_texts([query])[0]]


class HashingEmbeddingProvider:
    """Small deterministic local embedding fallback for tests and offline use."""

    def __init__(self, dimensions: int = 128) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = int(dimensions)
        self.cache_key = "hashing-v1"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_normalize(_hashing_vector(text, self.dimensions)) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self.embed_texts([query])[0]


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        session: Any | None = None,
        dimensions: int = 0,
        cache_namespace: str = "openai-compatible",
        authorization_check: Callable[[], None] | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required for openai-compatible embeddings")
        if not api_key:
            raise ValueError("api_key is required for openai-compatible embeddings")
        if not model:
            raise ValueError("model is required for openai-compatible embeddings")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self.authorization_check = authorization_check
        self.dimensions = max(0, int(dimensions or 0))
        self.device = "remote"
        # The key deliberately excludes the credential.  A dimension suffix
        # prevents a lower-dimensional SiliconFlow projection from reusing a
        # cache generated with the default 4096-dimensional output.
        suffix = f":{self.dimensions}" if self.dimensions > 0 else ""
        self.cache_key = f"{cache_namespace}:{self.base_url}:{self.model}{suffix}"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        normalized = [str(text) for text in texts]
        if not normalized:
            return []
        if len(normalized) > _MAX_REMOTE_EMBED_TEXTS:
            raise ValueError("Remote embedding request contains too many texts")
        if any(len(text) > _MAX_REMOTE_EMBED_TEXT_CHARS for text in normalized):
            raise ValueError("A remote embedding input exceeds the per-text character limit")
        if sum(len(text) for text in normalized) > _MAX_REMOTE_EMBED_TOTAL_CHARS:
            raise ValueError("Remote embedding request exceeds the total character budget")

        vectors: list[list[float]] = []
        batch: list[str] = []
        batch_chars = 0
        for text in normalized:
            if batch and (
                len(batch) >= _MAX_REMOTE_EMBED_BATCH_ITEMS
                or batch_chars + len(text) > _MAX_REMOTE_EMBED_BATCH_CHARS
            ):
                vectors.extend(self._embed_batch(batch))
                batch = []
                batch_chars = 0
            batch.append(text)
            batch_chars += len(text)
        if batch:
            vectors.extend(self._embed_batch(batch))
        if len(vectors) != len(normalized):
            raise RuntimeError(
                f"Embedding provider returned {len(vectors)} vectors for {len(normalized)} texts"
            )
        dimensions = {len(vector) for vector in vectors}
        if 0 in dimensions or len(dimensions) != 1:
            raise RuntimeError("Embedding provider returned empty or inconsistent vectors")
        resolved_dimensions = next(iter(dimensions))
        if self.dimensions and resolved_dimensions != self.dimensions:
            raise RuntimeError(
                f"Embedding provider returned {resolved_dimensions} dimensions; "
                f"expected {self.dimensions}"
            )
        if not self.dimensions:
            self.dimensions = resolved_dimensions
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self.authorization_check:
            self.authorization_check()
        payload = {"model": self.model, "input": texts}
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions
        response = self.session.post(
            f"{self.base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
            allow_redirects=False,
        )
        if 300 <= getattr(response, "status_code", 200) < 400:
            raise RuntimeError("Embedding provider redirected the request")
        response.raise_for_status()
        payload = response.json()
        rows = list(payload.get("data", []) or [])
        if len(rows) != len(texts):
            raise RuntimeError(
                f"Embedding provider returned {len(rows)} vectors for a batch of {len(texts)} texts"
            )
        return [[float(value) for value in list(item["embedding"])] for item in rows]

    def embed_query(self, query: str) -> list[float]:
        return self.embed_texts([query])[0]


class SiliconFlowEmbeddingProvider(OpenAICompatibleEmbeddingProvider):
    """Remote SiliconFlow embedding adapter for Qwen3 Embedding models.

    SiliconFlow documents the Qwen3-Embedding-8B endpoint as OpenAI
    compatible and supports an explicit 4096-dimensional projection.  Giving
    the provider a known dimension lets the vector cache be prepared without
    a local model probe or a GPU warm-up request.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_SILICONFLOW_EMBEDDING_MODEL,
        base_url: str = "https://api.siliconflow.cn/v1",
        dimensions: int = 0,
        timeout: float = 30.0,
        session: Any | None = None,
        authorization_check: Callable[[], None] | None = None,
    ) -> None:
        requested_dimensions = int(dimensions or 0)
        resolved_dimensions = requested_dimensions or _default_siliconflow_embedding_dimensions(model)
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
            session=session,
            dimensions=resolved_dimensions,
            cache_namespace="siliconflow",
            authorization_check=authorization_check,
        )


class SentenceTransformersEmbeddingProvider:
    def __init__(
        self,
        *,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        cache_identity: str = "",
        model: Any | None = None,
        batch_size: int = 32,
        max_seq_length: int = 0,
        normalize_embeddings: bool = True,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.cache_identity = str(cache_identity or model_name).strip()
        self.cache_key = f"sentence-transformers:{self.cache_identity}"
        self.device = resolve_retrieval_device(device)
        if model is not None:
            self.model = model
        else:
            try:
                self.model = _load_sentence_transformer_model(model_name, device=self.device)
            except TypeError as error:
                # Keep compatibility with lightweight test/integration loaders
                # that predate the explicit device argument.
                if "unexpected keyword argument 'device'" not in str(error):
                    raise
                self.model = _load_sentence_transformer_model(model_name)
        if model is not None:
            self.model = self.model.to(self.device) if callable(getattr(self.model, "to", None)) else self.model
        self.device = model_device(self.model) or self.device
        self.batch_size = int(batch_size)
        self.max_seq_length = max(0, int(max_seq_length or 0))
        if self.max_seq_length and hasattr(self.model, "max_seq_length"):
            self.model.max_seq_length = self.max_seq_length
        self.normalize_embeddings = bool(normalize_embeddings)
        dimension_getter = getattr(self.model, "get_embedding_dimension", None)
        if not callable(dimension_getter):
            dimension_getter = getattr(self.model, "get_sentence_embedding_dimension", None)
        self.dimensions = int(dimension_getter() or 0) if callable(dimension_getter) else 0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_query(self, query: str) -> list[float]:
        kwargs: dict[str, Any] = {}
        if self._has_query_prompt():
            kwargs["prompt_name"] = "query"
        return self._encode([query], **kwargs)[0]

    def _encode(self, texts: list[str], **extra_kwargs: Any) -> list[list[float]]:
        kwargs = {
            "batch_size": self.batch_size,
            "normalize_embeddings": self.normalize_embeddings,
        }
        kwargs.update(extra_kwargs)
        with retrieval_inference_lock():
            encoded = self.model.encode(
                texts,
                **kwargs,
            )
        if hasattr(encoded, "tolist"):
            encoded = encoded.tolist()
        return [[float(value) for value in vector] for vector in encoded]

    def _has_query_prompt(self) -> bool:
        prompts = getattr(self.model, "prompts", None)
        return isinstance(prompts, dict) and "query" in prompts


def build_embedding_provider(
    provider: str,
    *,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    cache_identity: str = "",
    dimensions: int = 128,
    batch_size: int = 32,
    max_seq_length: int = 0,
    device: str | None = None,
    authorization_check: Callable[[], None] | None = None,
) -> EmbeddingProvider:
    name = (provider or "local").strip().lower()
    if name in {"local", "hashing", "hash"}:
        return HashingEmbeddingProvider(dimensions=dimensions)
    if name in {"openai-compatible", "openai"}:
        resolved_base_url = base_url or os.getenv("SCANSCI_EMBEDDING_BASE_URL", "")
        resolved_api_key = api_key or os.getenv("SCANSCI_EMBEDDING_API_KEY", "")
        resolved_model = model or os.getenv("SCANSCI_EMBEDDING_MODEL", "")
        return OpenAICompatibleEmbeddingProvider(
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            model=resolved_model,
            dimensions=0,
            authorization_check=authorization_check,
        )
    if name in {"siliconflow", "silicon-flow"}:
        resolved_base_url = base_url or "https://api.siliconflow.cn/v1"
        resolved_api_key = api_key or os.getenv("SCANSCI_EMBEDDING_API_KEY", "")
        resolved_model = model or os.getenv("SCANSCI_EMBEDDING_MODEL", "") or DEFAULT_SILICONFLOW_EMBEDDING_MODEL
        requested_dimensions = int(dimensions or 0)
        # ``128`` is the historical build_embedding_provider default for the
        # local hashing backend, not an explicit SiliconFlow projection.
        resolved_dimensions = (
            _default_siliconflow_embedding_dimensions(resolved_model)
            if requested_dimensions in {0, 128}
            else requested_dimensions
        )
        return SiliconFlowEmbeddingProvider(
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            model=resolved_model,
            dimensions=resolved_dimensions,
            authorization_check=authorization_check,
        )
    if name in {"sentence-transformers", "sentence_transformers", "sbert"}:
        resolved_model = (
            model
            or os.getenv("SCANSCI_EMBEDDING_MODEL", "")
            or "sentence-transformers/all-MiniLM-L6-v2"
        )
        return SentenceTransformersEmbeddingProvider(
            model_name=resolved_model,
            cache_identity=cache_identity,
            batch_size=batch_size,
            max_seq_length=max_seq_length,
            device=device,
        )
    raise ValueError(f"Unsupported embedding provider: {provider}")


def _load_sentence_transformer_model(model_name: str, *, device: str = "cpu") -> Any:
    try:
        module = importlib.import_module("sentence_transformers.sentence_transformer.model")
        SentenceTransformer = getattr(module, "SentenceTransformer")
    except ImportError as error:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "sentence-transformers is required for --embedding-provider sentence-transformers; "
            "install sentence-transformers or use --embedding-provider local"
        ) from error
    return SentenceTransformer(model_name, device=device)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _hashing_vector(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    for token in lexical_tokens(text):
        digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if int.from_bytes(digest[4:], "big") % 2 == 0 else -1.0
        vector[bucket] += sign
    return vector


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]

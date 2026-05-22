"""Embedders: a small Protocol plus two real backends (fastembed, Ollama).

Heavy dependencies (``fastembed``) are imported lazily inside the constructor so
importing this module never requires a model to be present on disk. The factory
``get_embedder`` is cached so every package shares one warm model.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol, runtime_checkable

import httpx

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger

log = get_logger("retrieval.embeddings")

# Batch sizes balance throughput vs. memory. Documents are chunkier than queries.
_DOC_BATCH = 64
_OLLAMA_BATCH = 16


@runtime_checkable
class Embedder(Protocol):
    """Anything that can turn text into vectors. Implemented by both backends."""

    dim: int
    name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of corpus documents."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query (may use a query-specific prompt)."""
        ...


class FastEmbedEmbedder:
    """Local, in-process embeddings via ``fastembed.TextEmbedding`` (default backend).

    The model downloads on first use and is cached by fastembed under the user's
    cache dir. We never download at import time.
    """

    def __init__(self, model_name: str | None = None, dim: int | None = None) -> None:
        settings = get_settings()
        self.name = model_name or settings.embedding_model
        # ``dim`` is taken from settings but validated against the live model below.
        self.dim = int(dim or settings.embedding_dim)
        self._model = None  # lazy

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from fastembed import TextEmbedding  # type: ignore
        except Exception as exc:  # pragma: no cover - optional heavy dep
            raise RuntimeError(
                "fastembed is not installed; install tracepilot-retrieval extras "
                "or switch EMBEDDING_PROVIDER=ollama"
            ) from exc
        log.info("loading fastembed model %s", self.name)
        self._model = TextEmbedding(model_name=self.name)
        # Reconcile dim with what the model actually produces.
        try:
            probe = next(iter(self._model.embed(["dimension probe"])))
            real_dim = len(probe)
            if real_dim != self.dim:
                log.warning(
                    "embedding_dim=%d but model %s yields dim=%d; using model dim",
                    self.dim,
                    self.name,
                    real_dim,
                )
                self.dim = real_dim
        except Exception as exc:  # pragma: no cover
            log.warning("dim probe failed for %s: %s", self.name, exc)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        out: list[list[float]] = []
        # fastembed already batches internally but we stream to bound memory.
        for vec in model.embed(texts, batch_size=_DOC_BATCH):
            out.append([float(x) for x in vec])
        return out

    def embed_query(self, text: str) -> list[float]:
        model = self._ensure_model()
        # query_embed applies the model's retrieval query prompt when supported.
        embed_fn = getattr(model, "query_embed", None)
        try:
            if embed_fn is not None:
                vec = next(iter(embed_fn([text])))
            else:  # pragma: no cover - older fastembed
                vec = next(iter(model.embed([text])))
        except StopIteration:  # pragma: no cover
            return [0.0] * self.dim
        return [float(x) for x in vec]


class OllamaEmbedder:
    """Embeddings served by an Ollama instance over HTTP (``/api/embeddings``)."""

    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self.name = s.ollama_embed_model
        self.dim = int(s.embedding_dim)
        self._base_url = s.ollama_base_url.rstrip("/")
        self._timeout = float(s.request_timeout_seconds)
        self._client = httpx.Client(base_url=self._base_url, timeout=self._timeout)
        self._dim_checked = False

    def _embed_one(self, text: str) -> list[float]:
        try:
            resp = self._client.post(
                "/api/embeddings",
                json={"model": self.name, "prompt": text},
            )
            resp.raise_for_status()
            data = resp.json()
            vec = data.get("embedding") or data.get("embeddings") or []
            # Some Ollama versions nest under "embeddings": [[...]]
            if vec and isinstance(vec[0], list):
                vec = vec[0]
            vec = [float(x) for x in vec]
        except Exception as exc:
            log.warning("ollama embedding failed (%s); returning zero vector: %s", self.name, exc)
            return [0.0] * self.dim
        if not self._dim_checked and vec:
            self._dim_checked = True
            if len(vec) != self.dim:
                log.warning(
                    "embedding_dim=%d but ollama model %s yields dim=%d; using model dim",
                    self.dim,
                    self.name,
                    len(vec),
                )
                self.dim = len(vec)
        return vec or [0.0] * self.dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Ollama's embeddings endpoint is single-prompt; iterate sequentially.
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self._client.close()
        except Exception:
            pass


def _build_embedder(provider: str, model: str, dim: int) -> Embedder:
    if provider == "ollama":
        return OllamaEmbedder()
    # default + unknown -> fastembed
    return FastEmbedEmbedder(model_name=model, dim=dim)


@lru_cache(maxsize=8)
def _cached_embedder(provider: str, model: str, dim: int) -> Embedder:
    return _build_embedder(provider, model, dim)


def get_embedder(settings: Settings | None = None) -> Embedder:
    """Return a cached embedder selected by ``settings.embedding_provider``.

    Cached on the (provider, model, dim) tuple so distinct configs don't collide
    while the common case stays a singleton.
    """
    s = settings or get_settings()
    return _cached_embedder(s.embedding_provider, s.embedding_model, s.embedding_dim)

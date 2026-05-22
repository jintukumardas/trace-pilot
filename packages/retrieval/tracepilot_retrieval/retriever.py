"""Retriever: dense (Qdrant), sparse (BM25), hybrid fusion, optional rerank.

``Retriever.retrieve`` dispatches on ``query.strategy`` and always returns a
populated ``RetrievalResult`` (possibly empty) with per-evidence ``rank`` and
``retriever`` set and an overall ``latency_ms``. Cross-encoder reranking is lazy
and only runs when ``settings.rerank_enabled`` (or ``query.rerank``) is true.
"""

from __future__ import annotations

import re
import time

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import (
    Evidence,
    RetrievalQuery,
    RetrievalResult,
)
from tracepilot_shared.telemetry import Tracer

from .embeddings import Embedder
from .qdrant_store import QdrantStore

log = get_logger("retrieval.retriever")

# Pool sizes: fetch wider than top_k before fusion/rerank to improve recall.
_DENSE_POOL_MULT = 3
_SPARSE_POOL = 400
_BM25_CORPUS_LIMIT = 2000

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
# Split camelCase / PascalCase boundaries: lower|Upper and acronym|Word.
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokenize(text: str) -> list[str]:
    """Split identifiers + words; also split camelCase / snake_case for code recall.

    e.g. ``fetchData_helper`` -> ``fetchdata_helper, fetchdata, helper, fetch, data``.
    Sub-tokens are added in *addition* to the whole token so exact matches still win.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        lowered = raw.lower()
        tokens.append(lowered)
        # snake_case parts
        snake_parts = [p for p in raw.split("_") if p]
        for part in snake_parts:
            if part.lower() != lowered:
                tokens.append(part.lower())
            # camelCase sub-parts within each snake segment
            for sub in _CAMEL_RE.split(part):
                sub = sub.lower()
                if sub and sub not in (lowered, part.lower()):
                    tokens.append(sub)
    return tokens


def _min_max_norm(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [1.0 if hi > 0 else 0.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


class Retriever:
    """Hybrid code/document retriever over a single Qdrant collection."""

    def __init__(self, store: QdrantStore, embedder: Embedder, settings: Settings | None = None) -> None:
        self.store = store
        self.embedder = embedder
        self.settings = settings or get_settings()
        self._cross_encoder = None  # lazy
        self._cross_encoder_failed = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def retrieve(self, query: RetrievalQuery, tracer: Tracer | None = None) -> RetrievalResult:
        """Run retrieval for ``query`` and return a ranked ``RetrievalResult``."""
        t0 = time.perf_counter()
        strategy = query.strategy
        top_k = int(query.top_k)
        reranked = False

        owns_tracer = tracer is None
        if owns_tracer:
            tracer = Tracer(name="retrieve", workflow="retrieval", input={"q": query.query})

        try:
            with tracer.span(
                "retrieve", type="retrieval", input={"strategy": strategy, "top_k": top_k}
            ) as sp:
                if strategy == "dense":
                    evidence = self._dense(query, top_k)
                elif strategy == "sparse":
                    evidence = self._sparse(query, top_k)
                else:
                    evidence = self._hybrid(query, top_k)

                # Optional cross-encoder rerank.
                want_rerank = bool(query.rerank or self.settings.rerank_enabled)
                if want_rerank and evidence:
                    reranked_ev = self._rerank(query.query, evidence, top_k)
                    if reranked_ev is not None:
                        evidence = reranked_ev
                        reranked = True

                # Finalize ranks (1-based-ish: 0-indexed rank field per Evidence contract).
                for rank, ev in enumerate(evidence[:top_k]):
                    ev.rank = rank
                evidence = evidence[:top_k]
                sp.update(output={"n": len(evidence), "reranked": reranked})
        except Exception as exc:  # pragma: no cover - top-level guard
            log.warning("retrieve failed for %r: %s", query.query, exc)
            evidence = []

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        result = RetrievalResult(
            query=query.query,
            strategy=strategy,
            evidence=evidence,
            latency_ms=latency_ms,
            reranked=reranked,
        )
        if owns_tracer:
            tracer.finish(output={"n": len(evidence), "latency_ms": latency_ms})
        return result

    # ------------------------------------------------------------------ #
    # Dense
    # ------------------------------------------------------------------ #
    def _dense(self, query: RetrievalQuery, top_k: int) -> list[Evidence]:
        try:
            vector = self.embedder.embed_query(query.query)
        except Exception as exc:
            log.warning("query embedding failed: %s", exc)
            return []
        pool = max(top_k, top_k * _DENSE_POOL_MULT)
        evidence = self.store.search(vector, query.filter, pool)
        for ev in evidence:
            ev.retriever = "dense"
        return evidence[:top_k] if top_k else evidence

    # ------------------------------------------------------------------ #
    # Sparse (BM25)
    # ------------------------------------------------------------------ #
    def _sparse(self, query: RetrievalQuery, top_k: int) -> list[Evidence]:
        corpus = self.store.iter_chunks(query.filter, limit=_BM25_CORPUS_LIMIT)
        if not corpus:
            return []
        scored = self._bm25_score(query.query, corpus)
        scored.sort(key=lambda pair: pair[1], reverse=True)
        out: list[Evidence] = []
        for ev, score in scored[: max(top_k, _SPARSE_POOL // 10) or top_k]:
            ev.score = float(score)
            ev.retriever = "sparse"
            out.append(ev)
        return out[:top_k] if top_k else out

    def _bm25_score(self, query: str, corpus: list[Evidence]) -> list[tuple[Evidence, float]]:
        """Score a corpus of Evidence with BM25Okapi. Fail-soft to empty scores."""
        try:
            from rank_bm25 import BM25Okapi  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dep
            log.warning("rank-bm25 unavailable, sparse disabled: %s", exc)
            return [(ev, 0.0) for ev in corpus]
        tokenized = [_tokenize(ev.text) for ev in corpus]
        try:
            bm25 = BM25Okapi(tokenized)
            scores = bm25.get_scores(_tokenize(query))
        except Exception as exc:  # pragma: no cover
            log.warning("BM25 scoring failed: %s", exc)
            return [(ev, 0.0) for ev in corpus]
        return list(zip(corpus, (float(s) for s in scores)))

    # ------------------------------------------------------------------ #
    # Hybrid (score fusion)
    # ------------------------------------------------------------------ #
    def _hybrid(self, query: RetrievalQuery, top_k: int) -> list[Evidence]:
        alpha = float(self.settings.hybrid_alpha)
        pool = max(top_k * _DENSE_POOL_MULT, top_k)

        # Dense leg.
        dense: list[Evidence] = []
        try:
            vector = self.embedder.embed_query(query.query)
            dense = self.store.search(vector, query.filter, pool)
        except Exception as exc:
            log.warning("hybrid dense leg failed: %s", exc)

        # Sparse leg over the (broader) corpus.
        corpus = self.store.iter_chunks(query.filter, limit=_BM25_CORPUS_LIMIT)
        sparse_scored = self._bm25_score(query.query, corpus) if corpus else []

        # If one leg is empty, return the other directly (already ranked).
        if not corpus and dense:
            for ev in dense:
                ev.retriever = "dense"
            return dense[:top_k]
        if not dense and sparse_scored:
            sparse_scored.sort(key=lambda p: p[1], reverse=True)
            out = []
            for ev, score in sparse_scored[:top_k]:
                ev.score = float(score)
                ev.retriever = "sparse"
                out.append(ev)
            return out

        # Normalize each leg's scores to [0,1] then weight-fuse.
        dense_norm = _min_max_norm([ev.score for ev in dense])
        sparse_evs = [ev for ev, _ in sparse_scored]
        sparse_norm = _min_max_norm([s for _, s in sparse_scored])

        fused: dict[str, Evidence] = {}
        fused_score: dict[str, float] = {}

        for ev, ns in zip(dense, dense_norm):
            fused[ev.id] = ev
            fused_score[ev.id] = alpha * ns

        for ev, ns in zip(sparse_evs, sparse_norm):
            contrib = (1.0 - alpha) * ns
            if ev.id in fused_score:
                fused_score[ev.id] += contrib
            else:
                fused[ev.id] = ev
                fused_score[ev.id] = contrib

        ranked_ids = sorted(fused_score, key=lambda i: fused_score[i], reverse=True)
        out: list[Evidence] = []
        for cid in ranked_ids[:top_k]:
            ev = fused[cid]
            ev.score = round(fused_score[cid], 6)
            ev.retriever = "hybrid"
            out.append(ev)
        return out

    # ------------------------------------------------------------------ #
    # Cross-encoder rerank (lazy)
    # ------------------------------------------------------------------ #
    def _get_cross_encoder(self):
        if self._cross_encoder is not None or self._cross_encoder_failed:
            return self._cross_encoder
        try:
            from fastembed import TextCrossEncoder  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dep
            log.warning("TextCrossEncoder unavailable, rerank disabled: %s", exc)
            self._cross_encoder_failed = True
            return None
        try:
            model_name = getattr(self.settings, "rerank_model", None) or "Xenova/ms-marco-MiniLM-L-6-v2"
            self._cross_encoder = TextCrossEncoder(model_name=model_name)
        except Exception as exc:  # pragma: no cover
            log.warning("failed to load cross-encoder %s: %s", model_name, exc)
            self._cross_encoder_failed = True
            self._cross_encoder = None
        return self._cross_encoder

    def _rerank(self, query: str, evidence: list[Evidence], top_k: int) -> list[Evidence] | None:
        encoder = self._get_cross_encoder()
        if encoder is None:
            return None
        docs = [ev.text for ev in evidence]
        try:
            scores = list(encoder.rerank(query, docs))
        except Exception as exc:  # pragma: no cover
            log.warning("cross-encoder rerank failed: %s", exc)
            return None
        if len(scores) != len(evidence):
            return None
        order = sorted(range(len(evidence)), key=lambda i: scores[i], reverse=True)
        reranked: list[Evidence] = []
        for new_rank, idx in enumerate(order):
            ev = evidence[idx]
            ev.score = float(scores[idx])
            ev.rank = new_rank
            reranked.append(ev)
        return reranked[:top_k] if top_k else reranked

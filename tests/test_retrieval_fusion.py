"""Tests for hybrid retrieval fusion + citation/context packing.

The ``Retriever`` runs its real dense / sparse / hybrid code against the in-memory
``FakeQdrantStore`` and ``FakeEmbedder`` (deterministic). We assert the fusion
dedupes by chunk, ranks contiguously, and that ``build_citations`` /
``pack_context`` index correctly and respect their budgets.
"""

from __future__ import annotations

import pytest

from tracepilot_retrieval import build_citations, pack_context
from tracepilot_retrieval.retriever import Retriever
from tracepilot_shared.models import (
    ChunkMetadata,
    CodeChunk,
    Evidence,
    RetrievalFilter,
    RetrievalQuery,
)


def _chunk(i: int, text: str, file_path: str) -> CodeChunk:
    return CodeChunk(
        id=f"c{i}",
        text=text,
        content_hash=f"h{i}",
        metadata=ChunkMetadata(
            repository_id="repo_1",
            repo_name="demo",
            file_path=file_path,
            start_line=1,
            end_line=5,
        ),
    )


@pytest.fixture()
def populated_retriever(fake_store, fake_embedder):
    chunks = [
        _chunk(1, "def load_settings(): return Settings()", "config.py"),
        _chunk(2, "class Service:\n    def run(self): pass", "service.py"),
        _chunk(3, "configuration is loaded from environment variables", "README.md"),
        _chunk(4, "unrelated helper utility function", "pkg/helpers.py"),
    ]
    fake_store.ensure_collection(fake_embedder.dim)
    fake_store.upsert(chunks, fake_embedder.embed_documents([c.text for c in chunks]))
    from tracepilot_shared.config import get_settings

    return Retriever(fake_store, fake_embedder, get_settings())


def _query(text: str, strategy: str = "hybrid", top_k: int = 4) -> RetrievalQuery:
    return RetrievalQuery(
        query=text,
        strategy=strategy,
        top_k=top_k,  # type: ignore[arg-type]
        filter=RetrievalFilter(repository_ids=["repo_1"]),
    )


# --------------------------------------------------------------------------- #
# Hybrid fusion
# --------------------------------------------------------------------------- #
def test_hybrid_dedupes_and_ranks(populated_retriever):
    result = populated_retriever.retrieve(_query("how is configuration loaded"))
    assert result.strategy == "hybrid"
    assert result.evidence

    ids = [e.id for e in result.evidence]
    assert len(ids) == len(set(ids)), "fusion must dedupe by chunk id"

    # Ranks are contiguous 0..n-1 in descending score order.
    ranks = [e.rank for e in result.evidence]
    assert ranks == list(range(len(result.evidence)))
    scores = [e.score for e in result.evidence]
    assert scores == sorted(scores, reverse=True)

    # All survivors are tagged with the hybrid retriever.
    assert all(e.retriever == "hybrid" for e in result.evidence)
    assert result.latency_ms >= 0.0


def test_hybrid_orders_most_relevant_first(populated_retriever):
    # The README chunk shares the most tokens with the query → should rank top.
    result = populated_retriever.retrieve(_query("configuration loaded from environment"))
    assert result.evidence[0].metadata.file_path == "README.md"


def test_dense_strategy_returns_dense_tagged(populated_retriever):
    result = populated_retriever.retrieve(_query("configuration", strategy="dense", top_k=2))
    assert result.strategy == "dense"
    assert len(result.evidence) <= 2
    assert all(e.retriever == "dense" for e in result.evidence)


def test_sparse_strategy_uses_bm25(populated_retriever):
    result = populated_retriever.retrieve(
        _query("configuration loaded environment", strategy="sparse", top_k=2)
    )
    assert result.strategy == "sparse"
    assert result.evidence
    assert all(e.retriever == "sparse" for e in result.evidence)
    # BM25 should rank the README chunk (richest term overlap) first.
    assert result.evidence[0].metadata.file_path == "README.md"


def test_retrieve_respects_top_k(populated_retriever):
    result = populated_retriever.retrieve(_query("function", top_k=1))
    assert len(result.evidence) <= 1


def test_filter_scopes_by_repository(fake_store, fake_embedder):
    from tracepilot_shared.config import get_settings

    chunks = [
        _chunk(1, "repo one content about config", "a.py"),
        _chunk(2, "repo two content about config", "b.py"),
    ]
    chunks[1].metadata.repository_id = "repo_2"
    fake_store.ensure_collection(fake_embedder.dim)
    fake_store.upsert(chunks, fake_embedder.embed_documents([c.text for c in chunks]))
    retriever = Retriever(fake_store, fake_embedder, get_settings())

    result = retriever.retrieve(_query("config"))
    assert result.evidence
    assert all(e.metadata.repository_id == "repo_1" for e in result.evidence)


def test_retrieve_empty_store_returns_empty(fake_store, fake_embedder):
    from tracepilot_shared.config import get_settings

    fake_store.ensure_collection(fake_embedder.dim)
    retriever = Retriever(fake_store, fake_embedder, get_settings())
    result = retriever.retrieve(_query("anything"))
    assert result.evidence == []


# --------------------------------------------------------------------------- #
# build_citations
# --------------------------------------------------------------------------- #
def _ev(i, text, fp, start, end, score):
    return Evidence(
        id=f"e{i}",
        text=text,
        score=score,
        metadata=ChunkMetadata(
            repository_id="repo_1", repo_name="demo", file_path=fp, start_line=start, end_line=end
        ),
    )


def test_build_citations_indexes_and_dedupes():
    evidence = [
        _ev(1, "alpha\nbeta", "a.py", 1, 2, 0.9),
        _ev(2, "alpha\nbeta", "a.py", 1, 2, 0.8),  # identical span → collapsed
        _ev(3, "gamma", "b.py", 10, 12, 0.7),
    ]
    citations = build_citations(evidence)
    assert [c.index for c in citations] == [1, 2]  # 1-based, contiguous after dedupe
    assert [c.file_path for c in citations] == ["a.py", "b.py"]
    # Highest-ranked occurrence is kept; repo name carried through.
    assert citations[0].repository == "demo"
    assert citations[0].score == pytest.approx(0.9)


def test_build_citations_trims_snippet():
    long_text = "\n".join(f"line {i}" for i in range(40))
    citations = build_citations([_ev(1, long_text, "c.py", 1, 40, 0.5)], max_snippet_lines=5)
    snippet = citations[0].snippet
    assert "more lines" in snippet
    assert snippet.count("\n") <= 6  # 5 lines + the "more lines" marker


def test_build_citations_empty():
    assert build_citations([]) == []


# --------------------------------------------------------------------------- #
# pack_context
# --------------------------------------------------------------------------- #
def test_pack_context_numbers_blocks_and_fits_budget():
    evidence = [
        _ev(1, "alpha body text", "a.py", 1, 3, 0.9),
        _ev(2, "beta body text", "b.py", 4, 6, 0.8),
    ]
    packed = pack_context(evidence, max_chars=4000)
    assert "[1]" in packed and "[2]" in packed
    assert "a.py:1-3" in packed
    assert "b.py:4-6" in packed
    assert len(packed) <= 4000


def test_pack_context_truncates_when_over_budget():
    big = "\n".join("x" * 80 for _ in range(50))
    evidence = [
        _ev(1, big, "a.py", 1, 50, 0.9),
        _ev(2, big, "b.py", 1, 50, 0.8),
    ]
    # Budget large enough for one block + header, forcing the 2nd to truncate/drop.
    packed = pack_context(evidence, max_chars=1200)
    assert len(packed) <= 1200
    assert "[1]" in packed
    # Either the 2nd block is truncated or dropped — but the budget holds.
    assert "truncated" in packed or "[2]" not in packed


def test_pack_context_empty_evidence():
    assert pack_context([]) == "(no evidence retrieved)"

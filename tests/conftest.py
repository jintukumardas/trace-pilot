"""Shared pytest fixtures for the TracePilot test-suite.

The whole suite is designed to run **without** any live backend (Ollama, Qdrant,
Redis, Langfuse). We achieve that with a handful of fakes:

* ``sample_repo``         — a throwaway workspace dir holding a tiny real repo
                            (a couple of ``.py`` files + a ``README.md``) so the
                            sandboxed tools and the chunker have real bytes to chew on.
* ``FakeEmbedder``        — deterministic, hash-based vectors (no model download).
* ``FakeQdrantStore``     — an in-memory vector store implementing the same surface
                            as ``tracepilot_retrieval.QdrantStore`` (brute-force
                            cosine search + filtered scroll), so the ``Retriever``
                            runs its real dense/sparse/hybrid math against it.
* ``canned_model``        — monkeypatches ``tracepilot_agent.models.complete`` (and
                            every node module that imported it) to return canned
                            JSON / text, so the agent graph runs Ollama-free.
* ``api_client``          — a FastAPI ``TestClient`` whose ``app.state`` is wired to
                            the fakes (store, retriever, orchestrator, …).

Integration tests that genuinely need a live service are marked
``@pytest.mark.integration`` and skipped unless ``QDRANT_URL`` / ``OLLAMA_BASE_URL``
point at something reachable (see ``pytest_collection_modifyitems`` below).

Config is pinned to in-memory / disabled backends via env vars set in
``_isolate_environment`` *before* ``tracepilot_shared.config`` is first imported,
so ``get_settings()`` never reaches out to a real Redis/Qdrant/Langfuse.
"""

from __future__ import annotations

import hashlib
import math
import os
import socket
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

# --------------------------------------------------------------------------- #
# Environment isolation — must run before anything imports the shared config.
# --------------------------------------------------------------------------- #
_ENV_OVERRIDES = {
    "APP_ENV": "local",
    "LOG_JSON": "false",
    "DATABASE_URL": "sqlite:///:memory:",
    "DATA_DIR": "",  # filled in by the fixture with a tmp dir
    "WORKSPACES_DIR": "",  # filled in by the fixture with a tmp dir
    "REDIS_URL": "redis://127.0.0.1:6390/15",  # an almost-certainly-dead port
    "QDRANT_URL": "http://127.0.0.1:6399",
    "OLLAMA_BASE_URL": "http://127.0.0.1:11499",
    "LANGFUSE_ENABLED": "false",
    "LANGFUSE_PUBLIC_KEY": "",
    "LANGFUSE_SECRET_KEY": "",
    "EMBEDDING_PROVIDER": "fastembed",
    "EMBEDDING_DIM": "32",
    "RERANK_ENABLED": "false",
    "HYBRID_ALPHA": "0.6",
    "MAX_CONTEXT_CHARS": "16000",
    "TOOL_TIMEOUT_SECONDS": "10",
    "TOOL_MAX_OUTPUT_BYTES": "64000",
}


@pytest.fixture(scope="session", autouse=True)
def _isolate_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point every backend at a dead/in-memory target before config is read."""
    data_dir = tmp_path_factory.mktemp("tp_data")
    overrides = dict(_ENV_OVERRIDES)
    overrides["DATA_DIR"] = str(data_dir)
    overrides["WORKSPACES_DIR"] = str(data_dir / "workspaces")
    Path(overrides["WORKSPACES_DIR"]).mkdir(parents=True, exist_ok=True)

    saved: dict[str, str | None] = {}
    for key, value in overrides.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = value

    # Reset the cached settings singleton so the overrides take effect even if a
    # prior import already built one.
    try:
        from tracepilot_shared.config import get_settings

        get_settings.cache_clear()
    except Exception:  # pragma: no cover - shared always importable
        pass

    yield

    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture()
def settings():
    """Return a fresh settings object honoring the isolated environment."""
    from tracepilot_shared.config import get_settings

    get_settings.cache_clear()
    return get_settings()


# --------------------------------------------------------------------------- #
# A tiny, real sample repository on disk.
# --------------------------------------------------------------------------- #
_SAMPLE_CONFIG_PY = '''\
"""Application configuration for the sample project."""
import os


DEFAULT_TIMEOUT = 30


class Settings:
    """Holds runtime configuration loaded from the environment."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self.debug = os.environ.get("DEBUG", "false") == "true"

    def as_dict(self) -> dict:
        return {"timeout": self.timeout, "debug": self.debug}


def load_settings() -> Settings:
    """Build a Settings object from environment variables."""
    raw = os.environ.get("TIMEOUT", str(DEFAULT_TIMEOUT))
    return Settings(timeout=int(raw))
'''

_SAMPLE_SERVICE_PY = '''\
"""A small service module that uses the config loader."""
from config import Settings, load_settings


class Service:
    """Coordinates work using the loaded settings."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()

    def run(self, payload: str) -> str:
        if not payload:
            raise ValueError("payload must not be empty")
        return payload.upper()

    def healthcheck(self) -> bool:
        return self.settings.timeout > 0
'''

_SAMPLE_README = """\
# Sample Project

A minimal demo repository used by the TracePilot test-suite.

## Configuration

Configuration is loaded from environment variables by `load_settings()` in
`config.py`. The most important knob is `TIMEOUT` (seconds).

## Running

Instantiate `Service` from `service.py` and call `run()` with a payload.
"""


@pytest.fixture()
def sample_repo(tmp_path: Path) -> Path:
    """Materialize a tiny repo (2 .py + README) and return its absolute root."""
    root = tmp_path / "sample_repo"
    root.mkdir()
    (root / "config.py").write_text(_SAMPLE_CONFIG_PY, encoding="utf-8")
    (root / "service.py").write_text(_SAMPLE_SERVICE_PY, encoding="utf-8")
    (root / "README.md").write_text(_SAMPLE_README, encoding="utf-8")
    # A subpackage so path-prefix / recursion is exercised.
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "helpers.py").write_text("def helper(x):\n    return x + 1\n", encoding="utf-8")
    return root.resolve()


# --------------------------------------------------------------------------- #
# Deterministic fake embedder.
# --------------------------------------------------------------------------- #
class FakeEmbedder:
    """Hash-based deterministic embedder. No model download, fully reproducible.

    Implements the ``tracepilot_retrieval.Embedder`` protocol: ``dim``, ``name``,
    ``embed_documents`` and ``embed_query``. Vectors are L2-normalized so cosine
    similarity is well-behaved, and token overlap between two texts increases
    their similarity (so retrieval ordering is meaningful, not random).
    """

    def __init__(self, dim: int = 32) -> None:
        self.dim = dim
        self.name = "fake-embedder"

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = [t for t in _simple_tokens(text)] or [text or ""]
        for tok in tokens:
            h = hashlib.sha1(tok.encode("utf-8")).digest()
            for i in range(self.dim):
                # Map two hash bytes to a signed contribution per dimension.
                byte = h[(i * 2) % len(h)]
                sign = 1.0 if h[(i * 2 + 1) % len(h)] & 1 else -1.0
                vec[i] += sign * (byte / 255.0)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def _simple_tokens(text: str) -> list[str]:
    out: list[str] = []
    cur = []
    for ch in (text or "").lower():
        if ch.isalnum() or ch == "_":
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


@pytest.fixture()
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder(dim=32)


# --------------------------------------------------------------------------- #
# In-memory fake Qdrant store (same surface as QdrantStore).
# --------------------------------------------------------------------------- #
def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class FakeQdrantStore:
    """A list-backed stand-in for ``QdrantStore`` used across the suite.

    Stores ``(CodeChunk, vector)`` pairs and answers ``search`` with a
    brute-force cosine scan and ``iter_chunks`` with a filtered scroll, matching
    the real store's behavior closely enough that the ``Retriever`` exercises its
    real fusion code path. Filtering honors ``repository_ids``, ``branch``,
    ``chunk_types`` and ``path_prefix``.
    """

    def __init__(self) -> None:
        self._points: list[tuple[Any, list[float]]] = []
        self.dim: int | None = None

    # -- lifecycle ----------------------------------------------------------
    def ensure_collection(self, dim: int) -> None:
        self.dim = int(dim)

    # -- writes -------------------------------------------------------------
    def upsert(self, chunks: list[Any], vectors: list[list[float]]) -> None:
        for chunk, vector in zip(chunks, vectors, strict=False):
            # Replace any existing point with the same chunk id (upsert semantics).
            self._points = [p for p in self._points if p[0].id != chunk.id]
            self._points.append((chunk, list(vector)))

    # -- filtering ----------------------------------------------------------
    @staticmethod
    def _matches(md: Any, flt: Any) -> bool:
        if flt is None:
            return True
        if flt.repository_ids and md.repository_id not in flt.repository_ids:
            return False
        if flt.branch and md.branch != flt.branch:
            return False
        if flt.chunk_types:
            allowed = {str(ct) for ct in flt.chunk_types}
            if str(md.chunk_type) not in allowed:
                return False
        if flt.path_prefix and not str(md.file_path).startswith(flt.path_prefix):
            return False
        return True

    def _evidence(self, chunk: Any, score: float, rank: int, retriever: str):
        from tracepilot_shared.models import Evidence

        return Evidence(
            id=chunk.id,
            text=chunk.text,
            score=float(score),
            metadata=chunk.metadata,
            rank=rank,
            retriever=retriever,  # type: ignore[arg-type]
        )

    # -- reads --------------------------------------------------------------
    def search(self, vector: list[float], flt: Any, top_k: int) -> list[Any]:
        scored = []
        for chunk, vec in self._points:
            if not self._matches(chunk.metadata, flt):
                continue
            scored.append((chunk, _cosine(vector, vec)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [
            self._evidence(chunk, score, rank, "dense")
            for rank, (chunk, score) in enumerate(scored[: max(1, int(top_k))])
        ]

    def iter_chunks(self, flt: Any, limit: int = 2000) -> list[Any]:
        out = []
        for chunk, _vec in self._points:
            if not self._matches(chunk.metadata, flt):
                continue
            out.append(self._evidence(chunk, 0.0, 0, "sparse"))
            if len(out) >= limit:
                break
        return out

    def delete_repository(self, repository_id: str) -> None:
        self._points = [p for p in self._points if p[0].metadata.repository_id != repository_id]

    def count(self, repository_id: str | None = None) -> int:
        if repository_id is None:
            return len(self._points)
        return sum(1 for c, _ in self._points if c.metadata.repository_id == repository_id)

    def content_hashes(self, repository_id: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for chunk, _ in self._points:
            md = chunk.metadata
            if md.repository_id == repository_id and md.file_path not in out:
                out[md.file_path] = chunk.content_hash
        return out


@pytest.fixture()
def fake_store() -> FakeQdrantStore:
    return FakeQdrantStore()


# --------------------------------------------------------------------------- #
# Evidence / chunk factories used by several modules.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def make_evidence() -> Callable[..., Any]:
    """Return a factory that builds an ``Evidence`` with sane metadata defaults."""

    def _factory(
        *,
        id: str = "ev",
        text: str = "sample text",
        score: float = 0.5,
        repository_id: str = "repo_1",
        repo_name: str = "demo",
        file_path: str = "config.py",
        start_line: int = 1,
        end_line: int = 5,
        symbol: str | None = None,
        rank: int = 0,
        retriever: str = "hybrid",
    ):
        from tracepilot_shared.models import ChunkMetadata, Evidence

        return Evidence(
            id=id,
            text=text,
            score=score,
            metadata=ChunkMetadata(
                repository_id=repository_id,
                repo_name=repo_name,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                symbol=symbol,
            ),
            rank=rank,
            retriever=retriever,  # type: ignore[arg-type]
        )

    return _factory


@pytest.fixture()
def indexed_store(sample_repo: Path, fake_store: FakeQdrantStore, fake_embedder: FakeEmbedder):
    """A FakeQdrantStore pre-populated with chunks from the sample repo."""
    from tracepilot_retrieval.chunking import chunk_file

    chunks = []
    for path in sorted(sample_repo.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(sample_repo))
        text = path.read_text(encoding="utf-8")
        chunks.extend(
            chunk_file(
                file_path=rel,
                text=text,
                repository_id="repo_1",
                repo_name="demo",
                branch="main",
                commit_hash="deadbeef",
            )
        )
    vectors = fake_embedder.embed_documents([c.text for c in chunks])
    fake_store.ensure_collection(fake_embedder.dim)
    fake_store.upsert(chunks, vectors)
    return fake_store


# --------------------------------------------------------------------------- #
# Canned, Ollama-free model.
# --------------------------------------------------------------------------- #
def default_canned_complete(
    prompt: str, role: str = "gen", want_json: bool = False, settings: Any = None
) -> Any:
    """A deterministic stand-in for ``tracepilot_agent.models.complete``.

    Dispatches on cues in the rendered prompt so each node receives a
    schema-correct reply. Returns text for ``want_json=False`` and a ``dict`` for
    ``want_json=True`` (never a degraded ``_warning`` dict, so the happy path is
    exercised).
    """
    p = prompt.lower()
    if not want_json:
        # code_analyst free-text reasoning.
        return (
            "The configuration is loaded from environment variables via "
            "load_settings() in config.py [1]. Service depends on it [2]."
        )
    if "task: router" in p:
        return {"intent": "question", "rationale": "asks how the code works", "repository_focus": []}
    if "task: retrieval" in p:
        return {
            "queries": [
                {"query": "how is configuration loaded", "strategy": "hybrid", "top_k": 5},
            ]
        }
    if "task: action" in p:
        return {"needs_tools": False, "tool_calls": [], "rationale": "evidence is sufficient"}
    if '"grounding"' in p or "task: judge" in p:
        return {"grounding": 0.82, "relevance": 0.9, "completeness": 0.8, "confidence": "high", "issues": []}
    if "task: debug" in p:
        return {
            "summary": "A ValueError is raised for empty payloads in Service.run [1].",
            "root_cause_candidates": [
                {
                    "hypothesis": "Empty payload reaches run()",
                    "confidence": "high",
                    "impacted_files": ["service.py"],
                    "reasoning": "run() guards empty input [1].",
                    "evidence_indices": [0],
                },
            ],
            "impacted_files": ["service.py"],
            "diagnostic_steps": ["Add a test calling run('')"],
            "fix_plan": {
                "steps": ["Validate upstream"],
                "risks": ["none"],
                "test_strategy": ["unit test"],
                "rollback": "revert",
            },
            "confidence": "high",
        }
    if "task: change" in p or "change review" in p or "task: review" in p:
        return {
            "summary": "The change adjusts the timeout default [1].",
            "impact": "Low blast radius, config-only.",
            "risk_level": "low",
            "affected_areas": ["config.py"],
            "suggested_tests": ["Test load_settings with TIMEOUT set"],
        }
    # Default: synthesizer answer.
    return {
        "answer": "Configuration is loaded by load_settings() in config.py [1], which Service consumes [2].",
        "confidence": "high",
        "next_actions": [
            {
                "title": "Read config.py",
                "detail": "Inspect load_settings()",
                "rationale": "It is the source of configuration.",
            },
        ],
    }


# Node modules that did ``from ..models import complete`` and therefore hold their
# own reference that must be patched independently of ``models.complete``.
_NODE_MODULES = (
    "tracepilot_agent.nodes.router",
    "tracepilot_agent.nodes.retrieval_planner",
    "tracepilot_agent.nodes.code_analyst",
    "tracepilot_agent.nodes.action_planner",
    "tracepilot_agent.nodes.synthesizer",
    "tracepilot_agent.nodes.judge",
)


def _patch_complete(monkeypatch: pytest.MonkeyPatch, fn: Callable[..., Any]) -> None:
    import importlib

    import tracepilot_agent.models as models_mod

    monkeypatch.setattr(models_mod, "complete", fn, raising=True)
    for name in _NODE_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception:  # pragma: no cover - node module always importable
            continue
        if hasattr(mod, "complete"):
            monkeypatch.setattr(mod, "complete", fn, raising=True)


@pytest.fixture()
def canned_model(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Any]:
    """Patch ``complete`` everywhere it is used to the default canned responder.

    Returns the responder so a test can call it directly if it wants to assert on
    a specific prompt → reply mapping.
    """
    _patch_complete(monkeypatch, default_canned_complete)
    return default_canned_complete


@pytest.fixture()
def patch_model(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[..., Any]], None]:
    """Return a helper to install a *custom* ``complete`` across all node modules."""

    def _install(fn: Callable[..., Any]) -> None:
        _patch_complete(monkeypatch, fn)

    return _install


# --------------------------------------------------------------------------- #
# Agent-graph forward-ref shim.
# --------------------------------------------------------------------------- #
@pytest.fixture()
def agent_state_module():
    """Import ``tracepilot_agent.state`` with its TYPE_CHECKING names resolvable.

    ``AgentState`` references ``Retriever`` / ``RepoLocator`` as string forward
    refs that only exist under ``TYPE_CHECKING``. ``langgraph``'s ``StateGraph``
    runs ``get_type_hints`` on the schema and needs those names at runtime, so we
    inject the real classes into the module namespace. This touches only the test
    process — the frozen package is unchanged on disk.
    """
    import tracepilot_agent.state as state_mod

    try:
        from tracepilot_retrieval import Retriever

        state_mod.Retriever = Retriever  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass
    try:
        from tracepilot_agent.runtime import RepoLocator

        state_mod.RepoLocator = RepoLocator  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass
    return state_mod


# --------------------------------------------------------------------------- #
# Fake orchestrator + repo locator for API tests.
# --------------------------------------------------------------------------- #
class FakeRepoLocator:
    """Resolve repo ids to on-disk paths from a simple dict (or None)."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self._mapping = mapping or {}

    def resolve(self, repository_id: str) -> str | None:
        return self._mapping.get(repository_id)

    def name(self, repository_id: str) -> str:
        return repository_id


class FakeOrchestrator:
    """Returns canned, schema-valid responses for chat/debug/review.

    Mirrors the real ``Orchestrator`` surface so the API routes serialize a proper
    ``ChatResponse`` / ``DebugResponse`` / ``DiffReviewResponse`` without invoking
    the LangGraph graph or a model.
    """

    def chat(self, req: Any) -> Any:
        from tracepilot_shared.models import (
            ChatResponse,
            ChunkMetadata,
            Citation,
            Confidence,
            Evidence,
            IntentType,
            NextAction,
        )

        md = ChunkMetadata(
            repository_id="repo_1", repo_name="demo", file_path="config.py", start_line=1, end_line=5
        )
        evidence = [Evidence(id="ev1", text="load_settings()", score=0.9, metadata=md)]
        citations = [
            Citation(
                index=1,
                repository="demo",
                file_path="config.py",
                start_line=1,
                end_line=5,
                snippet="load_settings()",
                score=0.9,
            )
        ]
        return ChatResponse(
            answer="Configuration is loaded by load_settings() in config.py [1].",
            confidence=Confidence.HIGH,
            intent=IntentType.QUESTION,
            mode=req.mode,
            evidence=evidence,
            citations=citations,
            next_actions=[NextAction(title="Read config.py", detail="open it", rationale="source of config")],
            tools_used=[],
            trace_id="trace_fake123",
            latency_ms=12.3,
            warnings=[],
        )

    def debug(self, req: Any) -> Any:
        from tracepilot_shared.models import (
            Confidence,
            DebugResponse,
            RootCauseCandidate,
        )

        return DebugResponse(
            summary="Empty payloads raise a ValueError in Service.run.",
            root_cause_candidates=[
                RootCauseCandidate(
                    hypothesis="empty payload", confidence=Confidence.HIGH, impacted_files=["service.py"]
                ),
            ],
            impacted_files=["service.py"],
            diagnostic_steps=["call run('')"],
            confidence=Confidence.HIGH,
            trace_id="trace_fake_debug",
            latency_ms=10.0,
        )

    def review(self, req: Any) -> Any:
        from tracepilot_shared.models import Confidence, DiffReviewResponse

        return DiffReviewResponse(
            summary="Config-only change.",
            impact="Low blast radius.",
            risk_level=Confidence.LOW,
            affected_areas=["config.py"],
            suggested_tests=["test load_settings"],
            trace_id="trace_fake_review",
            latency_ms=8.0,
        )


@pytest.fixture()
def api_client(settings, fake_store) -> Iterator[Any]:
    """A FastAPI ``TestClient`` with ``app.state`` wired to in-memory fakes.

    The real lifespan is bypassed (it would try to build the heavy retrieval +
    agent stack); instead we construct the app and stamp ``app.state`` ourselves
    with a real in-memory ``MetadataStore`` and the fakes above.
    """
    from fastapi.testclient import TestClient

    from tracepilot_api.core.runtime import ApiRepoLocator
    from tracepilot_api.core.store import MetadataStore
    from tracepilot_api.main import create_app

    app = create_app()
    store = MetadataStore(":memory:")

    # Wire state directly so we never enter the (heavy) lifespan startup path.
    app.state.settings = settings
    app.state.store = store
    app.state.repo_locator = ApiRepoLocator(store, settings)
    app.state.embedder = None
    app.state.qdrant_store = fake_store
    app.state.retriever = object()  # presence is enough for /health + deps
    app.state.ingestor = object()
    app.state.orchestrator = FakeOrchestrator()

    # ``with TestClient`` would run the lifespan; we deliberately avoid that and
    # construct the client without triggering startup so our wiring stands.
    client = TestClient(app)
    client.app.state.store = store  # ensure visibility through the client
    try:
        yield client
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# Integration marker + skip guard.
# --------------------------------------------------------------------------- #
def _tcp_reachable(url: str, default_port: int) -> bool:
    """True if a TCP connection to the host:port behind ``url`` succeeds quickly."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url if "://" in url else f"http://{url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or default_port
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except Exception:
        return False


def _qdrant_reachable() -> bool:
    return _tcp_reachable(os.environ.get("QDRANT_URL", ""), 6333)


def _ollama_reachable() -> bool:
    return _tcp_reachable(os.environ.get("OLLAMA_BASE_URL", ""), 11434)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: test needs a live backend (Qdrant/Ollama); skipped when unreachable",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``@pytest.mark.integration`` tests unless the backends are reachable."""
    if _qdrant_reachable() and _ollama_reachable():
        return
    skip = pytest.mark.skip(
        reason="integration backend unreachable (set QDRANT_URL + OLLAMA_BASE_URL to live services)"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)

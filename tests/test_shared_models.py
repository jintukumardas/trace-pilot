"""Round-trip + validation tests for the shared Pydantic models.

These guard the public data contract every other package links against: the
required fields on ``ChatResponse``, the enum membership, and that the models
serialize and re-parse losslessly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tracepilot_shared.models import (
    ChatMessage,
    ChatMode,
    ChatRequest,
    ChatResponse,
    ChunkMetadata,
    ChunkType,
    Citation,
    CodeChunk,
    Confidence,
    DebugResponse,
    EvalMetric,
    EvalResult,
    EvalScore,
    Evidence,
    IntentType,
    JobStatus,
    NextAction,
    Repository,
    RepositoryStats,
    RepoStatus,
    RetrievalQuery,
    RetrievalResult,
    ToolCall,
    ToolName,
    ToolResult,
    ToolSpec,
    Workspace,
)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
def test_enum_values_are_stable_strings():
    assert {c.value for c in Confidence} == {"low", "medium", "high"}
    assert {m.value for m in ChatMode} == {"ask", "onboard", "debug", "change_review", "fix_plan"}
    assert {i.value for i in IntentType} == {
        "question",
        "onboarding",
        "debugging",
        "change_review",
        "fix_plan",
        "smalltalk",
    }
    assert {t.value for t in ChunkType} >= {"code", "markdown", "doc", "config"}
    assert {s.value for s in RepoStatus} == {"registered", "indexing", "indexed", "error"}
    assert {j.value for j in JobStatus} == {"pending", "running", "succeeded", "failed"}
    assert {m.value for m in EvalMetric} == {
        "grounding",
        "relevance",
        "completeness",
        "tool_success",
        "retrieval_quality",
    }
    assert {t.value for t in ToolName} == {
        "repo_search",
        "read_file",
        "dep_tree",
        "run_tests",
        "run_lint",
        "git_diff",
        "static_analysis",
    }


def test_str_enum_serializes_to_value():
    # StrEnum members compare/serialize as their string value.
    assert Confidence.HIGH == "high"
    assert str(ChatMode.DEBUG) == "debug"
    assert ToolName.REPO_SEARCH.value == "repo_search"


# --------------------------------------------------------------------------- #
# ChatResponse required fields
# --------------------------------------------------------------------------- #
def test_chat_response_requires_answer():
    with pytest.raises(ValidationError):
        ChatResponse()  # type: ignore[call-arg]


def test_chat_response_defaults_and_required_fields_present():
    resp = ChatResponse(answer="hello")
    # Required-by-spec fields must all be present with sane defaults.
    assert resp.answer == "hello"
    assert resp.confidence == Confidence.MEDIUM
    assert resp.intent == IntentType.QUESTION
    assert resp.mode == ChatMode.ASK
    assert resp.evidence == []
    assert resp.citations == []
    assert resp.next_actions == []
    assert resp.tools_used == []
    assert resp.trace_id is None
    assert resp.latency_ms == 0.0
    assert resp.warnings == []
    # All spec fields exist on the schema.
    for field in (
        "answer",
        "confidence",
        "intent",
        "mode",
        "evidence",
        "citations",
        "next_actions",
        "tools_used",
        "trace_id",
        "latency_ms",
        "warnings",
    ):
        assert field in ChatResponse.model_fields


def test_chat_response_round_trip():
    md = ChunkMetadata(
        repository_id="repo_1", repo_name="demo", file_path="a.py", start_line=1, end_line=10, symbol="foo"
    )
    ev = Evidence(id="e1", text="code", score=0.9, metadata=md)
    cit = Citation(
        index=1, repository="demo", file_path="a.py", start_line=1, end_line=10, snippet="code", score=0.9
    )
    resp = ChatResponse(
        answer="grounded [1]",
        confidence=Confidence.HIGH,
        intent=IntentType.QUESTION,
        mode=ChatMode.ASK,
        evidence=[ev],
        citations=[cit],
        next_actions=[NextAction(title="t", detail="d", rationale="r")],
        trace_id="trace_x",
        latency_ms=42.0,
    )
    dumped = resp.model_dump()
    restored = ChatResponse.model_validate(dumped)
    assert restored == resp
    # JSON round-trip too.
    assert ChatResponse.model_validate_json(resp.model_dump_json()) == resp


# --------------------------------------------------------------------------- #
# ChatRequest validation
# --------------------------------------------------------------------------- #
def test_chat_request_message_min_length():
    with pytest.raises(ValidationError):
        ChatRequest(workspace_id="ws_1", message="")


def test_chat_request_top_k_bounds():
    ok = ChatRequest(workspace_id="ws_1", message="hi", top_k=50)
    assert ok.top_k == 50
    with pytest.raises(ValidationError):
        ChatRequest(workspace_id="ws_1", message="hi", top_k=0)
    with pytest.raises(ValidationError):
        ChatRequest(workspace_id="ws_1", message="hi", top_k=51)


def test_chat_request_defaults():
    req = ChatRequest(workspace_id="ws_1", message="hi")
    assert req.mode == ChatMode.ASK
    assert req.repository_ids == []
    assert req.history == []
    assert req.branch is None


def test_chat_message_role_literal():
    ChatMessage(role="user", content="hi")
    with pytest.raises(ValidationError):
        ChatMessage(role="robot", content="hi")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Retrieval models
# --------------------------------------------------------------------------- #
def test_retrieval_query_defaults_and_strategy():
    q = RetrievalQuery(query="how does config load")
    assert q.strategy == "hybrid"
    assert q.top_k == 8
    assert q.filter.repository_ids is None
    with pytest.raises(ValidationError):
        RetrievalQuery(query="x", strategy="magic")  # type: ignore[arg-type]


def test_evidence_score_required():
    md = ChunkMetadata(repository_id="r", repo_name="n", file_path="f.py")
    with pytest.raises(ValidationError):
        Evidence(id="e", text="t", metadata=md)  # type: ignore[call-arg]


def test_code_chunk_round_trip():
    md = ChunkMetadata(
        repository_id="r",
        repo_name="n",
        file_path="f.py",
        chunk_type=ChunkType.CODE,
        start_line=1,
        end_line=4,
    )
    chunk = CodeChunk(id="c1", text="def f(): ...", metadata=md, content_hash="abc", token_estimate=4)
    restored = CodeChunk.model_validate_json(chunk.model_dump_json())
    assert restored == chunk
    assert restored.metadata.chunk_type == ChunkType.CODE


def test_retrieval_result_default_evidence_empty():
    r = RetrievalResult(query="q", strategy="dense")
    assert r.evidence == []
    assert r.reranked is False


# --------------------------------------------------------------------------- #
# Tooling models
# --------------------------------------------------------------------------- #
def test_tool_call_and_result_round_trip():
    call = ToolCall(id="tc1", tool=ToolName.READ_FILE, args={"path": "a.py"}, reason="inspect")
    result = ToolResult(id="tc1", tool=ToolName.READ_FILE, ok=True, output="…", exit_code=0)
    assert ToolCall.model_validate_json(call.model_dump_json()) == call
    assert ToolResult.model_validate_json(result.model_dump_json()) == result


def test_tool_spec_defaults():
    spec = ToolSpec(name=ToolName.REPO_SEARCH, description="search")
    assert spec.destructive is False
    assert spec.args_schema == {}


# --------------------------------------------------------------------------- #
# Workspace / repository models
# --------------------------------------------------------------------------- #
def test_workspace_round_trip():
    ws = Workspace(id="ws_1", name="Platform", slug="platform", repository_count=3)
    assert Workspace.model_validate_json(ws.model_dump_json()) == ws


def test_repository_defaults():
    repo = Repository(id="repo_1", workspace_id="ws_1", name="demo")
    assert repo.status == RepoStatus.REGISTERED
    assert repo.branch == "main"
    assert isinstance(repo.stats, RepositoryStats)
    assert repo.stats.num_chunks == 0


# --------------------------------------------------------------------------- #
# Eval models
# --------------------------------------------------------------------------- #
def test_eval_score_bounds():
    EvalScore(metric=EvalMetric.GROUNDING, score=0.0)
    EvalScore(metric=EvalMetric.GROUNDING, score=1.0)
    with pytest.raises(ValidationError):
        EvalScore(metric=EvalMetric.GROUNDING, score=1.5)
    with pytest.raises(ValidationError):
        EvalScore(metric=EvalMetric.GROUNDING, score=-0.1)


def test_eval_result_average():
    result = EvalResult(
        scores=[
            EvalScore(metric=EvalMetric.GROUNDING, score=0.8),
            EvalScore(metric=EvalMetric.RELEVANCE, score=0.6),
        ]
    )
    assert result.average() == pytest.approx(0.7)
    assert EvalResult().average() == 0.0


def test_debug_response_minimal():
    resp = DebugResponse(summary="root cause is X")
    assert resp.confidence == Confidence.MEDIUM
    assert resp.root_cause_candidates == []
    assert resp.fix_plan is None

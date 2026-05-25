"""Tests for the agent graph: initial state shape, an end-to-end node run that
produces a grounded answer with citations/confidence/next_actions, and the
bounded tool loop.

We drive the *real* node functions (router → … → judge) with a canned,
Ollama-free model and a fake retriever. This exercises the genuine agent logic
without LangGraph's runtime. A second test additionally tries the compiled graph
and skips cleanly if the installed LangGraph can't compile this package's schema.
"""

from __future__ import annotations

import pytest

from tracepilot_agent.graph import MAX_TOOL_ITERATIONS, _route_after_action
from tracepilot_agent.nodes import (
    action_planner_node,
    code_analyst_node,
    judge_node,
    retrieval_planner_node,
    retriever_node,
    router_node,
    synthesizer_node,
    tool_executor_node,
)
from tracepilot_agent.state import make_initial_state
from tracepilot_shared.models import (
    Citation,
    Confidence,
    Evidence,
    NextAction,
    RetrievalQuery,
    RetrievalResult,
)
from tracepilot_shared.telemetry import Tracer


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
class _FakeRetriever:
    """Returns one canned Evidence so the retriever node has something to ground on."""

    def __init__(self, make_evidence):
        self._make = make_evidence

    def retrieve(self, query: RetrievalQuery, tracer=None) -> RetrievalResult:
        ev = self._make(
            id="ev1",
            text="def load_settings():\n    return Settings()",
            score=0.95,
            file_path="config.py",
            start_line=1,
            end_line=2,
            symbol="load_settings",
        )
        return RetrievalResult(query=query.query, strategy="hybrid", evidence=[ev])


def _run_pipeline(state: dict) -> dict:
    """Run the linear node spine (no tools) and return the merged final state."""
    for node in (
        router_node,
        retrieval_planner_node,
        retriever_node,
        code_analyst_node,
        action_planner_node,
        synthesizer_node,
        judge_node,
    ):
        state.update(node(state))
    return state


@pytest.fixture()
def initial_state(make_evidence):
    tracer = Tracer(name="test-chat", workflow="ask")
    return make_initial_state(
        request="How is configuration loaded?",
        mode="ask",
        tracer=tracer,
        retriever=_FakeRetriever(make_evidence),
        repo_locator=None,
        workspace_id="ws_1",
        repository_ids=["repo_1"],
    )


# --------------------------------------------------------------------------- #
# make_initial_state shape
# --------------------------------------------------------------------------- #
def test_make_initial_state_shape(initial_state):
    s = initial_state
    # Request envelope.
    assert s["request"] == "How is configuration loaded?"
    assert s["mode"] == "ask"
    assert s["workspace_id"] == "ws_1"
    assert s["repository_ids"] == ["repo_1"]
    # Per-node products seeded empty.
    for key in (
        "plan",
        "queries",
        "evidence",
        "citations",
        "tool_calls",
        "tool_results",
        "next_actions",
        "warnings",
        "errors",
    ):
        assert s[key] == []
    assert s["context"] == ""
    assert s["analysis"] == ""
    assert s["needs_tools"] is False
    assert s["iterations"] == 0
    # Answer envelope defaults.
    assert s["answer"] == ""
    assert s["confidence"] == Confidence.MEDIUM.value
    assert s["debug"] == {} and s["review"] == {} and s["scores"] == {}
    # Runtime handles injected.
    assert s["retriever"] is not None
    assert s["tracer"] is not None
    assert s["settings"] is not None


def test_make_initial_state_merges_single_repo_id(make_evidence):
    tracer = Tracer(name="t", workflow="change_review")
    s = make_initial_state(
        request="review",
        mode="change_review",
        tracer=tracer,
        retriever=_FakeRetriever(make_evidence),
        repo_locator=None,
        repository_id="repo_9",
    )
    # repository_id is folded into repository_ids for consistent scoping.
    assert "repo_9" in s["repository_ids"]
    assert s["repository_id"] == "repo_9"


# --------------------------------------------------------------------------- #
# End-to-end node run → grounded answer
# --------------------------------------------------------------------------- #
def test_pipeline_produces_grounded_answer(canned_model, initial_state):
    final = _run_pipeline(initial_state)

    # Router classified the intent.
    assert final["intent"] == "question"
    # Retrieval planned and ran, producing evidence + citations.
    assert final["queries"]
    assert all(isinstance(q, RetrievalQuery) for q in final["queries"])
    assert final["evidence"] and all(isinstance(e, Evidence) for e in final["evidence"])
    assert final["citations"] and all(isinstance(c, Citation) for c in final["citations"])
    assert final["citations"][0].index == 1

    # Synthesizer wrote a grounded answer with an inline marker + next actions.
    assert final["answer"]
    assert "[1]" in final["answer"]
    assert final["confidence"] in {c.value for c in Confidence}
    assert final["next_actions"]
    assert all(isinstance(a, NextAction) for a in final["next_actions"])

    # Judge produced scores in [0,1].
    assert set(final["scores"]) >= {"grounding", "relevance", "completeness"}
    assert all(0.0 <= v <= 1.0 for v in final["scores"].values())

    # No degraded-model warnings on the happy path.
    assert not any("model unavailable" in w for w in final["warnings"])


def test_pipeline_maps_cleanly_to_chat_response(canned_model, initial_state):
    """The terminal state carries everything the ChatResponse contract needs."""
    from tracepilot_shared.models import ChatMode, ChatResponse, IntentType

    final = _run_pipeline(initial_state)
    resp = ChatResponse(
        answer=final["answer"],
        confidence=Confidence(final["confidence"]),
        intent=IntentType(final["intent"]),
        mode=ChatMode.ASK,
        evidence=[e for e in final["evidence"] if isinstance(e, Evidence)],
        citations=[c for c in final["citations"] if isinstance(c, Citation)],
        next_actions=[a for a in final["next_actions"] if isinstance(a, NextAction)],
    )
    assert resp.answer
    assert resp.citations
    assert resp.confidence == Confidence.HIGH
    assert resp.next_actions


def test_pipeline_fails_soft_when_model_unavailable(patch_model, initial_state):
    """With a degraded model the run still completes with an evidence-only answer."""

    def degraded(prompt, role="gen", want_json=False, settings=None):
        if want_json:
            return {"_warning": "model unavailable (ConnectionError)"}
        return "[model unavailable (ConnectionError)]"

    patch_model(degraded)
    final = _run_pipeline(initial_state)

    # Still grounded on retrieved evidence.
    assert final["evidence"]
    assert final["citations"]
    assert final["answer"]  # a fallback answer was synthesized
    assert final["confidence"] == "low"
    assert any("model unavailable" in w for w in final["warnings"])
    # Judge fell back to heuristic scores, still in range.
    assert all(0.0 <= v <= 1.0 for v in final["scores"].values())


# --------------------------------------------------------------------------- #
# Bounded tool loop
# --------------------------------------------------------------------------- #
def test_route_after_action_loops_then_stops():
    # needs tools + has calls + budget remaining → loop into tool_executor.
    looping = {"needs_tools": True, "tool_calls": [object()], "iterations": 0}
    assert _route_after_action(looping) == "tool_executor"

    # Budget spent → synthesize regardless of needs_tools.
    spent = {"needs_tools": True, "tool_calls": [object()], "iterations": MAX_TOOL_ITERATIONS}
    assert _route_after_action(spent) == "synthesizer"

    # No tool calls → synthesize.
    no_calls = {"needs_tools": True, "tool_calls": [], "iterations": 0}
    assert _route_after_action(no_calls) == "synthesizer"

    # Doesn't need tools → synthesize.
    no_need = {"needs_tools": False, "tool_calls": [object()], "iterations": 0}
    assert _route_after_action(no_need) == "synthesizer"


def test_action_planner_refuses_tools_when_budget_spent(canned_model, initial_state):
    initial_state["iterations"] = MAX_TOOL_ITERATIONS
    out = action_planner_node(initial_state)
    assert out["needs_tools"] is False
    assert out["tool_calls"] == []


def test_action_planner_skips_tools_without_resolvable_repo(patch_model, initial_state):
    # Model asks for a tool, but no repo_locator → can't run tools → skip.
    def wants_tool(prompt, role="gen", want_json=False, settings=None):
        if not want_json:
            return "analysis"
        if "task: action" in prompt.lower():
            return {
                "needs_tools": True,
                "tool_calls": [{"tool": "read_file", "args": {"path": "config.py"}, "reason": "inspect"}],
            }
        if "task: router" in prompt.lower():
            return {"intent": "question", "repository_focus": []}
        return {"queries": [{"query": "config", "strategy": "hybrid", "top_k": 5}]}

    patch_model(wants_tool)
    initial_state["repo_locator"] = None
    out = action_planner_node(initial_state)
    assert out["needs_tools"] is False
    assert any("not resolvable" in w for w in out["warnings"])


def test_tool_executor_increments_iteration_and_clears_plan(canned_model, initial_state):
    from tracepilot_agent.nodes.action_planner import _coerce_tool_calls

    # Plan a tool call but give no workspace path → executor skips but still
    # increments the iteration counter and clears the plan (loop-termination guard).
    initial_state["tool_calls"] = _coerce_tool_calls(
        [{"tool": "read_file", "args": {"path": "config.py"}, "reason": "x"}]
    )
    initial_state["repo_locator"] = None
    out = tool_executor_node(initial_state)
    assert out["iterations"] == 1
    assert out["tool_calls"] == []
    assert out["needs_tools"] is False


def test_tool_loop_is_bounded(canned_model, make_evidence):
    """Even if the model always wants tools, the loop terminates within the budget."""
    tracer = Tracer(name="t", workflow="ask")
    state = make_initial_state(
        request="debug something",
        mode="ask",
        tracer=tracer,
        retriever=_FakeRetriever(make_evidence),
        repo_locator=None,
        repository_ids=["repo_1"],
    )
    # Prime evidence so retriever-dependent nodes have context.
    state.update(
        retriever_node({**state, "queries": [RetrievalQuery(query="config", strategy="hybrid", top_k=5)]})
    )

    iterations = 0
    # Simulate the graph's bounded loop manually.
    for _ in range(MAX_TOOL_ITERATIONS + 3):  # generous upper bound; must break early
        state.update(action_planner_node(state))
        if _route_after_action(state) != "tool_executor":
            break
        state.update(tool_executor_node(state))
        iterations += 1
    assert iterations <= MAX_TOOL_ITERATIONS


# --------------------------------------------------------------------------- #
# Compiled graph (best-effort — skips if the installed LangGraph can't compile)
# --------------------------------------------------------------------------- #
def test_compiled_graph_runs_end_to_end(canned_model, agent_state_module, make_evidence):
    from tracepilot_agent.graph import build_graph

    try:
        graph = build_graph()
    except Exception as exc:  # pragma: no cover - depends on installed LangGraph version
        pytest.skip(f"build_graph() unsupported in this environment: {exc}")

    tracer = Tracer(name="t", workflow="ask")
    state = make_initial_state(
        request="How is configuration loaded?",
        mode="ask",
        tracer=tracer,
        retriever=_FakeRetriever(make_evidence),
        repo_locator=None,
        workspace_id="ws_1",
        repository_ids=["repo_1"],
    )
    final = graph.invoke(state, config={"recursion_limit": 40})
    assert isinstance(final, dict)
    assert final.get("answer")
    assert final.get("citations")
    assert final.get("scores")

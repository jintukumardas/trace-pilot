"""Assemble and compile the LangGraph agent graph.

Topology (see ``docs/INTERNAL_CONTRACTS.md``)::

    START → router → retrieval_planner → evidence_retriever → code_analyst → action_planner
                                                                       │
                          ┌── needs_tools and iterations < 2 ──────────┤
                          ▼                                            ▼
                   tool_executor ──► code_analyst (loop)          synthesizer
                                                                       │
                                                                     judge → END

The tool loop is bounded to two iterations: :func:`_route_after_action` only
sends control back to ``tool_executor`` while ``needs_tools`` is set *and* the
iteration budget remains. ``tool_executor`` increments the counter and clears the
plan, and ``action_planner`` itself refuses to plan tools once the budget is
spent — three independent guards against an infinite loop.

The compiled graph is cached process-wide; it is stateless (all per-request data,
including the tracer/retriever/repo_locator, rides on :class:`AgentState`), so a
single compiled instance is safely shared across requests.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from tracepilot_shared.logging import get_logger

from .nodes import (
    action_planner_node,
    code_analyst_node,
    judge_node,
    retrieval_planner_node,
    retriever_node,
    router_node,
    synthesizer_node,
    tool_executor_node,
)
from .state import AgentState

log = get_logger("agent.graph")

MAX_TOOL_ITERATIONS = 2


def _route_after_action(state: AgentState) -> str:
    """Conditional edge out of ``action_planner``: loop into tools or synthesize."""
    needs = bool(state.get("needs_tools"))
    iterations = int(state.get("iterations", 0))
    has_calls = bool(state.get("tool_calls"))
    if needs and has_calls and iterations < MAX_TOOL_ITERATIONS:
        return "tool_executor"
    return "synthesizer"


def build_graph() -> Any:
    """Build, wire, and compile the agent ``StateGraph``. Returns a compiled graph."""
    # Lazy import so importing this package doesn't hard-require langgraph at import
    # time (e.g. for static analysis / docs tooling).
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(AgentState)

    graph.add_node("router", router_node)
    graph.add_node("retrieval_planner", retrieval_planner_node)
    graph.add_node("evidence_retriever", retriever_node)
    graph.add_node("code_analyst", code_analyst_node)
    graph.add_node("action_planner", action_planner_node)
    graph.add_node("tool_executor", tool_executor_node)
    graph.add_node("synthesizer", synthesizer_node)
    graph.add_node("judge", judge_node)

    # Linear spine.
    graph.add_edge(START, "router")
    graph.add_edge("router", "retrieval_planner")
    graph.add_edge("retrieval_planner", "evidence_retriever")
    graph.add_edge("evidence_retriever", "code_analyst")
    graph.add_edge("code_analyst", "action_planner")

    # Bounded tool loop vs. straight-to-synthesis.
    graph.add_conditional_edges(
        "action_planner",
        _route_after_action,
        {"tool_executor": "tool_executor", "synthesizer": "synthesizer"},
    )
    graph.add_edge("tool_executor", "code_analyst")  # re-analyze with tool results

    # Final answer + evaluation.
    graph.add_edge("synthesizer", "judge")
    graph.add_edge("judge", END)

    compiled = graph.compile()
    log.debug("agent graph compiled")
    return compiled


@lru_cache(maxsize=1)
def get_compiled_graph() -> Any:
    """Return the process-wide compiled graph (built once, then cached)."""
    return build_graph()

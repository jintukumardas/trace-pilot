"""Graph nodes for the TracePilot agent.

Each node is a pure ``(AgentState) -> dict`` function that opens a tracer span,
renders its prompt via :func:`tracepilot_prompts.render`, calls the local model
through :func:`tracepilot_agent.models.complete`, and returns a *partial* state
update for LangGraph to merge. Nodes never raise — every model/tool failure is
captured into ``warnings`` and a grounded fallback.
"""

from __future__ import annotations

from .action_planner import action_planner_node
from .code_analyst import code_analyst_node
from .judge import judge_node
from .retrieval_planner import retrieval_planner_node
from .retriever import retriever_node
from .router import router_node
from .synthesizer import synthesizer_node
from .tool_executor import tool_executor_node

__all__ = [
    "router_node",
    "retrieval_planner_node",
    "retriever_node",
    "code_analyst_node",
    "action_planner_node",
    "tool_executor_node",
    "synthesizer_node",
    "judge_node",
]

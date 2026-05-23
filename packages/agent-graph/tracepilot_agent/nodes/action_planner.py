"""Action-planner node — decide whether sandboxed tools should run, and which.

Contract emitted by the prompt::

    {"needs_tools": bool,
     "tool_calls": [{"tool": "<ToolName>", "args": {}, "reason": str}],
     "rationale": str}

We only honor tool calls naming a real :class:`ToolName`, cap the batch at 3, and
require a resolvable repo before declaring ``needs_tools`` — there is no point
planning tools we cannot execute. The graph separately bounds the tool loop to
two iterations, and we refuse to plan tools once that budget is spent so a model
can't spin the loop.
"""

from __future__ import annotations

from tracepilot_prompts import render
from tracepilot_shared.ids import TOOLCALL, new_id
from tracepilot_shared.models import ToolCall, ToolName
from tracepilot_tooling import get_tool_specs

from ..models import complete
from ..state import AgentState
from . import _common as C

_MAX_TOOL_CALLS = 3
_MAX_ITERATIONS = 2


def action_planner_node(state: AgentState) -> dict:
    tracer = state["tracer"]
    iterations = int(state.get("iterations", 0))
    warnings = list(state.get("warnings", []))

    # If we've already spent the tool budget, short-circuit to synthesis. This is
    # the safety net behind the graph's conditional edge.
    if iterations >= _MAX_ITERATIONS:
        return {"needs_tools": False, "tool_calls": []}

    repo_path = _resolvable_repo(state)

    with tracer.span("action_planner", type="generation", input={"iteration": iterations}) as sp:
        prompt = render(
            "action_planner",
            question=state["request"],
            intent=state.get("intent"),
            mode=state.get("mode"),
            evidence=C.evidence_view(state.get("citations", [])),
            analysis=state.get("analysis", ""),
            tools=get_tool_specs(),
        )
        parsed = complete(prompt, role="reason", want_json=True, settings=state.get("settings"))
        C.warn_if_degraded(parsed, "action_planner", warnings)

        needs = C.as_bool(parsed.get("needs_tools")) if isinstance(parsed, dict) else False
        calls = _coerce_tool_calls(parsed.get("tool_calls") if isinstance(parsed, dict) else None)

        # Can't run tools without a workspace on disk — fall through to synthesis.
        if needs and repo_path is None:
            warnings.append("action_planner: repository not resolvable on disk; skipping tools")
            needs = False
            calls = []
        if not calls:
            needs = False

        sp.update(output={"needs_tools": needs, "n_calls": len(calls)})

    return {"needs_tools": needs, "tool_calls": calls, "warnings": warnings}


def _coerce_tool_calls(raw: object) -> list[ToolCall]:
    if not isinstance(raw, list):
        return []
    calls: list[ToolCall] = []
    for item in raw[:_MAX_TOOL_CALLS]:
        if not isinstance(item, dict):
            continue
        name = C.coerce_tool_name(item.get("tool"))
        if name is None:
            continue
        args = item.get("args")
        if not isinstance(args, dict):
            args = {}
        calls.append(
            ToolCall(
                id=new_id(TOOLCALL),
                tool=ToolName(name),
                args=args,
                reason=C.as_str(item.get("reason")).strip(),
            )
        )
    return calls


def _resolvable_repo(state: AgentState) -> str | None:
    """Resolve the repository this run can run tools against, or ``None``."""
    locator = state.get("repo_locator")
    if locator is None:
        return None
    repo_id = _target_repo_id(state)
    if not repo_id:
        return None
    try:
        return locator.resolve(repo_id)
    except Exception:
        return None


def _target_repo_id(state: AgentState) -> str | None:
    if state.get("repository_id"):
        return state["repository_id"]
    repo_ids = state.get("repository_ids") or []
    return repo_ids[0] if repo_ids else None

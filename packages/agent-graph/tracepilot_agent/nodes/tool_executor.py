"""Tool-executor node — run the planned tool calls inside the sandbox.

Resolves the target repository to an absolute path via the injected
:class:`RepoLocator`, builds a workspace-confined :class:`ToolContext`, and runs
each planned :class:`ToolCall` through :func:`tracepilot_tooling.execute_tool`
(which is itself fail-soft and traced). Increments the bounded iteration counter
so the graph's conditional edge eventually routes to synthesis.

Tool results accumulate across loop iterations so the analyst/synthesizer see the
full picture. The node never raises.
"""

from __future__ import annotations

from tracepilot_tooling import ToolContext, execute_tool

from ..state import AgentState

_MAX_RESULTS = 6  # hard cap on accumulated tool results across loops


def tool_executor_node(state: AgentState) -> dict:
    tracer = state["tracer"]
    settings = state.get("settings")
    locator = state.get("repo_locator")
    planned = state.get("tool_calls", [])
    iterations = int(state.get("iterations", 0)) + 1
    warnings = list(state.get("warnings", []))
    existing = list(state.get("tool_results", []))

    repo_id = state.get("repository_id") or (
        state.get("repository_ids", [None])[0] if state.get("repository_ids") else None
    )

    with tracer.span(
        "tool_executor", type="tool", input={"n_calls": len(planned), "iteration": iterations}
    ) as sp:
        workspace_root = None
        if locator is not None and repo_id:
            try:
                workspace_root = locator.resolve(repo_id)
            except Exception as exc:
                warnings.append(f"tool_executor: repo resolve failed ({type(exc).__name__})")

        if not workspace_root:
            warnings.append("tool_executor: no workspace path; tool calls skipped")
            sp.update(output={"executed": 0, "skipped": len(planned)})
            return {
                "tool_results": existing,
                "iterations": iterations,
                "needs_tools": False,
                "tool_calls": [],
                "warnings": warnings,
            }

        ctx = ToolContext.for_workspace(workspace_root, settings)
        new_results = []
        for call in planned:
            result = execute_tool(call, ctx, tracer=tracer)
            new_results.append(result)
            if not result.ok and result.error:
                warnings.append(f"tool_executor: {result.tool} failed: {result.error}")

        combined = (existing + new_results)[-_MAX_RESULTS:]
        sp.update(
            output={
                "executed": len(new_results),
                "ok": sum(1 for r in new_results if r.ok),
            }
        )

    return {
        "tool_results": combined,
        "iterations": iterations,
        # Clear the plan so the next action_planner pass starts fresh.
        "tool_calls": [],
        "needs_tools": False,
        "warnings": warnings,
    }

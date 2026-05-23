"""Tool registry and the single public entry point :func:`execute_tool`.

The registry maps each :class:`~tracepilot_shared.models.ToolName` to a
singleton :class:`~tracepilot_tooling.base.Tool` instance. ``execute_tool`` is
the only sanctioned way to run a tool: it looks the tool up, validates the call,
runs it inside an optional tracer span, catches *all* exceptions into a failed
:class:`ToolResult`, and logs the invocation. Tools never bubble exceptions to
the agent.
"""

from __future__ import annotations

import time

from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import ToolCall, ToolName, ToolResult, ToolSpec
from tracepilot_shared.telemetry import Tracer

from .base import Tool, ToolContext
from .sandbox import SandboxError
from .tools.dep_tree import DepTreeTool
from .tools.git_diff import GitDiffTool
from .tools.read_file import ReadFileTool
from .tools.repo_search import RepoSearchTool
from .tools.run_lint import RunLintTool
from .tools.run_tests import RunTestsTool
from .tools.static_analysis import StaticAnalysisTool

log = get_logger("tooling.registry")


def _build_registry() -> dict[ToolName, Tool]:
    """Instantiate every tool exactly once, keyed by its canonical name."""
    tools: list[Tool] = [
        RepoSearchTool(),
        ReadFileTool(),
        DepTreeTool(),
        RunTestsTool(),
        RunLintTool(),
        GitDiffTool(),
        StaticAnalysisTool(),
    ]
    registry: dict[ToolName, Tool] = {}
    for tool in tools:
        registry[tool.name] = tool
    return registry


#: Process-wide tool registry. Stateless tools, safe to share.
REGISTRY: dict[ToolName, Tool] = _build_registry()


def get_registry() -> dict[ToolName, Tool]:
    """Return the shared name → tool mapping."""
    return REGISTRY


def get_tool_specs() -> list[ToolSpec]:
    """Return the declarative spec of every registered tool (planner/UI facing)."""
    return [tool.spec for tool in REGISTRY.values()]


def _failed(
    call: ToolCall,
    error: str,
    *,
    duration_ms: float = 0.0,
    meta: dict | None = None,
    exit_code: int | None = None,
) -> ToolResult:
    """Construct a uniformly-shaped failed result for a call."""
    return ToolResult(
        id=call.id,
        tool=call.tool,
        ok=False,
        output="",
        truncated=False,
        exit_code=exit_code,
        duration_ms=round(duration_ms, 2),
        error=error,
        meta=meta or {},
    )


def execute_tool(call: ToolCall, ctx: ToolContext, tracer: Tracer | None = None) -> ToolResult:
    """Look up, run, and observe a single tool call.

    * Unknown tools yield a failed result (never an exception).
    * Sandbox breaches (:class:`SandboxError`) are caught and returned as failed
      results tagged ``meta.sandbox_violation = True`` so the caller can treat a
      hard guardrail breach differently from an ordinary tool failure.
    * Any other exception is caught and embedded in the result.
    * When ``tracer`` is provided the whole invocation is wrapped in a
      ``type="tool"`` span whose output mirrors the result.
    """
    if not isinstance(call.tool, ToolName):
        try:
            call.tool = ToolName(str(call.tool))
        except ValueError:
            log.warning("execute_tool: unknown tool name %r", call.tool)
            return _failed(call, f"unknown tool: {call.tool!r}")

    tool = REGISTRY.get(call.tool)
    if tool is None:
        log.warning("execute_tool: no tool registered for %s", call.tool)
        return _failed(call, f"no tool registered for {call.tool}")

    span_input = {"tool": str(call.tool), "args": call.args, "reason": call.reason}

    # Pass the call id to the tool via a reserved key so it can stamp the result
    # directly. The registry still enforces id/tool below as a safety net.
    invoke_args = dict(call.args or {})
    invoke_args["__id"] = call.id

    def _invoke() -> ToolResult:
        return tool.run(invoke_args, ctx)

    t0 = time.perf_counter()
    if tracer is not None:
        with tracer.span(f"tool:{call.tool}", type="tool", input=span_input) as sp:
            result = _run_guarded(tool, call, ctx, _invoke, t0)
            sp.update(
                output={
                    "ok": result.ok,
                    "exit_code": result.exit_code,
                    "truncated": result.truncated,
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                    "output_preview": (result.output or "")[:1000],
                },
                metadata=result.meta,
            )
            if not result.ok and result.error:
                sp.error(result.error)
        return result
    return _run_guarded(tool, call, ctx, _invoke, t0)


def _run_guarded(tool: Tool, call: ToolCall, ctx: ToolContext, invoke, t0: float) -> ToolResult:
    """Run ``invoke`` catching every failure mode into a :class:`ToolResult`."""
    try:
        result = invoke()
        # Defensive: guarantee the contract (id + tool match the call) even if a
        # tool constructed the result loosely.
        if result.id != call.id:
            result.id = call.id
        if result.tool != tool.name:
            result.tool = tool.name
        log.info(
            "tool %s -> ok=%s exit=%s dur=%.1fms%s",
            call.tool,
            result.ok,
            result.exit_code,
            result.duration_ms,
            f" err={result.error}" if result.error else "",
        )
        return result
    except SandboxError as exc:
        dur = (time.perf_counter() - t0) * 1000.0
        log.warning("tool %s sandbox violation: %s", call.tool, exc)
        return _failed(
            call,
            f"sandbox violation: {exc}",
            duration_ms=dur,
            meta={"sandbox_violation": True},
        )
    except Exception as exc:  # noqa: BLE001 - fail soft is the contract
        dur = (time.perf_counter() - t0) * 1000.0
        log.exception("tool %s raised: %s", call.tool, exc)
        return _failed(call, f"{type(exc).__name__}: {exc}", duration_ms=dur)

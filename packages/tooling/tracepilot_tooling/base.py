"""Tool abstraction and the execution context passed to every tool.

A :class:`Tool` is a small, self-describing unit of work confined to a single
workspace via :class:`ToolContext`. Every tool returns a fully-populated
:class:`~tracepilot_shared.models.ToolResult`; tools never raise to the caller —
the registry wraps them, but individual tools also fail soft and embed their
errors in the result so the agent can reason about partial failures.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from tracepilot_shared.config import Settings
from tracepilot_shared.models import ToolCall, ToolName, ToolResult, ToolSpec


@dataclass
class ToolContext:
    """Sandbox boundary + runtime budget handed to every tool invocation.

    ``workspace_root`` is the absolute directory the tool may read from; any
    path that resolves outside of it (and outside ``extra_allowlist``) is
    rejected by :func:`tracepilot_tooling.sandbox.safe_path`.
    """

    workspace_root: str
    settings: Settings
    timeout_s: int = 30
    max_output_bytes: int = 64_000
    extra_allowlist: list[str] = field(default_factory=list)

    @classmethod
    def for_workspace(
        cls, workspace_root: str, settings: Settings | None = None, **overrides: Any
    ) -> ToolContext:
        """Build a context from settings, applying the tool sandbox defaults.

        Pulls ``timeout_s`` / ``max_output_bytes`` / ``extra_allowlist`` from
        the shared settings unless explicitly overridden.
        """
        if settings is None:
            from tracepilot_shared.config import get_settings

            settings = get_settings()
        params: dict[str, Any] = {
            "timeout_s": settings.tool_timeout_seconds,
            "max_output_bytes": settings.tool_max_output_bytes,
            "extra_allowlist": list(settings.tool_allowlist_paths),
        }
        params.update(overrides)
        return cls(workspace_root=workspace_root, settings=settings, **params)


class Tool(ABC):
    """Base class for an allowlisted developer tool.

    Subclasses set :attr:`name` / :attr:`spec` as class attributes and implement
    :meth:`run`. Implementations should *fail soft*: catch their own expected
    errors and return a non-``ok`` :class:`ToolResult` rather than raising.
    """

    #: Stable identity, must match a member of :class:`ToolName`.
    name: ToolName
    #: Declarative spec surfaced to the planner and UI.
    spec: ToolSpec

    @abstractmethod
    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Execute the tool against ``args`` within ``ctx`` and return a result."""
        raise NotImplementedError

    # -- helpers shared by concrete tools ---------------------------------- #
    def _result(
        self,
        call_id: str,
        *,
        ok: bool,
        output: str = "",
        truncated: bool = False,
        exit_code: int | None = None,
        duration_ms: float = 0.0,
        error: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Construct a fully-populated :class:`ToolResult` for this tool."""
        return ToolResult(
            id=call_id,
            tool=self.name,
            ok=ok,
            output=output,
            truncated=truncated,
            exit_code=exit_code,
            duration_ms=round(duration_ms, 2),
            error=error,
            meta=meta or {},
        )

    @staticmethod
    def _now_ms() -> float:
        return time.perf_counter() * 1000.0


def make_call(tool: ToolName | str, args: dict[str, Any] | None = None, reason: str = "") -> ToolCall:
    """Convenience constructor for a :class:`ToolCall` with a fresh id."""
    from tracepilot_shared.ids import TOOLCALL, new_id

    return ToolCall(id=new_id(TOOLCALL), tool=ToolName(str(tool)), args=args or {}, reason=reason)

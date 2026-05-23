"""tracepilot_tooling — sandboxed, allowlisted developer tools.

Public surface (see ``docs/INTERNAL_CONTRACTS.md``)::

    from tracepilot_tooling import (
        ToolContext, execute_tool, get_tool_specs, get_registry,
    )

Every tool is confined to a single workspace via :class:`ToolContext`, runs
through the guarded :mod:`tracepilot_tooling.sandbox`, and returns a
fully-populated :class:`tracepilot_shared.models.ToolResult`. ``execute_tool`` is
the only sanctioned entry point; it never raises to the caller.
"""

from .base import Tool, ToolContext, make_call
from .registry import REGISTRY, execute_tool, get_registry, get_tool_specs
from .sandbox import SandboxError, run_subprocess, safe_path

__version__ = "0.1.0"

__all__ = [
    # contract surface
    "ToolContext",
    "execute_tool",
    "get_tool_specs",
    "get_registry",
    # extension points / helpers
    "Tool",
    "make_call",
    "REGISTRY",
    "SandboxError",
    "safe_path",
    "run_subprocess",
    "__version__",
]

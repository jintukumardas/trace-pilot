"""Tool invocation models for the sandboxed tooling layer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .common import StrEnum


class ToolName(StrEnum):
    """Allowlisted tools. The executor refuses anything not in this enum."""

    REPO_SEARCH = "repo_search"  # ripgrep / grep over the workspace
    READ_FILE = "read_file"  # bounded file reader
    DEP_TREE = "dep_tree"  # dependency / import map
    RUN_TESTS = "run_tests"  # pytest / configured test runner
    RUN_LINT = "run_lint"  # ruff / configured linter
    GIT_DIFF = "git_diff"  # git diff inspector
    STATIC_ANALYSIS = "static_analysis"  # lightweight static checks


class ToolSpec(BaseModel):
    """Declarative description of a tool, surfaced to the planner and UI."""

    name: ToolName
    description: str
    args_schema: dict[str, Any] = Field(default_factory=dict, description="JSON-schema-ish arg description")
    destructive: bool = False


class ToolCall(BaseModel):
    """A requested tool invocation produced by the action planner."""

    id: str
    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", description="Why the agent chose this tool")


class ToolResult(BaseModel):
    """Structured, telemetry-friendly result of a tool execution."""

    id: str
    tool: ToolName
    ok: bool
    output: str = ""
    truncated: bool = False
    exit_code: int | None = None
    duration_ms: float = 0.0
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

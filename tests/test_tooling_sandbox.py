"""Adversarial tests for the tooling sandbox + happy paths for the read tools.

Covers:
* ``safe_path`` blocks ``..`` traversal and absolute paths outside the root.
* the command guard rejects destructive binaries / shell metacharacters / write
  git sub-commands.
* ``read_file`` and ``repo_search`` work on the sample repo and confine to it.
* output truncation is reported for oversized reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracepilot_shared.config import get_settings
from tracepilot_shared.models import ToolName
from tracepilot_tooling import ToolContext, execute_tool, get_tool_specs
from tracepilot_tooling.base import make_call
from tracepilot_tooling.sandbox import (
    SandboxError,
    _check_command,
    safe_path,
)


@pytest.fixture()
def ctx(sample_repo: Path) -> ToolContext:
    return ToolContext.for_workspace(str(sample_repo), get_settings())


# --------------------------------------------------------------------------- #
# safe_path
# --------------------------------------------------------------------------- #
def test_safe_path_allows_in_tree(ctx: ToolContext, sample_repo: Path):
    resolved = safe_path(ctx, "config.py")
    assert resolved == (sample_repo / "config.py").resolve()
    # nested file under a subpackage is fine too
    assert safe_path(ctx, "pkg/helpers.py") == (sample_repo / "pkg/helpers.py").resolve()


def test_safe_path_blocks_dotdot_escape(ctx: ToolContext):
    with pytest.raises(SandboxError):
        safe_path(ctx, "../outside.txt")
    with pytest.raises(SandboxError):
        safe_path(ctx, "pkg/../../etc/passwd")


def test_safe_path_blocks_absolute_outside_root(ctx: ToolContext):
    with pytest.raises(SandboxError):
        safe_path(ctx, "/etc/passwd")
    with pytest.raises(SandboxError):
        safe_path(ctx, "/tmp")


def test_safe_path_blocks_symlink_escape(ctx: ToolContext, sample_repo: Path, tmp_path: Path):
    # A symlink inside the sandbox pointing outside must be rejected.
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret", encoding="utf-8")
    link = sample_repo / "escape_link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - platform without symlinks
        pytest.skip("symlinks unsupported on this platform")
    with pytest.raises(SandboxError):
        safe_path(ctx, "escape_link")


def test_safe_path_respects_extra_allowlist(sample_repo: Path, tmp_path: Path):
    extra = tmp_path / "extra_root"
    extra.mkdir()
    (extra / "ok.txt").write_text("fine", encoding="utf-8")
    ctx = ToolContext.for_workspace(str(sample_repo), get_settings(), extra_allowlist=[str(extra)])
    # Now a path under the extra allowlisted root resolves without error.
    assert safe_path(ctx, str(extra / "ok.txt")) == (extra / "ok.txt").resolve()


# --------------------------------------------------------------------------- #
# command guard
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "cmd",
    [
        ["rm", "-rf", "/"],
        ["curl", "http://evil"],
        ["wget", "http://evil"],
        ["mv", "a", "b"],
        ["chmod", "777", "x"],
        ["sudo", "rg", "x"],
        ["dd", "if=/dev/zero"],
        ["bash", "-c", "echo hi"],  # binary not on allowlist
        ["rg", "x", ";", "rm", "y"],  # shell metacharacter token
        ["rg", "pattern > out"],  # redirection embedded in an arg
        ["git", "push"],  # write git sub-command
        ["git", "commit", "-m", "x"],
        ["python", "-m", "pip", "install", "evil"],
    ],
)
def test_check_command_rejects_dangerous(cmd):
    with pytest.raises(SandboxError):
        _check_command(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        ["rg", "pattern", "."],
        ["grep", "-n", "x", "file"],
        ["git", "diff", "HEAD~1"],
        ["git", "log", "--oneline"],
        ["pytest", "-q"],
        ["ruff", "check", "."],
    ],
)
def test_check_command_allows_read_only(cmd):
    # Should not raise.
    _check_command(cmd)


def test_check_command_rejects_empty():
    with pytest.raises(SandboxError):
        _check_command([])


# --------------------------------------------------------------------------- #
# read_file happy paths + truncation
# --------------------------------------------------------------------------- #
def test_read_file_happy_path(ctx: ToolContext):
    call = make_call(ToolName.READ_FILE, {"path": "config.py"})
    result = execute_tool(call, ctx)
    assert result.ok
    assert result.id == call.id
    assert result.tool == ToolName.READ_FILE
    assert "load_settings" in result.output
    # Line-number gutter present.
    assert "\t" in result.output
    assert result.meta["total_lines"] > 0


def test_read_file_line_slice(ctx: ToolContext):
    call = make_call(ToolName.READ_FILE, {"path": "config.py", "start_line": 1, "end_line": 1})
    result = execute_tool(call, ctx)
    assert result.ok
    assert result.truncated is True  # slicing off the rest of the file flags truncation
    assert result.meta["returned_lines"][0] == 1


def test_read_file_rejects_escape(ctx: ToolContext):
    call = make_call(ToolName.READ_FILE, {"path": "../../etc/passwd"})
    result = execute_tool(call, ctx)
    assert result.ok is False
    # The registry tags a hard guardrail breach.
    assert result.meta.get("sandbox_violation") is True


def test_read_file_missing_file(ctx: ToolContext):
    call = make_call(ToolName.READ_FILE, {"path": "nope.py"})
    result = execute_tool(call, ctx)
    assert result.ok is False
    assert "not found" in (result.error or "")


def test_read_file_truncates_large_output(ctx: ToolContext, sample_repo: Path):
    # Write a big file then read with a tiny byte budget to force byte truncation.
    big = sample_repo / "big.py"
    big.write_text("x = 1\n" * 5000, encoding="utf-8")
    tiny_ctx = ToolContext.for_workspace(str(sample_repo), get_settings(), max_output_bytes=200)
    call = make_call(ToolName.READ_FILE, {"path": "big.py"})
    result = execute_tool(call, tiny_ctx)
    assert result.ok
    assert result.truncated is True
    assert result.meta["byte_truncated"] is True
    # The tool decoded at most ``max_output_bytes`` of source before rendering the
    # gutter, so the returned text is bounded to a small multiple of the budget
    # (never the full 30KB file).
    assert len(result.output) < 30_000
    assert "x = 1" in result.output


# --------------------------------------------------------------------------- #
# repo_search happy path (python-walk fallback always available)
# --------------------------------------------------------------------------- #
def test_repo_search_finds_symbol(ctx: ToolContext):
    call = make_call(ToolName.REPO_SEARCH, {"pattern": "load_settings"})
    result = execute_tool(call, ctx)
    assert result.ok
    assert "config.py" in result.output
    # Results are file:line:match and confined to the workspace (relative paths).
    assert ":" in result.output
    assert not result.output.startswith("/")
    assert result.meta["match_count"] >= 1


def test_repo_search_glob_scopes_files(ctx: ToolContext):
    call = make_call(ToolName.REPO_SEARCH, {"pattern": "Configuration", "glob": "*.md"})
    result = execute_tool(call, ctx)
    assert result.ok
    # Only README.md should match a markdown-scoped search.
    for line in result.output.splitlines():
        if line and line != "(no matches)":
            assert line.startswith("README.md")


def test_repo_search_no_matches(ctx: ToolContext):
    call = make_call(ToolName.REPO_SEARCH, {"pattern": "zzz_no_such_token_zzz"})
    result = execute_tool(call, ctx)
    assert result.ok
    assert result.output == "(no matches)" or result.meta["match_count"] == 0


def test_repo_search_rejects_path_escape(ctx: ToolContext):
    call = make_call(ToolName.REPO_SEARCH, {"pattern": "x", "path": "../.."})
    result = execute_tool(call, ctx)
    assert result.ok is False
    assert result.meta.get("sandbox_violation") is True


def test_repo_search_requires_pattern(ctx: ToolContext):
    call = make_call(ToolName.REPO_SEARCH, {})
    result = execute_tool(call, ctx)
    assert result.ok is False
    assert "pattern" in (result.error or "")


# --------------------------------------------------------------------------- #
# registry / specs
# --------------------------------------------------------------------------- #
def test_get_tool_specs_lists_all_tools():
    specs = get_tool_specs()
    names = {s.name for s in specs}
    assert names == set(ToolName)
    assert all(s.description for s in specs)


def test_registry_covers_every_tool_name():
    from tracepilot_tooling import get_registry

    registry = get_registry()
    # Every ToolName resolves to a tool whose own ``name`` matches its key.
    assert set(registry) == set(ToolName)
    for name, tool in registry.items():
        assert tool.name == name


def test_tool_failures_are_returned_not_raised(ctx: ToolContext):
    # A tool that hits an error (missing required arg) yields a failed result,
    # never an exception — execute_tool is the fail-soft boundary.
    call = make_call(ToolName.READ_FILE, {})  # no 'path'
    result = execute_tool(call, ctx)
    assert result.ok is False
    assert result.error
    assert result.tool == ToolName.READ_FILE
    assert result.id == call.id

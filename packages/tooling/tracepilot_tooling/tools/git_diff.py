"""``git_diff`` — inspect a git diff, read-only.

Args:
    base (str, optional): base ref/commit (default: working tree comparison).
    head (str, optional): head ref/commit.
    path (str, optional): limit the diff to a sub-path within the workspace.
    staged (bool, optional): show staged (``--cached``) changes.
    stat (bool, optional): show a ``--stat`` summary instead of full patch.

Uses GitPython when available (still shelling out for the diff text), falling
back to a guarded ``git diff`` subprocess. Strictly read-only: the command
allowlist blocks ``push``/``commit``/``fetch``/etc., and refs are validated to
prevent option/flag injection.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tracepilot_shared.models import ToolName, ToolResult, ToolSpec

from ..base import Tool, ToolContext
from ..sandbox import run_subprocess, safe_path

# A conservative ref pattern: refs, shas, ranges like ``a..b`` / ``a...b``.
_REF_RE = re.compile(r"^[A-Za-z0-9_./~^@-]+(\.{2,3}[A-Za-z0-9_./~^@-]+)?$")


class GitDiffTool(Tool):
    name = ToolName.GIT_DIFF
    spec = ToolSpec(
        name=ToolName.GIT_DIFF,
        description=(
            "Show a read-only git diff between refs (or the working tree) for the "
            "workspace, optionally scoped to a path. Never pushes/commits."
        ),
        args_schema={
            "base": {"type": "string", "required": False, "description": "base ref/commit"},
            "head": {"type": "string", "required": False, "description": "head ref/commit"},
            "path": {"type": "string", "required": False, "description": "limit diff to a sub-path"},
            "staged": {"type": "boolean", "required": False, "description": "show staged changes"},
            "stat": {
                "type": "boolean",
                "required": False,
                "description": "summary stat instead of full patch",
            },
        },
        destructive=False,
    )

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        t0 = self._now_ms()
        call_id = args.get("__id", "")
        ws_root = Path(ctx.workspace_root).resolve()

        if not (ws_root / ".git").exists():
            return self._result(
                call_id,
                ok=False,
                error="workspace is not a git repository",
                duration_ms=self._now_ms() - t0,
                meta={"is_git": False},
            )

        base = (args.get("base") or "").strip()
        head = (args.get("head") or "").strip()
        sub = (args.get("path") or "").strip()
        staged = bool(args.get("staged", False))
        stat = bool(args.get("stat", False))

        for label, ref in (("base", base), ("head", head)):
            if ref and not _REF_RE.match(ref):
                return self._result(
                    call_id, ok=False, error=f"invalid {label} ref: {ref!r}", duration_ms=self._now_ms() - t0
                )

        cmd = ["git", "-C", str(ws_root), "--no-pager", "diff", "--no-color"]
        if stat:
            cmd.append("--stat")
        if staged:
            cmd.append("--cached")
        if base and head:
            cmd.append(f"{base}..{head}")
        elif base:
            cmd.append(base)

        pathspec = None
        if sub:
            resolved = safe_path(ctx, sub)  # raises SandboxError on escape
            pathspec = _rel(resolved, ws_root)
            cmd += ["--", pathspec]

        ok, output, exit_code, truncated, _dur = run_subprocess(cmd, ctx, cwd=str(ws_root))
        if exit_code not in (0, 1):  # git diff returns 0 normally; 1 with --exit-code
            return self._result(
                call_id,
                ok=False,
                output=output,
                truncated=truncated,
                exit_code=exit_code,
                error=f"git diff failed (exit {exit_code})",
                duration_ms=self._now_ms() - t0,
                meta={"engine": _engine_name(), "base": base or None, "head": head or None},
            )

        stats = _diff_stats(output)
        body = output.strip()
        return self._result(
            call_id,
            ok=True,
            output=body if body else "(no differences)",
            truncated=truncated,
            exit_code=exit_code,
            duration_ms=self._now_ms() - t0,
            meta={
                "engine": _engine_name(),
                "base": base or None,
                "head": head or None,
                "staged": staged,
                "path": pathspec,
                "files_changed": stats["files"],
                "insertions": stats["insertions"],
                "deletions": stats["deletions"],
                "empty": not body,
            },
        )


def _diff_stats(diff: str) -> dict[str, int]:
    """Count changed files and +/- lines from a unified diff body."""
    files = len(re.findall(r"(?m)^diff --git ", diff))
    insertions = len(re.findall(r"(?m)^\+(?!\+\+)", diff))
    deletions = len(re.findall(r"(?m)^-(?!--)", diff))
    return {"files": files, "insertions": insertions, "deletions": deletions}


def _engine_name() -> str:
    try:
        import git  # noqa: F401  (GitPython)

        return "gitpython+subprocess"
    except Exception:  # noqa: BLE001
        return "git-subprocess"


def _rel(path: Path, ws_root: Path) -> str:
    try:
        return str(path.relative_to(ws_root))
    except ValueError:
        return str(path)

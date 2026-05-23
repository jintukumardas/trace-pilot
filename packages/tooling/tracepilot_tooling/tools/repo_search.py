"""``repo_search`` — fast code search via ripgrep, with a pure-Python fallback.

Args:
    pattern (str, required): regex/literal to search for.
    path (str, optional): sub-path within the workspace to scope the search.
    glob (str, optional): file glob to include (e.g. ``*.py``).
    max_results (int, optional): cap on emitted ``file:line:match`` lines.
    fixed (bool, optional): treat ``pattern`` as a literal string, not regex.

Returns ``file:line:match`` lines (paths relative to the workspace root).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from tracepilot_shared.models import ToolName, ToolResult, ToolSpec

from ..base import Tool, ToolContext
from ..sandbox import SandboxError, run_subprocess, safe_path

_DEFAULT_MAX = 100
_HARD_MAX = 1000
# Directories never worth searching; mirrors the ingestion excludes.
_SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    ".next",
    "__pycache__",
    ".venv",
    "venv",
    "vendor",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
_MAX_FILE_BYTES = 2_000_000


class RepoSearchTool(Tool):
    name = ToolName.REPO_SEARCH
    spec = ToolSpec(
        name=ToolName.REPO_SEARCH,
        description=(
            "Search the workspace for a regex/literal pattern (ripgrep, with a "
            "Python fallback). Returns file:line:match lines."
        ),
        args_schema={
            "pattern": {"type": "string", "required": True, "description": "regex or literal to find"},
            "path": {"type": "string", "required": False, "description": "sub-path to scope the search"},
            "glob": {
                "type": "string",
                "required": False,
                "description": "file glob include filter, e.g. *.py",
            },
            "max_results": {"type": "integer", "required": False, "default": _DEFAULT_MAX},
            "fixed": {"type": "boolean", "required": False, "description": "treat pattern as a literal"},
        },
        destructive=False,
    )

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        t0 = self._now_ms()
        pattern = (args.get("pattern") or "").strip()
        if not pattern:
            return self._result(
                args.get("__id", ""), ok=False, error="'pattern' is required", duration_ms=self._now_ms() - t0
            )

        call_id = args.get("__id", "")
        sub = args.get("path") or "."
        glob = args.get("glob")
        fixed = bool(args.get("fixed", False))
        try:
            max_results = int(args.get("max_results") or _DEFAULT_MAX)
        except (TypeError, ValueError):
            max_results = _DEFAULT_MAX
        max_results = max(1, min(max_results, _HARD_MAX))

        try:
            search_root = safe_path(ctx, sub)
        except SandboxError as exc:
            # Re-raise so the registry can flag a guardrail breach.
            raise exc
        if not search_root.exists():
            return self._result(
                call_id, ok=False, error=f"path not found: {sub}", duration_ms=self._now_ms() - t0
            )

        # Prefer ripgrep; fall back to a Python walk if rg is absent or errors.
        ok, output, exit_code, truncated, engine = self._ripgrep(
            pattern, search_root, glob, fixed, max_results, ctx
        )
        if engine == "ripgrep-unavailable":
            output, truncated, _count = self._python_search(
                pattern, search_root, glob, fixed, max_results, ctx
            )
            ok = True
            exit_code = 0
            engine = "python-walk"

        match_count = len([ln for ln in output.splitlines() if ln.strip()])
        scope = (
            str(search_root.relative_to(Path(ctx.workspace_root).resolve()))
            if _safe_rel(search_root, ctx)
            else str(search_root)
        )

        return self._result(
            call_id,
            ok=ok,
            output=output if output else "(no matches)",
            truncated=truncated,
            exit_code=exit_code,
            duration_ms=self._now_ms() - t0,
            meta={
                "engine": engine,
                "pattern": pattern,
                "match_count": match_count,
                "scope": scope,
            },
        )

    # -- ripgrep path ------------------------------------------------------ #
    def _ripgrep(self, pattern, root, glob, fixed, max_results, ctx):
        cmd = [
            "rg",
            "--line-number",
            "--no-heading",
            "--color",
            "never",
            "--max-count",
            str(max_results),
            "--max-columns",
            "400",
        ]
        if fixed:
            cmd.append("--fixed-strings")
        for skip in _SKIP_DIRS:
            cmd += ["--glob", f"!{skip}/**"]
        if glob:
            cmd += ["--glob", glob]
        cmd += ["--", pattern, str(root)]
        try:
            ok, output, exit_code, truncated, _dur = run_subprocess(cmd, ctx, cwd=ctx.workspace_root)
        except SandboxError:
            raise
        # rg exit 127/-1 → binary missing → signal fallback.
        if exit_code in (127, -1) and ("not found" in output or not output):
            return False, "", exit_code, False, "ripgrep-unavailable"
        # rg exits 1 when there are simply no matches — that's a *successful* run.
        normalized = self._relativize(output, ctx)
        success = exit_code in (0, 1)
        return success, normalized, exit_code, truncated, "ripgrep"

    def _relativize(self, output: str, ctx: ToolContext) -> str:
        """Rewrite absolute file paths in rg output to workspace-relative."""
        root = str(Path(ctx.workspace_root).resolve())
        lines = []
        for line in output.splitlines():
            if line.startswith(root + "/"):
                line = line[len(root) + 1 :]
            elif line.startswith(root):
                line = line[len(root) :].lstrip("/")
            lines.append(line)
        return "\n".join(lines)

    # -- python fallback --------------------------------------------------- #
    def _python_search(self, pattern, root, glob, fixed, max_results, ctx):
        try:
            regex = re.compile(re.escape(pattern) if fixed else pattern)
        except re.error:
            # Bad regex → degrade to literal substring matching.
            regex = re.compile(re.escape(pattern))
        results: list[str] = []
        truncated = False
        ws_root = Path(ctx.workspace_root).resolve()
        for file in _iter_files(root, glob):
            if len(results) >= max_results:
                truncated = True
                break
            try:
                if file.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            try:
                rel = str(file.relative_to(ws_root))
            except ValueError:
                rel = str(file)
            try:
                with file.open("r", encoding="utf-8", errors="replace") as fh:
                    for lineno, text in enumerate(fh, start=1):
                        if regex.search(text):
                            snippet = text.rstrip("\n")[:400]
                            results.append(f"{rel}:{lineno}:{snippet}")
                            if len(results) >= max_results:
                                truncated = True
                                break
            except OSError:
                continue
        return "\n".join(results), truncated, len(results)


def _iter_files(root: Path, glob: str | None):
    """Yield files under ``root`` honoring skip dirs and an optional glob."""
    if root.is_file():
        if not glob or fnmatch.fnmatch(root.name, glob):
            yield root
        return
    for dirpath, dirnames, filenames in _os_walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".git")]
        for fname in filenames:
            if glob and not fnmatch.fnmatch(fname, glob):
                continue
            yield Path(dirpath) / fname


def _os_walk(root: Path):
    import os

    yield from os.walk(root)


def _safe_rel(path: Path, ctx: ToolContext) -> bool:
    try:
        path.relative_to(Path(ctx.workspace_root).resolve())
        return True
    except ValueError:
        return False

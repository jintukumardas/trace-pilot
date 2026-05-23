"""``run_lint`` — run ``ruff check`` over a path and return a structured summary.

Args:
    path (str, optional): sub-path to lint (default: workspace root).
    select (str, optional): comma-separated ruff rule codes to select.
    fix (bool, ignored): never honored — the sandbox is read-only.

Returns a per-rule violation summary plus the (truncated) JSON detail. ``ruff``
is preferred; if unavailable the tool fails soft with an explanatory result.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from tracepilot_shared.models import ToolName, ToolResult, ToolSpec

from ..base import Tool, ToolContext
from ..sandbox import run_subprocess, safe_path

_MAX_DETAIL = 60  # max individual findings rendered into output


class RunLintTool(Tool):
    name = ToolName.RUN_LINT
    spec = ToolSpec(
        name=ToolName.RUN_LINT,
        description=(
            "Run 'ruff check' over a workspace path and return a structured "
            "violation summary (read-only; never applies fixes)."
        ),
        args_schema={
            "path": {"type": "string", "required": False, "description": "sub-path to lint"},
            "select": {"type": "string", "required": False, "description": "comma-separated ruff rule codes"},
        },
        destructive=False,
    )

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        t0 = self._now_ms()
        call_id = args.get("__id", "")
        ws_root = Path(ctx.workspace_root).resolve()
        sub = (args.get("path") or ".").strip() or "."
        select = (args.get("select") or "").strip()

        # Validate the path first so a sandbox escape is rejected regardless of
        # whether ruff happens to be installed.
        target = safe_path(ctx, sub)  # raises SandboxError on escape
        if not target.exists():
            return self._result(
                call_id, ok=False, error=f"path not found: {sub}", duration_ms=self._now_ms() - t0
            )
        if select and any(ch in select for ch in ";|&`$><"):
            return self._result(
                call_id, ok=False, error="invalid 'select' value", duration_ms=self._now_ms() - t0
            )

        if not shutil.which("ruff"):
            return self._result(
                call_id,
                ok=False,
                error="ruff is not available in this environment",
                duration_ms=self._now_ms() - t0,
                meta={"available": False},
            )

        cmd = ["ruff", "check", "--output-format", "json", "--no-cache"]
        if select:
            cmd += ["--select", select]
        cmd.append(str(target))

        ok, output, exit_code, truncated, _dur = run_subprocess(cmd, ctx, cwd=str(ws_root))

        findings, parse_warning = _parse_findings(output)
        rel_target = _rel(target, ws_root)
        rendered = _render(findings, rel_target, parse_warning)
        clamped, was_clamped = _clamp(rendered, ctx.max_output_bytes)

        # ruff exit 0 = clean, 1 = lint violations found (a successful *run*).
        run_succeeded = exit_code in (0, 1)
        return self._result(
            call_id,
            ok=run_succeeded,
            output=clamped,
            truncated=truncated or was_clamped,
            exit_code=exit_code,
            duration_ms=self._now_ms() - t0,
            error=None if run_succeeded else f"ruff failed (exit {exit_code})",
            meta={
                "path": rel_target,
                "violation_count": len(findings),
                "by_rule": dict(Counter(f.get("code") or "?" for f in findings).most_common(20)),
                "clean": run_succeeded and len(findings) == 0,
            },
        )


def _parse_findings(output: str) -> tuple[list[dict], str | None]:
    """Parse ruff JSON output; degrade to empty list with a warning on failure."""
    text = output.strip()
    # The subprocess may append a trailing truncation marker; isolate the JSON.
    if not text:
        return [], None
    # ruff JSON is a top-level array; find the first '['.
    start = text.find("[")
    if start == -1:
        return [], None
    candidate = text[start:]
    for end in (candidate.rfind("]") + 1, len(candidate)):
        if end <= 0:
            continue
        try:
            data = json.loads(candidate[:end])
            if isinstance(data, list):
                return data, None
        except json.JSONDecodeError:
            continue
    return [], "could not parse ruff JSON (output may be truncated)"


def _render(findings: list[dict], target: str, parse_warning: str | None) -> str:
    lines = [f"# ruff check: {target}", ""]
    if parse_warning:
        lines.append(f"warning: {parse_warning}")
        lines.append("")
    if not findings:
        lines.append("No lint violations found.")
        return "\n".join(lines)

    by_rule = Counter(f.get("code") or "?" for f in findings)
    lines.append(f"{len(findings)} violation(s) across {len(by_rule)} rule(s):")
    for code, count in by_rule.most_common(25):
        lines.append(f"  {code}: {count}")
    lines.append("")
    lines.append("Details:")
    for f in findings[:_MAX_DETAIL]:
        loc = f.get("location") or {}
        row = loc.get("row", "?")
        col = loc.get("column", "?")
        path = f.get("filename") or "?"
        code = f.get("code") or "?"
        msg = (f.get("message") or "").strip()
        lines.append(f"  {path}:{row}:{col}: {code} {msg}")
    if len(findings) > _MAX_DETAIL:
        lines.append(f"  ... and {len(findings) - _MAX_DETAIL} more")
    return "\n".join(lines)


def _rel(path: Path, ws_root: Path) -> str:
    try:
        return str(path.relative_to(ws_root))
    except ValueError:
        return str(path)


def _clamp(text: str, limit: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if limit > 0 and len(encoded) > limit:
        return encoded[:limit].decode("utf-8", errors="replace") + "\n... [truncated]", True
    return text, False

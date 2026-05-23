"""``read_file`` — bounded, sandbox-confined file reader.

Args:
    path (str, required): file path within the workspace.
    start_line (int, optional, 1-based): first line to return.
    end_line (int, optional, 1-based, inclusive): last line to return.
    max_bytes (int, optional): hard cap on bytes read (defaults to ctx budget).

Returns the requested slice with 1-based line-number gutters so the agent can
cite exact lines. Never reads outside the sandbox and never reads binary blobs
or oversized files in full.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tracepilot_shared.models import ToolName, ToolResult, ToolSpec

from ..base import Tool, ToolContext
from ..sandbox import safe_path

_DEFAULT_MAX_BYTES = 64_000
_LINE_NUMBER_WIDTH = 6


class ReadFileTool(Tool):
    name = ToolName.READ_FILE
    spec = ToolSpec(
        name=ToolName.READ_FILE,
        description=(
            "Read a bounded slice of a workspace file with line numbers. "
            "Confined to the sandbox; refuses binary or oversized files."
        ),
        args_schema={
            "path": {"type": "string", "required": True, "description": "file path within the workspace"},
            "start_line": {"type": "integer", "required": False, "description": "1-based first line"},
            "end_line": {"type": "integer", "required": False, "description": "1-based inclusive last line"},
            "max_bytes": {"type": "integer", "required": False, "default": _DEFAULT_MAX_BYTES},
        },
        destructive=False,
    )

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        t0 = self._now_ms()
        call_id = args.get("__id", "")
        raw_path = (args.get("path") or "").strip()
        if not raw_path:
            return self._result(
                call_id, ok=False, error="'path' is required", duration_ms=self._now_ms() - t0
            )

        # safe_path raises SandboxError on escape; the registry catches it.
        resolved = safe_path(ctx, raw_path)
        if not resolved.exists():
            return self._result(
                call_id, ok=False, error=f"file not found: {raw_path}", duration_ms=self._now_ms() - t0
            )
        if resolved.is_dir():
            return self._result(
                call_id, ok=False, error=f"path is a directory: {raw_path}", duration_ms=self._now_ms() - t0
            )

        max_bytes = _coerce_int(args.get("max_bytes"), ctx.max_output_bytes or _DEFAULT_MAX_BYTES)
        max_bytes = max(1, min(max_bytes, ctx.max_output_bytes or _DEFAULT_MAX_BYTES))
        start_line = _coerce_int(args.get("start_line"), None)
        end_line = _coerce_int(args.get("end_line"), None)

        try:
            size = resolved.stat().st_size
        except OSError as exc:
            return self._result(
                call_id, ok=False, error=f"stat failed: {exc}", duration_ms=self._now_ms() - t0
            )

        try:
            data = resolved.read_bytes()[: max_bytes + 1]
        except OSError as exc:
            return self._result(
                call_id, ok=False, error=f"read failed: {exc}", duration_ms=self._now_ms() - t0
            )

        if _looks_binary(data):
            return self._result(
                call_id,
                ok=False,
                error="refusing to read binary file",
                duration_ms=self._now_ms() - t0,
                meta={"path": _rel(resolved, ctx), "bytes": size, "binary": True},
            )

        byte_truncated = len(data) > max_bytes
        text = data[:max_bytes].decode("utf-8", errors="replace")
        lines = text.splitlines()

        rel = _rel(resolved, ctx)
        sliced, line_truncated, lo, hi = _slice_lines(lines, start_line, end_line)
        rendered = _render(sliced, start_at=lo)

        truncated = byte_truncated or line_truncated
        return self._result(
            call_id,
            ok=True,
            output=rendered if rendered else "(empty file)",
            truncated=truncated,
            exit_code=0,
            duration_ms=self._now_ms() - t0,
            meta={
                "path": rel,
                "bytes": size,
                "total_lines": len(lines),
                "returned_lines": [lo, hi] if sliced else [0, 0],
                "byte_truncated": byte_truncated,
            },
        )


def _slice_lines(lines: list[str], start: int | None, end: int | None):
    """Return ``(slice, truncated, first_lineno, last_lineno)`` (1-based)."""
    total = len(lines)
    if total == 0:
        return [], False, 0, 0
    lo = 1 if start is None else max(1, start)
    hi = total if end is None else min(total, end)
    if hi < lo:
        hi = lo
    sliced = lines[lo - 1 : hi]
    truncated = (start is not None and start > 1) or (end is not None and end < total)
    return sliced, truncated, lo, lo + len(sliced) - 1


def _render(lines: list[str], start_at: int) -> str:
    """Render lines with a fixed-width 1-based line-number gutter."""
    out = []
    for offset, line in enumerate(lines):
        num = str(start_at + offset).rjust(_LINE_NUMBER_WIDTH)
        out.append(f"{num}\t{line}")
    return "\n".join(out)


def _looks_binary(data: bytes) -> bool:
    """Heuristic binary detection: NUL byte or excessive non-text bytes."""
    if b"\x00" in data:
        return True
    if not data:
        return False
    sample = data[:4096]
    text_chars = bytes(range(0x20, 0x7F)) + b"\n\r\t\f\b"
    non_text = sum(byte not in text_chars for byte in sample)
    return non_text / len(sample) > 0.30


def _coerce_int(value: Any, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rel(path: Path, ctx: ToolContext) -> str:
    try:
        return str(path.relative_to(Path(ctx.workspace_root).resolve()))
    except ValueError:
        return str(path)

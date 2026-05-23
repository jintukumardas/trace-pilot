"""``static_analysis`` — lightweight, dependency-free heuristics over Python.

Checks, per file:
  * syntax errors (via ``ast``);
  * bare ``except:`` clauses;
  * ``TODO`` / ``FIXME`` / ``XXX`` / ``HACK`` markers;
  * over-long functions (by line count);
  * package directories missing ``__init__.py`` that nonetheless contain modules;
  * ``print()`` calls (low-signal, reported as info).

Args:
    path (str, optional): sub-path to analyze (default: workspace root).
    max_function_lines (int, optional): threshold for "long function" (default 60).
    max_files (int, optional): cap on files analyzed.

Read-only and sandbox-confined. Returns a grouped findings report.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from tracepilot_shared.models import ToolName, ToolResult, ToolSpec

from ..base import Tool, ToolContext
from ..sandbox import safe_path

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
_MARKER_RE = re.compile(r"#.*\b(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)
_DEFAULT_MAX_FN_LINES = 60
_DEFAULT_MAX_FILES = 600
_MAX_FILE_BYTES = 1_000_000
_MAX_FINDINGS_RENDERED = 200


class StaticAnalysisTool(Tool):
    name = ToolName.STATIC_ANALYSIS
    spec = ToolSpec(
        name=ToolName.STATIC_ANALYSIS,
        description=(
            "Lightweight Python heuristics: syntax errors, bare excepts, "
            "TODO/FIXME markers, long functions, missing __init__.py."
        ),
        args_schema={
            "path": {"type": "string", "required": False, "description": "sub-path to analyze"},
            "max_function_lines": {"type": "integer", "required": False, "default": _DEFAULT_MAX_FN_LINES},
            "max_files": {"type": "integer", "required": False, "default": _DEFAULT_MAX_FILES},
        },
        destructive=False,
    )

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        t0 = self._now_ms()
        call_id = args.get("__id", "")
        ws_root = Path(ctx.workspace_root).resolve()
        sub = (args.get("path") or ".").strip() or "."
        max_fn = _coerce_int(args.get("max_function_lines"), _DEFAULT_MAX_FN_LINES)
        max_fn = max(10, min(max_fn, 1000))
        max_files = _coerce_int(args.get("max_files"), _DEFAULT_MAX_FILES)
        max_files = max(1, min(max_files, 5000))

        root = safe_path(ctx, sub)  # raises SandboxError on escape
        if not root.exists():
            return self._result(
                call_id, ok=False, error=f"path not found: {sub}", duration_ms=self._now_ms() - t0
            )

        findings: list[dict[str, Any]] = []
        scanned = 0
        truncated = False

        files = [root] if root.is_file() else self._walk(root)
        pkg_dirs_with_modules: dict[Path, bool] = {}

        for file in files:
            if scanned >= max_files:
                truncated = True
                break
            if file.suffix.lower() not in {".py", ".pyi"}:
                continue
            try:
                if file.stat().st_size > _MAX_FILE_BYTES:
                    continue
                source = file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(_finding("read-error", file, ws_root, 0, str(exc)))
                continue

            rel = _rel(file, ws_root)
            self._analyze_file(source, file, ws_root, rel, max_fn, findings)
            # Track package-dir completeness for missing __init__ detection.
            parent = file.parent
            pkg_dirs_with_modules.setdefault(parent, True)
            scanned += 1

        # Missing __init__.py: any dir holding .py modules but no __init__.py,
        # excluding the top-level root itself (often a flat scripts dir).
        for pkg_dir in pkg_dirs_with_modules:
            if pkg_dir == ws_root or pkg_dir == root:
                continue
            if not (pkg_dir / "__init__.py").exists():
                # Only flag if a sibling/parent is a package (heuristic).
                if (pkg_dir.parent / "__init__.py").exists():
                    findings.append(
                        _finding(
                            "missing-init",
                            pkg_dir,
                            ws_root,
                            0,
                            "directory contains modules but no __init__.py",
                        )
                    )

        output = _render(findings, scanned)
        clamped, was_clamped = _clamp(output, ctx.max_output_bytes)
        summary = _summarize(findings)
        return self._result(
            call_id,
            ok=True,
            output=clamped,
            truncated=truncated or was_clamped,
            exit_code=0,
            duration_ms=self._now_ms() - t0,
            meta={
                "files_analyzed": scanned,
                "finding_count": len(findings),
                "by_kind": summary,
                "clean": len(findings) == 0,
            },
        )

    def _walk(self, root: Path):
        import os

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fname in sorted(filenames):
                yield Path(dirpath) / fname

    def _analyze_file(self, source, file, ws_root, rel, max_fn, findings):
        # Marker scan is line-based and works even when the file won't parse.
        for lineno, line in enumerate(source.splitlines(), start=1):
            m = _MARKER_RE.search(line)
            if m:
                findings.append(
                    _finding(f"marker:{m.group(1).upper()}", file, ws_root, lineno, line.strip()[:160])
                )

        try:
            tree = ast.parse(source, filename=str(file))
        except SyntaxError as exc:
            findings.append(
                _finding(
                    "syntax-error",
                    file,
                    ws_root,
                    exc.lineno or 0,
                    f"{exc.msg}",
                )
            )
            return

        for node in ast.walk(tree):
            # Bare except.
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                findings.append(
                    _finding(
                        "bare-except",
                        file,
                        ws_root,
                        node.lineno,
                        "bare 'except:' catches everything (including KeyboardInterrupt)",
                    )
                )
            # Long functions.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", None)
                if end is not None:
                    length = end - node.lineno + 1
                    if length > max_fn:
                        findings.append(
                            _finding(
                                "long-function",
                                file,
                                ws_root,
                                node.lineno,
                                f"function '{node.name}' is {length} lines (> {max_fn})",
                            )
                        )
            # print() calls.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                findings.append(
                    _finding(
                        "print-call",
                        file,
                        ws_root,
                        node.lineno,
                        "print() call (prefer the logging module in library code)",
                    )
                )


def _finding(kind: str, path: Path, ws_root: Path, line: int, message: str) -> dict[str, Any]:
    return {"kind": kind, "path": _rel(path, ws_root), "line": line, "message": message}


def _summarize(findings: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        key = f["kind"].split(":")[0]
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _render(findings: list[dict], scanned: int) -> str:
    lines = [f"# static analysis ({scanned} file(s) analyzed)", ""]
    if not findings:
        lines.append("No issues found.")
        return "\n".join(lines)

    summary = _summarize(findings)
    lines.append("## Summary")
    for kind, count in summary.items():
        lines.append(f"  {kind}: {count}")
    lines.append("")
    lines.append("## Findings")
    # Sort by severity-ish ordering then path/line for stable, useful output.
    order = {
        "syntax-error": 0,
        "bare-except": 1,
        "missing-init": 2,
        "long-function": 3,
        "marker": 4,
        "print-call": 5,
        "read-error": 6,
    }
    findings_sorted = sorted(
        findings,
        key=lambda f: (order.get(f["kind"].split(":")[0], 9), f["path"], f["line"]),
    )
    for f in findings_sorted[:_MAX_FINDINGS_RENDERED]:
        lines.append(f"  {f['path']}:{f['line']}: [{f['kind']}] {f['message']}")
    if len(findings_sorted) > _MAX_FINDINGS_RENDERED:
        lines.append(f"  ... and {len(findings_sorted) - _MAX_FINDINGS_RENDERED} more")
    return "\n".join(lines)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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

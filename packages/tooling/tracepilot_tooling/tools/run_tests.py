"""``run_tests`` — run pytest in a sandboxed subprocess and summarize results.

Args:
    target (str, optional): module path or test path to run (within workspace).
    k (str, optional): pytest ``-k`` expression to filter tests.
    maxfail (int, optional): stop after N failures (default 5).

Collection is *always* confined to the workspace root: ``target`` is validated
via ``safe_path`` and pytest is invoked with ``--rootdir`` pinned to the
workspace so it can never collect tests outside the sandbox.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from tracepilot_shared.models import ToolName, ToolResult, ToolSpec

from ..base import Tool, ToolContext
from ..sandbox import run_subprocess, safe_path

# Parse the pytest summary line, e.g. "= 3 failed, 12 passed, 1 skipped in 4.2s ="
_SUMMARY_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<kind>passed|failed|error|errors|skipped|xfailed|xpassed|deselected|warnings?)"
)
_NO_TESTS_RE = re.compile(r"no tests ran", re.IGNORECASE)


class RunTestsTool(Tool):
    name = ToolName.RUN_TESTS
    spec = ToolSpec(
        name=ToolName.RUN_TESTS,
        description=(
            "Run pytest against a path/module inside the workspace and return a "
            "pass/fail summary. Collection is confined to the sandbox."
        ),
        args_schema={
            "target": {
                "type": "string",
                "required": False,
                "description": "test path or module within workspace",
            },
            "k": {"type": "string", "required": False, "description": "pytest -k filter expression"},
            "maxfail": {"type": "integer", "required": False, "default": 5},
        },
        destructive=False,
    )

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        t0 = self._now_ms()
        call_id = args.get("__id", "")
        ws_root = Path(ctx.workspace_root).resolve()

        target = (args.get("target") or "").strip()
        k_expr = (args.get("k") or "").strip()
        maxfail = _coerce_int(args.get("maxfail"), 5)
        maxfail = max(0, min(maxfail, 100))

        # Validate the target *first* so a sandbox-escaping path is rejected even
        # when no runner is installed (the guardrail must not be bypassable).
        resolved_target: Path | None = None
        if target:
            resolved_target = safe_path(ctx, target)  # raises SandboxError on escape
            if not resolved_target.exists():
                return self._result(
                    call_id, ok=False, error=f"target not found: {target}", duration_ms=self._now_ms() - t0
                )
        if k_expr and any(ch in k_expr for ch in ";|&`$><"):
            return self._result(
                call_id, ok=False, error="invalid -k expression", duration_ms=self._now_ms() - t0
            )

        # Confirm pytest is available; degrade gracefully if not.
        runner = self._select_runner()
        if runner is None:
            return self._result(
                call_id,
                ok=False,
                error="pytest is not available in this environment",
                duration_ms=self._now_ms() - t0,
                meta={"available": False},
            )

        cmd = list(runner)
        cmd += ["--rootdir", str(ws_root), "-q", "--color", "no", "-p", "no:cacheprovider"]
        if maxfail > 0:
            cmd += ["--maxfail", str(maxfail)]
        if k_expr:
            cmd += ["-k", k_expr]
        cmd.append(str(resolved_target) if resolved_target is not None else str(ws_root))

        ok, output, exit_code, truncated, _dur = run_subprocess(cmd, ctx, cwd=str(ws_root))
        summary = _parse_summary(output, exit_code)

        return self._result(
            call_id,
            # pytest exit 0 = all passed, 5 = no tests collected (treat as soft-ok).
            ok=exit_code == 0,
            output=output if output.strip() else "(no output)",
            truncated=truncated,
            exit_code=exit_code,
            duration_ms=self._now_ms() - t0,
            meta={
                "runner": " ".join(runner),
                "target": target or ".",
                "k": k_expr or None,
                **summary,
            },
        )

    @staticmethod
    def _select_runner() -> list[str] | None:
        """Prefer the ``pytest`` binary, else ``python -m pytest`` if importable."""
        if shutil.which("pytest"):
            return ["pytest"]
        # Fall back to module invocation under an allowlisted python.
        for py in ("python", "python3"):
            if shutil.which(py):
                if _pytest_importable(py):
                    return [py, "-m", "pytest"]
        return None


def _pytest_importable(py: str) -> bool:
    import subprocess

    try:
        res = subprocess.run(  # noqa: S603 - fixed, trusted args
            [py, "-c", "import pytest"],
            capture_output=True,
            timeout=10,
            shell=False,
            check=False,
        )
        return res.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _parse_summary(output: str, exit_code: int) -> dict[str, Any]:
    """Extract pass/fail counts and a pass-bool from pytest's terminal output."""
    counts: dict[str, int] = {}
    # Look at the last few non-empty lines where the summary lives.
    tail = "\n".join([ln for ln in output.splitlines() if ln.strip()][-6:])
    for m in _SUMMARY_RE.finditer(tail):
        kind = m.group("kind").rstrip("s") if m.group("kind").startswith("error") else m.group("kind")
        kind = "error" if kind.startswith("error") else kind
        counts[kind] = counts.get(kind, 0) + int(m.group("count"))
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0)
    errors = counts.get("error", 0)
    skipped = counts.get("skipped", 0)
    total = passed + failed + errors + skipped
    no_tests = exit_code == 5 or _NO_TESTS_RE.search(output) is not None
    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "total": total,
        "all_passed": exit_code == 0 and (failed == 0 and errors == 0),
        "no_tests_collected": no_tests,
    }


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

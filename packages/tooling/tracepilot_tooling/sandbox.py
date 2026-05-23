"""Sandboxing primitives: path confinement and guarded subprocess execution.

This module is the *only* place tools are allowed to touch the filesystem (via
:func:`safe_path`) or spawn a process (via :func:`run_subprocess`). The
guardrails here are real and adversarially tested:

* ``safe_path`` resolves a (possibly relative) path and refuses anything that
  escapes ``ctx.workspace_root`` or an entry of ``ctx.extra_allowlist`` —
  including ``..`` traversal, absolute paths, and symlinks that point outside
  the allowed roots.
* ``run_subprocess`` only allows a fixed set of known-read-only binaries, blocks
  a denylist of destructive tokens, enforces a wall-clock timeout, captures and
  truncates output to ``ctx.max_output_bytes``, and runs with a sanitized
  environment, no shell, and ``cwd`` confined to the workspace.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

from tracepilot_shared.logging import get_logger

from .base import ToolContext

log = get_logger("tooling.sandbox")


class SandboxError(Exception):
    """Raised when a path or command would breach the sandbox boundary."""


# Binaries the executor is permitted to spawn. Everything is read-only or
# read-only-by-argument (we additionally block write/network sub-commands of
# git below). The key is the program basename as it appears in ``cmd[0]``.
ALLOWED_BINARIES: frozenset[str] = frozenset({"rg", "grep", "git", "pytest", "ruff", "python", "python3"})

# Tokens that must never appear anywhere in an argument vector. These cover
# destructive filesystem ops, privilege/permission changes, network fetches,
# and shell redirection/expansion that could smuggle in side effects.
DENY_TOKENS: tuple[str, ...] = (
    "rm",
    "rmdir",
    "mv",
    "dd",
    "curl",
    "wget",
    "chmod",
    "chown",
    "chgrp",
    "mkfs",
    "kill",
    "killall",
    "shutdown",
    "reboot",
    "sudo",
    "su",
    "eval",
    "exec",
    "source",
    ":>",
    ">",
    ">>",
    "<",
    "|",
    "&",
    ";",
    "$(",
    "`",
    "&&",
    "||",
)

# Sub-commands of otherwise-allowed binaries that mutate state or hit the
# network. ``git`` is allowed for diffs/logs but must stay read-only and local.
DENY_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "push",
        "commit",
        "merge",
        "rebase",
        "reset",
        "clean",
        "checkout",
        "switch",
        "pull",
        "fetch",
        "clone",
        "remote",
        "gc",
        "rm",
        "mv",
        "apply",
        "am",
        "cherry-pick",
        "revert",
        "tag",
        "branch",
        "stash",
        "config",
    }
)

# Network-capable pip operations are blocked; we never install at tool time.
DENY_PIP_TOKENS: frozenset[str] = frozenset({"install", "uninstall", "download"})


# --------------------------------------------------------------------------- #
# Path confinement
# --------------------------------------------------------------------------- #
def _allowed_roots(ctx: ToolContext) -> list[Path]:
    """Return the resolved set of roots a path is permitted to live under."""
    roots: list[Path] = []
    try:
        roots.append(Path(ctx.workspace_root).resolve())
    except (OSError, RuntimeError):  # pragma: no cover - defensive
        roots.append(Path(ctx.workspace_root))
    for extra in ctx.extra_allowlist:
        if not extra:
            continue
        try:
            roots.append(Path(extra).expanduser().resolve())
        except (OSError, RuntimeError):  # pragma: no cover - defensive
            roots.append(Path(extra))
    return roots


def _is_within(child: Path, root: Path) -> bool:
    """True if ``child`` is ``root`` or a descendant of it (already resolved)."""
    try:
        child.relative_to(root)
        return True
    except ValueError:
        return False


def safe_path(ctx: ToolContext, rel: str | os.PathLike[str]) -> Path:
    """Resolve ``rel`` and confirm it stays inside an allowed root.

    ``rel`` may be relative (resolved against ``ctx.workspace_root``) or
    absolute. The final, symlink-resolved path must fall within
    ``ctx.workspace_root`` or one of ``ctx.extra_allowlist``; otherwise a
    :class:`SandboxError` is raised. The path need not exist — we resolve with
    ``strict=False`` and validate the resolved location, which still defeats
    ``..`` traversal and absolute escapes.

    For existing symlinks we additionally validate the *real* target so a link
    inside the workspace cannot be used to read an outside file.
    """
    workspace_root = Path(ctx.workspace_root).resolve()
    roots = _allowed_roots(ctx)

    raw = Path(rel)
    candidate = raw if raw.is_absolute() else (workspace_root / raw)

    # Resolve traversal and symlinks. strict=False so non-existent paths (e.g.
    # an output file a caller intends to create) still get normalized.
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:  # pragma: no cover - defensive
        raise SandboxError(f"could not resolve path {rel!r}: {exc}") from exc

    if not any(_is_within(resolved, root) for root in roots):
        raise SandboxError(
            f"path escapes sandbox: {rel!r} -> {resolved} (allowed roots: {', '.join(str(r) for r in roots)})"
        )

    # If the path exists and is (or contains) a symlink, re-validate the real
    # target. ``resolve`` already followed links, but an attacker-controlled
    # symlink created after resolution is still bounded because we re-check.
    if candidate.is_symlink() or (resolved.exists() and os.path.realpath(resolved) != str(resolved)):
        real = Path(os.path.realpath(resolved))
        if not any(_is_within(real, root) for root in roots):
            raise SandboxError(f"symlink target escapes sandbox: {rel!r} -> {real}")

    return resolved


# --------------------------------------------------------------------------- #
# Command guarding
# --------------------------------------------------------------------------- #
def _binary_basename(arg0: str) -> str:
    """Normalize ``cmd[0]`` to a bare program name for allowlist checks."""
    name = os.path.basename(arg0)
    # Strip a trailing version suffix like ``python3.11`` -> ``python3``? No —
    # keep exact; only ``python``/``python3`` are listed explicitly.
    return name


def _check_command(cmd: list[str]) -> None:
    """Validate ``cmd`` against the binary allowlist and the token denylist.

    Raises :class:`SandboxError` on any violation. This runs *before* a process
    is ever spawned.
    """
    if not cmd or not isinstance(cmd, list):
        raise SandboxError("empty or malformed command")
    if not all(isinstance(part, str) for part in cmd):
        raise SandboxError("command must be a list of strings")

    binary = _binary_basename(cmd[0])
    if binary not in ALLOWED_BINARIES:
        raise SandboxError(f"binary not allowed: {cmd[0]!r} (allowed: {', '.join(sorted(ALLOWED_BINARIES))})")

    # Token-level denylist. Compare whole tokens and substrings for the shell
    # metacharacters so e.g. ``foo>bar`` is caught even unspaced.
    lowered = [part.lower() for part in cmd]
    word_deny = {t for t in DENY_TOKENS if t.isalpha()}
    meta_deny = [t for t in DENY_TOKENS if not t.isalpha()]
    for original, token in zip(cmd, lowered):
        if token in word_deny:
            raise SandboxError(f"denied token in command: {original!r}")
        for meta in meta_deny:
            if meta in original:
                raise SandboxError(f"denied shell metacharacter {meta!r} in argument {original!r}")

    # git: enforce read-only, local-only sub-commands.
    if binary == "git":
        sub = _first_git_subcommand(cmd[1:])
        if sub is not None and sub in DENY_GIT_SUBCOMMANDS:
            raise SandboxError(f"git sub-command not allowed (read-only sandbox): {sub!r}")

    # python/pip: block network installs even when invoked as ``python -m pip``.
    if binary in {"python", "python3"}:
        if "pip" in lowered:
            if any(tok in lowered for tok in DENY_PIP_TOKENS):
                raise SandboxError("pip install/uninstall/download is not allowed in the sandbox")


def _first_git_subcommand(args: list[str]) -> str | None:
    """Return the first git sub-command, skipping leading ``-c``/global flags."""
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in ("-c", "-C", "--git-dir", "--work-tree", "--namespace"):
            i += 2  # flag + value
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok
    return None


def _sanitized_env(ctx: ToolContext) -> dict[str, str]:
    """A minimal, non-interactive environment for spawned processes."""
    base = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": os.environ.get("HOME", ctx.workspace_root),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        # Make git/pytest non-interactive and offline-friendly.
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
        "PIP_NO_INPUT": "1",
    }
    # Preserve a virtualenv if the host is running inside one (so pytest/ruff
    # resolve the right interpreter) without leaking arbitrary secrets.
    if "VIRTUAL_ENV" in os.environ:
        base["VIRTUAL_ENV"] = os.environ["VIRTUAL_ENV"]
    return base


def _truncate(data: bytes, limit: int) -> tuple[str, bool]:
    """Decode and clamp ``data`` to ``limit`` bytes; report whether clamped."""
    if limit > 0 and len(data) > limit:
        clipped = data[:limit]
        text = clipped.decode("utf-8", errors="replace")
        return text + f"\n... [truncated, {len(data) - limit} more bytes]", True
    return data.decode("utf-8", errors="replace"), False


def run_subprocess(
    cmd: list[str],
    ctx: ToolContext,
    cwd: str | os.PathLike[str] | None = None,
) -> tuple[bool, str, int, bool, float]:
    """Run an allowlisted command with full sandbox enforcement.

    Returns ``(ok, output, exit_code, truncated, duration_ms)`` where ``ok`` is
    ``True`` only when the command was permitted, ran to completion, and exited
    ``0``. ``output`` is combined stdout+stderr, truncated to
    ``ctx.max_output_bytes``. This function never raises for *process* failures
    (timeouts, non-zero exits, missing binaries) — those are surfaced in the
    return tuple. It *does* raise :class:`SandboxError` for policy violations so
    the caller can record a hard guardrail breach.
    """
    _check_command(cmd)  # raises SandboxError on violation (intentional)

    # Confine and validate the working directory.
    cwd_path = safe_path(ctx, cwd) if cwd is not None else Path(ctx.workspace_root).resolve()
    if not cwd_path.exists() or not cwd_path.is_dir():
        return False, f"working directory does not exist: {cwd_path}", -1, False, 0.0

    timeout = max(1, int(ctx.timeout_s))
    env = _sanitized_env(ctx)
    t0 = time.perf_counter()

    log.debug("sandbox exec: %s (cwd=%s, timeout=%ss)", shlex.join(cmd), cwd_path, timeout)
    try:
        proc = subprocess.run(  # noqa: S603 - args validated, shell disabled
            cmd,
            cwd=str(cwd_path),
            env=env,
            capture_output=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except FileNotFoundError:
        dur = (time.perf_counter() - t0) * 1000.0
        return False, f"binary not found on PATH: {cmd[0]!r}", 127, False, dur
    except subprocess.TimeoutExpired as exc:
        dur = (time.perf_counter() - t0) * 1000.0
        partial = b""
        if exc.stdout:
            partial += exc.stdout if isinstance(exc.stdout, bytes) else exc.stdout.encode()
        if exc.stderr:
            partial += b"\n" + (exc.stderr if isinstance(exc.stderr, bytes) else exc.stderr.encode())
        text, trunc = _truncate(partial, ctx.max_output_bytes)
        msg = f"command timed out after {timeout}s\n{text}".strip()
        return False, msg, 124, trunc, dur
    except OSError as exc:  # pragma: no cover - defensive
        dur = (time.perf_counter() - t0) * 1000.0
        return False, f"failed to launch command: {exc}", -1, False, dur

    dur = (time.perf_counter() - t0) * 1000.0
    combined = (proc.stdout or b"") + (b"\n" + proc.stderr if proc.stderr else b"")
    text, trunc = _truncate(combined, ctx.max_output_bytes)
    return proc.returncode == 0, text, proc.returncode, trunc, dur

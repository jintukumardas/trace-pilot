"""Local-model access for the agent graph.

Two roles map to two Ollama models from settings:

* ``"gen"``   → ``settings.ollama_gen_model``       (fast, user-facing synthesis)
* ``"reason"``→ ``settings.ollama_reasoning_model`` (router/planner/judge reasoning)

:func:`get_llm` returns a configured, cached :class:`langchain_ollama.ChatOllama`.
:func:`complete` is the only call sites use: it renders a prompt, invokes the
model, and — for structured nodes — extracts a JSON object robustly (strips code
fences, finds the first balanced ``{...}``), retries once on a parse miss, and on
any model/transport failure returns a safe fallback ``dict`` (with a ``_warning``
marker) instead of raising. Callers therefore never have to wrap us in try/except.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Literal

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger

log = get_logger("agent.models")

Role = Literal["gen", "reason"]

# Sentinel keys stamped onto fallback/parse-failure dicts so downstream nodes can
# detect a degraded completion and surface a warning without inspecting strings.
WARNING_KEY = "_warning"
RAW_KEY = "_raw"

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


# --------------------------------------------------------------------------- #
# Model construction
# --------------------------------------------------------------------------- #
def _model_name(role: Role, settings: Settings) -> str:
    return settings.ollama_reasoning_model if role == "reason" else settings.ollama_gen_model


@lru_cache(maxsize=4)
def _build_llm(
    role: Role, base_url: str, model: str, temperature: float, num_ctx: int, timeout_s: int
) -> Any:
    """Construct (and cache) a ChatOllama for the given concrete config.

    Cached on the *resolved* parameters rather than ``Settings`` (which is not
    hashable) so tests overriding settings get a fresh client. Import is lazy so
    importing this package never requires ``langchain_ollama`` to be installed.
    """
    from langchain_ollama import ChatOllama  # lazy, optional dependency

    return ChatOllama(
        model=model,
        base_url=base_url,
        temperature=temperature,
        num_ctx=num_ctx,
        # Per-request transport timeout so a hung Ollama can't wedge a request.
        client_kwargs={"timeout": float(timeout_s)},
    )


def get_llm(role: Role = "gen", settings: Settings | None = None) -> Any:
    """Return a configured, cached ``ChatOllama`` for ``role`` (``"gen"``|``"reason"``)."""
    settings = settings or get_settings()
    return _build_llm(
        role,
        settings.ollama_base_url,
        _model_name(role, settings),
        float(settings.model_temperature),
        int(settings.model_num_ctx),
        int(settings.request_timeout_seconds),
    )


# --------------------------------------------------------------------------- #
# Robust JSON extraction
# --------------------------------------------------------------------------- #
def _strip_fences(text: str) -> str:
    """Remove a surrounding ```json ...``` fence if the model added one."""
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _first_json_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` substring, respecting strings/escapes.

    Small local models often prepend a sentence or wrap JSON in prose despite the
    instructions, so we scan for the first ``{`` and walk to its matching ``}``
    while ignoring braces inside string literals.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(text: str) -> dict | None:
    """Best-effort parse of a JSON object from raw model text. ``None`` on failure."""
    if not text:
        return None
    candidate = _strip_fences(text)
    # Fast path: the whole (de-fenced) reply is the object.
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # Slow path: carve out the first balanced object and parse it.
    snippet = _first_json_object(candidate) or _first_json_object(text)
    if snippet is None:
        return None
    try:
        obj = json.loads(snippet)
    except Exception:
        # Last resort: tolerate trailing commas, a common small-model slip.
        cleaned = re.sub(r",\s*([}\]])", r"\1", snippet)
        try:
            obj = json.loads(cleaned)
        except Exception:
            return None
    return obj if isinstance(obj, dict) else None


# --------------------------------------------------------------------------- #
# Completion
# --------------------------------------------------------------------------- #
def _invoke_text(llm: Any, prompt: str) -> str:
    """Invoke the model and normalize the reply to a plain string."""
    msg = llm.invoke(prompt)
    content = getattr(msg, "content", msg)
    if isinstance(content, list):  # some chat models return content parts
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)


def _fallback_dict(prompt: str, reason: str) -> dict:
    """A schema-agnostic fallback object returned when the model is unreachable.

    Carries the warning so nodes can record it; nodes apply their own
    schema-specific defaults on top (e.g. ``intent``, ``confidence``).
    """
    return {WARNING_KEY: reason}


_JSON_REPAIR_SUFFIX = (
    "\n\nREMINDER: Output must be ONE JSON object only. "
    "The first character must be `{` and the last must be `}`. No prose, no code fences."
)


def complete(
    prompt: str,
    role: Role = "gen",
    want_json: bool = False,
    settings: Settings | None = None,
) -> str | dict:
    """Run a single completion. Never raises.

    * ``want_json=False`` → returns the model's text (``str``); on model error
      returns a short ``"[model unavailable: ...]"`` marker string so synthesis
      can still proceed grounded on evidence.
    * ``want_json=True``  → returns a parsed ``dict``. On a parse miss it retries
      once with a terse JSON reminder; if still unparseable, or if the model is
      unreachable, it returns a fallback ``dict`` carrying a ``_warning`` and (when
      available) the raw text under ``_raw`` for debugging.
    """
    settings = settings or get_settings()
    try:
        llm = get_llm(role, settings)
    except Exception as exc:  # langchain_ollama missing / construction failure
        log.warning("get_llm(%s) failed: %s", role, exc)
        msg = f"model unavailable ({type(exc).__name__})"
        return _fallback_dict(prompt, msg) if want_json else f"[{msg}]"

    # First attempt.
    try:
        text = _invoke_text(llm, prompt)
    except Exception as exc:  # transport / Ollama down / model not pulled
        log.warning("completion failed (role=%s): %s", role, exc)
        msg = f"model unavailable ({type(exc).__name__}: {exc})"
        return _fallback_dict(prompt, msg) if want_json else f"[{msg}]"

    if not want_json:
        return text

    parsed = extract_json(text)
    if parsed is not None:
        return parsed

    # One retry with a stronger JSON reminder before giving up.
    log.info("JSON parse miss (role=%s); retrying once", role)
    try:
        retry_text = _invoke_text(llm, prompt + _JSON_REPAIR_SUFFIX)
    except Exception as exc:
        log.warning("JSON retry failed (role=%s): %s", role, exc)
        out = _fallback_dict(prompt, f"model error on retry ({type(exc).__name__})")
        out[RAW_KEY] = text[:2000]
        return out

    parsed = extract_json(retry_text)
    if parsed is not None:
        return parsed

    log.warning("JSON unparseable after retry (role=%s)", role)
    out = _fallback_dict(prompt, "model returned unparseable JSON")
    out[RAW_KEY] = (retry_text or text)[:2000]
    return out


def is_degraded(obj: str | dict | None) -> bool:
    """True if ``obj`` is a fallback/degraded completion (model unavailable / unparsed)."""
    if isinstance(obj, dict):
        return WARNING_KEY in obj
    if isinstance(obj, str):
        return obj.startswith("[model unavailable")
    return obj is None


__all__ = [
    "Role",
    "get_llm",
    "complete",
    "extract_json",
    "is_degraded",
    "WARNING_KEY",
    "RAW_KEY",
]

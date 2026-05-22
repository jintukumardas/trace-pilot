"""Jinja2-backed prompt loader for TracePilot.

All prompts live as ``templates/<name>.jinja`` next to this module. The
``Environment`` is built once and cached; rendering is cheap and thread-safe.

Design notes
------------
- ``autoescape=False`` — these are LLM prompts, not HTML. We never want
  ``&amp;`` leaking into a model's context window.
- ``trim_blocks`` / ``lstrip_blocks`` keep the rendered text tight so small
  local models aren't fed ragged whitespace.
- ``undefined=ChainableUndefined`` makes templates fail soft: an unset
  variable renders empty instead of raising mid-prompt, which matters because
  callers pass heterogeneous context (chat vs. debug vs. review).
- Missing template names raise a clear ``KeyError`` that lists every available
  prompt, so a typo at a call site is obvious immediately.
"""

from __future__ import annotations

import functools
from pathlib import Path

from jinja2 import (
    ChainableUndefined,
    Environment,
    FileSystemLoader,
    TemplateNotFound,
    select_autoescape,
)

from tracepilot_shared.logging import get_logger

logger = get_logger("tracepilot_prompts")

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_SUFFIX = ".jinja"


@functools.lru_cache(maxsize=1)
def get_environment() -> Environment:
    """Build and cache the shared Jinja2 ``Environment``.

    Cached for the process lifetime; templates are read from disk lazily and
    Jinja keeps its own compiled-template cache keyed by name.
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=(), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        undefined=ChainableUndefined,
        auto_reload=False,
    )
    # Small, prompt-focused filters used across templates.
    env.filters["indent_block"] = _indent_block
    env.filters["oneline"] = _oneline
    logger.debug("Jinja environment initialised at %s", TEMPLATE_DIR)
    return env


def available_prompts() -> list[str]:
    """Return the sorted list of public prompt names (filenames without ``.jinja``).

    Underscore-prefixed templates (e.g. ``_macros.jinja``) are internal partials
    that are ``{% import %}``-ed by other templates and are not renderable on
    their own, so they are excluded from discovery.
    """
    if not TEMPLATE_DIR.is_dir():
        return []
    names = [
        p.stem for p in TEMPLATE_DIR.glob(f"*{TEMPLATE_SUFFIX}") if p.is_file() and not p.name.startswith("_")
    ]
    return sorted(names)


def _template_filename(name: str) -> str:
    """Normalise a prompt name to its template filename."""
    name = name.strip()
    if name.endswith(TEMPLATE_SUFFIX):
        return name
    return f"{name}{TEMPLATE_SUFFIX}"


def _missing(name: str) -> KeyError:
    avail = ", ".join(available_prompts()) or "(none found)"
    return KeyError(f"Unknown prompt {name!r}. Available prompts: {avail}")


def load_prompt(name: str) -> str:
    """Return the *raw* template source for ``name`` (no rendering).

    Raises ``KeyError`` listing available prompts if the template is missing.
    """
    path = TEMPLATE_DIR / _template_filename(name)
    if not path.is_file():
        raise _missing(name)
    return path.read_text(encoding="utf-8")


def render(name: str, **context: object) -> str:
    """Render ``templates/<name>.jinja`` with ``context`` and return text.

    Raises ``KeyError`` (listing available prompts) when the template does not
    exist. Trailing whitespace is stripped and a single newline guaranteed so
    rendered prompts concatenate cleanly.
    """
    env = get_environment()
    try:
        template = env.get_template(_template_filename(name))
    except TemplateNotFound as exc:  # pragma: no cover - exercised via render tests
        raise _missing(name) from exc
    rendered = template.render(**context)
    return rendered.strip() + "\n"


# --- Jinja filters ------------------------------------------------------------


def _indent_block(text: object, width: int = 2) -> str:
    """Indent every line of ``text`` by ``width`` spaces (for nested snippets)."""
    pad = " " * width
    body = "" if text is None else str(text)
    return "\n".join(pad + line for line in body.splitlines())


def _oneline(text: object) -> str:
    """Collapse whitespace/newlines into a single line (for compact summaries)."""
    body = "" if text is None else str(text)
    return " ".join(body.split())

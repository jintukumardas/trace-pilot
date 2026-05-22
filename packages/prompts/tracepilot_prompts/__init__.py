"""tracepilot_prompts — file-backed Jinja2 prompt templates.

Public API (see docs/INTERNAL_CONTRACTS.md)::

    from tracepilot_prompts import render, load_prompt, available_prompts

    render(name: str, **context) -> str    # render templates/<name>.jinja
    load_prompt(name: str) -> str           # raw template source
    available_prompts() -> list[str]        # discoverable prompt names

Structured prompts (router, retrieval_planner, action_planner, judge,
debug_synthesizer, change_review) instruct the model to emit STRICT JSON whose
shape matches the contracts the agent-graph parser expects.
"""

from __future__ import annotations

from .loader import available_prompts, load_prompt, render

__version__ = "0.1.0"

__all__ = ["render", "load_prompt", "available_prompts", "__version__"]

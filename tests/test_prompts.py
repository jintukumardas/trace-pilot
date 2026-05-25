"""Tests for ``tracepilot_prompts``: every required template renders non-empty,
structured templates instruct strict JSON, and the loader API behaves.
"""

from __future__ import annotations

import pytest

from tracepilot_prompts import available_prompts, load_prompt, render

# Templates the contract requires (filenames without extension).
REQUIRED_TEMPLATES = [
    "system_preamble",
    "router",
    "retrieval_planner",
    "code_analyst",
    "action_planner",
    "synthesizer",
    "judge",
    "debug_synthesizer",
    "change_review",
    "fix_plan",
    "onboard",
]

# Templates that must instruct the model to emit strict JSON.
STRUCTURED_TEMPLATES = [
    "router",
    "retrieval_planner",
    "action_planner",
    "judge",
    "debug_synthesizer",
    "change_review",
]

# A generous context superset so any template variable resolves.
_CONTEXT = {
    "question": "How is configuration loaded?",
    "mode": "ask",
    "intent": "question",
    "history": [{"role": "user", "content": "hi"}],
    "evidence": [
        {
            "index": 1,
            "repo": "demo",
            "file_path": "config.py",
            "start_line": 1,
            "end_line": 5,
            "snippet": "def load_settings(): ...",
        },
    ],
    "analysis": "the loader reads env vars",
    "tools": [],
    "tool_results": [],
    "answer": "Config is loaded by load_settings() [1].",
    "diff": "--- a/config.py\n+++ b/config.py\n@@\n-timeout=30\n+timeout=60\n",
    "title": "Bump timeout",
    "bug_report": "ValueError on empty payload",
    "stack_trace": "Traceback ...",
    "reproduction": "call run('')",
}


def test_available_prompts_covers_required():
    available = set(available_prompts())
    missing = [t for t in REQUIRED_TEMPLATES if t not in available]
    assert not missing, f"missing templates: {missing}"


def test_available_prompts_excludes_partials():
    # Underscore-prefixed partials (e.g. _macros) are not renderable on their own.
    assert all(not name.startswith("_") for name in available_prompts())


@pytest.mark.parametrize("name", REQUIRED_TEMPLATES)
def test_render_produces_non_empty(name):
    out = render(name, **_CONTEXT)
    assert isinstance(out, str)
    assert out.strip(), f"{name} rendered empty"
    # The rendered prompt should at least be a few words long.
    assert len(out.split()) > 5


@pytest.mark.parametrize("name", STRUCTURED_TEMPLATES)
def test_structured_templates_mention_json(name):
    out = render(name, **_CONTEXT).lower()
    assert "json" in out, f"{name} does not instruct JSON output"


@pytest.mark.parametrize("name", STRUCTURED_TEMPLATES)
def test_structured_templates_show_a_schema_object(name):
    out = render(name, **_CONTEXT)
    # A schema/example object should appear so the small model has a shape to copy.
    assert "{" in out and "}" in out


def test_synthesizer_includes_question_and_evidence():
    out = render("synthesizer", **_CONTEXT)
    assert "How is configuration loaded?" in out
    assert "config.py" in out  # evidence block rendered


def test_load_prompt_returns_raw_source():
    raw = load_prompt("router")
    assert "TASK: ROUTER" in raw
    # Raw source still contains Jinja constructs (un-rendered).
    assert "{{" in raw or "{%" in raw


def test_render_unknown_template_raises_keyerror():
    with pytest.raises(KeyError):
        render("does_not_exist")


def test_render_missing_variable_fails_soft():
    # ChainableUndefined: an unset variable renders empty rather than raising.
    out = render("synthesizer")  # no context at all
    assert isinstance(out, str)
    assert out.strip()

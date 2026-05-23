"""Router node — classify the user message into an :class:`IntentType`.

Contract emitted by the prompt::

    {"intent": "question|onboarding|debugging|change_review|fix_plan|smalltalk",
     "rationale": str, "repository_focus": [str]}

The router uses the reasoning model. When the request mode already pins the
intent (debug / change_review), we honor that as a strong prior so a degraded
model still routes correctly.
"""

from __future__ import annotations

from tracepilot_prompts import render
from tracepilot_shared.models import IntentType

from ..models import complete
from ..state import AgentState
from . import _common as C

_VALID_INTENTS = {i.value for i in IntentType}

# Map the UI mode to the intent it implies, used as a fallback / prior.
_MODE_INTENT = {
    "ask": IntentType.QUESTION.value,
    "onboard": IntentType.ONBOARDING.value,
    "debug": IntentType.DEBUGGING.value,
    "change_review": IntentType.CHANGE_REVIEW.value,
    "fix_plan": IntentType.FIX_PLAN.value,
}


def router_node(state: AgentState) -> dict:
    tracer = state["tracer"]
    mode = state.get("mode", "ask")
    prior = _MODE_INTENT.get(mode, IntentType.QUESTION.value)
    warnings = list(state.get("warnings", []))

    with tracer.span("router", type="generation", input={"mode": mode}) as sp:
        prompt = render(
            "router",
            question=state["request"],
            mode=mode,
            history=state.get("history", []),
        )
        parsed = complete(prompt, role="reason", want_json=True, settings=state.get("settings"))
        C.warn_if_degraded(parsed, "router", warnings)

        intent = prior
        focus: list[str] = []
        if isinstance(parsed, dict):
            cand = C.as_str(parsed.get("intent")).strip().lower()
            if cand in _VALID_INTENTS:
                intent = cand
            focus = C.as_str_list(parsed.get("repository_focus"))

        # Debug/review UI modes are explicit user choices: don't let a model
        # downgrade them to "question" on a thin classification.
        if mode in ("debug", "change_review") and intent not in (
            IntentType.DEBUGGING.value,
            IntentType.CHANGE_REVIEW.value,
        ):
            intent = prior

        sp.update(output={"intent": intent, "repository_focus": focus})

    return {"intent": intent, "repository_focus": focus, "warnings": warnings}

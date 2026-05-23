"""``AgentState`` — the typed payload that flows through the LangGraph graph.

LangGraph invokes each node with the current state and merges the partial
``dict`` a node returns back into it. We therefore keep the state a plain
``TypedDict`` (not a Pydantic model) so partial updates are cheap and so the
non-serializable runtime handles (``tracer``, ``retriever``, ``repo_locator``)
can ride along untouched.

``make_initial_state`` builds the starting payload for a request, injecting the
runtime dependencies the nodes need. The graph never reads the dependencies off
module globals — everything it needs is on the state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.models import (
    ChatMessage,
    Citation,
    Confidence,
    Evidence,
    IntentType,
    NextAction,
    RetrievalQuery,
    ToolCall,
    ToolResult,
)
from tracepilot_shared.telemetry import Tracer

if TYPE_CHECKING:  # avoid importing heavy/optional deps at module import time
    from tracepilot_retrieval import Retriever

    from .runtime import RepoLocator


class AgentState(TypedDict, total=False):
    """Mutable working memory for a single investigation run.

    Fields are grouped as: the request envelope, per-node products, the final
    answer envelope, bookkeeping (warnings/errors/iterations), and the injected
    runtime handles. ``total=False`` so nodes may return partial updates.
    """

    # --- request envelope ---------------------------------------------------
    request: str  # the raw user message / bug report / review intent
    mode: str  # ChatMode value: ask|onboard|debug|change_review|fix_plan
    history: list[ChatMessage]  # prior conversation turns
    repository_ids: list[str]  # repos to scope retrieval to ([] = all in workspace)
    workspace_id: str
    branch: str | None
    # debug-specific inputs (empty for non-debug runs)
    stack_trace: str | None
    reproduction: str | None
    # review-specific inputs (empty for non-review runs)
    diff: str | None
    title: str | None
    repository_id: str | None  # single repo for review/tool resolution

    # --- per-node products --------------------------------------------------
    intent: str  # IntentType value from the router
    repository_focus: list[str]  # repo/path hints the user mentioned
    plan: list[str]  # human-readable retrieval plan (one line per query)
    queries: list[RetrievalQuery]  # structured retrieval plan
    evidence: list[Evidence]  # merged, deduped, ranked evidence
    citations: list[Citation]  # 1-based citations aligned to packed context
    context: str  # packed, budget-bounded evidence block for prompts
    analysis: str  # code-analyst free-text reasoning
    needs_tools: bool
    tool_calls: list[ToolCall]  # planned tool invocations
    tool_results: list[ToolResult]  # executed tool outputs

    # --- answer envelope ----------------------------------------------------
    answer: str
    confidence: str  # Confidence value
    next_actions: list[NextAction]
    # structured debug/review products (populated only in their modes)
    debug: dict[str, Any]
    review: dict[str, Any]
    scores: dict[str, float]  # judge scores, mirrored onto the trace

    # --- bookkeeping --------------------------------------------------------
    iterations: int  # tool-loop counter (bounded to <= 2)
    warnings: list[str]
    errors: list[str]

    # --- injected runtime handles (not serialized into telemetry) ----------
    # NOTE: retriever/repo_locator are typed ``Any`` on purpose. LangGraph calls
    # get_type_hints(AgentState) at build time, which evaluates every annotation;
    # a forward-ref to a TYPE_CHECKING-only import would raise NameError there.
    # These are opaque runtime handles, not graph channels, so Any is correct.
    tracer: Tracer
    settings: Settings
    retriever: Any  # tracepilot_retrieval.Retriever
    repo_locator: Any  # tracepilot_agent.runtime.RepoLocator


# Modes that should run the specialized synthesizer templates.
DEBUG_MODES = {"debug"}
REVIEW_MODES = {"change_review"}


def make_initial_state(
    *,
    request: str,
    mode: str,
    tracer: Tracer,
    retriever: Retriever,
    repo_locator: RepoLocator,
    settings: Settings | None = None,
    history: list[ChatMessage] | None = None,
    repository_ids: list[str] | None = None,
    workspace_id: str = "",
    branch: str | None = None,
    stack_trace: str | None = None,
    reproduction: str | None = None,
    diff: str | None = None,
    title: str | None = None,
    repository_id: str | None = None,
) -> AgentState:
    """Build the starting state for a run, injecting runtime dependencies.

    All per-node product fields are seeded to safe empty values so nodes can
    append without guarding for missing keys, and so the terminal-state → response
    mapping in the orchestrator always finds well-typed defaults.
    """
    settings = settings or get_settings()
    # For a single-repo flow (review) make repository_ids consistent so retrieval
    # is scoped the same way tool resolution is.
    repo_ids = list(repository_ids or [])
    if repository_id and repository_id not in repo_ids:
        repo_ids.append(repository_id)

    state: AgentState = {
        "request": request,
        "mode": mode,
        "history": list(history or []),
        "repository_ids": repo_ids,
        "workspace_id": workspace_id,
        "branch": branch,
        "stack_trace": stack_trace,
        "reproduction": reproduction,
        "diff": diff,
        "title": title,
        "repository_id": repository_id,
        # per-node products
        "intent": IntentType.QUESTION.value,
        "repository_focus": [],
        "plan": [],
        "queries": [],
        "evidence": [],
        "citations": [],
        "context": "",
        "analysis": "",
        "needs_tools": False,
        "tool_calls": [],
        "tool_results": [],
        # answer envelope
        "answer": "",
        "confidence": Confidence.MEDIUM.value,
        "next_actions": [],
        "debug": {},
        "review": {},
        "scores": {},
        # bookkeeping
        "iterations": 0,
        "warnings": [],
        "errors": [],
        # runtime handles
        "tracer": tracer,
        "settings": settings,
        "retriever": retriever,
        "repo_locator": repo_locator,
    }
    return state

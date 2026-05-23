"""The :class:`Orchestrator` — the package's public entry point.

It owns nothing stateful beyond its injected dependencies (a ``Retriever`` and a
``RepoLocator``). For each request it:

1. builds a per-request :class:`Tracer` tagged with the workflow/mode,
2. constructs the initial :class:`AgentState`, injecting the retriever, repo
   locator, settings and tracer,
3. invokes the cached, compiled LangGraph graph,
4. finishes the trace and maps the terminal state into the right response model
   (``ChatResponse`` / ``DebugResponse`` / ``DiffReviewResponse``), stamping
   ``trace_id`` and ``latency_ms``.

Everything fails soft: a crash inside the graph is caught and turned into a
degraded-but-valid response carrying the error as a warning, so callers (the API)
never see an exception from a chat/debug/review call.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from tracepilot_retrieval import Retriever
from tracepilot_shared.config import Settings, get_settings
from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import (
    ChatMode,
    ChatRequest,
    ChatResponse,
    Citation,
    Confidence,
    DebugRequest,
    DebugResponse,
    DiffReviewRequest,
    DiffReviewResponse,
    Evidence,
    FixPlan,
    IntentType,
    NextAction,
    RootCauseCandidate,
    ToolResult,
)
from tracepilot_shared.telemetry import Tracer

from .graph import get_compiled_graph
from .state import make_initial_state

log = get_logger("agent.runtime")

# LangGraph recursion guard. Our longest path is bounded (≤2 tool loops), but we
# give headroom so a future node addition doesn't trip the default limit.
_RECURSION_LIMIT = 40


@runtime_checkable
class RepoLocator(Protocol):
    """Resolve a repository id to its on-disk workspace and display name.

    Implemented by the API's ``ApiRepoLocator``. ``resolve`` returns an absolute
    local path the tools may run against, or ``None`` if the repo isn't on disk.
    """

    def resolve(self, repository_id: str) -> str | None: ...

    def name(self, repository_id: str) -> str: ...


class Orchestrator:
    """Runs the agent graph and maps results to the public response models."""

    def __init__(
        self, retriever: Retriever, repo_locator: RepoLocator, settings: Settings | None = None
    ) -> None:
        self.retriever = retriever
        self.repo_locator = repo_locator
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def chat(self, req: ChatRequest) -> ChatResponse:
        """Grounded Q&A / onboarding / fix_plan. Debug & review have their own modes."""
        mode = req.mode.value if isinstance(req.mode, ChatMode) else str(req.mode)
        tracer = Tracer(
            name="chat",
            workflow=mode,
            input={"message": req.message, "mode": mode, "workspace": req.workspace_id},
            tags=["chat", mode],
        )
        t0 = time.perf_counter()
        state = make_initial_state(
            request=req.message,
            mode=mode,
            tracer=tracer,
            retriever=self.retriever,
            repo_locator=self.repo_locator,
            settings=self.settings,
            history=req.history,
            repository_ids=req.repository_ids,
            workspace_id=req.workspace_id,
            branch=req.branch,
            repository_id=req.repository_ids[0] if req.repository_ids else None,
        )
        final = self._run(state, tracer)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        resp = ChatResponse(
            answer=final.get("answer") or _empty_answer(),
            confidence=_confidence(final.get("confidence")),
            intent=_intent(final.get("intent")),
            mode=req.mode if isinstance(req.mode, ChatMode) else ChatMode(mode),
            evidence=_evidence(final),
            citations=_citations(final),
            next_actions=_next_actions(final),
            tools_used=_tool_results(final),
            trace_id=tracer.id,
            latency_ms=latency_ms,
            warnings=list(final.get("warnings", [])),
        )
        tracer.finish(
            output={
                "confidence": resp.confidence.value,
                "n_citations": len(resp.citations),
                "n_tools": len(resp.tools_used),
            }
        )
        return resp

    def debug(self, req: DebugRequest) -> DebugResponse:
        """Structured root-cause analysis + fix plan over the bug report."""
        tracer = Tracer(
            name="debug",
            workflow="debug",
            input={"bug_report": req.bug_report, "workspace": req.workspace_id},
            tags=["debug"],
        )
        t0 = time.perf_counter()
        request_text = _compose_debug_request(req)
        state = make_initial_state(
            request=request_text,
            mode=ChatMode.DEBUG.value,
            tracer=tracer,
            retriever=self.retriever,
            repo_locator=self.repo_locator,
            settings=self.settings,
            repository_ids=req.repository_ids,
            workspace_id=req.workspace_id,
            branch=req.branch,
            stack_trace=req.stack_trace,
            reproduction=req.reproduction,
            repository_id=req.repository_ids[0] if req.repository_ids else None,
        )
        final = self._run(state, tracer)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        debug = final.get("debug") or {}

        resp = DebugResponse(
            summary=debug.get("summary") or final.get("answer") or _empty_answer(),
            root_cause_candidates=_root_causes(debug),
            impacted_files=list(debug.get("impacted_files", [])),
            diagnostic_steps=list(debug.get("diagnostic_steps", [])),
            fix_plan=_fix_plan(debug.get("fix_plan")),
            evidence=_evidence(final),
            citations=_citations(final),
            tools_used=_tool_results(final),
            confidence=_confidence(debug.get("confidence") or final.get("confidence")),
            trace_id=tracer.id,
            latency_ms=latency_ms,
        )
        tracer.finish(
            output={
                "confidence": resp.confidence.value,
                "n_candidates": len(resp.root_cause_candidates),
            }
        )
        return resp

    def review(self, req: DiffReviewRequest) -> DiffReviewResponse:
        """Change/impact review of a unified diff."""
        tracer = Tracer(
            name="review",
            workflow="change_review",
            input={"title": req.title, "repository": req.repository_id},
            tags=["change_review"],
        )
        t0 = time.perf_counter()
        diff = self._resolve_diff(req, tracer)
        request_text = _compose_review_request(req, diff)
        state = make_initial_state(
            request=request_text,
            mode=ChatMode.CHANGE_REVIEW.value,
            tracer=tracer,
            retriever=self.retriever,
            repo_locator=self.repo_locator,
            settings=self.settings,
            workspace_id=req.workspace_id,
            diff=diff,
            title=req.title,
            repository_id=req.repository_id,
            repository_ids=[req.repository_id] if req.repository_id else [],
        )
        final = self._run(state, tracer)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        review = final.get("review") or {}

        resp = DiffReviewResponse(
            summary=review.get("summary") or final.get("answer") or _empty_answer(),
            impact=review.get("impact", ""),
            risk_level=_confidence(review.get("risk_level") or final.get("confidence")),
            affected_areas=list(review.get("affected_areas", [])),
            suggested_tests=list(review.get("suggested_tests", [])),
            citations=_citations(final),
            evidence=_evidence(final),
            trace_id=tracer.id,
            latency_ms=latency_ms,
        )
        tracer.finish(
            output={
                "risk_level": resp.risk_level.value,
                "n_affected": len(resp.affected_areas),
            }
        )
        return resp

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _run(self, state: dict, tracer: Tracer) -> dict:
        """Invoke the compiled graph, returning the terminal state (fail-soft)."""
        try:
            graph = get_compiled_graph()
            final = graph.invoke(state, config={"recursion_limit": _RECURSION_LIMIT})
            # LangGraph returns the merged state dict.
            if isinstance(final, dict):
                return final
            log.warning("graph returned non-dict state: %r", type(final))
            return dict(state)
        except Exception as exc:  # the contract: never raise to the caller
            log.exception("agent graph failed: %s", exc)
            tracer.record.metadata["graph_error"] = repr(exc)
            degraded = dict(state)
            warnings = list(degraded.get("warnings", []))
            warnings.append(f"orchestrator: graph execution failed ({type(exc).__name__})")
            degraded["warnings"] = warnings
            if not degraded.get("answer"):
                degraded["answer"] = (
                    "The investigation could not be completed due to an internal error. "
                    "Partial results (retrieved evidence, if any) are returned below."
                )
                degraded["confidence"] = Confidence.LOW.value
            return degraded

    def _resolve_diff(self, req: DiffReviewRequest, tracer: Tracer) -> str:
        """Return the diff text to review, computing it via git when not supplied."""
        if req.diff and req.diff.strip():
            return req.diff
        # Compute base..head via the git_diff tool, bounded + sandboxed.
        try:
            path = self.repo_locator.resolve(req.repository_id)
        except Exception:
            path = None
        if not path:
            return req.diff or ""
        try:
            from tracepilot_tooling import ToolContext, execute_tool, make_call

            args: dict[str, Any] = {}
            if req.base_ref:
                args["base"] = req.base_ref
            if req.head_ref:
                args["head"] = req.head_ref
            call = make_call("git_diff", args, reason="materialize diff for review")
            ctx = ToolContext.for_workspace(path, self.settings)
            result = execute_tool(call, ctx, tracer=tracer)
            if result.ok and result.output.strip():
                return result.output
        except Exception as exc:  # fail soft to whatever the request carried
            log.warning("git_diff materialization failed: %s", exc)
        return req.diff or ""


# --------------------------------------------------------------------------- #
# State → response mapping helpers
# --------------------------------------------------------------------------- #
def _empty_answer() -> str:
    return "No answer could be produced for this request."


def _confidence(value: Any) -> Confidence:
    try:
        return Confidence(str(value).strip().lower())
    except Exception:
        return Confidence.MEDIUM


def _intent(value: Any) -> IntentType:
    try:
        return IntentType(str(value).strip().lower())
    except Exception:
        return IntentType.QUESTION


def _evidence(final: dict) -> list[Evidence]:
    out = final.get("evidence", [])
    return [e for e in out if isinstance(e, Evidence)]


def _citations(final: dict) -> list[Citation]:
    out = final.get("citations", [])
    return [c for c in out if isinstance(c, Citation)]


def _next_actions(final: dict) -> list[NextAction]:
    out = final.get("next_actions", [])
    return [a for a in out if isinstance(a, NextAction)]


def _tool_results(final: dict) -> list[ToolResult]:
    out = final.get("tool_results", [])
    return [t for t in out if isinstance(t, ToolResult)]


def _root_causes(debug: dict) -> list[RootCauseCandidate]:
    candidates: list[RootCauseCandidate] = []
    for c in debug.get("root_cause_candidates", []):
        if not isinstance(c, dict):
            continue
        try:
            candidates.append(
                RootCauseCandidate(
                    hypothesis=c.get("hypothesis", ""),
                    confidence=_confidence(c.get("confidence")),
                    impacted_files=list(c.get("impacted_files", [])),
                    reasoning=c.get("reasoning", ""),
                    evidence_indices=[int(i) for i in c.get("evidence_indices", []) if _is_int(i)],
                )
            )
        except Exception:
            continue
    return candidates


def _fix_plan(raw: Any) -> FixPlan | None:
    if not isinstance(raw, dict):
        return None
    if not any(raw.get(k) for k in ("steps", "risks", "test_strategy", "rollback")):
        return None
    return FixPlan(
        steps=list(raw.get("steps", [])),
        risks=list(raw.get("risks", [])),
        test_strategy=list(raw.get("test_strategy", [])),
        rollback=raw.get("rollback") or None,
    )


def _is_int(value: Any) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Request → graph input composition
# --------------------------------------------------------------------------- #
def _compose_debug_request(req: DebugRequest) -> str:
    """Flatten a debug request into one retrievable request string.

    Retrieval keys off this text, so we include the bug report plus the most
    search-worthy fragments (stack-trace frames, reproduction) to seed good queries.
    The structured stack_trace/reproduction also ride on the state for the prompt.
    """
    parts = [req.bug_report.strip()]
    if req.stack_trace and req.stack_trace.strip():
        parts.append("Stack trace:\n" + req.stack_trace.strip())
    if req.reproduction and req.reproduction.strip():
        parts.append("Reproduction:\n" + req.reproduction.strip())
    return "\n\n".join(p for p in parts if p)


def _compose_review_request(req: DiffReviewRequest, diff: str) -> str:
    """Compose the review request text used to seed retrieval of surrounding code."""
    parts: list[str] = []
    if req.title and req.title.strip():
        parts.append(f"Review of: {req.title.strip()}")
    else:
        parts.append("Review the proposed change for correctness, impact, and risk.")
    # Seed retrieval with the file paths the diff touches so we pull surrounding code.
    touched = _diff_paths(diff)
    if touched:
        parts.append("Files changed: " + ", ".join(touched[:20]))
    return "\n".join(parts)


def _diff_paths(diff: str) -> list[str]:
    """Extract changed file paths from a unified diff (``+++ b/<path>`` lines)."""
    paths: list[str] = []
    for line in (diff or "").splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            token = line[4:].strip()
            if token in ("/dev/null", ""):
                continue
            if token.startswith("a/") or token.startswith("b/"):
                token = token[2:]
            if token not in paths:
                paths.append(token)
    return paths

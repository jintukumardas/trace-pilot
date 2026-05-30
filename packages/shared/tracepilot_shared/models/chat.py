"""Chat, debug and review request/response models — the public API surface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .common import ChatMode, Confidence, IntentType
from .retrieval import Citation, Evidence
from .tools import ToolCall, ToolResult


class ChatMessage(BaseModel):
    """A single conversation turn."""

    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    """Primary entrypoint for grounded Q&A and investigation modes."""

    workspace_id: str
    repository_ids: list[str] = Field(
        default_factory=list, description="Empty = search all repos in workspace"
    )
    mode: ChatMode = ChatMode.ASK
    message: str = Field(..., min_length=1)
    history: list[ChatMessage] = Field(default_factory=list)
    top_k: int = Field(default=8, ge=1, le=50)
    branch: str | None = None


class NextAction(BaseModel):
    """A concrete, suggested follow-up step."""

    title: str
    detail: str = ""
    rationale: str = ""


class ChatResponse(BaseModel):
    """Grounded answer envelope. Every field below is required by the product spec."""

    answer: str
    confidence: Confidence = Confidence.MEDIUM
    intent: IntentType = IntentType.QUESTION
    mode: ChatMode = ChatMode.ASK
    evidence: list[Evidence] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    tools_used: list[ToolResult] = Field(default_factory=list)
    trace_id: str | None = None
    latency_ms: float = 0.0
    warnings: list[str] = Field(default_factory=list)


# --- Debug mode ---------------------------------------------------------------


class DebugRequest(BaseModel):
    workspace_id: str
    repository_ids: list[str] = Field(default_factory=list)
    bug_report: str = Field(..., min_length=1)
    stack_trace: str | None = None
    reproduction: str | None = None
    branch: str | None = None


class RootCauseCandidate(BaseModel):
    hypothesis: str
    confidence: Confidence = Confidence.MEDIUM
    impacted_files: list[str] = Field(default_factory=list)
    reasoning: str = ""
    evidence_indices: list[int] = Field(default_factory=list, description="References into the evidence list")


class FixPlan(BaseModel):
    steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    test_strategy: list[str] = Field(default_factory=list)
    rollback: str | None = None


class DebugResponse(BaseModel):
    summary: str
    root_cause_candidates: list[RootCauseCandidate] = Field(default_factory=list)
    impacted_files: list[str] = Field(default_factory=list)
    diagnostic_steps: list[str] = Field(default_factory=list)
    fix_plan: FixPlan | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    tools_used: list[ToolResult] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM
    trace_id: str | None = None
    latency_ms: float = 0.0


# --- Change review mode -------------------------------------------------------


class DiffReviewRequest(BaseModel):
    workspace_id: str
    repository_id: str
    diff: str | None = Field(default=None, description="Unified diff text; if omitted, base..head is used")
    base_ref: str | None = None
    head_ref: str | None = None
    title: str | None = None


class DiffReviewResponse(BaseModel):
    summary: str
    impact: str = ""
    risk_level: Confidence = Confidence.MEDIUM
    affected_areas: list[str] = Field(default_factory=list)
    suggested_tests: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    trace_id: str | None = None
    latency_ms: float = 0.0


# Re-export for planner convenience
__all__ = [
    "ChatMessage",
    "ChatRequest",
    "NextAction",
    "ChatResponse",
    "DebugRequest",
    "RootCauseCandidate",
    "FixPlan",
    "DebugResponse",
    "DiffReviewRequest",
    "DiffReviewResponse",
    "ToolCall",
    "ToolResult",
]

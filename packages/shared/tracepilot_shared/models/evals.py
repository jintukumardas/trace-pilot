"""Evaluation and trace-summary models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .common import ChatMode, StrEnum, utcnow


class EvalMetric(StrEnum):
    """The evaluation dimensions TracePilot scores. Maps to Langfuse scores."""

    GROUNDING = "grounding"  # is the answer supported by retrieved evidence?
    RELEVANCE = "relevance"  # does the answer address the question?
    COMPLETENESS = "completeness"  # are the required sections present and useful?
    TOOL_SUCCESS = "tool_success"  # did invoked tools succeed and help?
    RETRIEVAL_QUALITY = "retrieval_quality"  # did retrieval surface relevant chunks?


class EvalScore(BaseModel):
    metric: EvalMetric
    score: float = Field(..., ge=0.0, le=1.0)
    passed: bool = True
    rationale: str = ""


class EvalResult(BaseModel):
    trace_id: str | None = None
    workflow: str = "chat"
    scores: list[EvalScore] = Field(default_factory=list)
    overall: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)

    def average(self) -> float:
        if not self.scores:
            return 0.0
        return sum(s.score for s in self.scores) / len(self.scores)


class EvalExample(BaseModel):
    """A labeled example used in dataset-driven offline evaluation."""

    id: str
    question: str
    mode: ChatMode = ChatMode.ASK
    repository_id: str | None = None
    expected_files: list[str] = Field(default_factory=list, description="Files a good answer should cite")
    expected_keywords: list[str] = Field(default_factory=list)
    notes: str = ""


class EvalRunSummary(BaseModel):
    dataset: str
    n: int
    metric_averages: dict[str, float] = Field(default_factory=dict)
    pass_rate: float = 0.0
    results: list[EvalResult] = Field(default_factory=list)


class TraceSummary(BaseModel):
    """Condensed view of a Langfuse trace for the diagnostics UI."""

    id: str
    name: str
    workflow: str = "chat"
    status: str = "ok"
    latency_ms: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)
    input_preview: str = ""
    output_preview: str = ""
    total_tokens: int | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

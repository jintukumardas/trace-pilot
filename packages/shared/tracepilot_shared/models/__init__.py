"""Re-export every shared domain model from a single namespace."""

from .chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    DebugRequest,
    DebugResponse,
    DiffReviewRequest,
    DiffReviewResponse,
    FixPlan,
    NextAction,
    RootCauseCandidate,
)
from .common import (
    ChatMode,
    ChunkType,
    Confidence,
    IntentType,
    JobStatus,
    RepoStatus,
    StrEnum,
    TimestampedModel,
    utcnow,
)
from .evals import (
    EvalExample,
    EvalMetric,
    EvalResult,
    EvalRunSummary,
    EvalScore,
    TraceSummary,
)
from .retrieval import (
    ChunkMetadata,
    Citation,
    CodeChunk,
    Evidence,
    RetrievalFilter,
    RetrievalQuery,
    RetrievalResult,
    RetrievalStrategy,
)
from .tools import ToolCall, ToolName, ToolResult, ToolSpec
from .workspace import (
    IndexJob,
    IndexRequest,
    Repository,
    RepositoryConnectRequest,
    RepositoryStats,
    Workspace,
    WorkspaceCreate,
)

__all__ = [
    # common
    "ChatMode",
    "ChunkType",
    "Confidence",
    "IntentType",
    "JobStatus",
    "RepoStatus",
    "StrEnum",
    "TimestampedModel",
    "utcnow",
    # workspace
    "Workspace",
    "WorkspaceCreate",
    "Repository",
    "RepositoryConnectRequest",
    "RepositoryStats",
    "IndexRequest",
    "IndexJob",
    # retrieval
    "RetrievalQuery",
    "RetrievalFilter",
    "RetrievalResult",
    "RetrievalStrategy",
    "ChunkMetadata",
    "CodeChunk",
    "Evidence",
    "Citation",
    # tools
    "ToolName",
    "ToolSpec",
    "ToolCall",
    "ToolResult",
    # chat
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "NextAction",
    "DebugRequest",
    "DebugResponse",
    "RootCauseCandidate",
    "FixPlan",
    "DiffReviewRequest",
    "DiffReviewResponse",
    # evals
    "EvalMetric",
    "EvalScore",
    "EvalResult",
    "EvalExample",
    "EvalRunSummary",
    "TraceSummary",
]

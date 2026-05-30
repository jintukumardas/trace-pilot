"""Common enums and base models shared across all TracePilot services."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    """Timezone-aware UTC now. Use everywhere instead of datetime.utcnow()."""
    return datetime.now(UTC)


class StrEnum(str, Enum):
    """String enum that serializes to its value (stable across Python versions)."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class Confidence(StrEnum):
    """Coarse confidence band attached to grounded answers."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ChatMode(StrEnum):
    """Investigation mode selected by the user in the UI."""

    ASK = "ask"
    ONBOARD = "onboard"
    DEBUG = "debug"
    CHANGE_REVIEW = "change_review"
    FIX_PLAN = "fix_plan"


class IntentType(StrEnum):
    """Intent produced by the router node. May differ from the requested mode."""

    QUESTION = "question"
    ONBOARDING = "onboarding"
    DEBUGGING = "debugging"
    CHANGE_REVIEW = "change_review"
    FIX_PLAN = "fix_plan"
    SMALLTALK = "smalltalk"


class ChunkType(StrEnum):
    """Classification of an indexed chunk, used for filtering and prompting."""

    CODE = "code"
    MARKDOWN = "markdown"
    DOC = "doc"
    CONFIG = "config"
    ISSUE = "issue"
    PR = "pr"
    UNKNOWN = "unknown"


class RepoStatus(StrEnum):
    """Lifecycle state of a connected repository."""

    REGISTERED = "registered"
    INDEXING = "indexing"
    INDEXED = "indexed"
    ERROR = "error"


class JobStatus(StrEnum):
    """Lifecycle state of an async indexing job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TimestampedModel(BaseModel):
    """Base model carrying a creation timestamp."""

    created_at: datetime = Field(default_factory=utcnow)

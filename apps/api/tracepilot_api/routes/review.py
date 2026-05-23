"""Review route: change-review of a diff via the orchestrator."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from tracepilot_shared.models import DiffReviewRequest, DiffReviewResponse

from ..core.deps import get_orchestrator

router = APIRouter(tags=["review"])


@router.post("/review/diff", response_model=DiffReviewResponse)
def review_diff(
    body: DiffReviewRequest,
    orchestrator: Any = Depends(get_orchestrator),
) -> DiffReviewResponse:
    """Review a unified diff (or base..head): impact, risk, affected areas, tests."""
    return orchestrator.review(body)

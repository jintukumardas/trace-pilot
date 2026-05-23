"""Investigate route: debug-mode root-cause analysis via the orchestrator."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from tracepilot_shared.models import DebugRequest, DebugResponse

from ..core.deps import get_orchestrator

router = APIRouter(tags=["investigate"])


@router.post("/investigate/debug", response_model=DebugResponse)
def investigate_debug(
    body: DebugRequest,
    orchestrator: Any = Depends(get_orchestrator),
) -> DebugResponse:
    """Run the debug graph: hypotheses, impacted files, diagnostics, fix plan."""
    return orchestrator.debug(body)

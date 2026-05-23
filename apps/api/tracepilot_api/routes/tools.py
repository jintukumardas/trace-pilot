"""Tools route: expose the sandboxed tool specifications to the UI/planner."""

from __future__ import annotations

from fastapi import APIRouter

from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import ToolSpec

router = APIRouter(tags=["tools"])
log = get_logger("api.tools")


@router.get("/tools", response_model=list[ToolSpec])
def list_tools() -> list[ToolSpec]:
    """Return the declarative spec of every allowlisted tool."""
    try:
        from tracepilot_tooling import get_tool_specs  # type: ignore

        return get_tool_specs()
    except Exception as exc:  # pragma: no cover - tooling optional at boot
        log.warning("get_tool_specs failed: %s", exc)
        return []

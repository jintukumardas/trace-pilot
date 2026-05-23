"""HTTP routers for the TracePilot API.

Each module exposes a ``router: APIRouter``. Paths are absolute (no shared
prefix) to match ``docs/INTERNAL_CONTRACTS.md`` exactly. ``ALL_ROUTERS`` is the
ordered list the app factory mounts.
"""

from __future__ import annotations

from . import (
    chat,
    evals,
    health,
    investigate,
    repositories,
    review,
    tools,
    traces,
    workspaces,
)

ALL_ROUTERS = [
    health.router,
    workspaces.router,
    repositories.router,
    chat.router,
    investigate.router,
    review.router,
    traces.router,
    evals.router,
    tools.router,
]

__all__ = ["ALL_ROUTERS"]

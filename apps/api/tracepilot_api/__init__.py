"""tracepilot_api — the FastAPI backend for TracePilot.

The application factory lives in :mod:`tracepilot_api.main`; a module-level
``app = create_app()`` is exposed there for ``uvicorn tracepilot_api.main:app``.

The backend wires together the retrieval, agent, tooling and eval packages and
persists workspace/repository/job metadata in a local SQLite store. Every
sibling package is imported defensively: a missing or half-built dependency
degrades the affected route rather than preventing the server from starting.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]

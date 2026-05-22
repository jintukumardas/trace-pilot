"""Prefixed, sortable-ish identifier helpers.

IDs are ``<prefix>_<hex>`` so they are human-recognizable in logs and traces
(e.g. ``repo_3f9a...``). We avoid leaking the entity type into the DB schema and
keep IDs opaque to clients.
"""

from __future__ import annotations

import uuid

WORKSPACE = "ws"
REPOSITORY = "repo"
JOB = "job"
CHUNK = "chunk"
TRACE = "trace"
EVAL = "eval"
TOOLCALL = "tc"
MESSAGE = "msg"


def new_id(prefix: str) -> str:
    """Return a new opaque, prefixed identifier."""
    return f"{prefix}_{uuid.uuid4().hex}"


def short(value: str, length: int = 8) -> str:
    """Short, log-friendly form of an id or hash."""
    tail = value.split("_")[-1]
    return tail[:length]

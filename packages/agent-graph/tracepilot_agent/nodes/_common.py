"""Shared helpers for graph nodes: prompt context shaping, coercion, defaults.

Nodes are deliberately thin — they open a tracer span, render their template,
call :func:`tracepilot_agent.models.complete`, coerce the result into the shared
models, and return a partial state. The small, boring coercion logic that keeps a
flaky local model from corrupting state lives here so every node stays readable.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tracepilot_shared.models import (
    Citation,
    Confidence,
    Evidence,
    NextAction,
    RetrievalFilter,
    RetrievalQuery,
    ToolName,
)

from ..models import WARNING_KEY

_VALID_CONFIDENCE = {c.value for c in Confidence}
_VALID_STRATEGIES = {"hybrid", "dense", "sparse"}
_VALID_CHUNK_TYPES = {"code", "markdown", "doc", "config", "issue", "pr", "unknown"}
_VALID_TOOLS = {t.value for t in ToolName}


# --------------------------------------------------------------------------- #
# Scalar coercion
# --------------------------------------------------------------------------- #
def as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y"}
    if value is None:
        return default
    return bool(value)


def as_int(value: Any, default: int, *, lo: int | None = None, hi: int | None = None) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def as_str_list(value: Any) -> list[str]:
    """Coerce a value into a clean ``list[str]`` (single strings become one item)."""
    if value is None:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, Iterable):
        out: list[str] = []
        for item in value:
            s = as_str(item).strip()
            if s:
                out.append(s)
        return out
    return []


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, out))


def coerce_confidence(value: Any, default: str = "medium") -> str:
    s = as_str(value).strip().lower()
    return s if s in _VALID_CONFIDENCE else default


# --------------------------------------------------------------------------- #
# Structured coercion
# --------------------------------------------------------------------------- #
def coerce_filter(raw: Any, repository_ids: list[str], branch: str | None) -> RetrievalFilter:
    """Build a :class:`RetrievalFilter`, always scoping to the request's repos/branch."""
    raw = raw if isinstance(raw, dict) else {}
    file_types = as_str_list(raw.get("file_types")) or None
    chunk_raw = as_str_list(raw.get("chunk_types"))
    chunk_types = [c for c in chunk_raw if c in _VALID_CHUNK_TYPES] or None
    path_prefix = raw.get("path_prefix")
    path_prefix = path_prefix.strip() if isinstance(path_prefix, str) and path_prefix.strip() else None
    return RetrievalFilter(
        repository_ids=list(repository_ids) or None,
        branch=branch,
        file_types=file_types,
        path_prefix=path_prefix,
        chunk_types=chunk_types,  # type: ignore[arg-type]  # pydantic coerces str->ChunkType
    )


def coerce_query(
    raw: Any, *, repository_ids: list[str], branch: str | None, default_top_k: int
) -> RetrievalQuery | None:
    """Coerce one planned-query dict into a :class:`RetrievalQuery`, or ``None`` if empty."""
    if not isinstance(raw, dict):
        return None
    text = as_str(raw.get("query")).strip()
    if not text:
        return None
    strategy = as_str(raw.get("strategy"), "hybrid").strip().lower()
    if strategy not in _VALID_STRATEGIES:
        strategy = "hybrid"
    top_k = as_int(raw.get("top_k"), default_top_k, lo=1, hi=50)
    flt = coerce_filter(raw.get("filter"), repository_ids, branch)
    return RetrievalQuery(query=text, strategy=strategy, top_k=top_k, filter=flt)  # type: ignore[arg-type]


def coerce_next_actions(raw: Any, limit: int = 5) -> list[NextAction]:
    actions: list[NextAction] = []
    if not isinstance(raw, list):
        return actions
    for item in raw[:limit]:
        if isinstance(item, dict):
            title = as_str(item.get("title")).strip()
            if not title:
                continue
            actions.append(
                NextAction(
                    title=title,
                    detail=as_str(item.get("detail")).strip(),
                    rationale=as_str(item.get("rationale")).strip(),
                )
            )
        elif isinstance(item, str) and item.strip():
            actions.append(NextAction(title=item.strip()))
    return actions


def coerce_tool_name(value: Any) -> str | None:
    s = as_str(value).strip().lower()
    return s if s in _VALID_TOOLS else None


# --------------------------------------------------------------------------- #
# Prompt context shaping
# --------------------------------------------------------------------------- #
def evidence_view(citations: list[Citation]) -> list[dict[str, Any]]:
    """Render evidence for the ``_macros.evidence_block`` macro.

    The macro reads ``.index .repo .file_path .start_line .end_line .snippet``.
    :class:`Citation` already carries 1-based ``index`` and a trimmed ``snippet``
    aligned with ``pack_context``, so we map it straight through. Using the
    citations (not raw evidence) guarantees the [n] markers the model sees match
    the citations returned to the user.
    """
    return [
        {
            "index": c.index,
            "repo": c.repository,
            "file_path": c.file_path,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "snippet": c.snippet,
        }
        for c in citations
    ]


def warn_if_degraded(parsed: Any, node: str, state_warnings: list[str]) -> None:
    """Append a user-facing warning when a completion came back degraded."""
    if isinstance(parsed, dict) and WARNING_KEY in parsed:
        state_warnings.append(f"{node}: {parsed[WARNING_KEY]}")


def merge_evidence(groups: list[list[Evidence]]) -> list[Evidence]:
    """Merge per-query evidence lists, dedupe by (repo,file,lines), keep best score.

    Re-ranks the survivors by descending fused score and re-stamps ``rank`` so the
    1-based citation order is stable and deterministic.
    """
    best: dict[tuple[str, str, int, int], Evidence] = {}
    for group in groups:
        for ev in group:
            md = ev.metadata
            key = (md.repository_id, md.file_path, md.start_line, md.end_line)
            current = best.get(key)
            if current is None or ev.score > current.score:
                best[key] = ev
    merged = sorted(best.values(), key=lambda e: e.score, reverse=True)
    for rank, ev in enumerate(merged):
        ev.rank = rank
    return merged

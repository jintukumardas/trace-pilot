"""Turn ranked ``Evidence`` into user-facing ``Citation`` objects and a packed,
budget-bounded context block for the LLM prompt.

``build_citations`` dedupes overlapping evidence and assigns stable 1-based markers.
``pack_context`` renders a numbered evidence block with ``file_path:line`` headers,
trimmed to ``max_chars`` so it never blows the model's context window.
"""

from __future__ import annotations

from tracepilot_shared.models import Citation, Evidence

# A small header costs ~0 vs. the body; reserve a little headroom under max_chars.
_BUDGET_SLACK = 200


def _dedupe_key(ev: Evidence) -> tuple[str, str, int, int]:
    """Identity for dedup: same repo + file + overlapping line span -> duplicate."""
    md = ev.metadata
    return (md.repository_id, md.file_path, md.start_line, md.end_line)


def _trim_snippet(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text.strip("\n")
    head = lines[:max_lines]
    return "\n".join(head).rstrip() + f"\n… (+{len(lines) - max_lines} more lines)"


def build_citations(evidence: list[Evidence], max_snippet_lines: int = 22) -> list[Citation]:
    """Dedupe evidence, assign 1-based indices, trim snippets to ``max_snippet_lines``.

    Order is preserved from the (already ranked) evidence list. Overlapping chunks
    from the same file are collapsed to the highest-ranked occurrence.
    """
    seen: set[tuple[str, str, int, int]] = set()
    citations: list[Citation] = []
    index = 1
    for ev in evidence:
        key = _dedupe_key(ev)
        if key in seen:
            continue
        seen.add(key)
        md = ev.metadata
        citations.append(
            Citation(
                index=index,
                repository=md.repo_name or md.repository_id,
                file_path=md.file_path,
                start_line=md.start_line,
                end_line=md.end_line,
                snippet=_trim_snippet(ev.text, max_snippet_lines),
                score=round(float(ev.score), 4),
            )
        )
        index += 1
    return citations


def _header(ev: Evidence, marker: int) -> str:
    md = ev.metadata
    loc = f"{md.file_path}:{md.start_line}-{md.end_line}"
    sym = f" ({md.symbol})" if md.symbol else ""
    repo = md.repo_name or md.repository_id
    return f"[{marker}] {repo} · {loc}{sym}"


def pack_context(evidence: list[Evidence], max_chars: int = 16000) -> str:
    """Render a numbered, budget-bounded evidence block for the LLM prompt.

    Each block is ``[n] repo · path:start-end\\n<body>`` and markers align with the
    1-based indices that ``build_citations`` produces, so the model can cite ``[n]``.
    Stops adding blocks once ``max_chars`` is reached; the last block may be trimmed.
    """
    if not evidence:
        return "(no evidence retrieved)"

    budget = max(0, max_chars - _BUDGET_SLACK)
    parts: list[str] = []
    used = 0
    marker = 1
    for ev in evidence:
        header = _header(ev, marker)
        body = ev.text.strip("\n")
        block = f"{header}\n{body}"
        block_len = len(block) + 2  # account for the joining blank line

        if used + block_len > budget:
            remaining = budget - used - len(header) - 4
            if remaining > 80:  # only bother if a meaningful snippet fits
                truncated = body[:remaining].rstrip()
                parts.append(f"{header}\n{truncated}\n… (truncated)")
                marker += 1
            break

        parts.append(block)
        used += block_len
        marker += 1

    return "\n\n".join(parts) if parts else "(no evidence retrieved)"

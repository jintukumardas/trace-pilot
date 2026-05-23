"""Deterministic, pure-heuristic eval metrics.

Each public function takes a :class:`ChatRequest` + :class:`ChatResponse` (and a
small bag of optional offline labels) and returns one :class:`EvalScore` in the
``0..1`` range with a ``passed`` boolean derived from a fixed threshold. The
functions are intentionally **pure and deterministic** — no network, no model,
no clock — so a dataset run is reproducible and the online path is cheap. The
optional LLM-as-judge lives in :mod:`tracepilot_evals.judge` and only *replaces*
the grounding/relevance heuristics when explicitly enabled.

Design choices
--------------
* All scoring is token/snippet overlap with light structural checks. Small and
  boring beats clever here: the metrics must agree with themselves every run.
* "Offline mode" simply means the caller passed dataset labels
  (``expected_files`` / ``expected_keywords``); the same functions serve the
  online path with those arguments left empty.
* Thresholds are module constants so the API/UI can document them and so a
  ``passed`` flag is stable across callers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from tracepilot_shared.models import (
    ChatRequest,
    ChatResponse,
    Citation,
    Confidence,
    EvalMetric,
    EvalScore,
    Evidence,
)

# --------------------------------------------------------------------------- #
# Pass thresholds (a score >= threshold => passed). Tuned to be forgiving of a
# local model's verbosity but to fail clearly-broken answers.
# --------------------------------------------------------------------------- #
THRESHOLDS: dict[EvalMetric, float] = {
    EvalMetric.GROUNDING: 0.45,
    EvalMetric.RELEVANCE: 0.40,
    EvalMetric.COMPLETENESS: 0.75,
    EvalMetric.TOOL_SUCCESS: 0.99,  # any tool failure should be visible
    EvalMetric.RETRIEVAL_QUALITY: 0.40,
}

# Evidence whose fused score clears this is considered "good" retrieval signal.
_RETRIEVAL_SCORE_FLOOR = 0.25

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
# Generic English/code stopwords removed before overlap scoring so the signal is
# carried by the content terms (symbols, paths, domain nouns) not the glue words.
_STOPWORDS = frozenset(
    """
    a an and are as at be by do does for from how if in into is it its of on or
    that the their then there these this to use used uses using was were what
    when where which who why will with would you your can could should may might
    not no yes we i they he she them his her our us me my also been being have has
    had this those some any each per via about above below over under between
    """.split()
)


# --------------------------------------------------------------------------- #
# Tokenization / overlap primitives (pure)
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> list[str]:
    """Lowercase word/identifier tokens, e.g. ``getUser`` -> ``['getuser']``."""
    if not text:
        return []
    return [t.lower() for t in _WORD_RE.findall(text)]


def _content_terms(text: str, *, min_len: int = 3) -> set[str]:
    """Distinct, non-stopword tokens of length >= ``min_len``.

    Short tokens (e.g. ``id``, ``db``) are dropped to avoid spurious overlap, but
    purely-numeric tokens and snake/dotted identifiers survive tokenization as
    their parts, which is what we want for code grounding.
    """
    return {t for t in _tokens(text) if len(t) >= min_len and t not in _STOPWORDS}


def _overlap_ratio(claim: set[str], support: set[str]) -> float:
    """Fraction of ``claim`` terms also present in ``support`` (0..1)."""
    if not claim:
        return 0.0
    return len(claim & support) / len(claim)


def _basename_terms(file_path: str) -> set[str]:
    """Terms derived from a file path: the basename, stem, and split identifier parts."""
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    parts = re.split(r"[._\-/]", stem)
    out = {name.lower(), stem.lower()}
    out.update(p.lower() for p in parts if len(p) >= 3)
    return out


# --------------------------------------------------------------------------- #
# Evidence / citation views
# --------------------------------------------------------------------------- #
def _evidence_terms(evidence: Iterable[Evidence]) -> set[str]:
    terms: set[str] = set()
    for ev in evidence:
        terms |= _content_terms(ev.text)
        md = ev.metadata
        terms |= _basename_terms(md.file_path)
        if md.symbol:
            terms.add(md.symbol.lower())
    return terms


def _citation_terms(citations: Iterable[Citation]) -> set[str]:
    terms: set[str] = set()
    for c in citations:
        terms |= _content_terms(c.snippet)
        terms |= _basename_terms(c.file_path)
    return terms


def _cited_files(citations: Iterable[Citation]) -> set[str]:
    return {c.file_path.replace("\\", "/") for c in citations}


def _file_paths_mentioned(answer: str) -> set[str]:
    """Heuristically extract file-path-looking tokens the answer names.

    Matches ``a/b/c.py`` and bare ``thing.ext`` references so we can detect when an
    answer asserts a file that no citation/evidence supports (uncited file penalty).
    """
    paths: set[str] = set()
    for m in re.finditer(r"[`'\"(]?([A-Za-z0-9_./\-]+\.[A-Za-z]{1,5})", answer):
        token = m.group(1).strip("`'\"().,")
        # Require it to look like a real path/file, not a sentence ending or a
        # version like "3.11".
        if "/" in token or re.search(r"\.[A-Za-z]{1,5}$", token):
            if not re.fullmatch(r"\d+\.\d+", token):
                paths.add(token.replace("\\", "/"))
    return paths


def _make(metric: EvalMetric, score: float, rationale: str) -> EvalScore:
    score = max(0.0, min(1.0, round(float(score), 4)))
    return EvalScore(
        metric=metric,
        score=score,
        passed=score >= THRESHOLDS[metric],
        rationale=rationale,
    )


# --------------------------------------------------------------------------- #
# Metric: grounding
# --------------------------------------------------------------------------- #
def grounding(
    req: ChatRequest,
    resp: ChatResponse,
    *,
    expected_files: list[str] | None = None,
) -> EvalScore:
    """How well the answer's claims are supported by the cited evidence.

    Heuristic: term overlap between the answer and the union of cited
    evidence/citation snippets, with a penalty for naming files that appear in no
    citation/evidence (fabricated-path detection). With no answer or no evidence,
    grounding is 0 — an ungrounded answer should not pass.
    """
    answer = resp.answer or ""
    if not answer.strip():
        return _make(EvalMetric.GROUNDING, 0.0, "empty answer")

    support = _evidence_terms(resp.evidence) | _citation_terms(resp.citations)
    if not support:
        return _make(
            EvalMetric.GROUNDING,
            0.0,
            "no evidence or citations to ground the answer against",
        )

    claim_terms = _content_terms(answer)
    base = _overlap_ratio(claim_terms, support)

    # Reward inline [n] markers that point at real citations: a model that cites
    # its sources is demonstrably more grounded than one that doesn't.
    valid_markers = sum(1 for c in resp.citations if f"[{c.index}]" in answer)
    marker_bonus = 0.0
    if resp.citations:
        marker_bonus = 0.15 * (valid_markers / len(resp.citations))

    # Penalize files asserted in prose that aren't backed by any citation/evidence.
    cited_files = _cited_files(resp.citations) | {
        ev.metadata.file_path.replace("\\", "/") for ev in resp.evidence
    }
    mentioned = _file_paths_mentioned(answer)
    uncited = {p for p in mentioned if p not in cited_files and not _path_is_cited(p, cited_files)}
    penalty = min(0.4, 0.1 * len(uncited))

    score = base + marker_bonus - penalty
    rationale = (
        f"term-overlap={base:.2f}, markers={valid_markers}/{len(resp.citations)}, "
        f"uncited_files={len(uncited)}"
    )
    return _make(EvalMetric.GROUNDING, score, rationale)


def _path_is_cited(path: str, cited: set[str]) -> bool:
    """True if ``path`` matches a cited file by suffix/basename (tolerate a/ b/ prefixes)."""
    base = path.rsplit("/", 1)[-1]
    for c in cited:
        if c == path or c.endswith("/" + path) or path.endswith("/" + c):
            return True
        if c.rsplit("/", 1)[-1] == base:
            return True
    return False


# --------------------------------------------------------------------------- #
# Metric: relevance
# --------------------------------------------------------------------------- #
def relevance(
    req: ChatRequest,
    resp: ChatResponse,
    *,
    expected_keywords: list[str] | None = None,
) -> EvalScore:
    """Whether the answer addresses the question.

    Heuristic: overlap of the question's content terms with the answer, blended
    with expected-keyword coverage (offline) and a length-sanity factor that
    dampens trivially short or runaway answers.
    """
    answer = resp.answer or ""
    if not answer.strip():
        return _make(EvalMetric.RELEVANCE, 0.0, "empty answer")

    q_terms = _content_terms(req.message)
    a_terms = _content_terms(answer)
    qa_overlap = _overlap_ratio(q_terms, a_terms) if q_terms else 0.5

    # Expected-keyword coverage (offline). Each keyword may be a phrase; we count
    # it as covered if all its content terms appear in the answer.
    kw_cov: float | None = None
    if expected_keywords:
        covered = sum(1 for kw in expected_keywords if _keyword_present(kw, a_terms))
        kw_cov = covered / len(expected_keywords)

    length_factor = _length_sanity(answer)

    if kw_cov is not None:
        core = 0.5 * qa_overlap + 0.5 * kw_cov
        rationale = f"qa_overlap={qa_overlap:.2f}, keyword_cov={kw_cov:.2f}, len={length_factor:.2f}"
    else:
        core = qa_overlap
        rationale = f"qa_overlap={qa_overlap:.2f}, len={length_factor:.2f}"

    return _make(EvalMetric.RELEVANCE, core * length_factor, rationale)


def _keyword_present(keyword: str, answer_terms: set[str]) -> bool:
    terms = _content_terms(keyword, min_len=2)
    if not terms:
        # All-stopword/short keyword: fall back to substring on the raw token set.
        return keyword.lower() in answer_terms
    return terms.issubset(answer_terms)


def _length_sanity(answer: str) -> float:
    """A multiplier in (0,1]; full credit for a reasonable answer length.

    Penalizes one-liners (likely non-answers) and extreme walls of text (likely
    unfocused). Deterministic piecewise-linear shape on the token count.
    """
    n = len(_tokens(answer))
    if n <= 5:
        return 0.3
    if n < 20:
        return 0.7 + 0.3 * (n - 5) / 15.0  # 0.7 -> 1.0 across 5..20 tokens
    if n <= 600:
        return 1.0
    if n <= 1200:
        return 1.0 - 0.3 * (n - 600) / 600.0  # taper to 0.7 for very long answers
    return 0.7


# --------------------------------------------------------------------------- #
# Metric: completeness
# --------------------------------------------------------------------------- #
def completeness(req: ChatRequest, resp: ChatResponse) -> EvalScore:
    """Are the required answer sections present and useful?

    Required: a non-empty answer, >=1 citation, >=1 next action, and a confidence
    that was actually set (any valid band). Each contributes an equal share.
    """
    checks = {
        "answer": bool((resp.answer or "").strip()),
        "citation": len(resp.citations) >= 1,
        "next_action": len(resp.next_actions) >= 1,
        "confidence": isinstance(resp.confidence, Confidence),
    }
    score = sum(1 for ok in checks.values() if ok) / len(checks)
    missing = [k for k, ok in checks.items() if not ok]
    rationale = "all sections present" if not missing else f"missing: {', '.join(missing)}"
    return _make(EvalMetric.COMPLETENESS, score, rationale)


# --------------------------------------------------------------------------- #
# Metric: tool_success
# --------------------------------------------------------------------------- #
def tool_success(req: ChatRequest, resp: ChatResponse) -> EvalScore:
    """Fraction of invoked tools that succeeded. 1.0 when no tools were needed."""
    tools = resp.tools_used
    if not tools:
        return _make(EvalMetric.TOOL_SUCCESS, 1.0, "no tools invoked")
    succeeded = sum(1 for t in tools if t.ok)
    score = succeeded / len(tools)
    failed = [t.tool.value for t in tools if not t.ok]
    rationale = f"{succeeded}/{len(tools)} tools succeeded"
    if failed:
        rationale += f"; failed: {', '.join(sorted(set(failed)))}"
    return _make(EvalMetric.TOOL_SUCCESS, score, rationale)


# --------------------------------------------------------------------------- #
# Metric: retrieval_quality
# --------------------------------------------------------------------------- #
def retrieval_quality(
    req: ChatRequest,
    resp: ChatResponse,
    *,
    expected_files: list[str] | None = None,
) -> EvalScore:
    """Did retrieval surface relevant chunks?

    Online heuristic: evidence present + mean fused score above a floor, scaled by
    a "have we got enough breadth" factor. Offline (``expected_files`` provided)
    additionally rewards citing the labeled files.
    """
    evidence = resp.evidence
    if not evidence:
        return _make(EvalMetric.RETRIEVAL_QUALITY, 0.0, "no evidence retrieved")

    scores = [float(ev.score) for ev in evidence]
    mean_score = sum(scores) / len(scores)
    # Map mean fused score to 0..1 against the floor (scores are not normalized to
    # a fixed range across strategies, so we treat the floor as "ok" -> ~0.5).
    score_component = min(1.0, mean_score / (_RETRIEVAL_SCORE_FLOOR * 2.0)) if mean_score > 0 else 0.0

    # Breadth: a handful of distinct files is healthier than one repeated file.
    distinct_files = len({ev.metadata.file_path for ev in evidence})
    breadth = min(1.0, distinct_files / 3.0)

    base = 0.7 * score_component + 0.3 * breadth

    if expected_files:
        cited = _cited_files(resp.citations) | {ev.metadata.file_path.replace("\\", "/") for ev in evidence}
        hit = sum(1 for f in expected_files if _path_is_cited(f.replace("\\", "/"), cited))
        coverage = hit / len(expected_files)
        # Blend: half retrieval health, half label coverage in offline mode.
        final = 0.5 * base + 0.5 * coverage
        rationale = (
            f"mean_score={mean_score:.3f}, files={distinct_files}, expected_hit={hit}/{len(expected_files)}"
        )
        return _make(EvalMetric.RETRIEVAL_QUALITY, final, rationale)

    rationale = f"mean_score={mean_score:.3f}, distinct_files={distinct_files}"
    return _make(EvalMetric.RETRIEVAL_QUALITY, base, rationale)


# --------------------------------------------------------------------------- #
# Registry helpers
# --------------------------------------------------------------------------- #
def all_metrics(
    req: ChatRequest,
    resp: ChatResponse,
    *,
    expected_files: list[str] | None = None,
    expected_keywords: list[str] | None = None,
) -> list[EvalScore]:
    """Run every heuristic metric and return the scores in a stable order.

    ``expected_files`` / ``expected_keywords`` switch the relevant metrics into
    their offline (label-aware) variants; pass ``None`` for the online path.
    """
    return [
        grounding(req, resp, expected_files=expected_files),
        relevance(req, resp, expected_keywords=expected_keywords),
        completeness(req, resp),
        tool_success(req, resp),
        retrieval_quality(req, resp, expected_files=expected_files),
    ]


__all__ = [
    "THRESHOLDS",
    "grounding",
    "relevance",
    "completeness",
    "tool_success",
    "retrieval_quality",
    "all_metrics",
]

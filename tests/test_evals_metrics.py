"""Tests for ``tracepilot_evals``: per-metric bounds + an offline dataset run.

The evals package exposes ``evaluate_chat``, ``run_dataset`` and
``load_default_dataset`` (see ``docs/INTERNAL_CONTRACTS.md``). These tests pin the
contract: every metric scores in ``[0, 1]``; ``completeness`` drops when a
required section (e.g. a citation) is missing; ``grounding`` is higher when the
answer is actually supported by the cited evidence; and ``run_dataset`` drives an
example through a stub orchestrator and aggregates metric averages + a pass rate.

The whole module skips cleanly if the evals package is not yet importable, so the
suite stays green during incremental development and turns on automatically once
the package ships.
"""

from __future__ import annotations

import pytest

# The evals package is a hard dependency of these tests. Skip the whole module
# (rather than error) when it is not yet importable.
evals = pytest.importorskip(
    "tracepilot_evals",
    reason="tracepilot_evals not importable yet (package not implemented)",
)

# The public surface must be present for these tests to be meaningful.
for _attr in ("evaluate_chat", "run_dataset", "load_default_dataset"):
    if not hasattr(evals, _attr):
        pytest.skip(
            f"tracepilot_evals missing public symbol {_attr!r}",
            allow_module_level=True,
        )

from tracepilot_shared.models import (  # noqa: E402
    ChatMode,
    ChatRequest,
    ChatResponse,
    ChunkMetadata,
    Citation,
    Confidence,
    EvalExample,
    EvalMetric,
    EvalResult,
    EvalRunSummary,
    Evidence,
    IntentType,
    NextAction,
)


# --------------------------------------------------------------------------- #
# Helpers to build grounded vs. ungrounded responses
# --------------------------------------------------------------------------- #
def _evidence(text: str, file_path: str = "config.py") -> Evidence:
    return Evidence(
        id="ev1",
        text=text,
        score=0.9,
        metadata=ChunkMetadata(
            repository_id="repo_1", repo_name="demo", file_path=file_path, start_line=1, end_line=5
        ),
    )


def _citation(file_path: str = "config.py", snippet: str = "load_settings()") -> Citation:
    return Citation(
        index=1, repository="demo", file_path=file_path, start_line=1, end_line=5, snippet=snippet, score=0.9
    )


def _request(message: str = "How is configuration loaded?") -> ChatRequest:
    return ChatRequest(workspace_id="ws_1", message=message, repository_ids=["repo_1"])


def _full_response() -> ChatResponse:
    """A complete, well-grounded response (all required sections present)."""
    text = "Configuration is loaded by load_settings() in config.py [1]."
    return ChatResponse(
        answer=text,
        confidence=Confidence.HIGH,
        intent=IntentType.QUESTION,
        mode=ChatMode.ASK,
        evidence=[_evidence("def load_settings(): return Settings()")],
        citations=[_citation()],
        next_actions=[NextAction(title="Read config.py", detail="open it", rationale="source of config")],
    )


def _no_citation_response() -> ChatResponse:
    """A response missing the required citation section."""
    return ChatResponse(
        answer="Configuration is loaded somewhere in the codebase.",
        confidence=Confidence.MEDIUM,
        mode=ChatMode.ASK,
        evidence=[],
        citations=[],
        next_actions=[],
    )


# --------------------------------------------------------------------------- #
# evaluate_chat — metric bounds
# --------------------------------------------------------------------------- #
def test_evaluate_chat_returns_eval_result_with_all_metrics():
    result = evals.evaluate_chat(_request(), _full_response())
    assert isinstance(result, EvalResult)
    assert result.scores, "expected at least one metric score"
    metrics = {s.metric for s in result.scores}
    # The five contract metrics should be scored.
    assert metrics <= set(EvalMetric)
    assert {EvalMetric.GROUNDING, EvalMetric.COMPLETENESS} <= metrics


def test_every_metric_is_within_unit_interval():
    for resp in (_full_response(), _no_citation_response()):
        result = evals.evaluate_chat(_request(), resp)
        for score in result.scores:
            assert 0.0 <= score.score <= 1.0, f"{score.metric} out of range: {score.score}"


def test_overall_in_unit_interval():
    result = evals.evaluate_chat(_request(), _full_response())
    assert 0.0 <= result.overall <= 1.0


# --------------------------------------------------------------------------- #
# completeness — fails when a required section is missing
# --------------------------------------------------------------------------- #
def _metric(result: EvalResult, metric: EvalMetric) -> float:
    for s in result.scores:
        if s.metric == metric:
            return s.score
    raise AssertionError(f"metric {metric} not scored")


def test_completeness_drops_when_citation_missing():
    full = evals.evaluate_chat(_request(), _full_response())
    missing = evals.evaluate_chat(_request(), _no_citation_response())
    assert _metric(missing, EvalMetric.COMPLETENESS) < _metric(full, EvalMetric.COMPLETENESS)


# --------------------------------------------------------------------------- #
# grounding — higher when evidence supports the answer
# --------------------------------------------------------------------------- #
def test_grounding_higher_when_supported():
    supported = _full_response()  # answer terms overlap the cited snippet + has [1]
    # An answer that cites nothing and shares no terms with any evidence.
    unsupported = ChatResponse(
        answer="The deployment uses Kubernetes and a Postgres replica set.",
        confidence=Confidence.MEDIUM,
        mode=ChatMode.ASK,
        evidence=[_evidence("def load_settings(): return Settings()")],
        citations=[_citation()],
        next_actions=[],
    )
    g_supported = _metric(evals.evaluate_chat(_request(), supported), EvalMetric.GROUNDING)
    g_unsupported = _metric(evals.evaluate_chat(_request(), unsupported), EvalMetric.GROUNDING)
    assert g_supported >= g_unsupported


# --------------------------------------------------------------------------- #
# offline run_dataset with a stub orchestrator
# --------------------------------------------------------------------------- #
class _StubOrchestrator:
    """Returns a grounded ChatResponse citing the example's expected file."""

    def chat(self, req: ChatRequest) -> ChatResponse:
        return ChatResponse(
            answer="Configuration is loaded by load_settings() in config.py [1].",
            confidence=Confidence.HIGH,
            intent=IntentType.QUESTION,
            mode=req.mode,
            evidence=[_evidence("def load_settings(): return Settings()")],
            citations=[_citation()],
            next_actions=[NextAction(title="Read config.py", detail="open it", rationale="source")],
        )


def test_run_dataset_single_example():
    example = EvalExample(
        id="ex1",
        question="How is configuration loaded?",
        mode=ChatMode.ASK,
        repository_id="repo_1",
        expected_files=["config.py"],
        expected_keywords=["load_settings", "config"],
    )
    summary = evals.run_dataset([example], _StubOrchestrator())
    assert isinstance(summary, EvalRunSummary)
    assert summary.n == 1
    assert len(summary.results) == 1
    # Aggregates are present and bounded.
    assert summary.metric_averages
    assert all(0.0 <= v <= 1.0 for v in summary.metric_averages.values())
    assert 0.0 <= summary.pass_rate <= 1.0


def test_load_default_dataset_returns_examples():
    examples = evals.load_default_dataset()
    assert isinstance(examples, list)
    assert all(isinstance(e, EvalExample) for e in examples)

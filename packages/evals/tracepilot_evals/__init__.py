"""tracepilot_evals — online + offline evaluation for TracePilot.

Public surface (see ``docs/INTERNAL_CONTRACTS.md``)::

    from tracepilot_evals import evaluate_chat, run_dataset, load_default_dataset

    evaluate_chat(req, resp) -> EvalResult         # online: heuristic (+ optional LLM judge)
    run_dataset(examples, orchestrator) -> EvalRunSummary   # offline, label-aware
    load_default_dataset() -> list[EvalExample]    # bundled golden set

Five metrics (``tracepilot_evals.metrics``) each return an :class:`EvalScore` in
``0..1`` with a threshold-derived ``passed`` flag: ``grounding``, ``relevance``,
``completeness``, ``tool_success``, ``retrieval_quality``. Grounding/relevance can
be upgraded to an LLM-as-judge (``tracepilot_evals.judge``) when settings allow,
falling back to the deterministic heuristics on any error. Online scores are
pushed to Langfuse (when a ``trace_id`` is present) and persisted to Redis for the
``GET /evals`` dashboard; offline runs aggregate per-metric averages + pass rate.
"""

from __future__ import annotations

from .datasets import available_datasets, load_dataset, load_default_dataset
from .offline import run_dataset
from .online import evaluate_chat, recent_evals

__version__ = "0.1.0"

__all__ = [
    "evaluate_chat",
    "run_dataset",
    "load_default_dataset",
    # secondary, used by the API/CLI
    "load_dataset",
    "available_datasets",
    "recent_evals",
    "__version__",
]

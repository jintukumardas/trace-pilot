"""``python -m tracepilot_evals`` — run the default dataset against live services.

This is the offline-eval CLI. It builds a real :class:`Orchestrator` (embedder +
Qdrant store + retriever + a metadata-backed repo locator), runs the bundled
``default`` dataset through it, and prints a per-example and per-metric table.

It is deliberately defensive about its dependencies: the retrieval stack, the
agent graph, the API metadata store and the local model are all optional at
import/connect time. If any required piece is missing we print a clear, actionable
message and exit non-zero instead of dumping a traceback — the same "fail soft,
explain why" posture as the rest of the platform.

Usage::

    python -m tracepilot_evals                 # default dataset, live services
    python -m tracepilot_evals --dataset default
    python -m tracepilot_evals --no-judge      # heuristics only (no LLM judge)
    python -m tracepilot_evals --list          # list bundled datasets
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from tracepilot_shared.config import get_settings
from tracepilot_shared.logging import configure_logging, get_logger
from tracepilot_shared.models import EvalExample, EvalRunSummary

from .datasets import available_datasets, load_dataset
from .offline import run_dataset

log = get_logger("evals.cli")


class _SetupError(RuntimeError):
    """Raised when live services needed to build the orchestrator are unavailable."""


# --------------------------------------------------------------------------- #
# Live-service wiring (mirrors apps/api main.py, guarded for clear errors)
# --------------------------------------------------------------------------- #
def _build_orchestrator(settings: Any) -> Any:
    """Construct an Orchestrator against live retrieval + agent services.

    Raises :class:`_SetupError` with a human-readable cause if a required package
    or backend is missing, so ``main`` can print it cleanly.
    """
    # 1) Retrieval stack (embedder + Qdrant + retriever).
    try:
        from tracepilot_retrieval import Retriever, get_embedder, get_qdrant_store
    except Exception as exc:
        raise _SetupError(
            f"tracepilot_retrieval is not importable ({exc}). Install the retrieval package and its extras."
        ) from exc

    try:
        embedder = get_embedder(settings)
        store = get_qdrant_store(settings)
        retriever = Retriever(store, embedder, settings)
    except Exception as exc:
        raise _SetupError(
            f"could not initialize the retrieval stack ({exc}). "
            f"Is Qdrant reachable at {settings.qdrant_url} and the embedder available?"
        ) from exc

    # 2) Repo locator. Prefer the API's metadata-backed locator; if the API
    #    package isn't on the path, fall back to a retrieval-only locator so the
    #    eval still runs (tools that need a working tree simply no-op).
    repo_locator = _build_repo_locator(settings)

    # 3) Agent orchestrator.
    try:
        from tracepilot_agent import Orchestrator
    except Exception as exc:
        raise _SetupError(
            f"tracepilot_agent is not importable ({exc}). Install the agent-graph package."
        ) from exc

    try:
        return Orchestrator(retriever, repo_locator, settings)
    except Exception as exc:
        raise _SetupError(f"could not build the Orchestrator ({exc}).") from exc


def _build_repo_locator(settings: Any) -> Any:
    """Return a RepoLocator: API-backed if available, else a no-disk stub."""
    try:
        from tracepilot_api.core.runtime import ApiRepoLocator
        from tracepilot_api.core.store import MetadataStore

        url = settings.database_url
        if url.startswith("sqlite:///"):
            db_path = url[len("sqlite:///") :]
        elif url.startswith("sqlite://"):
            db_path = url[len("sqlite://") :]
        else:
            from pathlib import Path

            db_path = str(Path(settings.data_dir) / "tracepilot.db")
        return ApiRepoLocator(MetadataStore(db_path), settings)
    except Exception as exc:  # API package optional outside the monorepo app
        log.info("API repo locator unavailable (%s); using retrieval-only locator", exc)
        return _NullRepoLocator()


class _NullRepoLocator:
    """RepoLocator that never resolves a working tree (retrieval-only fallback)."""

    def resolve(self, repository_id: str) -> str | None:
        return None

    def name(self, repository_id: str) -> str:
        return repository_id


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
_METRIC_ORDER = ["grounding", "relevance", "completeness", "tool_success", "retrieval_quality"]
_METRIC_HEAD = {
    "grounding": "ground",
    "relevance": "relev",
    "completeness": "compl",
    "tool_success": "tools",
    "retrieval_quality": "retr",
}


def _print_table(examples: list[EvalExample], summary: EvalRunSummary) -> None:
    """Print a fixed-width per-example table followed by the aggregate summary."""
    by_id = {ex.id: ex for ex in examples}
    id_w = max([len("example")] + [len(ex.id) for ex in examples] + [10])

    header = f"{'example':<{id_w}}  " + "  ".join(f"{_METRIC_HEAD[m]:>6}" for m in _METRIC_ORDER)
    header += f"  {'overall':>7}  {'pass':>4}"
    line = "-" * len(header)

    print()
    print(f"TracePilot offline eval — dataset='{summary.dataset}'  n={summary.n}")
    print(line)
    print(header)
    print(line)

    # results align 1:1 with examples (run_dataset preserves order).
    for ex, res in zip(examples, summary.results):
        scores = {s.metric.value: s for s in res.scores}
        cells = []
        for m in _METRIC_ORDER:
            s = scores.get(m)
            cells.append(f"{s.score:>6.2f}" if s else f"{'-':>6}")
        passed = bool(res.scores) and all(s.passed for s in res.scores)
        label = by_id.get(ex.id).id if ex.id in by_id else ex.id
        row = f"{label:<{id_w}}  " + "  ".join(cells)
        row += f"  {res.overall:>7.2f}  {'Y' if passed else 'n':>4}"
        print(row)

    print(line)
    avgs = summary.metric_averages
    avg_cells = "  ".join(f"{avgs.get(m, 0.0):>6.2f}" for m in _METRIC_ORDER)
    print(f"{'AVERAGE':<{id_w}}  " + avg_cells + f"  {avgs.get('overall', 0.0):>7.2f}")
    print(line)
    print(f"pass_rate = {summary.pass_rate:.1%}   (an example passes when every metric clears its threshold)")
    print()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tracepilot_evals",
        description="Run the offline evaluation dataset against live TracePilot services.",
    )
    parser.add_argument("--dataset", default="default", help="bundled dataset name (default: default)")
    parser.add_argument("--list", action="store_true", help="list bundled datasets and exit")
    parser.add_argument("--no-judge", action="store_true", help="disable the LLM judge (heuristics only)")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    if args.list:
        for name in available_datasets():
            print(name)
        return 0

    examples = load_dataset(args.dataset)
    if not examples:
        print(
            f"error: dataset '{args.dataset}' is empty or not found. "
            f"available: {', '.join(available_datasets()) or '(none)'}",
            file=sys.stderr,
        )
        return 2

    try:
        orchestrator = _build_orchestrator(settings)
    except _SetupError as exc:
        print(f"error: cannot run evals — {exc}", file=sys.stderr)
        print(
            "hint: ensure Qdrant/Ollama are up and the retrieval+agent packages are installed.",
            file=sys.stderr,
        )
        return 3

    print(
        f"running {len(examples)} examples through the orchestrator "
        f"(judge={'off' if args.no_judge else 'on'})…",
        file=sys.stderr,
    )
    summary = run_dataset(
        examples,
        orchestrator,
        dataset=args.dataset,
        settings=settings,
        use_judge=not args.no_judge,
    )
    _print_table(examples, summary)
    # Non-zero exit if nothing passed, so CI can gate on it.
    return 0 if summary.pass_rate > 0.0 or summary.n == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

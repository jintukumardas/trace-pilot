"""Bundled evaluation datasets.

Datasets are JSON files in this package directory with the shape::

    {"name": str, "description": str, "examples": [ <EvalExample fields>, ... ]}

``load_dataset`` parses one into a ``list[EvalExample]``; ``load_default_dataset``
loads ``default.json``. Files are read via ``importlib.resources`` so they resolve
correctly whether the package is installed, zipped, or run from source.
"""

from __future__ import annotations

import json
from importlib import resources

from tracepilot_shared.logging import get_logger
from tracepilot_shared.models import EvalExample

log = get_logger("evals.datasets")

DEFAULT_DATASET = "default"


def _read_dataset_text(name: str) -> str:
    """Return the raw JSON text for ``<name>.json`` bundled in this package."""
    filename = name if name.endswith(".json") else f"{name}.json"
    return resources.files(__package__).joinpath(filename).read_text(encoding="utf-8")


def load_dataset(name: str = DEFAULT_DATASET) -> list[EvalExample]:
    """Load and validate a bundled dataset into ``EvalExample`` objects.

    Invalid rows are skipped with a warning rather than failing the whole load, so
    a single malformed example can't break an eval run.
    """
    try:
        raw = _read_dataset_text(name)
    except (FileNotFoundError, OSError) as exc:
        log.warning("dataset %r not found: %s", name, exc)
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("dataset %r is not valid JSON: %s", name, exc)
        return []

    rows = data.get("examples", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        log.warning("dataset %r has no example list", name)
        return []

    examples: list[EvalExample] = []
    for i, row in enumerate(rows):
        try:
            examples.append(EvalExample.model_validate(row))
        except Exception as exc:
            log.warning("dataset %r: skipping invalid example #%d: %s", name, i, exc)
    return examples


def load_default_dataset() -> list[EvalExample]:
    """Load the bundled ``default`` golden set."""
    return load_dataset(DEFAULT_DATASET)


def available_datasets() -> list[str]:
    """Names (without ``.json``) of the datasets bundled in this package."""
    names: list[str] = []
    for entry in resources.files(__package__).iterdir():
        fname = entry.name
        if fname.endswith(".json"):
            names.append(fname[: -len(".json")])
    return sorted(names)


__all__ = ["load_dataset", "load_default_dataset", "available_datasets", "DEFAULT_DATASET"]

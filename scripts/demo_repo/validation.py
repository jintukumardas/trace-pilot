"""Request validation: turn untrusted JSON into typed, mappable errors.

``app.py`` calls these helpers before touching the service layer so that bad
input becomes a clean ``400`` rather than an unhandled exception deeper in the
stack. Every failure raises :class:`ValidationError` with a human-readable
message and a stable ``field`` so the API can report exactly what was wrong.
"""
from __future__ import annotations

from typing import Any


class ValidationError(Exception):
    """A request failed validation. Carries the offending field name."""

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.field = field


def require(body: dict[str, Any], key: str) -> Any:
    """Return ``body[key]`` or raise :class:`ValidationError` if missing/empty."""
    if key not in body or body[key] in (None, ""):
        raise ValidationError(f"missing required field: {key}", field=key)
    return body[key]


def require_str(body: dict[str, Any], key: str) -> str:
    """Return a required, non-empty string field."""
    value = require(body, key)
    if not isinstance(value, str):
        raise ValidationError(f"field {key!r} must be a string", field=key)
    return value


def require_amount(body: dict[str, Any], key: str = "amount") -> float:
    """Return a required positive numeric amount (major units).

    Rejects zero and negative amounts: a transfer must move a positive sum.
    """
    value = require(body, key)
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"field {key!r} must be a number", field=key) from exc
    if amount <= 0:
        raise ValidationError(f"field {key!r} must be greater than zero", field=key)
    return amount

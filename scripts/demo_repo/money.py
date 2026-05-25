"""Money value object — integer minor units with safe parsing and formatting.

All monetary amounts in the ledger are stored as **integer minor units** (e.g.
cents for USD) to avoid floating-point rounding drift. This module is the single
place that converts between human-facing *major* units (dollars) and the internal
*minor* units, and it is the only place arithmetic on amounts should happen.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Minor units per major unit (100 cents == 1 dollar). Single source of truth.
MINOR_UNITS_PER_MAJOR = 100


class MoneyError(ValueError):
    """Raised when an amount cannot be parsed or would violate a money invariant."""


@dataclass(frozen=True)
class Money:
    """An immutable amount in integer minor units, tagged with a currency.

    Construct via :meth:`from_major` (human input) or :meth:`from_minor`
    (internal). Direct construction is allowed but expects minor units already.
    """

    minor: int
    currency: str = "USD"

    @classmethod
    def from_major(cls, major: float | int | str, currency: str = "USD") -> "Money":
        """Build from a major-unit value (e.g. ``"12.50"`` -> 1250 minor units)."""
        try:
            as_float = float(major)
        except (TypeError, ValueError) as exc:
            raise MoneyError(f"not a valid amount: {major!r}") from exc
        # Round to the nearest minor unit so "12.505" doesn't silently truncate.
        minor = round(as_float * MINOR_UNITS_PER_MAJOR)
        return cls(minor=int(minor), currency=currency)

    @classmethod
    def from_minor(cls, minor: int, currency: str = "USD") -> "Money":
        """Build directly from integer minor units."""
        return cls(minor=int(minor), currency=currency)

    def to_major(self) -> float:
        """Render as a major-unit float for API responses (display only)."""
        return self.minor / MINOR_UNITS_PER_MAJOR

    def is_positive(self) -> bool:
        """True when the amount is strictly greater than zero."""
        return self.minor > 0

    def __str__(self) -> str:
        return f"{self.to_major():.2f} {self.currency}"


def add_minor(a: int, b: int) -> int:
    """Add two minor-unit amounts. Centralized so overflow rules live in one place."""
    return int(a) + int(b)


def subtract_minor(a: int, b: int) -> int:
    """Subtract ``b`` minor units from ``a`` minor units."""
    return int(a) - int(b)

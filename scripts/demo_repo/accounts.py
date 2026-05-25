"""Account state: the ``Account`` dataclass and an in-memory ``AccountStore``.

Balances are tracked in integer minor units (see :mod:`money`). The store is a
plain dictionary keyed by account id; it is intentionally not thread-safe because
the demo service is single-threaded. Persistence is out of scope — restarting the
process clears all accounts.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from money import add_minor, subtract_minor


class AccountNotFound(KeyError):
    """Raised when an account id is not present in the store."""


@dataclass
class Account:
    """A single ledger account holding an integer minor-unit balance."""

    id: str
    owner: str
    currency: str = "USD"
    balance_minor: int = 0

    def credit(self, amount_minor: int) -> None:
        """Increase the balance by ``amount_minor`` (must be non-negative)."""
        if amount_minor < 0:
            raise ValueError("credit amount must be non-negative")
        self.balance_minor = add_minor(self.balance_minor, amount_minor)

    def debit(self, amount_minor: int) -> None:
        """Decrease the balance by ``amount_minor`` (must be non-negative).

        Callers are responsible for checking sufficient funds *before* debiting;
        this method only enforces the sign of the amount.
        """
        if amount_minor < 0:
            raise ValueError("debit amount must be non-negative")
        self.balance_minor = subtract_minor(self.balance_minor, amount_minor)

    def can_afford(self, amount_minor: int) -> bool:
        """True if debiting ``amount_minor`` would not overdraw the account."""
        return self.balance_minor >= amount_minor


class AccountStore:
    """In-memory collection of accounts keyed by id, with id generation."""

    def __init__(self) -> None:
        self._accounts: dict[str, Account] = {}
        self._ids = itertools.count(1)

    def open(self, owner: str, currency: str = "USD", opening_minor: int = 0) -> Account:
        """Create and store a new account, returning it."""
        account_id = f"acct_{next(self._ids):04d}"
        account = Account(id=account_id, owner=owner, currency=currency,
                          balance_minor=opening_minor)
        self._accounts[account_id] = account
        return account

    def get(self, account_id: str) -> Account:
        """Fetch an account or raise :class:`AccountNotFound`."""
        try:
            return self._accounts[account_id]
        except KeyError as exc:
            raise AccountNotFound(account_id) from exc

    def exists(self, account_id: str) -> bool:
        return account_id in self._accounts

    def all(self) -> list[Account]:
        """Return every account (insertion order)."""
        return list(self._accounts.values())

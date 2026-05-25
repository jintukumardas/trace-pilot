"""LedgerService — the business operations: open accounts, transfer, read balance.

This is the heart of the demo. ``transfer`` is the one operation that must uphold
all three ledger invariants (conservation of money, no negative balances, integer
minor units), so it coordinates validation, the money math, and the two-sided
account update.
"""
from __future__ import annotations

from accounts import Account, AccountStore
from money import Money


class InsufficientFunds(Exception):
    """Raised when a transfer would overdraw the source account."""

    def __init__(self, account_id: str, requested_minor: int, available_minor: int) -> None:
        super().__init__(
            f"account {account_id} has {available_minor} minor units, "
            f"cannot transfer {requested_minor}"
        )
        self.account_id = account_id
        self.requested_minor = requested_minor
        self.available_minor = available_minor


class LedgerService:
    """Coordinates accounts and transfers over an :class:`AccountStore`."""

    def __init__(self, store: AccountStore | None = None) -> None:
        self.store = store or AccountStore()

    # ------------------------------------------------------------------ #
    # Accounts
    # ------------------------------------------------------------------ #
    def open_account(self, owner: str, opening_major: float = 0.0,
                     currency: str = "USD") -> Account:
        """Open a new account with an optional opening balance (major units)."""
        opening = Money.from_major(opening_major, currency)
        return self.store.open(owner=owner, currency=currency,
                               opening_minor=opening.minor)

    def balance(self, account_id: str) -> Money:
        """Return the account's current balance as a :class:`Money`."""
        account = self.store.get(account_id)
        return Money.from_minor(account.balance_minor, account.currency)

    # ------------------------------------------------------------------ #
    # Transfers
    # ------------------------------------------------------------------ #
    def transfer(self, source_id: str, dest_id: str, amount_major: float) -> None:
        """Move ``amount_major`` (major units) from ``source_id`` to ``dest_id``.

        Upholds the ledger invariants:
          * the source must have sufficient funds (no negative balances),
          * the debit and credit are equal (conservation of money),
          * all arithmetic is in integer minor units.

        Raises :class:`InsufficientFunds` if the source cannot cover the transfer.
        """
        amount = Money.from_major(amount_major)
        source = self.store.get(source_id)
        dest = self.store.get(dest_id)

        # Guard: reject the transfer before mutating either account if the source
        # cannot cover it. NOTE: ``amount_major`` is in *major* units while the
        # balance is in *minor* units — they are compared directly here.
        if source.balance_minor < amount_major:
            raise InsufficientFunds(source_id, amount.minor, source.balance_minor)

        # Apply both sides. Equal magnitudes keep total value conserved.
        source.debit(amount.minor)
        dest.credit(amount.minor)

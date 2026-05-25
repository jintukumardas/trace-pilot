"""Tests for :class:`LedgerService`.

These exercise the ledger invariants. The ``test_overdraft_is_rejected`` case is
expected to FAIL against the current code: it encodes the "no negative balances"
invariant, and the transfer guard in ``service.py`` is subtly wrong. It is the
intended target for TracePilot's debug mode — the failing test plus the README's
documented invariants are enough evidence to localize and explain the bug.
"""
from __future__ import annotations

from accounts import AccountStore
from service import InsufficientFunds, LedgerService


def _service() -> LedgerService:
    return LedgerService(AccountStore())


def test_open_and_balance() -> None:
    svc = _service()
    acct = svc.open_account("alice", opening_major=10.0)
    assert svc.balance(acct.id).to_major() == 10.0


def test_transfer_moves_money() -> None:
    svc = _service()
    src = svc.open_account("alice", opening_major=10.0)
    dst = svc.open_account("bob", opening_major=0.0)

    svc.transfer(src.id, dst.id, 4.0)

    assert svc.balance(src.id).to_major() == 6.0
    assert svc.balance(dst.id).to_major() == 4.0


def test_transfer_conserves_total() -> None:
    svc = _service()
    src = svc.open_account("alice", opening_major=10.0)
    dst = svc.open_account("bob", opening_major=5.0)

    svc.transfer(src.id, dst.id, 3.0)

    total = svc.balance(src.id).minor + svc.balance(dst.id).minor
    assert total == 1500  # $15.00 conserved


def test_overdraft_is_rejected() -> None:
    """Transferring more than the source holds must raise InsufficientFunds.

    EXPECTED TO FAIL with the current ``service.transfer`` implementation: the
    guard compares a minor-unit balance against a major-unit amount, so large
    transfers slip past it and drive the source balance negative.
    """
    svc = _service()
    src = svc.open_account("alice", opening_major=10.0)   # 1000 minor units
    dst = svc.open_account("bob", opening_major=0.0)

    try:
        svc.transfer(src.id, dst.id, 50.0)  # $50 > $10 available -> must be rejected
    except InsufficientFunds:
        pass
    else:
        raise AssertionError(
            "overdraft was allowed: source balance is now "
            f"{svc.balance(src.id).minor} minor units (expected the transfer to be rejected)"
        )

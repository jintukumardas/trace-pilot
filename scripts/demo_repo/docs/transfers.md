# Transfer flow & edge cases

This note documents how a transfer moves through the ledger and the edge cases
the service is meant to handle. It is intended reading for anyone modifying
`service.py` or `money.py`.

## Happy path

```
POST /transfers  {"source": "acct_0001", "dest": "acct_0002", "amount": 25.0}
        │
        ▼
app.LedgerHandler._transfer
        │  validation.require_str / require_amount  (reject bad input as 400)
        ▼
service.LedgerService.transfer(source_id, dest_id, amount_major)
        │  1. parse amount_major -> Money (minor units)
        │  2. load source + dest accounts from the store
        │  3. GUARD: ensure the source can cover the amount   ← invariant check
        │  4. source.debit(amount.minor)   (Account.debit)
        │  5. dest.credit(amount.minor)    (Account.credit)
        ▼
200 OK
```

## Invariants the transfer must preserve

1. **No negative balances.** The guard in step 3 must reject any transfer the
   source cannot afford *before* either account is mutated. The check must
   compare like units: `account.balance_minor` is in **minor** units, so the
   amount it is compared against must also be in **minor** units
   (`Money.minor`), not the raw major-unit amount the caller passed.
2. **Conservation of money.** The debit and credit in steps 4–5 use the same
   `amount.minor`, so total value across all accounts is unchanged.
3. **Integer minor units.** All arithmetic happens on integers via
   `money.add_minor` / `money.subtract_minor`; only the API edge converts to the
   major-unit float clients see.

## Edge cases

- **Overdraft.** A transfer larger than the source balance must raise
  `InsufficientFunds` (mapped to HTTP `409`). This is the most important guard:
  getting the *units* of the comparison wrong silently breaks invariant #1 while
  the happy-path tests still pass, because they only ever transfer amounts
  smaller than the (numerically larger) minor-unit balance.
- **Non-positive amount.** `validation.require_amount` rejects zero and negative
  amounts at the edge, so `service.transfer` can assume a positive amount.
- **Unknown account.** `AccountStore.get` raises `AccountNotFound` (HTTP `404`).
- **Self-transfer.** `source == dest` is currently a no-op net change and is
  permitted; tightening this is a possible future change to review.

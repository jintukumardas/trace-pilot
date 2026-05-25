# Ledger — a tiny in-memory accounts service

`ledger` is a deliberately small HTTP service used as TracePilot's bundled demo
repository. It models a toy double-entry ledger: clients open **accounts**, post
**transfers** between them, and read back **balances**. It exists so retrieval,
citations, debug, and change-review modes have real, interconnected code to
ground answers against — not so it is production banking software.

## Architecture

The service is a thin, dependency-light stack:

```
HTTP request
    │
    ▼
app.py            ── routing + request/response plumbing (stdlib http.server)
    │
    ▼
service.py        ── LedgerService: the business operations (open / transfer / balance)
    │
    ├── accounts.py   ── Account + AccountStore (the in-memory state)
    ├── money.py      ── Money value object + safe arithmetic helpers
    └── validation.py ── request validation + typed errors
```

- **`app.py`** owns the network boundary. It parses JSON request bodies, dispatches
  to `LedgerService`, and serializes the result (or a structured error) back out.
- **`service.py`** holds `LedgerService`, the single place transfers are applied.
  It is intentionally the most interesting file: it coordinates validation,
  the money math, and the two-sided account update that makes a transfer atomic.
- **`accounts.py`** is the storage layer — an `AccountStore` keyed by account id,
  plus the `Account` dataclass that tracks a balance in integer minor units.
- **`money.py`** is a small value object. All amounts are stored as integer
  *minor units* (cents) to avoid floating-point drift; `money.py` is where parsing
  and formatting between major and minor units lives.
- **`validation.py`** turns untrusted input into typed errors (`ValidationError`)
  so `app.py` can map them to clean `400` responses.

## Invariants

1. **Conservation of money.** A transfer never creates or destroys value: the
   amount debited from the source equals the amount credited to the destination.
2. **No negative balances.** A transfer that would overdraw the source account is
   rejected *before* either side is mutated.
3. **Integer minor units everywhere.** Balances are integers; only the API edge
   converts to/from human-readable major units.

## Running

```bash
python app.py        # serves on http://localhost:8081
```

See `docs/transfers.md` for the transfer flow and the known edge cases.

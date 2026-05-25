"""ledger — a tiny in-memory accounts service used as TracePilot's demo repo.

See ``README.md`` for the architecture and ``docs/transfers.md`` for the transfer
flow and known edge cases. The package is import-light on purpose (stdlib only).
"""

__all__ = ["app", "service", "accounts", "money", "validation"]
__version__ = "0.1.0"

"""HTTP boundary for the ledger demo service (stdlib ``http.server`` only).

``app.py`` parses JSON request bodies, dispatches to :class:`LedgerService`, and
serializes results (or structured errors) back to the client. It deliberately has
no third-party dependencies so the demo repo indexes and runs anywhere.

Routes
------
* ``POST /accounts``               open an account ``{"owner", "opening"?}``
* ``GET  /accounts/<id>/balance``  read a balance
* ``POST /transfers``              transfer ``{"source", "dest", "amount"}``
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from accounts import AccountNotFound
from service import InsufficientFunds, LedgerService
from validation import ValidationError, require_amount, require_str

HOST = "127.0.0.1"
PORT = 8081

# A single process-wide service instance backs every request.
_service = LedgerService()


class LedgerHandler(BaseHTTPRequestHandler):
    """Maps HTTP requests onto :class:`LedgerService` operations."""

    # -- response helpers -------------------------------------------------- #
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"invalid JSON body: {exc}", field="body") from exc

    # -- routing ----------------------------------------------------------- #
    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        try:
            if self.path == "/accounts":
                return self._open_account()
            if self.path == "/transfers":
                return self._transfer()
            self._send(404, {"error": "not found", "path": self.path})
        except ValidationError as exc:
            self._send(400, {"error": exc.message, "field": exc.field})
        except AccountNotFound as exc:
            self._send(404, {"error": f"account not found: {exc.args[0]}"})
        except InsufficientFunds as exc:
            self._send(409, {"error": str(exc)})

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        # Expect /accounts/<id>/balance
        parts = self.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "accounts" and parts[2] == "balance":
            try:
                money = _service.balance(parts[1])
                return self._send(200, {"account": parts[1], "balance": money.to_major(),
                                        "currency": money.currency})
            except AccountNotFound as exc:
                return self._send(404, {"error": f"account not found: {exc.args[0]}"})
        self._send(404, {"error": "not found", "path": self.path})

    # -- handlers ---------------------------------------------------------- #
    def _open_account(self) -> None:
        body = self._read_json()
        owner = require_str(body, "owner")
        opening = float(body.get("opening", 0.0))
        account = _service.open_account(owner=owner, opening_major=opening)
        self._send(201, {"id": account.id, "owner": account.owner,
                         "balance": account.balance_minor / 100})

    def _transfer(self) -> None:
        body = self._read_json()
        source = require_str(body, "source")
        dest = require_str(body, "dest")
        amount = require_amount(body, "amount")
        _service.transfer(source, dest, amount)
        self._send(200, {"status": "ok", "source": source, "dest": dest, "amount": amount})

    def log_message(self, *args) -> None:  # noqa: D401 - silence default logging
        """Suppress the default per-request stderr logging."""
        return


def main() -> None:
    """Start the blocking HTTP server."""
    server = HTTPServer((HOST, PORT), LedgerHandler)
    print(f"ledger demo service listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()

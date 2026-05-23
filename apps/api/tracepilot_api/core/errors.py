"""API error type + global exception handlers.

Every error leaving the API is normalized to::

    {"error": {"type": "<ErrorType>", "message": "<human readable>"}}

``ApiError`` carries an HTTP status so services can raise a typed, intentional
failure (e.g. ``ApiError.not_found("workspace", id)``) instead of leaking
framework exceptions. A catch-all handler converts anything unexpected into a
500 with the same envelope so clients never see a stack trace.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tracepilot_shared.logging import get_logger

log = get_logger("api.errors")


class ApiError(Exception):
    """A typed, HTTP-aware application error."""

    def __init__(self, message: str, *, status_code: int = 400, error_type: str = "ApiError") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type

    def to_response(self) -> JSONResponse:
        return _envelope(self.error_type, self.message, self.status_code)

    # Convenience constructors -------------------------------------------------
    @classmethod
    def not_found(cls, kind: str, identifier: str) -> ApiError:
        return cls(f"{kind} '{identifier}' not found", status_code=404, error_type="NotFound")

    @classmethod
    def bad_request(cls, message: str) -> ApiError:
        return cls(message, status_code=400, error_type="BadRequest")

    @classmethod
    def conflict(cls, message: str) -> ApiError:
        return cls(message, status_code=409, error_type="Conflict")

    @classmethod
    def unavailable(cls, message: str) -> ApiError:
        return cls(message, status_code=503, error_type="ServiceUnavailable")


def _envelope(error_type: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"type": error_type, "message": message}},
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Register handlers that produce the uniform ``{"error": {...}}`` envelope."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Compact the first validation problem into a readable message.
        errors = exc.errors()
        first = errors[0] if errors else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        msg = first.get("msg", "invalid request")
        detail = f"{loc}: {msg}" if loc else msg
        return _envelope("ValidationError", detail, 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _envelope("HTTPError", str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", exc)
        return _envelope("InternalServerError", "An unexpected error occurred.", 500)

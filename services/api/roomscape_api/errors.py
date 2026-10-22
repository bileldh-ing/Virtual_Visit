"""Consistent machine-readable API errors.

Shape: {"error": {"code", "message", "fields"?, "request_id", "retryable"}}.
Stack traces, raw tool output and filesystem paths are never returned.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("roomscape.api")


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False,
                 fields: dict | None = None, headers: dict | None = None):
        self.status, self.code, self.message = status, code, message
        self.retryable, self.fields, self.headers = retryable, fields, headers


def not_found(what: str = "Resource") -> APIError:
    return APIError(404, "NOT_FOUND", f"{what} not found.")


def forbidden(msg: str = "You do not have permission to do that.") -> APIError:
    return APIError(403, "FORBIDDEN", msg)


def _body(request: Request, code: str, message: str, retryable: bool = False, fields=None) -> dict:
    err = {"code": code, "message": message, "request_id": getattr(request.state, "request_id", ""),
           "retryable": retryable}
    if fields:
        err["fields"] = fields
    return {"error": err}


def install(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _api_error(request: Request, exc: APIError):
        return JSONResponse(_body(request, exc.code, exc.message, exc.retryable, exc.fields),
                            status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = {}
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", [])[1:]) or "body"
            fields[loc] = e.get("msg", "invalid")
        return JSONResponse(_body(request, "VALIDATION_ERROR", "Some fields are invalid.", False, fields),
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHENTICATED"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(_body(request, code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"request_id": getattr(request.state, "request_id", "")})
        return JSONResponse(_body(request, "INTERNAL", "Something went wrong on our side.", True), status_code=500)

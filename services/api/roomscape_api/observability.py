"""Structured JSON logging, request IDs, simple Prometheus metrics, security headers."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

_SENSITIVE = re.compile(r"(sig=|token=|password=|Bearer\s+)[^&\s\"]+", re.I)


def redact(text: str) -> str:
    return _SENSITIVE.sub(lambda m: m.group(1) + "[redacted]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": round(record.created, 3), "level": record.levelname, "logger": record.name,
                   "msg": redact(record.getMessage()), "request_id": request_id_var.get("")}
        for k in ("job_id", "attempt_id", "stage", "worker_id"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))[-4000:]
        return json.dumps(payload)


def setup_logging(level: int = logging.INFO) -> None:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True


class Metrics:
    def __init__(self):
        self.requests = defaultdict(int)
        self.latency_sum = defaultdict(float)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        key = (method, route, str(status // 100) + "xx")
        self.requests[key] += 1
        self.latency_sum[(method, route)] += seconds

    def render(self) -> str:
        lines = ["# TYPE roomscape_http_requests_total counter"]
        for (m, r, s), n in sorted(self.requests.items()):
            lines.append(f'roomscape_http_requests_total{{method="{m}",route="{r}",status="{s}"}} {n}')
        lines.append("# TYPE roomscape_http_request_seconds_sum counter")
        for (m, r), v in sorted(self.latency_sum.items()):
            lines.append(f'roomscape_http_request_seconds_sum{{method="{m}",route="{r}"}} {v:.4f}')
        return "\n".join(lines) + "\n"


METRICS = Metrics()
access_log = logging.getLogger("roomscape.access")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "same-site",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", rid):
            rid = uuid.uuid4().hex
        request.state.request_id = rid
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            dur = time.perf_counter() - start
            route = getattr(request.scope.get("route"), "path", "unmatched")
            METRICS.observe(request.method, route, status, dur)
            access_log.info(f"{request.method} {route} {status} {dur * 1000:.1f}ms")
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

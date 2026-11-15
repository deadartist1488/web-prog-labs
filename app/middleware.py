"""Middleware (лабы 4, 8, 9): Request ID, логирование и время запроса, метрики, защитные заголовки.

Написано как чистое ASGI-middleware (без BaseHTTPMiddleware), чтобы request_id был виден
и в обработчиках, и в логах, и в ответе на непойманную ошибку.
"""
import json
import logging
import re
import time
import uuid

from prometheus_client import Counter, Gauge, Histogram

from app.errors import error_body
from app.logging_setup import request_id_var

log = logging.getLogger("app.http")

REQUESTS = Counter("http_requests_total", "Количество HTTP-запросов", ["method", "endpoint", "status"])
ERRORS = Counter("http_errors_total", "Количество ответов 5xx", ["endpoint"])
LATENCY = Histogram("http_request_duration_seconds", "Время обработки запроса", ["endpoint"],
                    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5))
QUEUE_SIZE = Gauge("email_queue_size", "Размер очереди писем")

_ID_RE = re.compile(r"/\d+(?=/|$)")
_VALID_RID = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"content-security-policy",
     b"default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
     b"script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
     b"style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; frame-ancestors 'self'"),
    (b"permissions-policy", b"geolocation=(), camera=(), microphone=()"),
]


def endpoint_label(path: str) -> str:
    """/users/42/loans -> /users/{id}/loans, чтобы метрики не разрастались по id."""
    return _ID_RE.sub("/{id}", path)


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_RID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        method, path = scope["method"], scope["path"]
        endpoint = endpoint_label(path)
        status_holder = {"status": 500, "sent": False}
        start = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                status_holder["sent"] = True
                hdrs = list(message.get("headers", []))
                hdrs.append((b"x-request-id", request_id.encode()))
                existing = {k.lower() for k, _ in hdrs}
                hdrs.extend(h for h in SECURITY_HEADERS if h[0] not in existing)
                message["headers"] = hdrs
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception("unhandled error on %s %s", method, path)
            if not status_holder["sent"]:
                body = json.dumps(error_body("INTERNAL_ERROR", "Внутренняя ошибка сервера"),
                                  ensure_ascii=False, separators=(",", ":")).encode()
                await send_wrapper({"type": "http.response.start", "status": 500,
                                    "headers": [(b"content-type", b"application/json; charset=utf-8")]})
                await send({"type": "http.response.body", "body": body})
        finally:
            elapsed = time.perf_counter() - start
            status = status_holder["status"]
            REQUESTS.labels(method, endpoint, str(status)).inc()
            LATENCY.labels(endpoint).observe(elapsed)
            if status >= 500:
                ERRORS.labels(endpoint).inc()
            client = scope.get("client") or ("-", 0)
            log.info("%s %s - %s - %.3fs", method, path, status, elapsed,
                     extra={"context": {"status": status, "duration_ms": round(elapsed * 1000, 1),
                                        "client_ip": client[0], "query": scope.get("query_string", b"").decode()}})
            request_id_var.reset(token)

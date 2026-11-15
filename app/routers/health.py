import logging
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.middleware import QUEUE_SIZE

router = APIRouter(tags=["service"])
log = logging.getLogger("app.health")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return ("<!doctype html><html lang='ru'><meta charset='utf-8'><title>Library API</title>"
            "<h1>Library API</h1><p>Документация: <a href='/docs'>/docs</a>, "
            "проверка: <a href='/health'>/health</a>, метрики: <a href='/metrics'>/metrics</a></p></html>")


@router.get("/health", summary="Проверка живости приложения, БД и Redis")
def health(request: Request):
    checks = {}
    started = time.perf_counter()
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        log.error("health: database down: %s", e)
        checks["database"] = "down"
    try:
        request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as e:  # noqa: BLE001
        log.error("health: redis down: %s", e)
        checks["redis"] = "down"
    try:
        queue = request.app.state.email_queue.size()
        QUEUE_SIZE.set(queue)
    except Exception:  # noqa: BLE001
        queue = None
    healthy = all(v == "ok" for v in checks.values())
    body = {"status": "ok" if healthy else "degraded", "checks": checks, "email_queue": queue,
            "check_ms": round((time.perf_counter() - started) * 1000, 1)}
    return JSONResponse(body, status_code=200 if healthy else 503)


@router.get("/metrics", summary="Метрики Prometheus", include_in_schema=False)
def metrics(request: Request) -> Response:
    try:
        QUEUE_SIZE.set(request.app.state.email_queue.size())
    except Exception:  # noqa: BLE001
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

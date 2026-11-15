"""Единый формат ошибок и их обработчики (лаба 3).

Любая ошибка API выглядит так:
{"error": {"code": "...", "message": "...", "details": [...], "request_id": "..."}}
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_setup import request_id_var

log = logging.getLogger("app.errors")


class AppError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str, details: Optional[List[Dict[str, Any]]] = None, headers: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.details = details or []
        self.headers = headers


class ValidationAppError(AppError):
    status_code = 400
    code = "VALIDATION_ERROR"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class BusinessRuleError(AppError):
    status_code = 422
    code = "BUSINESS_RULE_VIOLATION"


class TooManyRequestsError(AppError):
    status_code = 429
    code = "TOO_MANY_REQUESTS"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"


def error_body(code: str, message: str, details: Optional[list] = None) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or [],
                      "request_id": request_id_var.get()}}


def _field_name(loc: tuple) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header", "cookie")]
    return ".".join(parts) or "body"


_HTTP_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
               405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE"}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        level = logging.ERROR if exc.status_code >= 500 else logging.WARNING
        log.log(level, "%s %s -> %s %s", request.method, request.url.path, exc.status_code, exc.code,
                extra={"context": {"message": exc.message, "details": exc.details}})
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status_code,
                            headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        details = [{"field": _field_name(tuple(e.get("loc", ()))), "message": e.get("msg", ""),
                    "type": e.get("type", "")} for e in exc.errors()]
        log.warning("validation failed %s %s", request.method, request.url.path,
                    extra={"context": {"fields": [d["field"] for d in details]}})
        return JSONResponse(error_body("VALIDATION_ERROR", "Данные запроса не прошли проверку", details),
                            status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code
        return JSONResponse(error_body(code, message), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        # подстраховка: гонка, которую не поймала проверка в сервисе, всё равно даст 409, а не 500
        log.warning("integrity error on %s %s", request.method, request.url.path)
        return JSONResponse(error_body("CONFLICT", "Нарушено ограничение целостности данных"), status_code=409)

    @app.exception_handler(OperationalError)
    async def _db_down(request: Request, exc: OperationalError):
        log.error("database unavailable", exc_info=exc)
        return JSONResponse(error_body("SERVICE_UNAVAILABLE", "База данных временно недоступна"),
                            status_code=503)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        # клиенту не отдаём стек и внутренние детали, только request_id для поиска по логам
        return JSONResponse(error_body("INTERNAL_ERROR", "Внутренняя ошибка сервера"), status_code=500)

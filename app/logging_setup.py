"""Структурированное логирование (лабы 3, 9): JSON-строки с request_id в каждом сообщении."""
import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

SENSITIVE_KEYS = {"password", "password_hash", "token", "access_token", "refresh_token", "authorization",
                  "csrf_token", "secret", "cookie"}


def mask_sensitive(data: Any) -> Any:
    """Затирает пароли и токены перед записью в лог (лаба 3, задание 8)."""
    if isinstance(data, dict):
        return {k: ("***" if k.lower() in SENSITIVE_KEYS else mask_sensitive(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [mask_sensitive(x) for x in data]
    return data


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "context", None)
        if extra:
            payload["context"] = mask_sensitive(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", log_file: str = "") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        if getattr(h, "_library_api", False):
            root.removeHandler(h)
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for h in handlers:
        h._library_api = True  # type: ignore[attr-defined]
        h.setFormatter(JsonFormatter())
        h.addFilter(RequestIdFilter())
        root.addHandler(h)

"""Cookie, CSRF, XSS и служебные эндпоинты для демонстраций (лабы 6, 8, 9, 10)."""
import asyncio
import html
import logging
import random
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, Header, Query, Request, Response
from fastapi.responses import HTMLResponse

from app.config import Settings
from app.deps import get_current_user, get_settings_dep, require_permission
from app.errors import ForbiddenError
from app.models import User
from app.schemas import CookieIn, SettingsIn
from app.security import generate_csrf_token, validate_csrf_token

router = APIRouter(tags=["web"])
log = logging.getLogger("app.web")

CSRF_COOKIE = "csrf_token"
_user_settings = {}  # демо-хранилище настроек (в реальности — таблица в БД)


# ---------- cookie ----------

@router.post("/set-cookie", summary="Установить cookie (сессионная / на N дней)")
def set_cookie(data: CookieIn, response: Response, settings: Settings = Depends(get_settings_dep)):
    max_age = data.days * 86400 if data.days else None  # None = живёт до закрытия браузера
    response.set_cookie(data.name, data.value, max_age=max_age, httponly=True, secure=settings.cookie_secure,
                        samesite="lax", path="/")
    log.info("cookie set", extra={"context": {"name": data.name, "days": data.days}})
    return {"set": data.name, "max_age": max_age}


@router.get("/get-cookie", summary="Прочитать cookie")
def get_cookie(request: Request, name: str = Query(min_length=1, max_length=64)):
    value = request.cookies.get(name)
    log.info("cookie read", extra={"context": {"name": name, "found": value is not None}})
    return {"name": name, "value": value}


@router.post("/delete-cookie", summary="Удалить cookie")
def delete_cookie(response: Response, name: str = Query(min_length=1, max_length=64)):
    response.delete_cookie(name, path="/")
    log.info("cookie deleted", extra={"context": {"name": name}})
    return {"deleted": name}


# ---------- CSRF для HTML-формы ----------

def _csrf_cookie(response: Response, token: str, settings: Settings) -> None:
    # cookie с токеном не HttpOnly не нужна: токен кладём в форму сервером, cookie только для сверки
    response.set_cookie(CSRF_COOKIE, token, httponly=True, secure=settings.cookie_secure, samesite="strict",
                        path="/", max_age=3600)


@router.get("/form", response_class=HTMLResponse, summary="Форма с CSRF-токеном")
def form(settings: Settings = Depends(get_settings_dep)):
    token = generate_csrf_token(settings.secret_key)
    page = (
        "<!doctype html><html lang='ru'><meta charset='utf-8'><title>Обратная связь</title>"
        "<form action='/submit' method='POST'>"
        f"<input type='hidden' name='csrf_token' value='{html.escape(token)}'>"
        "<input type='text' name='message' maxlength='500' required>"
        "<button>Отправить</button></form></html>"
    )
    response = HTMLResponse(page)
    _csrf_cookie(response, token, settings)
    return response


@router.post("/submit", response_class=HTMLResponse, summary="Приём формы с проверкой CSRF и Origin")
def submit(request: Request, message: str = Form(max_length=500), csrf_token: str = Form(default=""),
           cookie_token: Optional[str] = Cookie(default=None, alias=CSRF_COOKIE),
           origin: Optional[str] = Header(default=None), settings: Settings = Depends(get_settings_dep)):
    host = request.headers.get("host", "")
    if origin and not origin.split("://", 1)[-1] == host:
        log.warning("csrf: foreign origin", extra={"context": {"origin": origin}})
        raise ForbiddenError("Запрос с чужого источника")
    if not validate_csrf_token(settings.secret_key, csrf_token, cookie_token):
        log.warning("csrf: token mismatch", extra={"context": {"has_cookie": bool(cookie_token)}})
        raise ForbiddenError("Неверный CSRF-токен")
    # вывод экранируется: <script> превратится в &lt;script&gt; и не выполнится (XSS)
    return HTMLResponse(f"<!doctype html><meta charset='utf-8'><p>Получено: {html.escape(message)}</p>")


@router.get("/echo", response_class=HTMLResponse, summary="Демонстрация защиты от XSS")
def echo(text: str = Query(default="", max_length=500)):
    return HTMLResponse(f"<!doctype html><meta charset='utf-8'><div class='comment'>{html.escape(text)}</div>")


# ---------- защищённые настройки: JWT + CSRF-заголовок ----------

@router.get("/settings", summary="Настройки пользователя + свежий CSRF-токен")
def get_user_settings(response: Response, user: User = Depends(get_current_user),
                      settings: Settings = Depends(get_settings_dep)):
    token = generate_csrf_token(settings.secret_key, str(user.id))
    _csrf_cookie(response, token, settings)
    return {"settings": _user_settings.get(user.id, {"theme": "light", "language": "ru"}), "csrf_token": token}


@router.post("/settings", summary="Сохранить настройки (нужен заголовок X-CSRF-Token)")
def save_user_settings(data: SettingsIn, user: User = Depends(get_current_user),
                       x_csrf_token: Optional[str] = Header(default=None),
                       cookie_token: Optional[str] = Cookie(default=None, alias=CSRF_COOKIE),
                       settings: Settings = Depends(get_settings_dep)):
    if not validate_csrf_token(settings.secret_key, x_csrf_token, cookie_token, str(user.id)):
        log.warning("csrf: settings rejected", extra={"context": {"user_id": user.id}})
        raise ForbiddenError("Неверный CSRF-токен")
    _user_settings[user.id] = data.model_dump()
    return {"settings": _user_settings[user.id]}


# ---------- служебное: время, долгий запрос, нестабильный эндпоинт, очередь ----------

@router.get("/api/time", summary="Текущее время сервера")
def server_time(request: Request):
    return {"time": datetime.now(timezone.utc).isoformat(), "x_forwarded_for": request.headers.get("x-forwarded-for"),
            "x_real_ip": request.headers.get("x-real-ip")}


@router.get("/api/long", summary="Долгий запрос для проверки тайм-аутов nginx")
async def long_request(seconds: float = Query(default=2, ge=0, le=600)):
    await asyncio.sleep(seconds)
    return {"slept": seconds}


@router.get("/debug/flaky", summary="Иногда падает (для разбора по request_id)")
def flaky(fail_rate: float = Query(default=0.3, ge=0, le=1)):
    step = random.random()
    log.info("flaky: start", extra={"context": {"roll": round(step, 3), "fail_rate": fail_rate}})
    if step < fail_rate:
        log.info("flaky: dividing by remaining stock")
        stock = 0
        return {"ratio": 10 / stock}  # ZeroDivisionError -> 500 с request_id
    return {"ok": True, "roll": step}


@router.get("/admin/email-queue", summary="Размер очереди писем (admin)")
def queue_size(request: Request, _: User = Depends(require_permission("admin.jobs"))):
    return {"queued": request.app.state.email_queue.size()}


@router.post("/admin/email-queue/process", summary="Обработать очередь вручную (admin)")
def queue_process(request: Request, _: User = Depends(require_permission("admin.jobs"))):
    return {"processed": request.app.state.email_queue.process()}

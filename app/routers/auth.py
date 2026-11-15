"""Аутентификация: вариант B (JWT) — /auth/*, вариант A (серверная сессия в Redis) — /session/*."""
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.config import Settings
from app.deps import bearer, get_auth_service, get_current_user, get_settings_dep, get_user_service
from app.errors import UnauthorizedError
from app.models import User
from app.schemas import LoginIn, RefreshIn, TokenPair, UserCreate, UserOut
from app.services.auth import AuthService
from app.services.users import UserService

router = APIRouter(tags=["auth"])

SESSION_COOKIE = "sid"


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "-")


@router.post("/auth/register", response_model=UserOut, status_code=201, summary="Регистрация")
def register(data: UserCreate, svc: UserService = Depends(get_user_service)):
    return svc.create(data)


@router.post("/auth/login", response_model=TokenPair, summary="Вход: access (15 мин) + refresh (7 дней)")
def login(data: LoginIn, request: Request, auth: AuthService = Depends(get_auth_service)):
    return auth.login(data.email, data.password, _ip(request))


@router.post("/auth/refresh", response_model=TokenPair, summary="Новый access по refresh (с ротацией)")
def refresh(data: RefreshIn, auth: AuthService = Depends(get_auth_service)):
    return auth.refresh(data.refresh_token)


@router.post("/auth/logout", status_code=204, summary="Выход: access в blacklist, refresh отзывается")
def logout(data: Optional[RefreshIn] = None, creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
           auth: AuthService = Depends(get_auth_service)):
    if creds is None:
        raise UnauthorizedError("Требуется access-токен", headers={"WWW-Authenticate": "Bearer"})
    auth.logout(creds.credentials, data.refresh_token if data else None)
    return Response(status_code=204)


@router.get("/auth/profile", response_model=UserOut, summary="Профиль (нужен access-токен)")
def profile(user: User = Depends(get_current_user)):
    return user


# ---------- вариант A: сессии ----------

@router.post("/session/login", response_model=UserOut, summary="Вход с серверной сессией (cookie sid)")
def session_login(data: LoginIn, request: Request, response: Response,
                  auth: AuthService = Depends(get_auth_service), settings: Settings = Depends(get_settings_dep)):
    user = auth.verify_credentials(data.email, data.password, _ip(request))
    sid = auth.create_session(user)
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, secure=settings.cookie_secure, samesite="strict",
                        max_age=settings.session_ttl_hours * 3600, path="/")
    return user


@router.get("/session/profile", response_model=UserOut, summary="Профиль по сессии")
def session_profile(sid: Optional[str] = Cookie(default=None), auth: AuthService = Depends(get_auth_service)):
    return auth.session_user(sid)


@router.post("/session/logout", status_code=204, summary="Выход: сессия удаляется из Redis")
def session_logout(response: Response, sid: Optional[str] = Cookie(default=None),
                   auth: AuthService = Depends(get_auth_service)):
    auth.destroy_session(sid)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = 204
    return response

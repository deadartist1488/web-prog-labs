"""Аутентификация (лаба 6): JWT access/refresh с ротацией, blacklist и сессии в Redis."""
import json
import logging
import secrets
import time
from typing import Any, Dict, Optional

from app.errors import TooManyRequestsError, UnauthorizedError
from app.models import User
from app.repositories import UserRepository
from app.security import TokenService, hash_password, verify_password

log = logging.getLogger("app.services.auth")

_dummy_hash: Optional[str] = None


def _dummy() -> str:
    """Фиктивный хеш: проверяем пароль даже для несуществующего email,
    чтобы по времени ответа нельзя было понять, зарегистрирован ли адрес."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("dummy-password-1")
    return _dummy_hash


class AuthService:
    def __init__(self, users: UserRepository, tokens: TokenService, redis_client, *, max_attempts: int = 5,
                 lock_minutes: int = 15, session_ttl_hours: int = 24):
        self.users = users
        self.tokens = tokens
        self.r = redis_client
        self.max_attempts = max_attempts
        self.lock_seconds = lock_minutes * 60
        self.session_ttl = session_ttl_hours * 3600

    # ---------- логин и защита от перебора ----------

    def _check_lock(self, email: str) -> None:
        attempts = int(self.r.get(f"login:fail:{email}") or 0)
        if attempts >= self.max_attempts:
            ttl = max(int(self.r.ttl(f"login:fail:{email}")), 1)
            raise TooManyRequestsError("Слишком много неудачных попыток входа, попробуйте позже",
                                       headers={"Retry-After": str(ttl)})

    def verify_credentials(self, email: str, password: str, ip: str = "-") -> User:
        email = email.lower()
        self._check_lock(email)
        user = self.users.get_by_email(email)
        ok = verify_password(password, user.password_hash if user else _dummy())
        if not user or not ok:
            key = f"login:fail:{email}"
            self.r.incr(key)
            self.r.expire(key, self.lock_seconds)
            log.warning("login failed", extra={"context": {"email": email, "ip": ip}})
            raise UnauthorizedError("Неверный email или пароль")
        self.r.delete(f"login:fail:{email}")
        log.info("login ok", extra={"context": {"user_id": user.id, "ip": ip}})
        return user

    # ---------- JWT ----------

    def issue_pair(self, user: User) -> Dict[str, Any]:
        access = self.tokens.create_access(user.id, user.email, user.role)
        refresh = self.tokens.create_refresh(user.id, user.email, user.role)
        # refresh-токен хранится в Redis: его можно отозвать, а при обновлении — заменить новым
        self.r.set(f"refresh:{refresh['payload']['jti']}", str(user.id),
                   ex=int(self.tokens.refresh_ttl.total_seconds()))
        return {"access_token": access["token"], "refresh_token": refresh["token"], "token_type": "bearer",
                "expires_in": int(self.tokens.access_ttl.total_seconds())}

    def login(self, email: str, password: str, ip: str = "-") -> Dict[str, Any]:
        return self.issue_pair(self.verify_credentials(email, password, ip))

    def refresh(self, refresh_token: str) -> Dict[str, Any]:
        payload = self.tokens.decode(refresh_token, "refresh")
        key = f"refresh:{payload['jti']}"
        # GETDEL: старый refresh-токен одноразовый (ротация). Повторное использование -> 401
        stored = self.r.getdel(key)
        if stored is None:
            log.warning("refresh token reuse or revoked", extra={"context": {"user_id": payload.get("user_id")}})
            raise UnauthorizedError("Refresh-токен отозван или уже использован")
        user = self.users.get(int(payload["user_id"]))
        if user is None:
            raise UnauthorizedError("Пользователь не найден")
        return self.issue_pair(user)

    def authenticate(self, access_token: str) -> User:
        payload = self.tokens.decode(access_token, "access")
        if self.r.exists(f"blacklist:{payload['jti']}"):
            raise UnauthorizedError("Токен отозван")
        user = self.users.get(int(payload["user_id"]))
        if user is None:
            raise UnauthorizedError("Пользователь не найден")
        return user

    def logout(self, access_token: str, refresh_token: Optional[str] = None) -> None:
        payload = self.tokens.decode(access_token, "access")
        ttl = max(int(payload["exp"] - time.time()), 1)
        self.r.set(f"blacklist:{payload['jti']}", "1", ex=ttl)  # живёт ровно до истечения токена
        if refresh_token:
            try:
                rp = self.tokens.decode(refresh_token, "refresh")
                self.r.delete(f"refresh:{rp['jti']}")
            except UnauthorizedError:
                pass
        log.info("logout", extra={"context": {"user_id": payload["user_id"]}})

    # ---------- серверные сессии в Redis (вариант A для сравнения с JWT) ----------

    def create_session(self, user: User) -> str:
        sid = secrets.token_urlsafe(32)
        self.r.set(f"session:{sid}", json.dumps({"user_id": user.id, "role": user.role}), ex=self.session_ttl)
        return sid

    def session_user(self, sid: Optional[str]) -> User:
        raw = self.r.get(f"session:{sid}") if sid else None
        if not raw:
            raise UnauthorizedError("Сессия не найдена или истекла")
        self.r.expire(f"session:{sid}", self.session_ttl)  # скользящий TTL
        user = self.users.get(int(json.loads(raw)["user_id"]))
        if user is None:
            raise UnauthorizedError("Пользователь не найден")
        return user

    def destroy_session(self, sid: Optional[str]) -> None:
        if sid:
            self.r.delete(f"session:{sid}")

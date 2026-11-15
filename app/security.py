"""Пароли, JWT и CSRF (лабы 6, 8)."""
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt

from app.errors import UnauthorizedError

# ---------- пароли ----------

BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", "12"))  # в тестах 4, чтобы не ждать


def hash_password(password: str) -> str:
    """bcrypt сам генерирует соль и хранит её внутри хеша."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------

class TokenService:
    def __init__(self, secret: str, algorithm: str = "HS256", access_minutes: int = 15, refresh_days: int = 7):
        self.secret = secret
        self.algorithm = algorithm
        self.access_ttl = timedelta(minutes=access_minutes)
        self.refresh_ttl = timedelta(days=refresh_days)

    def _encode(self, user_id: int, email: str, role: str, kind: str, ttl: timedelta) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        payload = {"user_id": user_id, "email": email, "role": role, "type": kind,
                   "jti": uuid.uuid4().hex, "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp())}
        token = jwt.encode(payload, self.secret, algorithm=self.algorithm)
        return {"token": token, "payload": payload}

    def create_access(self, user_id: int, email: str, role: str) -> Dict[str, Any]:
        return self._encode(user_id, email, role, "access", self.access_ttl)

    def create_refresh(self, user_id: int, email: str, role: str) -> Dict[str, Any]:
        return self._encode(user_id, email, role, "refresh", self.refresh_ttl)

    def decode(self, token: str, expected_type: str) -> Dict[str, Any]:
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
        except jwt.ExpiredSignatureError:
            raise UnauthorizedError("Срок действия токена истёк") from None
        except jwt.InvalidTokenError:
            raise UnauthorizedError("Недействительный токен") from None
        if payload.get("type") != expected_type:
            raise UnauthorizedError("Неверный тип токена")
        return payload


# ---------- CSRF (double submit cookie + HMAC-подпись) ----------

def generate_csrf_token(secret: str, session_id: Optional[str] = None) -> str:
    nonce = secrets.token_urlsafe(24)
    msg = f"{nonce}.{session_id or ''}".encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]
    return f"{nonce}.{sig}"


def validate_csrf_token(secret: str, token: Optional[str], cookie_token: Optional[str],
                        session_id: Optional[str] = None) -> bool:
    if not token or not cookie_token or not hmac.compare_digest(token, cookie_token):
        return False
    try:
        nonce, sig = token.split(".", 1)
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), f"{nonce}.{session_id or ''}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, expected)

"""Внедрение зависимостей (лаба 4) и проверка прав RBAC (лаба 8).

Схема: Settings -> engine / Redis / TokenService (один раз при старте, app.state)
       -> Session (на каждый запрос) -> Repository -> Service -> Handler.
Handler ничего не создаёт сам: всё приходит через Depends, поэтому в тестах легко подменить.
"""
import logging
from typing import Iterator, Optional, Set

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ForbiddenError, UnauthorizedError
from app.models import User
from app.repositories import BookRepository, LoanRepository, ReviewRepository, UserRepository
from app.services.auth import AuthService
from app.services.books import BookService, LoanService, ReviewService
from app.services.users import UserService

log = logging.getLogger("app.deps")

ROLE_PERMISSIONS = {
    "admin": {"*"},
    "librarian": {"books.read", "books.write", "loans.read_all", "reviews.write"},
    "user": {"books.read", "reviews.write", "loans.self"},
    "guest": {"books.read"},
}

bearer = HTTPBearer(auto_error=False)


def has_permission(user: Optional[User], permission: str) -> bool:
    role = user.role if user else "guest"
    perms: Set[str] = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or permission in perms


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_user_service(request: Request, db: Session = Depends(get_db)) -> UserService:
    return UserService(db, UserRepository(db), LoanRepository(db), request.app.state.email_queue)


def get_book_service(request: Request, db: Session = Depends(get_db)) -> BookService:
    return BookService(db, BookRepository(db), LoanRepository(db), request.app.state.cache)


def get_loan_service(request: Request, db: Session = Depends(get_db)) -> LoanService:
    s = request.app.state.settings
    return LoanService(db, BookRepository(db), LoanRepository(db), UserRepository(db),
                       max_active=s.max_active_loans, loan_days=s.loan_days, cache=request.app.state.cache)


def get_review_service(db: Session = Depends(get_db)) -> ReviewService:
    return ReviewService(db, ReviewRepository(db), BookRepository(db))


def get_auth_service(request: Request, db: Session = Depends(get_db)) -> AuthService:
    s = request.app.state.settings
    return AuthService(UserRepository(db), request.app.state.tokens, request.app.state.redis,
                       max_attempts=s.login_max_attempts, lock_minutes=s.login_lock_minutes,
                       session_ttl_hours=s.session_ttl_hours)


def get_current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
                     auth: AuthService = Depends(get_auth_service)) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise UnauthorizedError("Требуется заголовок Authorization: Bearer <token>",
                                headers={"WWW-Authenticate": "Bearer"})
    try:
        return auth.authenticate(creds.credentials)
    except UnauthorizedError as e:
        e.headers = {"WWW-Authenticate": 'Bearer error="invalid_token"'}
        raise


def get_optional_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
                      auth: AuthService = Depends(get_auth_service)) -> Optional[User]:
    if creds is None:
        return None
    return auth.authenticate(creds.credentials)


def require_permission(permission: str):
    """Зависимость-«декоратор»: пускает только тех, у чьей роли есть нужное право."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user, permission):
            log.warning("permission denied", extra={"context": {"user_id": user.id, "role": user.role,
                                                                "permission": permission}})
            raise ForbiddenError(f"Нужно право {permission}")
        return user

    return checker

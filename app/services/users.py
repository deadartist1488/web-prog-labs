import logging
import math
from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.models import User
from app.repositories import LoanRepository, UserRepository
from app.schemas import UserCreate, UserPatch, UserReplace
from app.security import hash_password

log = logging.getLogger("app.services.users")


def ensure_self_or_admin(actor: Optional[User], owner_id: int, action: str) -> None:
    """Проверка владения ресурсом (лаба 8): менять чужое может только admin."""
    if actor is None:
        raise ForbiddenError("Требуется вход в систему")
    if actor.id != owner_id and actor.role != "admin":
        log.warning("access denied", extra={"context": {"actor_id": actor.id, "owner_id": owner_id,
                                                        "action": action}})
        raise ForbiddenError("Недостаточно прав для этой операции")


class UserService:
    def __init__(self, session: Session, users: UserRepository, loans: LoanRepository, email_queue=None):
        self.s = session
        self.users = users
        self.loans = loans
        self.email_queue = email_queue

    def create(self, data: UserCreate, role: str = "user") -> User:
        if self.users.email_exists(data.email):
            raise ConflictError("Пользователь с таким email уже существует",
                                [{"field": "email", "message": "email уже занят"}])
        local_part = data.email.split("@", 1)[0]
        if data.password.lower() in (local_part.lower(), data.email.lower()):
            raise BusinessRuleError("Пароль не должен совпадать с логином",
                                    [{"field": "password", "message": "пароль совпадает с email"}])
        user = User(name=data.name, email=data.email, age=data.age,
                    password_hash=hash_password(data.password), role=role)
        try:
            self.users.add(user)
            self.s.commit()
        except IntegrityError:
            self.s.rollback()  # гонка: email заняли между проверкой и вставкой
            raise ConflictError("Пользователь с таким email уже существует") from None
        log.info("user created", extra={"context": {"user_id": user.id}})
        if self.email_queue is not None:
            # побочный эффект после коммита: письмо уходит в очередь, а не отправляется в запросе
            self.email_queue.enqueue(user.email, "Добро пожаловать в библиотеку",
                                     f"Здравствуйте, {user.name}!", job_id=f"welcome-{user.id}")
        return user

    def get(self, user_id: int) -> User:
        user = self.users.get(user_id)
        if user is None:
            raise NotFoundError(f"Пользователь {user_id} не найден")
        return user

    def list(self, page: int, size: int, **filters) -> Dict[str, Any]:
        items, total = self.users.list(page=page, size=size, **filters)
        return {"items": items, "total": total, "page": page, "size": size,
                "pages": math.ceil(total / size) if total else 0}

    def _apply(self, user: User, changes: Dict[str, Any]) -> User:
        if "email" in changes and changes["email"] != user.email and self.users.email_exists(changes["email"],
                                                                                             user.id):
            raise ConflictError("Пользователь с таким email уже существует",
                                [{"field": "email", "message": "email уже занят"}])
        for k, v in changes.items():
            setattr(user, k, v)
        try:
            self.s.commit()
        except IntegrityError:
            self.s.rollback()
            raise ConflictError("Пользователь с таким email уже существует") from None
        return user

    def replace(self, user_id: int, data: UserReplace, actor: Optional[User]) -> User:
        user = self.get(user_id)
        ensure_self_or_admin(actor, user.id, "user.replace")
        return self._apply(user, data.model_dump())

    def patch(self, user_id: int, data: UserPatch, actor: Optional[User]) -> User:
        user = self.get(user_id)
        ensure_self_or_admin(actor, user.id, "user.patch")
        return self._apply(user, data.model_dump(exclude_unset=True))

    def delete(self, user_id: int, actor: Optional[User]) -> None:
        user = self.get(user_id)
        ensure_self_or_admin(actor, user.id, "user.delete")
        if self.loans.count_active(user.id):
            raise BusinessRuleError("Нельзя удалить аккаунт, пока на руках есть книги")
        self.users.delete(user)
        self.s.commit()
        log.info("user deleted", extra={"context": {"user_id": user_id}})

    def set_role(self, user_id: int, role: str) -> User:
        user = self.get(user_id)
        user.role = role
        self.s.commit()
        return user

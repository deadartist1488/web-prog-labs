from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import User

USER_SORT = {"id": User.id, "name": User.name, "age": User.age, "created_at": User.created_at}


class UserRepository:
    def __init__(self, session: Session):
        self.s = session

    def get(self, user_id: int) -> Optional[User]:
        return self.s.get(User, user_id)

    def get_by_email(self, email: str) -> Optional[User]:
        # параметризованный запрос: значение уходит в БД отдельно от текста SQL
        return self.s.scalar(select(User).where(User.email == email.lower()))

    def email_exists(self, email: str, exclude_id: Optional[int] = None) -> bool:
        stmt = select(User.id).where(User.email == email.lower())
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        return self.s.scalar(stmt) is not None

    def list(self, *, page: int, size: int, search: Optional[str] = None, min_age: Optional[int] = None,
             sort: str = "id", order: str = "asc") -> Tuple[List[User], int]:
        stmt = select(User)
        if search:
            like = f"%{search.lower()}%"
            stmt = stmt.where(or_(func.lower(User.name).like(like), User.email.like(like)))
        if min_age is not None:
            stmt = stmt.where(User.age >= min_age)
        total = self.s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        col = USER_SORT.get(sort, User.id)
        stmt = stmt.order_by(col.desc() if order == "desc" else col.asc(), User.id)
        items = list(self.s.scalars(stmt.offset((page - 1) * size).limit(size)))
        return items, total

    def list_with_loans(self, limit: int = 100) -> List[User]:
        # selectinload: 2 запроса вместо N+1 (лаба 7)
        return list(self.s.scalars(select(User).options(selectinload(User.loans)).order_by(User.id).limit(limit)))

    def add(self, user: User) -> User:
        self.s.add(user)
        self.s.flush()
        return user

    def delete(self, user: User) -> None:
        self.s.delete(user)
        self.s.flush()

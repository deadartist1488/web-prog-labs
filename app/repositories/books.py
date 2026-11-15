from decimal import Decimal
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models import Book

BOOK_SORT = {"id": Book.id, "title": Book.title, "year": Book.year, "price": Book.price}


class BookRepository:
    def __init__(self, session: Session):
        self.s = session

    def get(self, book_id: int) -> Optional[Book]:
        return self.s.get(Book, book_id)

    def isbn_exists(self, isbn: str, exclude_id: Optional[int] = None) -> bool:
        stmt = select(Book.id).where(Book.isbn == isbn)
        if exclude_id is not None:
            stmt = stmt.where(Book.id != exclude_id)
        return self.s.scalar(stmt) is not None

    def list(self, *, page: int, size: int, q: Optional[str] = None, author: Optional[str] = None,
             genre: Optional[str] = None, min_price: Optional[Decimal] = None, max_price: Optional[Decimal] = None,
             available: Optional[bool] = None, sort: str = "id", order: str = "asc") -> Tuple[List[Book], int]:
        stmt = select(Book)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(or_(func.lower(Book.title).like(like), func.lower(Book.author).like(like)))
        if author:
            stmt = stmt.where(Book.author == author)
        if genre:
            stmt = stmt.where(Book.genre == genre)
        if min_price is not None:
            stmt = stmt.where(Book.price >= min_price)
        if max_price is not None:
            stmt = stmt.where(Book.price <= max_price)
        if available is True:
            stmt = stmt.where(Book.copies_available > 0)
        elif available is False:
            stmt = stmt.where(Book.copies_available == 0)
        total = self.s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        col = BOOK_SORT.get(sort, Book.id)
        stmt = stmt.order_by(col.desc() if order == "desc" else col.asc(), Book.id)
        return list(self.s.scalars(stmt.offset((page - 1) * size).limit(size))), total

    def add(self, book: Book) -> Book:
        self.s.add(book)
        self.s.flush()
        return book

    def delete(self, book: Book) -> None:
        self.s.delete(book)
        self.s.flush()

    def take_copy(self, book_id: int) -> bool:
        """Атомарно списать один экземпляр (лаба 7).

        UPDATE ... WHERE copies_available > 0 — проверка и изменение в одном операторе,
        поэтому два одновременных запроса не уведут остаток в минус.
        """
        res = self.s.execute(
            update(Book).where(Book.id == book_id, Book.copies_available > 0)
            .values(copies_available=Book.copies_available - 1)
            .execution_options(synchronize_session=False)
        )
        return res.rowcount == 1

    def return_copy(self, book_id: int) -> None:
        self.s.execute(
            update(Book).where(Book.id == book_id, Book.copies_available < Book.copies_total)
            .values(copies_available=Book.copies_available + 1)
            .execution_options(synchronize_session=False)
        )

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import BusinessRuleError, ConflictError, NotFoundError
from app.models import Book, Loan, Review, User
from app.repositories import BookRepository, LoanRepository, ReviewRepository, UserRepository
from app.schemas import BookCreate, BookOut, BookPatch, ReviewIn
from app.services.users import ensure_self_or_admin

log = logging.getLogger("app.services.books")


def book_key(book_id: int) -> str:
    return f"book:{book_id}"


class BookService:
    def __init__(self, session: Session, books: BookRepository, loans: LoanRepository, cache=None):
        self.s = session
        self.books = books
        self.loans = loans
        self.cache = cache

    def create(self, data: BookCreate) -> Book:
        if self.books.isbn_exists(data.isbn):
            raise ConflictError("Книга с таким ISBN уже есть", [{"field": "isbn", "message": "ISBN занят"}])
        book = Book(**data.model_dump(), copies_available=data.copies_total)
        try:
            self.books.add(book)
            self.s.commit()
        except IntegrityError:
            self.s.rollback()
            raise ConflictError("Книга с таким ISBN уже есть") from None
        return book

    def get(self, book_id: int) -> Book:
        book = self.books.get(book_id)
        if book is None:
            raise NotFoundError(f"Книга {book_id} не найдена")
        return book

    def get_cached(self, book_id: int) -> Dict[str, Any]:
        """Cache-aside (лаба 10): GET /books/{id} читает из Redis, при промахе — из БД."""
        def load() -> Optional[Dict[str, Any]]:
            book = self.books.get(book_id)
            return BookOut.model_validate(book).model_dump(mode="json") if book else None

        data = self.cache.get_or_load(book_key(book_id), load) if self.cache else load()
        if data is None:
            raise NotFoundError(f"Книга {book_id} не найдена")
        return data

    def list(self, page: int, size: int, **filters) -> Dict[str, Any]:
        items, total = self.books.list(page=page, size=size, **filters)
        return {"items": items, "total": total, "page": page, "size": size,
                "pages": math.ceil(total / size) if total else 0}

    def patch(self, book_id: int, data: BookPatch) -> Book:
        book = self.get(book_id)
        changes = data.model_dump(exclude_unset=True)
        if "copies_total" in changes:
            new_total = changes.pop("copies_total")
            on_hands = book.copies_total - book.copies_available
            if new_total < on_hands:
                raise BusinessRuleError(f"Сейчас выдано {on_hands} экз., нельзя сделать всего {new_total}")
            book.copies_available = new_total - on_hands
            book.copies_total = new_total
        for k, v in changes.items():
            setattr(book, k, v)
        self.s.commit()
        if self.cache:
            self.cache.invalidate(book_key(book_id))
        return book

    def delete(self, book_id: int) -> None:
        book = self.get(book_id)
        if self.loans.book_has_active(book_id):
            raise ConflictError("Книга выдана читателям, удалить нельзя")
        self.books.delete(book)
        self.s.commit()
        if self.cache:
            self.cache.invalidate(book_key(book_id))


class LoanService:
    """Выдача и возврат книг. Всё в одной транзакции (лаба 7)."""

    def __init__(self, session: Session, books: BookRepository, loans: LoanRepository, users: UserRepository,
                 max_active: int = 5, loan_days: int = 14, cache=None):
        self.s = session
        self.books = books
        self.loans = loans
        self.users = users
        self.max_active = max_active
        self.loan_days = loan_days
        self.cache = cache

    def borrow(self, user: User, book_id: int) -> Loan:
        if self.books.get(book_id) is None:
            raise NotFoundError(f"Книга {book_id} не найдена")
        if self.loans.count_active(user.id) >= self.max_active:
            raise BusinessRuleError(f"Нельзя держать больше {self.max_active} книг одновременно")
        if self.loans.active_for(user.id, book_id):
            raise ConflictError("Эта книга уже у вас на руках")
        try:
            # 1) атомарно уменьшаем остаток; 2) создаём запись о выдаче; 3) commit — всё или ничего
            if not self.books.take_copy(book_id):
                self.s.rollback()
                raise ConflictError("Свободных экземпляров нет")
            now = datetime.now(timezone.utc)
            loan = self.loans.add(Loan(user_id=user.id, book_id=book_id, taken_at=now,
                                       due_at=now + timedelta(days=self.loan_days)))
            self.s.commit()
        except IntegrityError:
            self.s.rollback()  # уникальный индекс поймал параллельную выдачу той же книги
            raise ConflictError("Эта книга уже у вас на руках") from None
        if self.cache:
            self.cache.invalidate(book_key(book_id))
        log.info("book borrowed", extra={"context": {"user_id": user.id, "book_id": book_id, "loan_id": loan.id}})
        return loan

    def give_back(self, loan_id: int, actor: User) -> Loan:
        loan = self.loans.get(loan_id)
        if loan is None:
            raise NotFoundError(f"Выдача {loan_id} не найдена")
        ensure_self_or_admin(actor, loan.user_id, "loan.return")
        if loan.returned_at is not None:
            raise ConflictError("Книга уже возвращена")
        loan.returned_at = datetime.now(timezone.utc)
        self.books.return_copy(loan.book_id)
        self.s.commit()
        if self.cache:
            self.cache.invalidate(book_key(loan.book_id))
        return loan

    def for_user(self, user_id: int, actor: User, active_only: bool = False) -> List[Loan]:
        if self.users.get(user_id) is None:
            raise NotFoundError(f"Пользователь {user_id} не найден")
        ensure_self_or_admin(actor, user_id, "loan.list")
        return self.loans.list_for_user(user_id, active_only)


class ReviewService:
    def __init__(self, session: Session, reviews: ReviewRepository, books: BookRepository):
        self.s = session
        self.reviews = reviews
        self.books = books

    def list_for_book(self, book_id: int) -> List[Review]:
        if self.books.get(book_id) is None:
            raise NotFoundError(f"Книга {book_id} не найдена")
        return self.reviews.list_for_book(book_id)

    def create(self, book_id: int, data: ReviewIn, author: User) -> Review:
        if self.books.get(book_id) is None:
            raise NotFoundError(f"Книга {book_id} не найдена")
        if self.reviews.exists(author.id, book_id):
            raise ConflictError("Вы уже оставили отзыв на эту книгу")
        review = self.reviews.add(Review(user_id=author.id, book_id=book_id, rating=data.rating, text=data.text))
        self.s.commit()
        return review

    def _get(self, review_id: int) -> Review:
        review = self.reviews.get(review_id)
        if review is None:
            raise NotFoundError(f"Отзыв {review_id} не найден")
        return review

    def update(self, review_id: int, data: ReviewIn, actor: User) -> Review:
        review = self._get(review_id)
        ensure_self_or_admin(actor, review.user_id, "review.update")
        review.rating, review.text = data.rating, data.text
        self.s.commit()
        return review

    def delete(self, review_id: int, actor: User) -> None:
        review = self._get(review_id)
        ensure_self_or_admin(actor, review.user_id, "review.delete")
        self.reviews.delete(review)
        self.s.commit()

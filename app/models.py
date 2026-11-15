"""ORM-модели (лаба 6): пользователи, книги, выдачи книг, отзывы."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("age >= 18 AND age <= 120", name="ck_users_age"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    loans: Mapped[List["Loan"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    reviews: Mapped[List["Review"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        CheckConstraint("copies_available >= 0", name="ck_books_copies_nonneg"),
        CheckConstraint("copies_available <= copies_total", name="ck_books_copies_le_total"),
        Index("idx_books_author", "author"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    author: Mapped[str] = mapped_column(String(100), nullable=False)
    isbn: Mapped[str] = mapped_column(String(13), unique=True, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    genre: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    copies_total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    copies_available: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    loans: Mapped[List["Loan"]] = relationship(back_populates="book")
    reviews: Mapped[List["Review"]] = relationship(back_populates="book", cascade="all, delete-orphan")


class Loan(Base):
    """Выдача книги читателю. Активная выдача: returned_at IS NULL."""

    __tablename__ = "loans"
    __table_args__ = (
        Index("idx_loans_user_id", "user_id"),
        Index("idx_loans_book_id", "book_id"),
        # один читатель не может одновременно держать два экземпляра одной книги
        Index(
            "uq_loans_active_user_book",
            "user_id",
            "book_id",
            unique=True,
            sqlite_where=text("returned_at IS NULL"),
            postgresql_where=text("returned_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="RESTRICT"), nullable=False)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    returned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="loans")
    book: Mapped[Book] = relationship(back_populates="loans")


class Review(Base):
    """Отзыв на книгу. Менять и удалять может только автор отзыва или admin (лаба 8)."""

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating"),
        Index("idx_reviews_book_id", "book_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="reviews")
    book: Mapped[Book] = relationship(back_populates="reviews")

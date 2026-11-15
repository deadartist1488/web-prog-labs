"""Схемы запросов и ответов (лабы 2, 3): проверка формата данных.

Проверки формата (тип, длина, диапазон, формат email/ISBN) делаются здесь, на входе.
Бизнес-правила (уникальность, лимиты, права) живут в сервисах.
"""
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, List, Optional, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated

NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё \-]*$")
ISBN_RE = re.compile(r"^(\d{9}[\dX]|\d{13})$")

T = TypeVar("T")


def _check_name(v: str) -> str:
    v = v.strip()
    if not NAME_RE.match(v):
        raise ValueError("имя может содержать только буквы, пробелы и дефис")
    return v


def _check_password(v: str) -> str:
    if not (re.search(r"[A-Za-zА-Яа-я]", v) and re.search(r"\d", v)):
        raise ValueError("пароль должен содержать буквы и цифры")
    return v


NameStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=50),
                    AfterValidator(_check_name)]
Email = Annotated[EmailStr, Field(max_length=100), AfterValidator(lambda v: v.lower())]
Age = Annotated[int, Field(ge=18, le=120, strict=True)]
Password = Annotated[str, Field(min_length=8, max_length=72), AfterValidator(_check_password)]


# ---------- пользователи ----------

class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "example": {"name": "Анна Петрова", "email": "anna@example.com", "age": 25, "password": "secret123"}})

    name: NameStr
    email: Email
    age: Age
    password: Password


class UserReplace(BaseModel):
    """PUT: полная замена, все поля обязательны."""

    model_config = ConfigDict(extra="forbid")
    name: NameStr
    email: Email
    age: Age


class UserPatch(BaseModel):
    """PATCH: частичное обновление. Отсутствующее поле = не менять; null запрещён."""

    model_config = ConfigDict(extra="forbid")
    name: Optional[NameStr] = None
    email: Optional[Email] = None
    age: Optional[Age] = None

    @model_validator(mode="after")
    def _no_nulls(self) -> "UserPatch":
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"поле {field} не может быть null")
        if not self.model_fields_set:
            raise ValueError("нужно передать хотя бы одно поле")
        return self


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    age: int
    role: str
    created_at: datetime


class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    size: int
    pages: int


# ---------- книги ----------

class BookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {
        "title": "Мастер и Маргарита", "author": "Михаил Булгаков", "isbn": "9785170900000",
        "year": 1967, "genre": "роман", "price": "450.00", "copies_total": 3}})

    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=2, max_length=100)
    isbn: str
    year: int = Field(ge=1450)
    genre: Optional[str] = Field(default=None, max_length=50)
    price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    copies_total: int = Field(default=1, ge=0, le=1000)

    @field_validator("isbn")
    @classmethod
    def _isbn(cls, v: str) -> str:
        v = v.replace("-", "").replace(" ", "").upper()
        if not ISBN_RE.match(v):
            raise ValueError("ISBN должен состоять из 10 или 13 цифр")
        return v

    @field_validator("year")
    @classmethod
    def _year(cls, v: int) -> int:
        if v > date.today().year:
            raise ValueError("год издания не может быть в будущем")
        return v

    @field_validator("title", "author")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("поле не может быть пустым")
        return v


class BookPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    author: Optional[str] = Field(default=None, min_length=2, max_length=100)
    genre: Optional[str] = Field(default=None, max_length=50)
    price: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    copies_total: Optional[int] = Field(default=None, ge=0, le=1000)


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    author: str
    isbn: str
    year: int
    genre: Optional[str]
    price: Decimal
    copies_total: int
    copies_available: int


# ---------- выдачи ----------

class LoanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    book_id: int
    taken_at: datetime
    due_at: datetime
    returned_at: Optional[datetime]


class UserWithLoans(UserOut):
    loans: List[LoanOut]


# ---------- отзывы ----------

class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: int = Field(ge=1, le=5, strict=True)
    text: str = Field(min_length=1, max_length=2000)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    book_id: int
    rating: int
    text: str
    created_at: datetime


# ---------- аутентификация ----------

class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class RefreshIn(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class CookieIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    value: str = Field(max_length=256)
    days: Optional[int] = Field(default=None, ge=0, le=365)  # None = сессионная cookie


class SettingsIn(BaseModel):
    theme: str = Field(pattern="^(light|dark)$")
    language: str = Field(default="ru", pattern="^(ru|en)$")

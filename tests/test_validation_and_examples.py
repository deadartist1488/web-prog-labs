"""Валидация схем (лаба 3) и функции из практических заданий занятия 6 (лаба 5)."""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.errors import UnauthorizedError
from app.examples import Product, calculate_discount, divide, late_fee, validate_email
from app.logging_setup import mask_sensitive
from app.middleware import endpoint_label
from app.schemas import BookCreate, UserCreate, UserPatch
from app.security import TokenService, generate_csrf_token, hash_password, validate_csrf_token, verify_password

pytestmark = pytest.mark.unit

VALID = {"name": "Анна Петрова", "email": "anna@example.com", "age": 25, "password": "secret123"}


# ---------- занятие 6, задание 1: validate_email ----------

@pytest.mark.parametrize("email,expected", [
    ("john@example.com", True),        # валидный
    ("johnexample.com", False),        # без @
    ("john@@example.com", False),      # несколько @
    ("a@b@example.com", False),
    ("@example.com", False),           # пустая локальная часть
    ("john@", False),                  # пустой домен
    ("john@localhost", False),         # домен без точки
])
def test_validate_email(email, expected):
    assert validate_email(email) is expected


# ---------- занятие 6, задания 6, 7; занятие 12, задание 5: регрессии ----------

def test_calculate_discount_regression():
    """Баг: функция возвращала размер скидки (10), а не итоговую цену (90)."""
    assert calculate_discount(100, 10) == 90
    assert calculate_discount(100, 0) == 100
    assert calculate_discount(100, 100) == 0
    with pytest.raises(ValueError):
        calculate_discount(100, 120)


def test_product_final_price_regression():
    assert Product(price=Decimal(100), discount=Decimal(10)).calculate_final_price() == Decimal("90.00")


@pytest.mark.parametrize("days,fee", [(-1, "0.00"), (0, "0.00"), (1, "10.00"), (30, "300.00"), (100, "300.00")])
def test_late_fee_regression_no_fee_on_due_date(days, fee):
    assert late_fee(days) == Decimal(fee)


def test_divide_full_coverage():
    assert divide(10, 2) == 5
    assert divide(-9, 3) == -3
    assert divide(1, 4) == 0.25
    with pytest.raises(ValueError, match="Division by zero"):
        divide(1, 0)


# ---------- занятие 4: валидация пользователя ----------

def test_valid_user_normalizes_email_and_strips_name():
    u = UserCreate(**{**VALID, "email": "Anna@Example.COM", "name": "  Анна  "})
    assert u.email == "anna@example.com" and u.name == "Анна"


@pytest.mark.parametrize("field,value", [
    ("name", "J"), ("name", "x" * 51), ("name", "John2"), ("name", "<script>"),
    ("email", "invalid-email"), ("email", ""),
    ("age", 15), ("age", 121), ("age", "abc"), ("age", "30"), ("age", None),
    ("password", "123"), ("password", "abcdefgh"), ("password", "12345678"),
])
def test_invalid_user_field(field, value):
    with pytest.raises(ValidationError) as e:
        UserCreate(**{**VALID, field: value})
    assert e.value.errors()[0]["loc"][0] == field


def test_all_errors_reported_at_once():
    """Задание 1 занятия 4: все 4 поля невалидны -> 4 ошибки в одном ответе."""
    with pytest.raises(ValidationError) as e:
        UserCreate(name="J", email="invalid-email", age=15, password="123")
    assert {err["loc"][0] for err in e.value.errors()} == {"name", "email", "age", "password"}


def test_missing_field_and_extra_field():
    with pytest.raises(ValidationError):
        UserCreate(name="Anna", email="a@b.io", age=20)  # нет password
    with pytest.raises(ValidationError):
        UserCreate(**VALID, role="admin")  # лишнее поле: нельзя самому назначить себе роль


def test_patch_distinguishes_missing_and_null():
    assert UserPatch(age=30).model_dump(exclude_unset=True) == {"age": 30}
    with pytest.raises(ValidationError):
        UserPatch(email=None)  # null явно — ошибка
    with pytest.raises(ValidationError):
        UserPatch()  # пустой PATCH — ошибка


@pytest.mark.parametrize("isbn,ok", [("978-5-17-090000-1", True), ("5170900001", True), ("123", False),
                                     ("97851709000ab", False)])
def test_book_isbn(isbn, ok):
    data = {"title": "T", "author": "Au", "isbn": isbn, "year": 2000}
    if ok:
        assert len(BookCreate(**data).isbn) in (10, 13)
    else:
        with pytest.raises(ValidationError):
            BookCreate(**data)


def test_book_year_not_in_future():
    with pytest.raises(ValidationError):
        BookCreate(title="T", author="Au", isbn="5170900001", year=3000)


# ---------- безопасность ----------

def test_password_hash_is_salted_and_verifiable():
    h1, h2 = hash_password("secret123"), hash_password("secret123")
    assert h1 != h2  # разная соль
    assert verify_password("secret123", h1) and not verify_password("Secret123", h1)
    assert not verify_password("x", "not-a-hash")


def test_tokens_types_and_expiry():
    ts = TokenService("k" * 32, access_minutes=-1)
    access = ts.create_access(1, "a@b.io", "user")["token"]
    with pytest.raises(UnauthorizedError, match="истёк"):
        ts.decode(access, "access")
    ts = TokenService("k" * 32)
    refresh = ts.create_refresh(1, "a@b.io", "user")["token"]
    with pytest.raises(UnauthorizedError, match="тип"):
        ts.decode(refresh, "access")
    with pytest.raises(UnauthorizedError):
        TokenService("other-secret" * 3).decode(ts.create_access(1, "a", "user")["token"], "access")


def test_csrf_token_validation():
    t = generate_csrf_token("s" * 32, "7")
    assert validate_csrf_token("s" * 32, t, t, "7")
    assert not validate_csrf_token("s" * 32, t, t, "8")           # чужая сессия
    assert not validate_csrf_token("s" * 32, t, None, "7")        # нет cookie
    assert not validate_csrf_token("s" * 32, "a.b", "a.b", "7")   # подделанная подпись
    assert not validate_csrf_token("s" * 32, "nodot", "nodot")


def test_mask_sensitive_hides_passwords_and_tokens():
    masked = mask_sensitive({"email": "a@b.io", "password": "x", "nested": [{"access_token": "t"}]})
    assert masked == {"email": "a@b.io", "password": "***", "nested": [{"access_token": "***"}]}


def test_endpoint_label_collapses_ids():
    assert endpoint_label("/users/42/loans") == "/users/{id}/loans"
    assert endpoint_label("/books/7") == "/books/{id}"

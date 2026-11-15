import os

os.environ.setdefault("BCRYPT_ROUNDS", "4")  # быстрее хешируем пароли в тестах

import itertools  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import app_factory  # noqa: E402
from app.models import User  # noqa: E402

_counter = itertools.count(1)


def make_settings(**overrides) -> Settings:
    base = dict(database_url="sqlite://", redis_url="fake://", cookie_secure=False, environment="test",
                log_level="WARNING", secret_key="test-secret-key-0123456789abcdef")
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def app():
    application = app_factory(make_settings())
    application.state.redis.flushall()  # fakeredis делит одно хранилище между экземплярами
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:  # with запускает lifespan: создаются таблицы
        yield c


def user_payload(**overrides):
    n = next(_counter)
    data = {"name": "Reader", "email": f"reader{n}@example.com", "age": 25, "password": "secret123"}
    data.update(overrides)
    return data


@pytest.fixture
def register(client):
    """Создаёт пользователя (при необходимости с ролью) и возвращает (user_json, headers)."""

    def _register(role: str = "user", **overrides):
        data = user_payload(**overrides)
        r = client.post("/users", json=data)
        assert r.status_code == 201, r.text
        user = r.json()
        if role != "user":
            with client.app.state.session_factory() as s:
                s.get(User, user["id"]).role = role
                s.commit()
        tokens = client.post("/auth/login", json={"email": data["email"], "password": data["password"]}).json()
        return user, {"Authorization": f"Bearer {tokens['access_token']}"}

    return _register


@pytest.fixture
def librarian(register):
    return register("librarian")[1]


def book_payload(**overrides):
    n = next(_counter)
    data = {"title": f"Book {n}", "author": "Leo Tolstoy", "isbn": f"978{n:010d}", "year": 1869,
            "genre": "novel", "price": "500.00", "copies_total": 2}
    data.update(overrides)
    return data


@pytest.fixture
def make_book(client, librarian):
    def _make(**overrides):
        r = client.post("/books", json=book_payload(**overrides), headers=librarian)
        assert r.status_code == 201, r.text
        return r.json()

    return _make

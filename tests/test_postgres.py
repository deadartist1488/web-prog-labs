"""Интеграционные тесты на настоящем PostgreSQL (лабы 6, 7, 9).

БД берётся из TEST_DATABASE_URL (например, docker-compose.test.yml) или поднимается через pgserver.
Если ни того ни другого нет — тесты пропускаются.
"""
import asyncio
import os
import threading
from decimal import Decimal

import psycopg2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import Base
from app.main import app_factory
from app.models import Book, User
from app.repositories import UserRepository
from app.schemas import UserCreate
from app.services.users import UserService
from tests.conftest import book_payload, make_settings, user_payload

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="session")
def pg_dsn(tmp_path_factory):
    dsn = os.getenv("TEST_DATABASE_URL")
    if dsn:
        return dsn
    try:
        import pgserver
    except ImportError:
        pytest.skip("нет TEST_DATABASE_URL и pgserver")
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")), cleanup_mode="stop")
    conn = psycopg2.connect(srv.get_uri())
    conn.autocommit = True
    conn.cursor().execute("CREATE DATABASE library_test")
    conn.close()
    return srv.get_uri().rsplit("/", 1)[0] + "/library_test"


@pytest.fixture
def pg_app(pg_dsn):
    app = app_factory(make_settings(database_url=pg_dsn, db_pool_size=10))
    app.state.redis.flushall()
    with TestClient(app) as client:
        yield client
    # очистка после теста: тесты не зависят от порядка запуска
    with app.state.engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
                          + " RESTART IDENTITY CASCADE"))


def _auth(client, role="user"):
    data = user_payload()
    user = client.post("/users", json=data).json()
    if role != "user":
        with client.app.state.session_factory() as s:
            s.get(User, user["id"]).role = role
            s.commit()
    token = client.post("/auth/login", json={"email": data["email"], "password": data["password"]}).json()
    return user, {"Authorization": f"Bearer {token['access_token']}"}


def test_create_user_is_really_in_postgres(pg_app):
    r = pg_app.post("/users", json=user_payload(email="pg@example.com", name="Иван"))
    assert r.status_code == 201
    with psycopg2.connect(pg_app.app.state.settings.database_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT name, email, role FROM users WHERE id = %s", (r.json()["id"],))
        assert cur.fetchone() == ("Иван", "pg@example.com", "user")
    # в PostgreSQL lower() понимает кириллицу — поиск без учёта регистра работает
    assert pg_app.get("/users", params={"search": "иван"}).json()["total"] == 1


def test_db_constraints_reject_bad_data_even_without_api(pg_app):
    with pg_app.app.state.session_factory() as s:
        s.add(User(name="Kid", email="kid@x.io", age=10, password_hash="x"))
        with pytest.raises(Exception) as e:
            s.commit()
        assert "ck_users_age" in str(e.value)


def test_repository_and_service_integration(pg_app):
    with pg_app.app.state.session_factory() as s:
        svc = UserService(s, UserRepository(s), None)  # type: ignore[arg-type]
        user = svc.create(UserCreate(name="John", email="john@example.com", age=40, password="secret123"))
        saved = UserRepository(s).get_by_email("JOHN@example.com")
        assert saved is not None and saved.id == user.id


def test_concurrent_borrow_of_last_copy(pg_app):
    """10 читателей одновременно берут последний экземпляр: выдать должны ровно одному."""
    book = pg_app.post("/books", json=book_payload(copies_total=1), headers=_auth(pg_app, "librarian")[1]).json()
    readers = [_auth(pg_app)[1] for _ in range(10)]
    barrier = threading.Barrier(len(readers))
    codes = []

    def go(h):
        barrier.wait()
        codes.append(pg_app.post(f"/books/{book['id']}/loans", headers=h).status_code)

    threads = [threading.Thread(target=go, args=(h,)) for h in readers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [201] + [409] * 9
    with pg_app.app.state.session_factory() as s:
        assert s.get(Book, book["id"]).copies_available == 0


def test_full_flow_on_postgres(pg_app):
    _, lib = _auth(pg_app, "librarian")
    user, h = _auth(pg_app)
    book = pg_app.post("/books", json=book_payload(copies_total=2, price="199.90"), headers=lib).json()
    assert book["price"] == "199.90"
    loan = pg_app.post(f"/books/{book['id']}/loans", headers=h).json()
    assert pg_app.post(f"/books/{book['id']}/loans", headers=h).status_code == 409  # частичный UNIQUE индекс
    assert pg_app.post(f"/loans/{loan['id']}/return", headers=h).status_code == 200
    assert pg_app.post(f"/books/{book['id']}/loans", headers=h).status_code == 201  # после возврата можно снова
    assert len(pg_app.get(f"/users/{user['id']}/loans", headers=h).json()) == 2


def test_migrations_match_orm(pg_dsn):
    """SQL-миграции создают ту же схему, что и модели (проверяем на отдельной схеме)."""
    from pathlib import Path

    conn = psycopg2.connect(pg_dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS mig CASCADE; CREATE SCHEMA mig; SET search_path TO mig")
    for f in sorted(Path(__file__).resolve().parents[1].joinpath("migrations").glob("*.sql")):
        cur.execute(f.read_text(encoding="utf-8"))
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='mig' ORDER BY 1")
    assert [r[0] for r in cur.fetchall()] == ["books", "loans", "reviews", "users"]
    cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname='mig' AND indexname LIKE 'uq_%'")
    assert cur.fetchall() == [("uq_loans_active_user_book",)]
    cur.execute("DROP SCHEMA mig CASCADE")
    conn.close()


# ---------- лаба 7 ----------

def test_lab07_transfer_and_rollback(pg_dsn):
    from lab07 import transactions as tx

    tx.setup(pg_dsn)
    tx.transfer(pg_dsn, 1, 2, Decimal("100"))
    assert tx.balances(pg_dsn) == {1: Decimal("900.00"), 2: Decimal("600.00")}
    with pytest.raises(tx.InsufficientFunds):
        tx.transfer(pg_dsn, 2, 1, Decimal("10000"))
    with pytest.raises(tx.AccountNotFound):
        tx.transfer(pg_dsn, 1, 999, Decimal("1"))
    with pytest.raises(ValueError):
        tx.transfer(pg_dsn, 1, 2, Decimal("-5"))
    assert tx.balances(pg_dsn) == {1: Decimal("900.00"), 2: Decimal("600.00")}  # ничего не изменилось


def test_lab07_race_naive_vs_safe(pg_dsn):
    from lab07 import transactions as tx

    tx.setup(pg_dsn)
    naive = tx.race(pg_dsn, tx.register_naive, threads=4, seats=1)
    assert naive["seats_left"] < 0 and naive["registrations"] > 1  # гонка воспроизведена
    for fn in (tx.register_for_update, tx.register_atomic):
        res = tx.race(pg_dsn, fn, threads=4, seats=1)
        assert (res["seats_left"], res["registrations"], res["no_seats"]) == (0, 1, 3)
    checked = tx.race(pg_dsn, tx.register_naive, threads=4, seats=1, table="courses")
    assert checked["check_violation"] >= 1 and checked["seats_left"] == 0
    assert tx.register_once(pg_dsn, 1, 1)
    with pytest.raises(tx.AlreadyRegistered):
        tx.register_once(pg_dsn, 1, 1)


def test_lab07_indexes_change_plans(pg_dsn):
    from lab07 import indexes

    rep = indexes.run(pg_dsn)
    for q in indexes.QUERIES:
        assert rep["before"][q]["plan_top"].startswith("Seq Scan")
        assert rep["after"][q]["uses_index"] and rep["after"][q]["cost"] < rep["before"][q]["cost"]
    p = rep["prefix"]
    assert p["name"]["uses_index"] and p["name_age"]["uses_index"] and p["name_city"]["uses_index"]
    assert not p["age"]["uses_index"] and not p["city"]["uses_index"]  # нет левого префикса
    assert rep["slow"]["after"]["cost"] < rep["slow"]["before"]["cost"]


def test_lab07_n_plus_1(pg_dsn):
    from lab07 import nplus1

    nplus1.setup(pg_dsn)
    raw = nplus1.run_raw(pg_dsn)
    assert [raw[k]["queries"] for k in ("raw_n_plus_1", "raw_join", "raw_in")] == [101, 1, 2]
    orm = nplus1.run_orm(pg_dsn)
    assert orm["get_users_without_eager"]["queries"] == 101
    assert orm["get_users_with_joinedload"]["queries"] == 1
    assert orm["get_users_with_selectinload"]["queries"] == 2
    assert all(v["orders"] == 500 for v in orm.values())


def test_async_crud_with_asyncpg(pg_dsn):
    asyncpg = pytest.importorskip("asyncpg")
    from scripts.async_db import run_demo

    result = asyncio.run(run_demo(pg_dsn))
    assert result["created"]["name"] == "Async User" and result["updated_age"] == 41 and result["deleted"] == 1
    assert asyncpg  # импорт нужен для importorskip

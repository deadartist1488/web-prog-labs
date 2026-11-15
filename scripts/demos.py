"""Демонстрации к лабам 6 и 8: SQL-инъекция, хеширование паролей, connection pool.

    python scripts/demos.py   -> docs/outputs/demos.txt
"""
import hashlib
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bcrypt  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool, QueuePool  # noqa: E402

out = []
p = out.append


# ---------- SQL-инъекция (занятие 8 задание 4, занятие 11 задание 2) ----------

def make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE, password TEXT, role TEXT);
        INSERT INTO users (email, password, role) VALUES
          ('admin@library.local', 'Adm1n-secret', 'admin'), ('anna@example.com', 'anna-pass1', 'user');
    """)
    return db


def search_user_vulnerable(db, email):
    query = f"SELECT id, email, role FROM users WHERE email = '{email}'"  # НЕЛЬЗЯ: строка склеивается с вводом
    return db.execute(query).fetchall()


def login_vulnerable(db, email, password):
    query = f"SELECT id, role FROM users WHERE email = '{email}' AND password = '{password}'"
    return db.execute(query).fetchone()


def delete_vulnerable(db, email):
    db.executescript(f"SELECT * FROM users WHERE email = '{email}'")  # executescript выполняет несколько команд


def search_user_safe(db, email):
    return db.execute("SELECT id, email, role FROM users WHERE email = ?", (email,)).fetchall()


def login_safe(db, email, password):
    return db.execute("SELECT id, role FROM users WHERE email = ? AND password = ?", (email, password)).fetchone()


def sqli() -> None:
    p("## SQL-инъекция")
    db = make_db()
    payload = "' OR '1'='1"
    p(f"1) поиск, ввод {payload!r}")
    p(f"   уязвимый:  {search_user_vulnerable(db, payload)}   <- утекли все пользователи")
    p(f"   безопасный: {search_user_safe(db, payload)}")
    bypass = "admin@library.local' --"
    p(f"2) обход авторизации, email={bypass!r}, пароль любой")
    p(f"   уязвимый:  {login_vulnerable(db, bypass, 'wrong')}   <- вошли как admin без пароля")
    p(f"   безопасный: {login_safe(db, bypass, 'wrong')}")
    drop = "x'; DROP TABLE users; --"
    p(f"3) удаление данных, ввод {drop!r}")
    delete_vulnerable(db, drop)
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    p(f"   уязвимый: таблицы после атаки = {tables}   <- таблица users удалена")
    db = make_db()
    search_user_safe(db, drop)
    p(f"   безопасный: таблицы = {db.execute('SELECT name FROM sqlite_master').fetchall()}")


# ---------- хеширование паролей (занятие 11, задание 1) ----------

def hashing() -> None:
    p("\n## Хеширование паролей")
    pwd = "secret123"
    s1 = hashlib.sha256(pwd.encode()).hexdigest()
    s2 = hashlib.sha256(pwd.encode()).hexdigest()
    p(f"sha256 без соли: одинаковые хеши для одинаковых паролей: {s1 == s2} ({s1[:16]}...)")
    t = time.perf_counter()
    for _ in range(100_000):
        hashlib.sha256(pwd.encode()).digest()
    sha_rate = 100_000 / (time.perf_counter() - t)
    p(f"sha256: ~{sha_rate:,.0f} хешей/с на одном ядре -> перебор словаря быстрый")
    for rounds in (10, 12, 14):
        t = time.perf_counter()
        h = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(rounds))
        dt = time.perf_counter() - t
        p(f"bcrypt rounds={rounds}: {dt * 1000:.0f} мс на хеш (~{1 / dt:.1f} хешей/с), пример {h.decode()[:29]}...")
    h1, h2 = bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(12)), bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(12))
    p(f"bcrypt: два хеша одного пароля различаются (соль): {h1 != h2}; оба проверяются: "
      f"{bcrypt.checkpw(pwd.encode(), h1) and bcrypt.checkpw(pwd.encode(), h2)}")


# ---------- connection pool (занятие 8, задание 8) ----------

def pooling(dsn: str) -> None:
    p("\n## Connection pool (PostgreSQL, 200 запросов SELECT 1)")
    url = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
    for name, kwargs in (("без пула (NullPool)", {"poolclass": NullPool}),
                         ("QueuePool pool_size=5", {"poolclass": QueuePool, "pool_size": 5, "max_overflow": 10})):
        engine = create_engine(url, **kwargs)
        t = time.perf_counter()
        for _ in range(200):
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        dt = time.perf_counter() - t
        with engine.connect() as conn:
            active = conn.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
                                  ).scalar()
        p(f"{name:<24} {dt * 1000:8.1f} мс всего, {dt * 5:6.2f} мс на запрос; соединений в pg_stat_activity: {active}")
        engine.dispose()


def main() -> None:
    p("# Демонстрации: SQL-инъекция, хеширование, пул соединений\n")
    sqli()
    hashing()
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        from scripts.local_postgres import get_dsn

        dsn = get_dsn()
    pooling(dsn)
    text_out = "\n".join(out) + "\n"
    (ROOT / "docs" / "outputs" / "demos.txt").write_text(text_out, encoding="utf-8")
    sys.stdout.buffer.write(text_out.encode("utf-8"))


if __name__ == "__main__":
    main()

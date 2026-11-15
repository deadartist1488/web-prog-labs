"""Лаба 7: транзакции и гонки данных (PostgreSQL, psycopg2).

Задания 1–4 занятия 9: перевод денег, гонка при записи на курс, UNIQUE, атомарный UPDATE.
"""
import threading
import time
from decimal import Decimal
from typing import Callable, Dict, List

import psycopg2
from psycopg2 import errors


class InsufficientFunds(Exception):
    pass


class AccountNotFound(Exception):
    pass


class NoSeats(Exception):
    pass


class AlreadyRegistered(Exception):
    pass


SCHEMA = """
DROP TABLE IF EXISTS registrations, courses, courses_nocheck, accounts CASCADE;
CREATE TABLE accounts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    balance DECIMAL(10, 2) NOT NULL CHECK (balance >= 0)
);
CREATE TABLE courses (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    seats INTEGER NOT NULL CHECK (seats >= 0)
);
-- копия без CHECK, чтобы гонка была видна как seats = -1
CREATE TABLE courses_nocheck (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL, seats INTEGER NOT NULL);
CREATE TABLE registrations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    course_id INTEGER NOT NULL,
    UNIQUE (user_id, course_id)
);
"""


def setup(dsn: str) -> None:
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
        cur.execute("INSERT INTO accounts (name, balance) VALUES ('Алиса', 1000), ('Борис', 500)")


# ---------- задание 1: перевод ----------

def transfer(dsn: str, from_id: int, to_id: int, amount: Decimal) -> None:
    if amount <= 0:
        raise ValueError("Сумма должна быть положительной")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True  # транзакцию открываем руками, чтобы были видны BEGIN/COMMIT/ROLLBACK
    cur = conn.cursor()
    try:
        cur.execute("BEGIN")
        # блокируем обе строки в одном порядке (по id) — так два встречных перевода не словят deadlock
        cur.execute("SELECT id, balance FROM accounts WHERE id IN (%s, %s) ORDER BY id FOR UPDATE",
                    (from_id, to_id))
        rows = {r[0]: r[1] for r in cur.fetchall()}
        if from_id not in rows or to_id not in rows:
            raise AccountNotFound(f"Счёт {from_id if from_id not in rows else to_id} не найден")
        if rows[from_id] < amount:
            raise InsufficientFunds(f"На счёте {from_id} {rows[from_id]}, нужно {amount}")
        cur.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s", (amount, from_id))
        cur.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s", (amount, to_id))
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise
    finally:
        cur.close()
        conn.close()


def balances(dsn: str) -> Dict[int, Decimal]:
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, balance FROM accounts ORDER BY id")
        return dict(cur.fetchall())


# ---------- задание 2: гонка и SELECT FOR UPDATE ----------

def _reset_course(dsn: str, table: str, seats: int) -> int:
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"TRUNCATE {table} RESTART IDENTITY; TRUNCATE registrations RESTART IDENTITY")
        cur.execute(f"INSERT INTO {table} (name, seats) VALUES ('Веб-программирование', %s) RETURNING id", (seats,))
        return cur.fetchone()[0]


def register_naive(conn, user_id: int, course_id: int, table: str = "courses_nocheck",
                   delay: float = 0.2) -> None:
    """Проверка и списание разделены — между ними другой поток успевает сделать то же самое."""
    with conn, conn.cursor() as cur:  # with conn = одна транзакция: COMMIT при успехе, ROLLBACK при ошибке
        cur.execute(f"SELECT seats FROM {table} WHERE id = %s", (course_id,))
        seats = cur.fetchone()[0]
        if seats <= 0:
            raise NoSeats()
        time.sleep(delay)  # имитация работы: окно для гонки
        cur.execute(f"UPDATE {table} SET seats = seats - 1 WHERE id = %s", (course_id,))
        cur.execute("INSERT INTO registrations (user_id, course_id) VALUES (%s, %s)", (user_id, course_id))


def register_for_update(conn, user_id: int, course_id: int, table: str = "courses_nocheck",
                        delay: float = 0.2) -> None:
    """SELECT ... FOR UPDATE: второй поток ждёт, пока первый не закончит транзакцию."""
    with conn, conn.cursor() as cur:
        cur.execute(f"SELECT seats FROM {table} WHERE id = %s FOR UPDATE", (course_id,))
        seats = cur.fetchone()[0]
        if seats <= 0:
            raise NoSeats()
        time.sleep(delay)
        cur.execute(f"UPDATE {table} SET seats = seats - 1 WHERE id = %s", (course_id,))
        cur.execute("INSERT INTO registrations (user_id, course_id) VALUES (%s, %s)", (user_id, course_id))


# ---------- задание 4: атомарный UPDATE ----------

def register_atomic(conn, user_id: int, course_id: int, table: str = "courses_nocheck",
                    delay: float = 0.0) -> None:
    """Проверка и изменение в одном операторе; rowcount говорит, удалось ли."""
    with conn, conn.cursor() as cur:
        cur.execute(f"UPDATE {table} SET seats = seats - 1 WHERE id = %s AND seats > 0", (course_id,))
        if cur.rowcount == 0:
            raise NoSeats()
        cur.execute("INSERT INTO registrations (user_id, course_id) VALUES (%s, %s)", (user_id, course_id))


def race(dsn: str, func: Callable, threads: int = 5, seats: int = 1, table: str = "courses_nocheck") -> Dict:
    course_id = _reset_course(dsn, table, seats)
    barrier = threading.Barrier(threads)
    results: List[str] = []
    lock = threading.Lock()

    conns = [psycopg2.connect(dsn) for _ in range(threads)]  # соединения заранее: старт строго одновременный

    def worker(uid: int) -> None:
        barrier.wait()  # все потоки стартуют одновременно
        try:
            func(conns[uid - 1], uid, course_id, table=table)
            outcome = "ok"
        except NoSeats:
            outcome = "no_seats"
        except errors.CheckViolation:
            outcome = "check_violation"
        with lock:
            results.append(outcome)

    start = time.perf_counter()
    ts = [threading.Thread(target=worker, args=(i + 1,)) for i in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    elapsed = time.perf_counter() - start
    for c in conns:
        c.close()
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT seats FROM {table} WHERE id = %s", (course_id,))
        left = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM registrations WHERE course_id = %s", (course_id,))
        regs = cur.fetchone()[0]
    return {"method": func.__name__, "threads": threads, "seats_initial": seats, "seats_left": left,
            "registrations": regs, "ok": results.count("ok"), "no_seats": results.count("no_seats"),
            "check_violation": results.count("check_violation"), "elapsed_ms": round(elapsed * 1000, 1)}


# ---------- задание 3: UNIQUE ----------

def register_once(dsn: str, user_id: int, course_id: int) -> str:
    try:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO registrations (user_id, course_id) VALUES (%s, %s)", (user_id, course_id))
        return "Вы записаны на курс"
    except errors.UniqueViolation:
        raise AlreadyRegistered("Вы уже записаны на этот курс") from None

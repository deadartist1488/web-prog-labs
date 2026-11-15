"""Лаба 7: проблема N+1 (задания 7, 8). 100 читателей x 5 выдач."""
import time
from collections import defaultdict
from typing import Dict, List

import psycopg2
from sqlalchemy import ForeignKey, String, create_engine, event, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    joinedload,
    mapped_column,
    relationship,
    selectinload,
    subqueryload,
)

USERS, PER_USER = 100, 5


class NBase(DeclarativeBase):
    pass


class NUser(NBase):
    __tablename__ = "np_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    orders: Mapped[List["NOrder"]] = relationship(back_populates="user", lazy="select")


class NOrder(NBase):
    __tablename__ = "np_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("np_users.id"), index=True)
    product: Mapped[str] = mapped_column(String(100))
    user: Mapped[NUser] = relationship(back_populates="orders")


def setup(dsn: str) -> None:
    engine = create_engine(dsn.replace("postgresql://", "postgresql+psycopg2://", 1))
    NBase.metadata.drop_all(engine)
    NBase.metadata.create_all(engine)
    with Session(engine) as s:
        for i in range(1, USERS + 1):
            u = NUser(id=i, name=f"reader{i}")
            u.orders = [NOrder(product=f"book {i}-{j}") for j in range(PER_USER)]
            s.add(u)
        s.commit()
    engine.dispose()


# ---------- задание 7: сырой SQL ----------

def raw_n_plus_1(cur) -> Dict:
    queries = 1
    cur.execute("SELECT id, name FROM np_users ORDER BY id LIMIT %s", (USERS,))
    result = {}
    for uid, _name in cur.fetchall():
        cur.execute("SELECT id, product FROM np_orders WHERE user_id = %s", (uid,))
        result[uid] = cur.fetchall()
        queries += 1
    return {"queries": queries, "orders": sum(len(v) for v in result.values())}


def raw_join(cur) -> Dict:
    cur.execute("""SELECT u.id, u.name, o.id, o.product FROM np_users u
                   LEFT JOIN np_orders o ON o.user_id = u.id ORDER BY u.id""")
    result = defaultdict(list)
    for uid, _n, oid, product in cur.fetchall():
        if oid is not None:
            result[uid].append((oid, product))
    return {"queries": 1, "orders": sum(len(v) for v in result.values())}


def raw_in(cur) -> Dict:
    cur.execute("SELECT id, name FROM np_users ORDER BY id LIMIT %s", (USERS,))
    ids = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT user_id, id, product FROM np_orders WHERE user_id = ANY(%s)", (ids,))
    grouped = defaultdict(list)
    for uid, oid, product in cur.fetchall():
        grouped[uid].append((oid, product))
    return {"queries": 2, "orders": sum(len(v) for v in grouped.values())}


def _timed(fn, *args, repeat: int = 5) -> Dict:
    best = None
    res: Dict = {}
    for _ in range(repeat):
        t = time.perf_counter()
        res = fn(*args)
        dt = time.perf_counter() - t
        best = dt if best is None else min(best, dt)
    res["time_ms"] = round(best * 1000, 2)
    return res


def run_raw(dsn: str) -> Dict:
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        return {f.__name__: _timed(f, cur) for f in (raw_n_plus_1, raw_join, raw_in)}


# ---------- задание 8: SQLAlchemy eager loading ----------

def run_orm(dsn: str) -> Dict:
    engine = create_engine(dsn.replace("postgresql://", "postgresql+psycopg2://", 1))
    counter = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(*_a, **_k):
        counter["n"] += 1

    def variant(option):
        def fn():
            with Session(engine) as s:
                stmt = select(NUser).order_by(NUser.id)
                if option is not None:
                    stmt = stmt.options(option(NUser.orders))
                users = s.scalars(stmt).unique().all()
                total = sum(len(u.orders) for u in users)  # обращение к связи -> ленивые запросы
            return {"orders": total}
        return fn

    report = {}
    for name, opt in (("get_users_without_eager", None), ("get_users_with_joinedload", joinedload),
                      ("get_users_with_subqueryload", subqueryload), ("get_users_with_selectinload", selectinload)):
        variant(opt)()  # прогрев
        counter["n"] = 0
        variant(opt)()
        queries = counter["n"]  # сколько SQL-запросов ушло в БД за один вызов
        report[name] = {**_timed(variant(opt), repeat=5), "queries": queries}
    engine.dispose()
    return report

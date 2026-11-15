"""Подключение к БД (лабы 4, 6): engine, фабрика сессий, connection pool."""
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_engine(url: str, *, pool_size: int = 5, max_overflow: int = 10, echo: bool = False) -> Engine:
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool  # одна общая in-memory БД на все сессии
        engine = create_engine(url, echo=echo, **kwargs)

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # в SQLite внешние ключи по умолчанию выключены
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    # PostgreSQL: QueuePool (по умолчанию) + проверка соединения перед выдачей из пула
    return create_engine(
        url, echo=echo, pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True
    )


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    from app import models  # noqa: F401  (регистрация моделей в metadata)

    if engine.dialect.name == "postgresql":
        # несколько экземпляров стартуют одновременно: advisory lock, чтобы CREATE TABLE не гонялись
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(20261115)"))
            Base.metadata.create_all(conn)
    else:
        Base.metadata.create_all(engine)

"""Точка входа. Запуск: uvicorn app.main:app_factory --factory --port 8000"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.db import init_db, make_engine, make_session_factory
from app.errors import register_error_handlers
from app.infra import Cache, EmailQueue, IdempotencyStore, make_redis
from app.logging_setup import setup_logging
from app.middleware import RequestContextMiddleware
from app.routers import auth, books, health, users, web
from app.security import TokenService

log = logging.getLogger("app")


async def _email_worker(queue: EmailQueue, interval: float = 1.0) -> None:
    """Фоновый обработчик очереди писем внутри процесса (в проде — отдельный контейнер worker)."""
    while True:
        try:
            await asyncio.to_thread(queue.process)
        except Exception:  # noqa: BLE001
            log.exception("email worker iteration failed")
        await asyncio.sleep(interval)


def _seed_admin(app: FastAPI) -> None:
    email, password = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD")
    if not email or not password:
        return
    from sqlalchemy.exc import IntegrityError

    from app.models import User
    from app.security import hash_password

    with app.state.session_factory() as s:
        if s.query(User).filter_by(email=email.lower()).first() is None:
            s.add(User(name="Администратор", email=email.lower(), age=30, role="admin",
                       password_hash=hash_password(password)))
            try:
                s.commit()
                log.info("admin user created")
            except IntegrityError:
                s.rollback()  # второй экземпляр успел создать админа раньше — это нормально


def app_factory(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.log_file)

    engine = make_engine(settings.database_url, pool_size=settings.db_pool_size,
                         max_overflow=settings.db_max_overflow, echo=settings.db_echo)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        _seed_admin(app)
        task = asyncio.create_task(_email_worker(app.state.email_queue)) if settings.run_worker else None
        log.info("startup complete", extra={"context": {"env": settings.environment,
                                                        "db": engine.url.get_backend_name()}})
        yield
        if task:
            task.cancel()
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Учебное API библиотеки: пользователи, книги, выдачи, отзывы. Лабораторные 1–10.",
        lifespan=lifespan,
    )
    redis_client = make_redis(settings.redis_url)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.redis = redis_client
    app.state.cache = Cache(redis_client, settings.cache_ttl_seconds)
    app.state.email_queue = EmailQueue(redis_client)
    app.state.idempotency = IdempotencyStore(redis_client)
    app.state.tokens = TokenService(settings.secret_key, settings.jwt_algorithm,
                                    settings.access_token_minutes, settings.refresh_token_days)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Location"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)  # добавлен последним = внешний слой

    register_error_handlers(app)
    for r in (health.router, auth.router, users.router, books.router, web.router):
        app.include_router(r)
    return app

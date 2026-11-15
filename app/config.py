"""Конфигурация приложения (лаба 4): всё читается из переменных окружения / .env."""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Library API"
    environment: str = "dev"  # dev | test | prod
    log_level: str = "INFO"
    log_file: str = ""  # пусто = только stdout

    # БД: по умолчанию SQLite, чтобы проект запускался без настройки.
    # В Docker подставляется PostgreSQL (см. docker-compose.yml).
    database_url: str = "sqlite:///./library.db"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # Redis: "fake://" = in-memory fakeredis (dev/тесты), иначе redis://host:6379/0
    redis_url: str = "fake://"

    # JWT
    secret_key: str = "change-me-in-production-please-32b"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    session_ttl_hours: int = 24

    # Безопасность
    cors_origins: List[str] = ["http://localhost:3000", "https://library.local"]
    cookie_secure: bool = True
    login_max_attempts: int = 5
    login_lock_minutes: int = 15

    # Бизнес-правила
    max_active_loans: int = 5
    loan_days: int = 14

    # Кэш и фоновые задачи (лаба 10)
    cache_ttl_seconds: int = 60
    run_worker: bool = False  # запускать фоновый обработчик очереди внутри приложения


@lru_cache
def get_settings() -> Settings:
    return Settings()

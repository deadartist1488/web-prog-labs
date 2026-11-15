# Лабораторные по веб-программированию

Учебное API библиотеки: пользователи, книги, выдачи книг и отзывы. Стек: **Python 3.12, FastAPI, SQLAlchemy 2,
PostgreSQL, Redis, nginx, Docker, pytest**. Все лабы сделаны на одном проекте, который растёт от лабы к лабе;
задания взяты из `materials/pract_*` курса.

Автор: DeadArtist

| Лаба | Тема | Где смотреть |
|---|---|---|
| 1 | Окружение, HTTP-сервер, Docker | [docs/lab_01.md](docs/lab_01.md), `Dockerfile`, `docker-compose.yml` |
| 2 | REST CRUD API, OpenAPI | [docs/lab_02.md](docs/lab_02.md), `app/routers/`, `openapi.yaml` |
| 3 | Валидация и обработка ошибок | [docs/lab_03.md](docs/lab_03.md), `app/schemas.py`, `app/errors.py` |
| 4 | Слои Handler / Service / Repository, DI, конфигурация | [docs/lab_04_05.md](docs/lab_04_05.md), `app/services/`, `app/repositories/`, `app/deps.py` |
| 5 | Тесты: unit, API, интеграционные, покрытие | [docs/lab_04_05.md](docs/lab_04_05.md), `tests/` |
| 6 | Аутентификация (JWT и сессии), cookie, CSRF, БД и миграции | [docs/lab_06.md](docs/lab_06.md), `app/services/auth.py`, `migrations/` |
| 7 | Транзакции, гонки, индексы, N+1 | [docs/lab_07.md](docs/lab_07.md), `lab07/` |
| 8 | nginx (reverse proxy, HTTPS, rate limit) и безопасность | [docs/lab_08.md](docs/lab_08.md), `nginx/` |
| 9* | Диагностика: request id, логи, метрики, тестовое окружение | [docs/lab_09_10.md](docs/lab_09_10.md), `app/middleware.py`, `prometheus/` |
| 10* | Redis: кэш, очередь писем, идемпотентность | [docs/lab_09_10.md](docs/lab_09_10.md), `app/infra.py`, `app/worker.py` |

## Запуск

**Docker (весь стенд: nginx → 2 экземпляра приложения → PostgreSQL + Redis, worker, Prometheus):**

```bash
cp .env.example .env            # поменять пароли и SECRET_KEY
sh scripts/make_cert.sh         # самоподписанный сертификат для HTTPS
docker compose up -d --build
docker compose ps
curl -k https://localhost/health
```

- Swagger UI: https://localhost/docs
- Prometheus: http://localhost:9090
- Первый администратор создаётся из `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

**Без Docker:**

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app_factory --factory --reload --port 8000
```

По умолчанию используются SQLite (`library.db`) и Redis в памяти (`REDIS_URL=fake://`), так что ничего ставить не
нужно. Для PostgreSQL без Docker: `python scripts/local_postgres.py` выводит DSN для `DATABASE_URL`.

## Проверка

```bash
pytest                                   # 121 тест (unit + API + PostgreSQL)
pytest -m unit                           # быстрые unit-тесты
pytest --cov --cov-report=term-missing   # покрытие: 96 %
ruff check .                             # линтер
python scripts/lab07_demo.py             # демонстрации лабы 7 на PostgreSQL
python scripts/demos.py                  # SQL-инъекция, bcrypt, connection pool
sh scripts/nginx_checks.sh               # проверки nginx (нужны nginx и 2 экземпляра приложения)
sh scripts/api_demo.sh                   # сценарий работы с API через nginx
```

Тесты PostgreSQL сами поднимают временный сервер через `pgserver`. Можно использовать и Docker:
`docker compose -f docker-compose.test.yml up -d`, затем
`TEST_DATABASE_URL=postgresql://test:test@localhost:5433/test_db pytest -m postgres`.

**Как проверялось.** Стенд запущен в Docker Desktop (`docker compose up -d --build`):

- все 7 контейнеров поднялись, приложения, PostgreSQL и Redis в статусе `healthy`;
- миграции применились;
- nginx делит запросы между `app1` и `app2` поровну, rate limit отдаёт 429;
- Prometheus видит обе цели, worker отправляет письма из очереди.

Вывод: [`docs/outputs/docker_checks.txt`](docs/outputs/docker_checks.txt) и
[`docs/outputs/api_demo_docker.txt`](docs/outputs/api_demo_docker.txt), команда `sh scripts/docker_checks.sh`.
Ту же схему можно запустить и без Docker (так делались первые прогоны):

- PostgreSQL через `pgserver`;
- общий Redis через `scripts/local_redis.py`;
- два uvicorn на 8000 и 8001;
- portable nginx с `nginx/nginx.local.conf` (`nginx_checks.txt`, `api_demo.txt`).

## Скриншоты и вывод команд

| Лаба | Скриншоты (`docs/screenshots/`) | Вывод команд (`docs/outputs/`) |
|---|---|---|
| 1 | `health.png` | `lab01_curl_httpbin.txt`, `docker_checks.txt` (compose ps, миграции) |
| 2 | `swagger_ui.png`, `books_list.png`, `users_404.png` | `lab02_httpbin_jsonplaceholder.txt`, `api_demo.txt` |
| 3 | `users_404.png` | `api_demo.txt` (400 / 404 / 409) |
| 5 | — | `pytest_coverage.txt` |
| 6 | `form_csrf.png` | `api_demo.txt` (login, refresh, Idempotency-Key), `demos.txt` (pool) |
| 7 | — | `lab07_demo.txt` |
| 8 | `static_page.png`, `xss_escaped.png` | `nginx_checks.txt`, `docker_checks.txt`, `demos.txt` (SQL-инъекция, bcrypt) |
| 9 | `metrics.png`, `prometheus_targets.png`, `prometheus_alerts.png` | `log_trace.txt`, `docker_checks.txt` |
| 10 | — | `api_demo.txt` (Idempotency-Key), тесты `test_diagnostics_jobs.py` |

## Структура

```
app/
  main.py            создание приложения, middleware, роутеры
  config.py          настройки из окружения
  db.py, models.py   подключение к БД и ORM-модели
  schemas.py         схемы запросов и ответов (валидация)
  errors.py          единый формат ошибок
  middleware.py      request id, access-лог, метрики, защитные заголовки
  security.py        bcrypt, JWT, CSRF
  deps.py            DI и RBAC
  routers/           handlers: users, books, auth, web, health
  services/          бизнес-логика
  repositories/      доступ к данным
  infra.py           Redis: кэш, очередь писем, идемпотентность
  worker.py          фоновый обработчик очереди
lab07/               транзакции, индексы, N+1 (лаба 7)
migrations/          SQL-миграции
nginx/               конфиги nginx и статика
prometheus/          сбор метрик и алерты
scripts/             демо-скрипты и утилиты
tests/               pytest
docs/                отчёты по лабам, скриншоты, выводы команд
```

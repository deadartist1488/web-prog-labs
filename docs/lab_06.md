# Лаба 6. Аутентификация и БД (занятия 7–8)

**Что сделано.** Два варианта аутентификации над одной таблицей пользователей:

- **JWT** (`/auth/*`): access-токен на 15 минут, refresh на 7 дней с ротацией, blacklist отозванных токенов в Redis;
- **серверная сессия** (`/session/*`): id сессии лежит в HttpOnly-cookie, данные сессии — в Redis с TTL 24 часа.

Пароли хранятся в bcrypt. После 5 неудачных входов вход блокируется на 15 минут (429). Есть CSRF-защита формы и
настроек, а работа с БД идёт через ORM с параметризованными запросами, пулом соединений и SQL-миграциями.

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/auth/register` | регистрация (валидация email, bcrypt) |
| POST | `/auth/login` | `{"access_token", "refresh_token", "token_type": "bearer", "expires_in": 900}` |
| POST | `/auth/refresh` | новая пара токенов; старый refresh одноразовый (`GETDEL` в Redis) |
| GET | `/auth/profile` | профиль, нужен `Authorization: Bearer` |
| POST | `/auth/logout` | `jti` access-токена в `blacklist:*` до его истечения + удаление refresh |
| POST / GET / POST | `/session/login`, `/session/profile`, `/session/logout` | то же на сессиях |
| POST / GET / POST | `/set-cookie`, `/get-cookie`, `/delete-cookie` | работа с cookie |
| GET / POST | `/form`, `/submit` | HTML-форма с CSRF-токеном |
| GET / POST | `/settings` | защищённые настройки: JWT + заголовок `X-CSRF-Token` |
| GET | `/users/{id}/loans` | выдачи пользователя (аналог `/users/{id}/orders`) |

Payload access-токена: `{"user_id": 3, "email": "...", "role": "user", "type": "access", "jti": "...", "iat": ..., "exp": ...}`.

Схема БД — три миграции, которые применяются автоматически при первом старте контейнера `postgres`
(`./migrations:/docker-entrypoint-initdb.d`):

- `migrations/001_create_users.sql` — пользователи: `UNIQUE(email)`, `CHECK(age BETWEEN 18 AND 120)`;
- `002_create_books.sql` — книги: `CHECK(copies_available >= 0 AND <= copies_total)`;
- `003_create_loans_reviews.sql` — выдачи и отзывы. FK `ON DELETE CASCADE/RESTRICT`, частичный уникальный индекс
  «одна активная выдача книги на читателя».

Тест `test_migrations_match_orm` проверяет, что миграции дают ту же схему, что и ORM-модели.

```bash
docker compose down -v && docker compose up -d       # пересоздать БД: миграции применятся заново
docker compose exec postgres psql -U library -d library -c "\dt"
docker compose exec postgres psql -U library -d library -c "\d loans"
```

---

## Занятие 7 — ответы

**Задание 1. Cookie.** `POST /set-cookie {"name","value","days"}`. Если `days` не передан, cookie сессионная, без
`Max-Age`; иначе `Max-Age = days * 86400`, то есть 1 день или 30 дней. Флаги: `HttpOnly`, `Secure` (из настройки
`COOKIE_SECURE`), `SameSite=Lax`. Каждая операция логируется, при этом имя cookie пишется, а значение нет. Тест
`test_cookie_endpoints`.

**Задание 2. Session-based.** `/session/login` проверяет пароль (bcrypt) и создаёт `session:<random>` в Redis.
`/session/profile` без сессии отвечает 401. Попытки входа логируются: успешные с `user_id`/IP, неудачные с email/IP.
Уникальность email обеспечивает `UNIQUE` в БД.

**Задание 3. JWT.**

- Access живёт 15 минут, refresh — 7 дней.
- `exp` проверяет PyJWT (`ExpiredSignatureError` → 401 «Срок действия токена истёк»).
- Blacklist ведётся в Redis по `jti` с TTL до истечения токена, поэтому мусор не копится.
- Токен проверяет зависимость `get_current_user`; в FastAPI это аналог middleware для защищённых маршрутов.

### Задание 4. Session vs JWT

| Критерий | Session (вариант A) | JWT (вариант B) |
|---|---|---|
| Сложность реализации | проще: случайный id и запись в хранилище | сложнее: подпись, типы токенов, refresh, ротация, blacklist |
| Производительность | обращение в Redis на каждый запрос (~0,1–1 мс) | подпись проверяется локально, но у нас ещё проверка blacklist в Redis |
| Масштабируемость | нужен общий стор сессий (Redis) для всех экземпляров | без blacklist — stateless, любой экземпляр проверит токен сам |
| Безопасность | id ничего не значит без сервера; HttpOnly + SameSite защищают от XSS-кражи и CSRF | токен самодостаточен: утечка = доступ до `exp`; хранить в JS-памяти, не в localStorage |
| Отзыв | мгновенный: удалили ключ — сессии нет | сложный: нужен blacklist или короткий TTL access + отзыв refresh |

**Вывод:** для браузерного сайта на одном домене проще и безопаснее сессии в HttpOnly-cookie. Для API с мобильными и
сторонними клиентами и несколькими сервисами удобнее JWT с коротким access и отзываемым refresh. Оба варианта есть в
проекте, тесты `test_session_based_auth_cookie_flags` и `test_refresh_rotation_and_logout`.

**Задание 5. CSRF.**

- `GET /form` генерирует токен (`nonce.HMAC(secret)`), кладёт его в скрытое поле формы и в cookie `csrf_token`
  (`HttpOnly`, `SameSite=Strict`).
- `POST /submit` сверяет поле с cookie и проверяет подпись, а заголовок `Origin` должен совпадать с хостом.
- Попытки с плохим токеном или чужим Origin логируются и получают 403. Тест `test_csrf_form_flow`.

**Задание 6. Сессии в Redis.**

- Ключ `session:<id>` с TTL 24 часа; TTL продлевается при каждом обращении (скользящая сессия), по истечении Redis
  удаляет ключ сам.
- Сессии живут вне процесса приложения, поэтому переживают его перезапуск, и их видят оба экземпляра `app1`/`app2`
  (общий Redis в compose).
- Операции с Redis логируются в сервисах: вход, выход, отказ.

### Задание 7. Разбор Set-Cookie

```
session_id=abc123; HttpOnly; Secure; SameSite=Strict
user_id=123; Expires=Wed, 09 Jun 2025 10:18:14 GMT
theme=dark; Path=/settings; SameSite=Lax
```

1. Атрибуты:
   - `HttpOnly` — cookie недоступна из JS (`document.cookie`);
   - `Secure` — отправляется только по HTTPS;
   - `SameSite=Strict` — не уходит ни с каких межсайтовых запросов;
   - `SameSite=Lax` — уходит только при переходе по ссылке верхнего уровня (GET);
   - `Expires` — абсолютная дата истечения; без `Expires`/`Max-Age` cookie сессионная;
   - `Path=/settings` — отправляется только на `/settings` и вложенные пути.
2. На `/settings` уйдут все три: `user_id` уже истекла (дата 2025 г. в прошлом), так что фактически только
   `session_id` (если запрос по HTTPS) и `theme`.
3. При переходе с другого сайта по ссылке `session_id` (Strict) не уйдёт. `theme` (Lax) уйдёт, если ссылка ведёт на
   `/settings`. `user_id` (без SameSite: в современных браузерах это Lax по умолчанию) уйдёт при обычном переходе по
   ссылке, но не в фоновом POST или iframe.
4. Из JavaScript доступны `user_id` и `theme`, у них нет `HttpOnly`.

**Задание 8. Refresh token.**

1. Логин выдаёт пару токенов, а `jti` refresh-токена кладётся в Redis на 7 дней.
2. Запросы идут с access-токеном.
3. Access истёк → `POST /auth/refresh`.
4. Сервер атомарно забирает ключ (`GETDEL`) и выдаёт новую пару. Это ротация: старый refresh больше не работает,
   повторное использование даёт 401 и пишется в лог как подозрение на кражу.
5. Refresh истёк или удалён при logout → нужно логиниться заново.

Тесты `test_refresh_rotation_old_token_cannot_be_reused` и `test_refresh_rotation_and_logout`.

**Задание 9. Аудит cookie:**

1. Все аутентификационные cookie (`sid`, `csrf_token`) — `HttpOnly`.
2. `Secure` включён (в prod `COOKIE_SECURE=true`); в тестах выключен только потому, что TestClient ходит по http.
3. `SameSite` везде задан: `Strict` для `sid` и `csrf_token`, `Lax` для прочих.
4. В `sid` лежит только случайный id (`secrets.token_urlsafe(32)`), подделать его нельзя, поэтому подписывать нечего.
   CSRF-токен подписан HMAC.
5. Чувствительных данных в cookie нет: ни ролей, ни email, ни user_id.

**Задание 10. Полная система.** Реализованы все пункты:

- регистрация с проверкой email;
- логин с JWT;
- защищённые эндпоинты (`/auth/profile`, `/users/{id}` PUT/PATCH/DELETE, `/books` на запись);
- refresh с ротацией;
- logout с blacklist;
- `/settings` с JWT + CSRF;
- логирование всех операций;
- единый формат ошибок.

---

## Занятие 8 — ответы

**Задание 1. Подключение.** Параметры приходят из окружения (`DATABASE_URL`, `DB_POOL_SIZE`...). Сессия — это
контекст-менеджер, который закрывается автоматически (`get_db` в `app/deps.py`, `with session_factory() as s`).
Ошибка подключения превращается в 503 «База данных временно недоступна» (`OperationalError` в `app/errors.py`).
`/health` пишет в `checks` `database: down`.

**Задания 2 и 5. Таблицы, ограничения, JOIN.** Таблицы `users` и `loans` (аналог `orders`) описаны в миграциях.
Ограничения UNIQUE/CHECK/NOT NULL работают на уровне БД: тест `test_db_constraints_reject_bad_data_even_without_api`
пытается вставить возраст 10 в обход API и получает ошибку `ck_users_age`.

Запросы:

```sql
-- INNER JOIN: читатели, у которых есть выдачи
SELECT u.name, l.book_id FROM users u JOIN loans l ON l.user_id = u.id;
-- LEFT JOIN + COUNT: все читатели, даже без выдач
SELECT u.name, COUNT(l.id) AS loans FROM users u LEFT JOIN loans l ON l.user_id = u.id GROUP BY u.id, u.name;
-- сумма: стоимость книг на руках у каждого
SELECT u.name, COALESCE(SUM(b.price), 0) AS on_hands
FROM users u LEFT JOIN loans l ON l.user_id = u.id AND l.returned_at IS NULL
LEFT JOIN books b ON b.id = l.book_id GROUP BY u.id, u.name;
```

**Задание 3. CRUD.**

- Репозитории и сервисы работают через ORM: SQLAlchemy всегда отправляет значения параметрами.
- Количество затронутых строк проверяется через `rowcount` (`BookRepository.take_copy`).
- Дубликат email даёт 409, отсутствующая запись — 404.
- Каждая операция выполняется в транзакции (`commit`/`rollback`).

**Задание 4. SQL-инъекция.** Демонстрация лежит в `scripts/demos.py`, вывод — в
[`outputs/demos.txt`](outputs/demos.txt). Уязвимый вариант с `f"... WHERE email = '{email}'"` при вводе `' OR '1'='1`
отдаёт всех пользователей. Параметризованный (`WHERE email = ?`) возвращает пустой список, потому что ввод
воспринимается как значение, а не как часть SQL.

**Задание 6. Миграции.** `docker compose down -v && docker compose up -d`: том удалён, postgres инициализируется
заново и выполняет `001_…sql`, `002_…`, `003_…` по порядку. `\dt` показывает `books, loans, reviews, users`.

**Задание 7. ORM SQLAlchemy.**

- Модели: `app/models.py`.
- CRUD: `app/repositories/*`.
- Фильтрация и сортировка: `UserRepository.list` (поиск по имени и email, `min_age`, сортировка по белому списку
  колонок) и `BookRepository.list`.

**Задание 8. Connection pooling.** Замер в `scripts/demos.py`: 200 запросов `SELECT 1` к PostgreSQL.

| Вариант | Всего | На запрос |
|---|---|---|
| без пула (`NullPool`, новое соединение каждый раз) | 12 078 мс | 60,4 мс |
| `QueuePool(pool_size=5, max_overflow=10)` | 235 мс | 1,2 мс |

Пул быстрее примерно в 50 раз: соединение с PostgreSQL дорогое (процесс на сервере, аутентификация). Число
соединений смотрится через `SELECT count(*) FROM pg_stat_activity`.

**Задание 9. Асинхронный доступ.** `scripts/async_db.py`: `asyncpg` с пулом (`create_pool`), асинхронный CRUD,
дубликат обрабатывается (`UniqueViolationError` → понятная ошибка), 20 параллельных чтений идут через `asyncio.gather`.
Тест `test_async_crud_with_asyncpg`.

**Задание 10. API с БД в Docker.** Полный стенд — `docker-compose.yml`:

- `postgres:15-alpine` с volume и healthcheck;
- `app` получает `DATABASE_URL` к postgres;
- `/users`, `/users/{id}`, `/users/{id}/loans`;
- параметризованные запросы, ошибки 400/404/409, пул соединений, логирование запросов.

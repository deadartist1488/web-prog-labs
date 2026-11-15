# Лаба 8. Nginx и безопасность (занятия 10–11)

## Nginx

Две конфигурации с одинаковой логикой:

- [`nginx/nginx.conf`](../nginx/nginx.conf) для Docker: upstream `app1:8000`, `app2:8000`, сертификаты в
  `nginx/certs/`, порты 80 и 443;
- [`nginx/nginx.local.conf`](../nginx/nginx.local.conf) для запуска без Docker (portable nginx 1.26.2 под Windows):
  upstream `127.0.0.1:8000/8001`, порты 8080 и 8443, плюс порт 8081 с round-robin для сравнения.

Общие настройки проксирования вынесены в [`nginx/proxy_params.conf`](../nginx/proxy_params.conf).

```bash
sh scripts/make_cert.sh                                   # самоподписанный сертификат (CN=localhost)
# Docker:
docker compose up -d --build && curl -k https://localhost/health
# без Docker (так проверялось локально):
python scripts/local_redis.py 6390 &                       # общий Redis для двух экземпляров
DATABASE_URL=... REDIS_URL=redis://127.0.0.1:6390/0 uvicorn app.main:app_factory --factory --port 8000 &
DATABASE_URL=... REDIS_URL=redis://127.0.0.1:6390/0 uvicorn app.main:app_factory --factory --port 8001 &
cd tools/nginx-1.26.2 && ./nginx.exe -c ../../nginx/nginx.local.conf && cd ../..
sh scripts/nginx_checks.sh > docs/outputs/nginx_checks.txt
```

Результаты проверок: [`outputs/nginx_checks.txt`](outputs/nginx_checks.txt). Скриншоты страниц через nginx:
`screenshots/static_page.png`, `health.png`, `swagger_ui.png`.

| Что | Как настроено | Проверка |
|---|---|---|
| HTTP → HTTPS | `server { listen 80; return 301 https://... }` | `GET http://localhost:8080/health -> 301 Location: https://localhost:8443/health` |
| HTTPS + HSTS | `listen 443 ssl; http2 on;` TLS 1.2/1.3, `Strict-Transport-Security max-age=31536000` | заголовок в ответе |
| Версия nginx скрыта | `server_tokens off` | `Server: nginx` без номера |
| Статика | `location /static/ { alias ...; expires 30d; }` | `Expires: +30 дней`, `Cache-Control: max-age=2592000` |
| gzip | `gzip on; gzip_types text/css application/json ...` | `Content-Encoding: gzip` на `/openapi.json` |
| Reverse proxy | `proxy_set_header Host / X-Real-IP / X-Forwarded-For / X-Forwarded-Proto / X-Request-ID` | `/api/time` видит `x_forwarded_for: "203.0.113.7, 127.0.0.1"`, `x_real_ip` |
| Балансировка | `upstream { least_conn; server app1; server app2; keepalive 16; }` | 12 запросов: 6 на `:8000`, 6 на `:8001`; round-robin тоже 6/6 |
| Rate limit | `limit_req_zone ... rate=10r/s;` `limit_req burst=20 nodelay;` для логина отдельно 1 r/s | 60 одновременных запросов: 36 × 200, 24 × 429 |
| 429/502/503/504 в JSON | `error_page 429 = @too_many; ...` | `{"error":{"code":"TOO_MANY_REQUESTS",...,"request_id":"..."}}` |
| Тайм-ауты | общие 30 с; для `/api/long` — `proxy_read_timeout 300s` (в local-конфиге 3 с для демонстрации), `proxy_next_upstream off` | `seconds=1 -> 200`, `seconds=5 -> 504 GATEWAY_TIMEOUT` через 3 с |
| Логи | свой `log_format` с `rt=$request_time urt=$upstream_response_time upstream=$upstream_addr rid=$request_id`; для API отдельный `api_access.log` | строка лога в выводе |
| Лимит тела | `client_max_body_size 10m` | тело больше 10 МБ nginx отклоняет с 413 (отдельно не замерялось) |
| Безопасность | `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` (`add_header ... always`); дубли от приложения убраны `proxy_hide_header` | заголовки в ответе |

### Ответы на задания занятия 10

1. **Установка, базовая раздача.** Под Linux: `apt install nginx`; у нас nginx в Docker (`nginx:1.27-alpine`) или
   portable под Windows. Страница `nginx/static/index.html` отдаётся по `/static/index.html`.
2. **Статика.** Есть `css/style.css` и `js/app.js`, location с `alias`, `expires 30d` и `gzip`. JS со страницы ходит в
   API `/api/time` через тот же nginx.
3. **Reverse proxy.**
   - Приложение работает на 8000, nginx на 8443.
   - Заголовки до приложения доходят: `X-Forwarded-For` дописывается (`$proxy_add_x_forwarded_for`, то есть
     клиентский адрес плюс адрес nginx), `X-Real-IP` содержит адрес клиента.
   - uvicorn запущен с `--proxy-headers`, поэтому видит настоящую схему и IP.
4. **Upstream.**
   - Два экземпляра, 8000 и 8001.
   - `least_conn` отправляет запрос туда, где меньше активных соединений. На одинаково быстрых запросах распределение
     как у round-robin (6/6). Разница видна, когда запросы разной длины: долгий запрос занимает экземпляр, и новые
     идут на второй. Round-robin раздаёт по кругу, не глядя на нагрузку.
   - `max_fails=3 fail_timeout=10s` выключает упавший экземпляр. Это проверено, когда один экземпляр не стартовал:
     nginx пометил его как `temporarily disabled` и слал всё на живой.
5. **HTTPS.** Локально используется самоподписанный сертификат (`scripts/make_cert.sh`). Для настоящего домена —
   Let's Encrypt:
   - `certbot certonly --webroot -w /var/www/certbot -d your-domain.com`;
   - для проверки `location /.well-known/acme-challenge/` уже вынесен в HTTP-сервер;
   - редирект HTTP → HTTPS и HSTS настроены.
6. **Docker Compose с nginx.** `docker-compose.yml`: nginx монтирует `nginx.conf`, `proxy_params.conf`, статику и
   сертификаты и стартует после того, как `app1` и `app2` стали `healthy`.
7. **Логи.**
   - Свой формат с `$request_time` и `$upstream_response_time`.
   - Отдельный `api_access.log` для HTTPS-сервера.
   - По логу видно, какой upstream обработал запрос и сколько времени заняли nginx и приложение. Например, при 504:
     `rt=3.010 urt=3.010 upstream=127.0.0.1:8001`.
8. **Тайм-ауты.**
   - `GET /api/long?seconds=N` — долгий эндпоинт.
   - Разные `proxy_read_timeout` для `/` и `/api/long`.
   - 504 отдаётся в JSON.
   - `proxy_next_upstream off`, чтобы долгий запрос не повторялся на втором сервере. Без этого nginx отправлял его
     дальше, и клиент ждал вдвое дольше.
9. **Безопасность.**
   - `server_tokens off`, `client_max_body_size 10m`;
   - защитные заголовки, проверка через `curl -kI https://localhost:8443/health`;
   - `X-XSS-Protection` не ставим: современные браузеры этот заголовок игнорируют, а защиту даёт CSP.
10. **Rate limiting.**
    - `10r/s` + `burst=20 nodelay`: из 60 одновременных запросов прошло 36, отклонено 24 с 429. Прошло чуть больше
      21, потому что запросы растянулись во времени и лимит частично восстановился.
    - Нагрузка давалась параллельным curl (`scripts/nginx_checks.sh`); `ab`/`wrk` под Windows без WSL недоступны.
    - Ответ 429 — JSON с `Retry-After: 1`.

## Безопасность (занятие 11)

| Угроза | Защита | Где |
|---|---|---|
| Утечка паролей | bcrypt, 12 раундов, соль внутри хеша | `app/security.py` |
| Перебор паролей | блокировка после 5 неудач на 15 мин (429 + `Retry-After`), лимит nginx 1 r/s на `/auth/login`, одинаковый ответ и время для «нет email» и «неверный пароль» | `services/auth.py` |
| SQL-инъекция | только ORM и параметризованные запросы; сортировка по белому списку колонок | `repositories/*` |
| XSS | весь пользовательский ввод в HTML проходит через `html.escape`; CSP `default-src 'self'`; токены в HttpOnly-cookie | `routers/web.py`, `middleware.py` |
| CSRF | токен в форме + cookie (double submit), HMAC-подпись, `SameSite=Strict`, проверка `Origin` | `security.py`, `routers/web.py` |
| Чужие данные (IDOR) | проверка владельца: изменить/удалить пользователя, выдачу, отзыв может только владелец или admin | `services/users.py: ensure_self_or_admin` |
| Повышение привилегий | RBAC: `admin` (всё), `librarian` (книги), `user` (отзывы, свои выдачи), `guest` (чтение); лишнее поле `role` при регистрации даёт 400 | `deps.py: require_permission` |
| Секреты в коде | `.env` + `.gitignore`, `.env.example` без настоящих значений | `config.py` |
| Чужие сайты в браузере | CORS только для `CORS_ORIGINS`, явные методы и заголовки | `main.py` |
| Clickjacking и MIME sniffing | `X-Frame-Options`, `frame-ancestors`, `nosniff` | middleware + nginx |

### Ответы на задания

**Задание 1. Хеширование.** Вывод: [`outputs/demos.txt`](outputs/demos.txt).

- sha256 без соли даёт одинаковый хеш для одинаковых паролей и считает около 940 000 хешей в секунду на ядро, так что
  перебор словаря идёт быстро.
- bcrypt при 12 раундах тратит около 270 мс на хеш (меньше 4 в секунду), каждый +1 раунд вдвое медленнее. Два хеша
  одного пароля разные из-за соли.
- Битый хеш не роняет проверку: `verify_password` возвращает `False`.

**Задание 2. SQL-инъекция** (`scripts/demos.py`, SQLite):

1. Ввод `' OR '1'='1` в поиск через уязвимую функцию отдаёт **всех** пользователей.
2. Email `admin@library.local' --` даёт вход как admin **без пароля**: остаток условия закомментирован.
3. `x'; DROP TABLE users; --` при выполнении нескольких команд за раз **удаляет таблицу**.
4. Параметризованный вариант на те же три ввода возвращает пусто или `None`, таблица цела. Значение передаётся
   драйверу отдельно от текста SQL и никогда не становится кодом.

**Задание 3. XSS.** `GET /echo?text=<script>...` и `POST /submit` экранируют вывод. Скриншот
`screenshots/xss_escaped.png`: тег показан как текст и не выполнился. CSP запрещает inline-скрипты с чужих доменов,
а cookie с токенами `HttpOnly` и недоступны для `document.cookie`. Ввод тоже валидируется: имя только из букв, поэтому
`<script>` в имени даёт 400. Тесты `test_xss_echo_is_escaped`, `test_csrf_form_flow`.

**Задание 4. CSRF.** Функции `generate_csrf_token` и `validate_csrf_token`, форма `/form`, `SameSite=Strict`, проверка
`Origin`. Атака: POST с токеном `forged.token`, или с `Origin: https://evil.com`, или без cookie — во всех случаях
**403**.

**Задание 5. RBAC.** `ROLE_PERMISSIONS` в `app/deps.py`:

```python
"admin": {"*"}, "librarian": {"books.read", "books.write", ...},
"user": {"books.read", "reviews.write", "loans.self"}, "guest": {"books.read"}
```

`has_permission(user, perm)` проверяет право; `require_permission("books.write")` — это зависимость-«декоратор» для
маршрута. Читатель на `POST /books` получает 403, библиотекарь — 201.

**Задание 6. Проверка владения.**

- Отзывы, выдачи и профиль проверяются через `ensure_self_or_admin`.
- Подмена id в URL (`PUT /reviews/{чужой id}`) даёт 403.
- Каждая попытка пишется в лог как `access denied` с `actor_id`, `owner_id` и `action`.
- Тест `test_reviews_ownership`.

**Задание 7. Секреты.** `.env` (в `.gitignore`), `.env.example` с заглушками, чтение через pydantic-settings.
`SECRET_KEY` по умолчанию годится только для разработки; в проде задаётся окружением (`openssl rand -hex 32`).

**Задание 8. CORS.** Разрешены только источники из `CORS_ORIGINS`, методы `GET/POST/PUT/PATCH/DELETE`, заголовки
`Content-Type, Authorization, X-CSRF-Token, Idempotency-Key, X-Request-ID`. Проверка curl-ом:

```bash
curl -k -i -X OPTIONS https://localhost:8443/users -H "Origin: https://library.local" -H "Access-Control-Request-Method: POST"
# -> access-control-allow-origin: https://library.local
curl -k -i -X OPTIONS https://localhost:8443/users -H "Origin: https://evil.com" -H "Access-Control-Request-Method: POST"
# -> заголовка allow-origin нет, браузер заблокирует запрос
```

Тест `test_cors_allows_only_configured_origins`.

**Задание 9. Защитные заголовки.** Их ставит middleware приложения (на случай запуска без nginx) и nginx:
`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, CSP. CSP разрешает только свой
origin и CDN Swagger UI. С CSP даже инъекция `<script src=https://evil.com/x.js>` не загрузится.

**Задание 10. Защищённое API.** Вместо постов здесь книги и отзывы:

- `POST /auth/register` и `/auth/login` (bcrypt + JWT);
- `GET /books` открыт для всех;
- `POST /books/{id}/reviews` — только авторизованным;
- `PUT/DELETE /reviews/{id}` — только автор или admin;
- RBAC, CSRF для форм и настроек, защитные заголовки, CORS.

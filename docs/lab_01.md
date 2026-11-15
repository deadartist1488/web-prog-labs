# Лаба 1. Окружение и первый HTTP-сервер (занятия 1–2)

**Что сделано.** Настроено окружение (Python 3.12 / venv, Git), написан HTTP-сервер на FastAPI + uvicorn
(`app/main.py`, точка входа `app.main:app_factory`). Приложение упаковано в Docker (`Dockerfile`), весь стенд
описан в `docker-compose.yml`: nginx, два экземпляра приложения, PostgreSQL, Redis, worker и Prometheus.

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app_factory --factory --port 8000
curl -v http://localhost:8000/health
```

С Docker:

```bash
cp .env.example .env
sh scripts/make_cert.sh               # самоподписанный сертификат для nginx
docker compose up -d --build
docker compose ps
curl -k https://localhost/health
```

Ответ `/health`: `{"status":"ok","checks":{"database":"ok","redis":"ok"},"email_queue":0,...}`
(скриншот `screenshots/health.png`).

Результат `docker compose up -d --build` (полный вывод в [`outputs/docker_checks.txt`](outputs/docker_checks.txt)):

```
SERVICE      IMAGE                     STATUS                        PORTS
app1         web-prog-labs-app1        Up About a minute (healthy)   8000/tcp
app2         web-prog-labs-app2        Up About a minute (healthy)   8000/tcp
nginx        nginx:1.27-alpine         Up 55 seconds                 0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
postgres     postgres:15-alpine        Up About a minute (healthy)   5432/tcp
prometheus   prom/prometheus:v2.54.1   Up About a minute             0.0.0.0:9090->9090/tcp
redis        redis:7-alpine            Up About a minute (healthy)   6379/tcp
worker       web-prog-labs-worker      Up About a minute             8000/tcp
```

Наружу опубликованы только nginx (80/443) и Prometheus (9090). Приложения, БД и Redis доступны лишь внутри сети
compose.

Как устроен `Dockerfile`: слим-образ `python:3.12-slim`, сначала копируется только `requirements.txt`, поэтому слой с
зависимостями кэшируется. Приложение запускается не от root, есть `HEALTHCHECK`. В `docker-compose.yml` у `postgres` и
`redis` есть healthcheck, а приложения стартуют через `depends_on: condition: service_healthy`, то есть только когда БД
реально готова принимать соединения.

---

## Занятие 1 — ответы

### Задание 1. Разбор запроса

```
POST /api/users HTTP/1.1
Host: api.example.com
Content-Type: application/json
Content-Length: 45
Authorization: Bearer eyJhbGciOi...
```

1. **Метод:** `POST`, создание нового ресурса в коллекции. Он не идемпотентный: повтор создаст второго пользователя.
2. **Ресурс:** коллекция `/api/users` на хосте `api.example.com`.
3. **Заголовки:**
   - `Host` нужен, чтобы на одном IP могли жить несколько сайтов;
   - `Content-Type: application/json` говорит, в каком формате тело;
   - `Content-Length: 45` задаёт длину тела в байтах, по ней сервер понимает, где тело кончается;
   - `Authorization: Bearer ...` передаёт токен доступа (JWT), по нему сервер узнаёт пользователя.
4. **Тело:** данные нового пользователя (имя, email, возраст).
5. **Формат:** JSON.

Замечание: реальное тело `{"name": "John Doe", "email": "john@example.com", "age": 30}` занимает 60 байт, а не 45.
С таким `Content-Length` сервер прочитает обрезанный JSON и вернёт 400.

### Задание 2. Статус-коды

| Операция | Код | Почему |
|---|---|---|
| Получение списка пользователей | 200 OK | успешный ответ с телом |
| Создание пользователя | 201 Created | создан новый ресурс (плюс заголовок `Location`) |
| Пользователь не найден по ID | 404 Not Found | ресурса с таким id нет |
| Некорректный email | 400 Bad Request | клиент прислал невалидные данные |
| Пользователь уже существует | 409 Conflict | конфликт с текущим состоянием (уникальность) |
| Внутренняя ошибка сервера | 500 Internal Server Error | ошибка на нашей стороне |
| Успешное удаление | 204 No Content | удалено, тело не нужно |
| Требуется аутентификация | 401 Unauthorized | нет токена или он невалиден |

Так же устроено и наше API: см. `tests/test_users_api.py`.

### Задание 3. DNS и порты

1. При вводе `https://example.com:8080` браузер:
   - через DNS получает IP для `example.com`;
   - открывает TCP-соединение на порт **8080**, а не на 443, потому что порт указан явно;
   - делает TLS-рукопожатие;
   - отправляет `GET /` с заголовком `Host: example.com:8080`.
2. **80** — стандартный порт HTTP (открытый текст), **443** — HTTPS (HTTP поверх TLS, трафик шифруется). В нашем nginx
   80 только редиректит на 443.
3. **localhost** — имя «этого же компьютера», адрес loopback `127.0.0.1` (IPv6: `::1`). Трафик не выходит в сеть.
4. **DNS** переводит имена в IP-адреса: людям удобны имена, сетям нужны адреса. Кроме того, адрес можно поменять,
   не меняя имя.
5. **Кэш DNS:** ответы хранятся в браузере, ОС и у провайдера на время TTL. Так меньше задержка и нагрузка на
   DNS-серверы. Минус в том, что при смене IP клиенты какое-то время ходят на старый адрес.

### Задание 4. Путь запроса

```
[Браузер] → [DNS] → [Nginx (reverse proxy, TLS)] → [Приложение (FastAPI)] → [База данных (PostgreSQL)]
```

- **DNS** выдаёт IP сервера.
- **Nginx** принимает HTTPS и снимает TLS, отдаёт статику, режет лишние запросы (rate limit) и раскидывает их по
  экземплярам приложения.
- **Приложение** разбирает запрос, проверяет данные и права, выполняет бизнес-логику.
- **БД** надёжно хранит данные и выполняет запросы в транзакциях.

В нашем стенде путь именно такой. Сбоку ещё стоят Redis (кэш, сессии, очередь) и worker.

### Задание 5. Content-Type

| Данные | Content-Type |
|---|---|
| HTML-страница | `text/html; charset=utf-8` |
| JSON-ответ API | `application/json` |
| XML | `application/xml` (или `text/xml`) |
| PNG | `image/png` |
| Загрузка файла формой | `multipart/form-data; boundary=...` |
| CSS | `text/css` |
| JavaScript | `text/javascript` (устар. `application/javascript`) |

### Задание 6. curl

Вывод: [`outputs/lab01_curl_httpbin.txt`](outputs/lab01_curl_httpbin.txt). В выводе `curl -v`:

- строки с `>` — это наш запрос (метод, путь, заголовки);
- строки с `<` — ответ: статус-строка `HTTP/1.1 200 OK`, заголовки (`content-type: application/json`,
  `content-length`), затем тело (у httpbin это эхо нашего запроса).

`/status/404` и `/status/500` возвращают пустое тело (`content-length: 0`) и только код. Код 404 — ошибка клиента:
такого ресурса нет, повторять запрос бессмысленно. Код 500 — ошибка сервера: повторить позже можно, проблема не в
запросе.

### Задание 7. Простой HTTP-сервер

`python -m http.server 8000` раздаёт текущую папку, а `index.html` открывается по `http://localhost:8000`.
Останавливается сервер по Ctrl+C. Порт 80 может не запуститься по двум причинам. В Linux порты ниже 1024 требуют root
(или `CAP_NET_BIND_SERVICE`). Кроме того, порт может быть уже занят: IIS, Skype, другой веб-сервер или nginx в Docker.

### Задание 8. DevTools

На любой странице во вкладке **Network** у первого запроса (документ) видно:

- метод GET и URL;
- заголовки запроса: `Accept`, `User-Agent`, `Cookie`, `Accept-Encoding`;
- заголовки ответа: `Content-Type: text/html`, `Cache-Control`, `Content-Encoding: br/gzip`;
- статус 200, размер (сжатый и исходный) и время.

Запросы к API — это XHR/fetch с JSON-ответом. Через «Copy as cURL» любой из них можно повторить в терминале.
Waterfall показывает:

- какие ресурсы грузятся параллельно;
- самый долгий запрос (обычно сам документ или большой JS-бандл);
- закэшированные ресурсы с пометкой `(memory cache)` / `(disk cache)` или ответом 304.

На нашей статике (`/static/...`) nginx ставит `Expires` и `Cache-Control: max-age=2592000`
(см. `outputs/nginx_checks.txt`).

---

## Занятие 2 — ответы

### Задание 1. Разбор `config.yaml`

1. Сервер работает на порту **8080**.
2. SSL **включён** (`ssl.enabled: true`), сертификат лежит в `/etc/ssl/cert.pem`.
3. Пул соединений с БД: от **5** до **20**.
4. Включены функции authentication, logging и caching.
5. TTL кэша **3600 с** (1 час).

### Задание 2. Разбор `docker-compose.yml`

1. **Сервисы:** web (nginx), app, db (postgres), redis.
2. **Порты на хосте:** опубликован только `web` (8080 → 80). Остальные сервисы доступны лишь внутри сети compose.
3. **Переменные app:** `DATABASE_URL=postgresql://user:secret@db:5432/mydb`, `REDIS_URL=redis://redis:6379`.
4. **Данные PostgreSQL** хранятся в именованном volume `pgdata` (смонтирован в `/var/lib/postgresql/data`).
5. **В web монтируются:** `./html` → `/usr/share/nginx/html` и `./nginx.conf` → `/etc/nginx/nginx.conf` (read-only).
6. **Порядок запуска:** db и redis → app → web. Но `depends_on` без `condition` ждёт только старта контейнера, а не
   готовности БД. Поэтому у нас используется healthcheck + `condition: service_healthy`.

### Задание 3. config.yaml

```yaml
server:
  port: 3000
  max_connections: 100
database:
  driver: postgresql
  host: db
  port: 5432
redis:
  host: redis
  port: 6379
logging:
  file: /var/log/app.log
  level: INFO
auth:
  enabled: true
```

В нашем проекте та же конфигурация задаётся через переменные окружения (`app/config.py`, `.env.example`). Это
удобнее для Docker, и секреты не попадают в репозиторий.

### Задание 4. docker-compose.yml

```yaml
services:
  app:
    build: ./app
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql://app:secret@db:5432/app
      REDIS_URL: redis://redis:6379/0
    volumes: [app_logs:/var/log/app]
    depends_on:
      db: { condition: service_healthy }
      redis: { condition: service_started }
  db:
    image: postgres:15
    environment: { POSTGRES_USER: app, POSTGRES_PASSWORD: secret, POSTGRES_DB: app }
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U app"], interval: 5s, retries: 10 }
  redis:
    image: redis:7
volumes:
  pgdata:
  app_logs:
```

Расширенная рабочая версия лежит в корне репозитория: [`docker-compose.yml`](../docker-compose.yml).

### Задание 5. Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "app.py"]
```

Наш [`Dockerfile`](../Dockerfile) сделан так же, но базой взят `python:3.12-slim` и добавлены пользователь без root,
`HEALTHCHECK` и запуск через uvicorn.

### Задания 6–7. Работа с Docker и docker compose

```bash
docker run -d -p 8080:80 --name web nginx    # http://localhost:8080 -> "Welcome to nginx!"
docker ps                                    # контейнер Up, 0.0.0.0:8080->80/tcp
docker logs web                              # access-лог: GET / 200
docker stop web && docker rm web

docker compose up -d --build                 # стенд проекта
docker compose ps                            # все сервисы Up (healthy)
docker compose logs -f app1                  # JSON-логи приложения
docker compose stop && docker compose down   # остановка / удаление контейнеров
```

### Задание 8. Диагностика контейнера

1. `docker ps -a` — смотрим статус: `Exited (1)` значит приложение упало, `Restarting` — крутится в цикле перезапуска.
2. `docker logs <id>` — ищем трейсбек: нет переменной окружения, не тот `DATABASE_URL`, порт занят.
3. `docker exec -it <id> sh` (в slim-образе нет bash) — заходим внутрь и смотрим файлы и конфиги.
4. `env` — проверяем, что переменные (`DATABASE_URL`, `SECRET_KEY`) дошли до контейнера.
5. `netstat -tlnp` или `ss -tlnp` — слушает ли приложение порт, и на `0.0.0.0`, а не на `127.0.0.1`. Второй вариант —
   частая ошибка: снаружи контейнера такой порт не виден.
6. `ping db` / `pg_isready -h db` — работает ли DNS внутри сети compose и доступна ли БД.

### Задание 9. Volumes

В нашем compose:

- `postgres_data` — named volume для БД;
- `app_logs` — volume для логов;
- `./nginx/static` — bind mount для статики.

Ответы:

1. **Bind mount** монтирует конкретную папку хоста: удобно в разработке, но зависит от путей хоста. **Named volume**
   живёт в хранилище Docker: им управляет Docker, он переносим и подходит для данных БД.
2. `docker compose down` удаляет контейнеры и сеть, а **volumes остаются**. Данные пропадут только при
   `docker compose down -v`.
3. Чтобы данные пережили удаление контейнера, их кладут в named volume (или bind mount). Внутри контейнера ничего
   ценного хранить нельзя.
4. Список volumes: `docker volume ls`, подробности: `docker volume inspect <name>`.

### Задание 10. Сеть контейнеров

Сервисы compose находятся в одной bridge-сети, а встроенный DNS Docker резолвит имя сервиса в IP контейнера. Поэтому
nginx обращается к `app1:8000`, а приложение — к `postgres:5432`. Проверка: `docker network inspect web-prog-labs_default`
(видны контейнеры и их IP), `docker compose exec nginx wget -qO- http://app1:8000/health`.
Конфигурация reverse proxy: [`nginx/nginx.conf`](../nginx/nginx.conf), `upstream library_api { server app1:8000; server app2:8000; }`.

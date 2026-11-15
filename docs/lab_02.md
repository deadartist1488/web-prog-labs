# Лаба 2. REST API для CRUD (занятие 3)

**Что сделано.** CRUD API для пользователей и книг с правильными методами и статус-кодами. Есть фильтрация,
сортировка, поиск и пагинация. Спецификация OpenAPI 3.1 лежит в [`openapi.yaml`](../openapi.yaml) (выгружается
командой `python scripts/export_openapi.py`), живая документация открывается в Swagger UI по `/docs`
(`screenshots/swagger_ui.png`).

| Метод | Путь | Что делает | Успех | Ошибки |
|---|---|---|---|---|
| GET | `/users?page&size&search&min_age&sort&order` | список пользователей | 200 | 400 |
| POST | `/users` | создать пользователя | 201 + `Location` | 400, 409, 422 |
| GET | `/users/{id}` | получить пользователя | 200 | 404 |
| PUT | `/users/{id}` | заменить целиком (сам/admin) | 200 | 400, 401, 403, 404, 409 |
| PATCH | `/users/{id}` | изменить часть полей (сам/admin) | 200 | 400, 401, 403, 404, 409 |
| DELETE | `/users/{id}` | удалить (сам/admin) | 204 | 401, 403, 404, 422 |
| GET | `/users/{id}/loans?active=` | выдачи пользователя | 200 | 401, 403, 404 |
| GET | `/books?q&author&genre&min_price&max_price&available&sort&order&page&size` | каталог | 200 | 400, 422 |
| POST | `/books` | добавить книгу (librarian/admin) | 201 | 400, 401, 403, 409 |
| GET / PATCH / DELETE | `/books/{id}` | книга | 200 / 200 / 204 | 404, 409, 422 |
| POST | `/books/{id}/loans` | взять книгу | 201 | 401, 404, 409, 422 |
| POST | `/loans/{id}/return` | вернуть книгу | 200 | 403, 404, 409 |
| GET / POST | `/books/{id}/reviews` | отзывы | 200 / 201 | 400, 404, 409 |
| PUT / DELETE | `/reviews/{id}` | изменить/удалить свой отзыв | 200 / 204 | 403, 404 |

Пример ответа со списком:
`{"items":[{...}],"total":1,"page":1,"size":10,"pages":1}`. Живой прогон через nginx:
[`outputs/api_demo.txt`](outputs/api_demo.txt).

---

## Ответы на задания занятия 3

### Задание 1. API блога

| Метод | Путь | Параметры | Коды |
|---|---|---|---|
| GET | `/posts` | `?page, size, tag, category, author_id, q, sort=-created_at` | 200 |
| POST | `/posts` | тело: `title, body, category_id, tags[]` | 201, 400, 401 |
| GET | `/posts/{post_id}` | | 200, 404 |
| PUT / PATCH | `/posts/{post_id}` | тело: все / часть полей | 200, 400, 403, 404 |
| DELETE | `/posts/{post_id}` | | 204, 403, 404 |
| GET | `/posts/{post_id}/comments` | `?page, size` | 200, 404 |
| POST | `/posts/{post_id}/comments` | тело: `text` | 201, 400, 401, 404 |
| DELETE | `/comments/{comment_id}` | | 204, 403, 404 |
| GET / POST | `/tags` | тело: `name` | 200 / 201, 409 |
| PUT / DELETE | `/posts/{post_id}/tags/{tag}` | привязать / отвязать тег | 204, 404 |
| GET / POST / PUT / DELETE | `/categories`, `/categories/{id}` | тело: `name, slug` | 200, 201, 204, 404, 409 |

Пост в ответе: `{"id": 1, "title": "...", "body": "...", "author": {"id": 3, "name": "..."}, "category": {"id": 2, "name": "..."}, "tags": ["python"], "created_at": "..."}`.

### Задание 2. Выбор методов

| Операция | Метод | Почему |
|---|---|---|
| Создать пост | `POST /posts` | новый ресурс в коллекции, id выдаёт сервер |
| Список постов | `GET /posts` | чтение, безопасно и кэшируемо |
| Пост по ID | `GET /posts/{id}` | чтение одного ресурса |
| Обновить весь пост | `PUT /posts/{id}` | полная замена, идемпотентно |
| Изменить заголовок | `PATCH /posts/{id}` | частичное изменение |
| Удалить пост | `DELETE /posts/{id}` | удаление, идемпотентно |
| Добавить комментарий | `POST /posts/{id}/comments` | создание вложенного ресурса |
| Комментарии поста | `GET /posts/{id}/comments` | чтение вложенной коллекции |
| Лайкнуть пост | `PUT /posts/{id}/likes/me` (или `POST /posts/{id}/likes`) | PUT идемпотентен: повторный лайк не создаёт второй |
| Популярные посты | `GET /posts?sort=-likes&limit=10` | это тот же список с сортировкой, отдельный глагол не нужен |

### Задание 3. Статус-коды

| Ситуация | Код |
|---|---|
| Успешное создание | 201 Created |
| Успешное получение | 200 OK |
| Ресурс не найден | 404 Not Found |
| Неверные данные | 400 Bad Request (у нас: формат → 400, нарушение бизнес-правила → 422) |
| Не авторизован | 401 Unauthorized + `WWW-Authenticate` |
| Нет прав | 403 Forbidden |
| Ресурс уже существует | 409 Conflict |
| Успешное удаление | 204 No Content |
| Внутренняя ошибка | 500 Internal Server Error |
| Сервис временно недоступен | 503 Service Unavailable (+ `Retry-After`) |

У нас `/health` отдаёт 503, когда недоступна БД или Redis (тест `test_health_degraded_when_redis_down`).

### Задание 4. Список товаров с фильтрами

`GET /products?category=books&min_price=100&max_price=500&q=python&sort=price&order=asc&page=2&size=20`

- Query-параметры (все необязательные): `category`, `min_price`, `max_price`, `q` (поиск по названию),
  `sort` из белого списка `price|name`, `order` из `asc|desc`, `page ≥ 1`, `1 ≤ size ≤ 100`.
- Ответ 200: `{"items": [...], "total": 134, "page": 2, "size": 20, "pages": 7}`.
- 400 при неверном типе, `size > 100` или неизвестном `sort`. 422 при `min_price > max_price`.

Именно так сделан наш `GET /books` (`app/routers/books.py`), тест `test_catalog_filters_sort_pagination`.

### Задание 5. Структура JSON-ответов

```json
// 1. список пользователей
{"items": [{"id": 1, "name": "Anna", "email": "anna@example.com", "age": 25, "role": "user",
            "created_at": "2026-11-15T19:35:11Z"}], "total": 1, "page": 1, "size": 20, "pages": 1}

// 2. создание пользователя (201, Location: /users/1)
{"id": 1, "name": "Anna", "email": "anna@example.com", "age": 25, "role": "user", "created_at": "2026-11-15T19:35:11Z"}

// 3. ошибка валидации (400)
{"error": {"code": "VALIDATION_ERROR", "message": "Данные запроса не прошли проверку",
           "details": [{"field": "email", "message": "value is not a valid email address", "type": "value_error"}],
           "request_id": "e527fac2..."}}

// 4. пагинация (метаданные рядом с данными)
{"items": [...], "total": 134, "page": 2, "size": 20, "pages": 7}
```

### Задание 6. Идемпотентность

| Операция | Идемпотентна? | Почему |
|---|---|---|
| `GET /users/123` | да | только чтение |
| `POST /users` | **нет** | каждый вызов создаёт нового пользователя (у нас защищено UNIQUE по email; для выдачи книг есть `Idempotency-Key`) |
| `PUT /users/123` | да | повтор устанавливает то же состояние |
| `PATCH /users/123/email` | да, если это «установить email = X» | «увеличить счётчик на 1» уже не был бы идемпотентным |
| `DELETE /users/123` | да | после первого вызова ресурса нет; повтор вернёт 404, но состояние не меняется |
| `GET /users?page=1` | да | чтение |

### Задание 7. httpbin

Вывод: [`outputs/lab02_httpbin_jsonplaceholder.txt`](outputs/lab02_httpbin_jsonplaceholder.txt). Все методы вернули
200, httpbin отдаёт эхо запроса. Для POST/PUT/PATCH (и DELETE с телом) отправленный JSON появился в поле `json`, у GET
тела нет. Тела ответов отличаются только полем `url`/методом и наличием `json`/`data`.

### Задание 8. OpenAPI

Файл [`openapi.yaml`](../openapi.yaml) генерируется из кода, поэтому не расходится с реализацией. В нём описаны:

- 31 путь: `/users`, `/users/{user_id}` (GET/PUT/PATCH/DELETE) и остальные;
- параметры с ограничениями (`page ≥ 1`, `size ≤ 100`, перечисления для `sort`);
- примеры тел (например, `UserCreate.example`);
- коды ответов 200/201/204/400/401/403/404/409/422;
- схема безопасности `HTTPBearer`.

### Задание 9. JSONPlaceholder

- `GET /posts` → 200 и массив из 100 постов `{userId, id, title, body}`; `GET /posts/1` → 200 и один объект.
- `POST /posts` → **201**, сервер вернул объект с новым `id: 101`. Это фейковое API, на самом деле ничего не сохраняется.
- `PUT /posts/1` → 200 и обновлённый объект; `DELETE /posts/1` → 200 и `{}`. Строже было бы отвечать 204 без тела.
- `GET /posts/9999` → 404.

Вывод: API следует REST (ресурсы — существительные, действия — методы HTTP, коды осмысленные), но пагинации и
единого формата ошибок у него нет.

### Задание 10. API интернет-магазина

| Ресурс | Эндпоинты | Авторизация |
|---|---|---|
| Categories | `GET /categories`, `GET /categories/{id}`, `POST/PUT/DELETE /categories[/{id}]` | запись: admin |
| Products | `GET /products?category&min_price&max_price&q&sort&page&size`, `GET /products/{id}`, `POST/PUT/PATCH/DELETE` | запись: admin |
| Users | `POST /users` (регистрация), `GET/PATCH /users/me`, `GET /users` (admin) | свой профиль: user |
| Orders | `GET /orders?status&page` (свои), `POST /orders`, `GET /orders/{id}`, `POST /orders/{id}/cancel` | user, только свои; admin — все |
| OrderItems | `GET /orders/{id}/items`, `POST /orders/{id}/items`, `DELETE /orders/{id}/items/{item_id}` (пока заказ `new`) | владелец заказа |

Пример `POST /orders`:

```json
// запрос
{"items": [{"product_id": 10, "quantity": 2}], "address": "Москва, ..."}
// 201
{"id": 501, "status": "new", "total": "1980.00",
 "items": [{"product_id": 10, "quantity": 2, "price": "990.00"}], "created_at": "..."}
// 409, если товара не хватает на складе
{"error": {"code": "CONFLICT", "message": "Недостаточно товара 'Книга' на складе", "details": [], "request_id": "..."}}
```

Отмену сделали отдельным действием `POST /orders/{id}/cancel`: это переход состояния, а не простое редактирование
поля. Цена фиксируется в OrderItem в момент заказа, поэтому смена цены товара не меняет старые заказы.

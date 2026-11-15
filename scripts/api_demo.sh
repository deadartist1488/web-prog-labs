#!/usr/bin/env sh
# Сценарий работы с API через nginx (HTTPS). Нужен админ из ADMIN_EMAIL/ADMIN_PASSWORD.
#   sh scripts/api_demo.sh https://localhost:8443 admin@example.com 'Admin-pass1' > docs/outputs/api_demo.txt
B="${1:-https://localhost:8443}"
ADMIN_EMAIL="${2:-admin@example.com}"
ADMIN_PASS="${3:-Admin-pass1}"
C="curl -sk"
J='Content-Type: application/json'
show() { echo; echo "\$ $1"; }
field() { python -c "import sys,json; print(json.load(sys.stdin)$1)"; }
# тело пишем в файл: так кириллица уходит в UTF-8 независимо от кодировки консоли Windows
post() { url="$1"; body="$2"; shift 2; printf '%s' "$body" > /tmp/api_body.json; $C -X POST "$url" -H "$J" --data-binary @/tmp/api_body.json "$@"; }

show "GET /health"
$C "$B/health"; echo

show "POST /users (валидные данные) -> 201"
$C -i -X POST "$B/users" -H "$J" -d '{"name":"Anna Petrova","email":"anna@example.com","age":25,"password":"secret123"}' \
  | tr -d '\r' | grep -iE '^(HTTP|location|x-request-id)|^\{'

show "POST /users (невалидные данные из задания 1 занятия 4) -> 400"
$C -X POST "$B/users" -H "$J" -d '{"name":"J","email":"invalid-email","age":15,"password":"123"}'; echo

show "POST /users (повтор email) -> 409"
$C -X POST "$B/users" -H "$J" -d '{"name":"Anna Two","email":"ANNA@example.com","age":30,"password":"secret456"}'; echo

show "POST /users (age = \"abc\") -> 400"
$C -X POST "$B/users" -H "$J" -d '{"name":"John","email":"john@example.com","age":"abc","password":"secret123"}'; echo

show "GET /users/999 -> 404"
$C "$B/users/999"; echo

show "POST /auth/login -> пара токенов"
TOKENS=$($C -X POST "$B/auth/login" -H "$J" -d '{"email":"anna@example.com","password":"secret123"}')
echo "$TOKENS" | head -c 120; echo "..."
ACCESS=$(echo "$TOKENS" | field "['access_token']")
REFRESH=$(echo "$TOKENS" | field "['refresh_token']")

show "GET /auth/profile без токена -> 401"
$C -i "$B/auth/profile" | tr -d '\r' | grep -iE '^(HTTP|www-authenticate)|^\{'

show "GET /auth/profile с токеном -> 200"
$C "$B/auth/profile" -H "Authorization: Bearer $ACCESS"; echo

ADMIN=$($C -X POST "$B/auth/login" -H "$J" -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\"}" | field "['access_token']")

show "POST /books читателем -> 403"
post "$B/books" '{"title":"Война и мир","author":"Лев Толстой","isbn":"9785170900001","year":1869,"copies_total":1}' \
  -H "Authorization: Bearer $ACCESS"; echo

show "POST /books админом -> 201"
BOOK=$(post "$B/books" '{"title":"Война и мир","author":"Лев Толстой","isbn":"978-5-17-090000-1","year":1869,"genre":"роман","price":"650.00","copies_total":1}' \
  -H "Authorization: Bearer $ADMIN")
echo "$BOOK"
BOOK_ID=$(echo "$BOOK" | field "['id']")

show "GET /books?genre=роман&sort=price&order=desc&page=1&size=10"
$C "$B/books?genre=%D1%80%D0%BE%D0%BC%D0%B0%D0%BD&sort=price&order=desc&page=1&size=10"; echo

show "POST /books/$BOOK_ID/loans (Idempotency-Key: k-1) -> 201"
$C -X POST "$B/books/$BOOK_ID/loans" -H "Authorization: Bearer $ACCESS" -H "Idempotency-Key: k-1"; echo
show "тот же запрос повторно (сеть моргнула) -> тот же ответ, вторая выдача не создаётся"
$C -i -X POST "$B/books/$BOOK_ID/loans" -H "Authorization: Bearer $ACCESS" -H "Idempotency-Key: k-1" \
  | tr -d '\r' | grep -iE '^(HTTP|idempotent-replay)|^\{'

show "POST /books/$BOOK_ID/loans без ключа -> 409 (уже на руках)"
$C -X POST "$B/books/$BOOK_ID/loans" -H "Authorization: Bearer $ACCESS"; echo

show "GET /books/$BOOK_ID (остаток 0)"
$C "$B/books/$BOOK_ID"; echo

show "POST /auth/refresh -> новая пара; старый refresh повторно -> 401"
$C -X POST "$B/auth/refresh" -H "$J" -d "{\"refresh_token\":\"$REFRESH\"}" | head -c 80; echo "..."
$C -X POST "$B/auth/refresh" -H "$J" -d "{\"refresh_token\":\"$REFRESH\"}"; echo

show "GET /debug/flaky?fail_rate=1 -> 500 с request_id для поиска по логам"
$C -i "$B/debug/flaky?fail_rate=1" | tr -d '\r' | grep -iE '^(HTTP|x-request-id)|^\{'

show "GET /metrics (фрагмент)"
$C "$B/metrics" | grep -E '^http_requests_total\{endpoint="/users"' | head -5

#!/usr/bin/env sh
# Проверки nginx (лаба 8). Ожидает: nginx на 8080/8443 (nginx.local.conf), приложения на 8000 и 8001.
#   sh scripts/nginx_checks.sh > docs/outputs/nginx_checks.txt
# Путь к access.log можно передать первым аргументом (по умолчанию tools/nginx-1.26.2/logs/access.log).
LOG="${1:-tools/nginx-1.26.2/logs/access.log}"
B=https://localhost:8443
RUN=$$

echo "## 1. HTTP -> HTTPS"
curl -s -o /dev/null -w "GET http://localhost:8080/health -> %{http_code} Location: %{redirect_url}\n" http://localhost:8080/health

echo; echo "## 2. Заголовки ответа через nginx (версии nginx нет, есть HSTS и X-Request-ID)"
curl -sk -D - -o /dev/null $B/health | tr -d '\r' | grep -iE "^(HTTP|server|strict-transport|x-frame|x-content|x-request-id|content-security)"

echo; echo "## 3. Статика: кэширование и gzip"
curl -sk -D - -o /dev/null -H "Accept-Encoding: gzip" $B/static/css/style.css | tr -d '\r' | grep -iE "^(HTTP|content-type|expires|cache-control|content-encoding)"
curl -sk -D - -o /dev/null -H "Accept-Encoding: gzip" "$B/openapi.json" | tr -d '\r' | grep -iE "^(HTTP|content-encoding)"

echo; echo "## 4. Передача заголовков приложению (X-Forwarded-For, X-Real-IP)"
curl -sk -H "X-Forwarded-For: 203.0.113.7" $B/api/time; echo

echo; echo "## 5. Балансировка least_conn между 127.0.0.1:8000 и 127.0.0.1:8001 (12 запросов /health, 4 потока)"
: > /tmp/ngx_bal.txt
for i in 1 2 3; do
  for j in 1 2 3 4; do curl -sk -o /dev/null "$B/health?b=$RUN-$i$j" & done; wait
done
grep -o "GET /health?b=$RUN-[0-9]* .*upstream=[0-9.:]*" "$LOG" | grep -o 'upstream=[0-9.:]*' | sort | uniq -c
echo "round-robin (порт 8081, 12 последовательных запросов):"
for i in $(seq 1 12); do curl -s -o /dev/null "http://localhost:8081/health?rr=$RUN-$i"; done
grep -o "GET /health?rr=$RUN-[0-9]* .*upstream=[0-9.:]*" "$LOG" | grep -o 'upstream=[0-9.:]*' | sort | uniq -c

echo; echo "## 6. Rate limiting: 60 одновременных запросов, лимит 10 r/s + burst 20"
for i in $(seq 1 60); do curl -sk -o /dev/null -w "%{http_code}\n" "$B/api/time" & done > /tmp/ngx_rl.txt; wait
sort /tmp/ngx_rl.txt | uniq -c
sleep 3; rm -f /tmp/ngx_b_*
for i in $(seq 1 40); do curl -sk -o /tmp/ngx_b_$i -w "%{http_code}" "$B/api/time" > /tmp/ngx_b_$i.code & done; wait
for i in $(seq 1 40); do if [ "$(cat /tmp/ngx_b_$i.code)" = "429" ]; then echo "тело ответа 429:"; cat /tmp/ngx_b_$i; echo; break; fi; done

echo; echo "## 7. Тайм-аут: /api/long с proxy_read_timeout 3s"
sleep 3
curl -sk -o /dev/null -w "seconds=1 -> %{http_code} in %{time_total}s\n" "$B/api/long?seconds=1"
curl -sk -w "\nseconds=5 -> %{http_code} in %{time_total}s\n" "$B/api/long?seconds=5"

echo; echo "## 8. Строка access.log (custom формат с request_time и upstream)"
tail -n 1 "$LOG"

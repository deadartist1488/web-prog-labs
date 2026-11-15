#!/usr/bin/env sh
# Проверки стенда в Docker (после `docker compose up -d --build`):
#   sh scripts/docker_checks.sh > docs/outputs/docker_checks.txt
B=https://localhost
RUN=$$

echo "## docker compose ps"
docker compose ps --format "table {{.Service}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"

echo; echo "## версии"
docker compose exec -T postgres psql -U "${POSTGRES_USER:-library}" -d "${POSTGRES_DB:-library}" -tAc "SELECT version()" | cut -c1-60
docker compose exec -T redis redis-cli INFO server | grep redis_version
docker compose exec -T nginx nginx -v 2>&1

echo; echo "## health и редирект"
curl -sk "$B/health"; echo
curl -s -o /dev/null -w "GET http://localhost/health -> %{http_code} Location: %{redirect_url}\n" http://localhost/health

echo; echo "## миграции применились (docker-entrypoint-initdb.d)"
docker compose exec -T postgres psql -U "${POSTGRES_USER:-library}" -d "${POSTGRES_DB:-library}" -c "\dt"

echo; echo "## балансировка: 20 запросов, кто ответил (по access-логу nginx)"
for i in $(seq 1 20); do curl -sk -o /dev/null "$B/health?run=$RUN-$i"; done
docker compose exec -T nginx sh -c "grep 'run=$RUN-' /var/log/nginx/api_access.log" | grep -o 'upstream=[0-9.:]*' | sort | uniq -c

echo; echo "## rate limit: 60 одновременных запросов"
sleep 2
for i in $(seq 1 60); do curl -sk -o /dev/null -w "%{http_code}\n" "$B/api/time" & done > /tmp/dc_rl.txt; wait
sort /tmp/dc_rl.txt | uniq -c

echo; echo "## Prometheus: цели и правила"
curl -s http://localhost:9090/api/v1/targets | grep -o '"scrapeUrl":"[^"]*","globalUrl":"[^"]*","lastError":"[^"]*","lastScrape":"[^"]*","lastScrapeDuration":[0-9.e-]*,"health":"[a-z]*"' \
  | sed -E 's/.*"scrapeUrl":"([^"]*)".*"health":"([a-z]*)"/\1 -> \2/'
curl -s http://localhost:9090/api/v1/rules | grep -o '"name":"[A-Za-z]*"' | grep -v library

echo; echo "## worker разбирает очередь писем"
docker compose logs worker --no-log-prefix | grep '"email sent"' | tail -2

echo; echo "## запрос с ошибкой: request_id -> логи обоих экземпляров"
RID=$(curl -sk "$B/debug/flaky?fail_rate=1" | grep -o '"request_id":"[0-9a-f]*"' | cut -d'"' -f4)
echo "request_id=$RID"
sleep 1
docker compose logs app1 app2 | grep "$RID" | sed -E 's/"exc": "Traceback.*(ZeroDivisionError[^"]*)"/"exc": "...\1"/'

"""Инфраструктура: клиент Redis, кэш (cache-aside), очередь email и идемпотентность (лабы 6, 10)."""
import json
import logging
import uuid
from typing import Any, Callable, Dict, Optional

import redis

log = logging.getLogger("app.infra")

EMAIL_QUEUE = "queue:emails"
SENT_EMAILS = "emails:sent"


def make_redis(url: str) -> "redis.Redis":
    if url.startswith("fake://"):
        import fakeredis

        return fakeredis.FakeRedis(decode_responses=True)
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)


class Cache:
    """Cache-aside: читаем из кэша, при промахе идём в БД и кладём результат в кэш с TTL."""

    def __init__(self, client: "redis.Redis", ttl: int = 60):
        self.r = client
        self.ttl = ttl
        self.hits = 0
        self.misses = 0

    def get_or_load(self, key: str, loader: Callable[[], Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        try:
            cached = self.r.get(key)
        except redis.RedisError:
            log.warning("redis unavailable, cache bypass for %s", key)
            return loader()
        if cached is not None:
            self.hits += 1
            log.info("cache hit %s", key)
            return json.loads(cached)
        self.misses += 1
        log.info("cache miss %s", key)
        value = loader()
        if value is not None:
            try:
                self.r.set(key, json.dumps(value, default=str), ex=self.ttl)
            except redis.RedisError:
                pass
        return value

    def invalidate(self, key: str) -> None:
        try:
            self.r.delete(key)
        except redis.RedisError:
            log.warning("redis unavailable, could not invalidate %s", key)


class EmailQueue:
    """Очередь писем в Redis (список). Отправка вынесена из запроса в фоновый обработчик."""

    def __init__(self, client: "redis.Redis"):
        self.r = client

    def enqueue(self, to: str, subject: str, body: str, job_id: Optional[str] = None) -> str:
        job = {"id": job_id or uuid.uuid4().hex, "to": to, "subject": subject, "body": body}
        self.r.rpush(EMAIL_QUEUE, json.dumps(job, ensure_ascii=False))
        log.info("email job queued", extra={"context": {"job_id": job["id"], "subject": subject}})
        return job["id"]

    def size(self) -> int:
        return int(self.r.llen(EMAIL_QUEUE))

    def process(self, sender: Optional[Callable[[Dict[str, Any]], None]] = None, limit: int = 100) -> int:
        """Обработать до limit задач. Идемпотентно: одна и та же задача не отправится дважды."""
        processed = 0
        for _ in range(limit):
            raw = self.r.lpop(EMAIL_QUEUE)
            if raw is None:
                break
            job = json.loads(raw)
            # SET NX: ключ ставится только если его ещё нет -> повторная доставка задачи игнорируется
            if not self.r.set(f"job:done:{job['id']}", "1", nx=True, ex=7 * 24 * 3600):
                log.info("duplicate job skipped", extra={"context": {"job_id": job["id"]}})
                continue
            try:
                (sender or self._fake_send)(job)
            except Exception:
                self.r.delete(f"job:done:{job['id']}")
                self.r.rpush(EMAIL_QUEUE, raw)  # вернуть в очередь для повторной попытки
                log.exception("email job failed, requeued")
                break
            processed += 1
        return processed

    def _fake_send(self, job: Dict[str, Any]) -> None:
        # вместо SMTP складываем письмо в список: так результат проверяется в тестах
        self.r.rpush(SENT_EMAILS, json.dumps(job, ensure_ascii=False))
        log.info("email sent", extra={"context": {"job_id": job["id"], "subject": job["subject"]}})


class IdempotencyStore:
    """Idempotency-Key: повтор запроса с тем же ключом возвращает сохранённый ответ."""

    def __init__(self, client: "redis.Redis", ttl: int = 24 * 3600):
        self.r = client
        self.ttl = ttl

    def get(self, scope: str, key: str) -> Optional[Dict[str, Any]]:
        raw = self.r.get(f"idem:{scope}:{key}")
        return json.loads(raw) if raw else None

    def save(self, scope: str, key: str, status: int, body: Any) -> None:
        self.r.set(f"idem:{scope}:{key}", json.dumps({"status": status, "body": body}, default=str), ex=self.ttl)

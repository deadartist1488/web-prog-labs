"""Диагностика (лаба 9) и фоновые задачи (лаба 10): request_id, логи, метрики, health, очередь писем."""
import json
import logging

import pytest
import redis

from app.infra import SENT_EMAILS, EmailQueue
from tests.conftest import user_payload
from tests.test_users_api import assert_error

pytestmark = pytest.mark.api


def test_request_id_generated_or_propagated(client):
    r1, r2 = client.get("/health"), client.get("/health")
    assert len(r1.headers["x-request-id"]) == 32 and r1.headers["x-request-id"] != r2.headers["x-request-id"]
    r = client.get("/health", headers={"X-Request-ID": "trace-12345678"})
    assert r.headers["x-request-id"] == "trace-12345678"
    r = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})  # мусор не принимаем
    assert r.headers["x-request-id"] != "bad id with spaces"


def test_flaky_failure_can_be_traced_by_request_id(client, caplog):
    """Сценарий занятия 12, задание 4: получили 500 -> берём request_id -> находим все логи запроса."""
    caplog.set_level(logging.INFO)
    r = client.get("/debug/flaky", params={"fail_rate": 1})
    err = assert_error(r, 500, "INTERNAL_ERROR")
    assert "ZeroDivision" not in r.text  # стек клиенту не отдаём
    rid = err["request_id"]
    trail = [rec for rec in caplog.records if getattr(rec, "request_id", None) == rid]
    messages = [rec.getMessage() for rec in trail]
    assert any("flaky: start" in m for m in messages)
    assert any("unhandled error" in m for m in messages)
    assert any(rec.exc_info for rec in trail)
    assert client.get("/debug/flaky", params={"fail_rate": 0}).json()["ok"] is True


def test_access_log_has_status_and_duration(client, caplog):
    caplog.set_level(logging.INFO, logger="app.http")
    client.get("/users", params={"page": 1})
    rec = [r for r in caplog.records if r.name == "app.http"][-1]
    assert rec.context["status"] == 200 and rec.context["duration_ms"] >= 0 and rec.context["query"] == "page=1"


def test_json_log_line_format():
    from app.logging_setup import JsonFormatter

    rec = logging.LogRecord("app", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    rec.request_id = "abc"
    rec.context = {"password": "p", "n": 1}
    line = json.loads(JsonFormatter().format(rec))
    assert line["msg"] == "hello world" and line["request_id"] == "abc" and line["context"]["password"] == "***"


def test_metrics_endpoint(client):
    client.get("/users")
    client.get("/users/42")
    text = client.get("/metrics").text
    assert 'http_requests_total{endpoint="/users/{id}",method="GET",status="404"}' in text
    assert "http_request_duration_seconds_bucket" in text and "email_queue_size" in text


def test_health_degraded_when_redis_down(client, monkeypatch):
    def boom():
        raise redis.ConnectionError("down")

    monkeypatch.setattr(client.app.state.redis, "ping", boom)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["checks"] == {"database": "ok", "redis": "down"}


def test_registration_puts_welcome_email_in_queue_and_worker_sends_it(client, register):
    queue = client.app.state.email_queue
    client.post("/users", json=user_payload(email="mail@example.com"))
    assert queue.size() == 1
    _, admin = register("admin")
    assert client.get("/admin/email-queue", headers=admin).json()["queued"] == 2
    assert client.post("/admin/email-queue/process", headers=admin).json()["processed"] == 2
    sent = [json.loads(x) for x in client.app.state.redis.lrange(SENT_EMAILS, 0, -1)]
    assert sent[0]["to"] == "mail@example.com" and queue.size() == 0


def test_queue_is_idempotent_and_retries_failures(app):
    r = app.state.redis
    q = EmailQueue(r)
    q.enqueue("a@b.io", "s", "b", job_id="job-1")
    q.enqueue("a@b.io", "s", "b", job_id="job-1")  # та же задача доставлена дважды
    assert q.process() == 1 and r.llen(SENT_EMAILS) == 1

    calls = {"n": 0}

    def flaky_sender(job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("smtp down")

    q.enqueue("c@d.io", "s", "b", job_id="job-2")
    assert q.process(sender=flaky_sender) == 0 and q.size() == 1  # вернулась в очередь
    assert q.process(sender=flaky_sender) == 1 and q.size() == 0


def test_cache_bypass_when_redis_down(client, make_book, monkeypatch):
    book = make_book()

    def boom(*_a, **_k):
        raise redis.ConnectionError("down")

    monkeypatch.setattr(client.app.state.redis, "get", boom)
    assert client.get(f"/books/{book['id']}").json()["id"] == book["id"]  # отвечаем из БД


def test_long_and_time_endpoints(client):
    assert client.get("/api/long", params={"seconds": 0}).json() == {"slept": 0}
    r = client.get("/api/time", headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.json()["x_forwarded_for"] == "1.2.3.4"
    assert "Library API" in client.get("/").text

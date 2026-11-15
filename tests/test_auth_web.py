"""Аутентификация, cookie, CSRF, XSS, CORS, заголовки безопасности (лабы 6, 8)."""
import re

import pytest

from tests.conftest import user_payload
from tests.test_users_api import assert_error

pytestmark = pytest.mark.api


def login(client, data):
    return client.post("/auth/login", json={"email": data["email"], "password": data["password"]})


def test_register_login_profile(client):
    data = user_payload()
    assert client.post("/auth/register", json=data).status_code == 201
    r = login(client, data)
    assert r.status_code == 200
    tokens = r.json()
    assert tokens["token_type"] == "bearer" and tokens["expires_in"] == 15 * 60
    me = client.get("/auth/profile", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.json()["email"] == data["email"]


def test_profile_without_or_with_bad_token(client):
    r = client.get("/auth/profile")
    assert_error(r, 401, "UNAUTHORIZED")
    assert r.headers["www-authenticate"] == "Bearer"
    r = client.get("/auth/profile", headers={"Authorization": "Bearer garbage"})
    assert_error(r, 401, "UNAUTHORIZED")
    assert "invalid_token" in r.headers["www-authenticate"]


def test_wrong_password_401_then_lockout_429(client):
    data = user_payload()
    client.post("/auth/register", json=data)
    for _ in range(5):
        assert_error(login(client, {**data, "password": "wrong-pass1"}), 401, "UNAUTHORIZED")
    r = login(client, data)
    assert_error(r, 429, "TOO_MANY_REQUESTS")
    assert int(r.headers["retry-after"]) > 0


def test_refresh_rotation_and_logout(client):
    data = user_payload()
    client.post("/auth/register", json=data)
    tokens = login(client, data).json()
    new = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert new.status_code == 200
    # старый refresh второй раз не работает (ротация)
    assert_error(client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}), 401, "UNAUTHORIZED")
    # access-токен нельзя использовать как refresh
    assert_error(client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]}), 401, "UNAUTHORIZED")
    h = {"Authorization": f"Bearer {new.json()['access_token']}"}
    r = client.post("/auth/logout", json={"refresh_token": new.json()["refresh_token"]}, headers=h)
    assert r.status_code == 204
    assert_error(client.get("/auth/profile", headers=h), 401, "UNAUTHORIZED")  # access в blacklist
    assert_error(client.post("/auth/refresh", json={"refresh_token": new.json()["refresh_token"]}), 401,
                 "UNAUTHORIZED")
    assert_error(client.post("/auth/logout"), 401, "UNAUTHORIZED")


def test_session_based_auth_cookie_flags(client):
    data = user_payload()
    client.post("/auth/register", json=data)
    r = client.post("/session/login", json={"email": data["email"], "password": data["password"]})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=86400" in cookie
    assert client.get("/session/profile").json()["email"] == data["email"]
    assert client.post("/session/logout").status_code == 204
    client.cookies.clear()
    assert_error(client.get("/session/profile"), 401, "UNAUTHORIZED")


def test_cookie_endpoints(client):
    r = client.post("/set-cookie", json={"name": "user_id", "value": "123", "days": 30})
    sc = r.headers["set-cookie"].lower()
    assert "max-age=2592000" in sc and "httponly" in sc and "samesite=lax" in sc
    assert client.get("/get-cookie", params={"name": "user_id"}).json()["value"] == "123"
    session_cookie = client.post("/set-cookie", json={"name": "tmp", "value": "1"}).headers["set-cookie"].lower()
    assert "max-age" not in session_cookie  # сессионная cookie
    r = client.post("/delete-cookie", params={"name": "user_id"})
    assert 'user_id=""' in r.headers["set-cookie"] or "max-age=0" in r.headers["set-cookie"].lower()
    assert_error(client.post("/set-cookie", json={"name": "bad name;", "value": "x"}), 400, "VALIDATION_ERROR")


def test_csrf_form_flow(client):
    page = client.get("/form")
    token = re.search(r"name='csrf_token' value='([^']+)'", page.text).group(1)
    ok = client.post("/submit", data={"message": "<script>alert(1)</script>", "csrf_token": token})
    assert ok.status_code == 200
    assert "&lt;script&gt;" in ok.text and "<script>" not in ok.text  # XSS экранирован
    assert_error(client.post("/submit", data={"message": "hi", "csrf_token": "forged.token"}), 403, "FORBIDDEN")
    r = client.post("/submit", data={"message": "hi", "csrf_token": token}, headers={"Origin": "https://evil.com"})
    assert_error(r, 403, "FORBIDDEN")
    client.cookies.clear()
    assert_error(client.post("/submit", data={"message": "hi", "csrf_token": token}), 403, "FORBIDDEN")


def test_settings_require_jwt_and_csrf_header(client, register):
    _, h = register()
    assert_error(client.get("/settings"), 401, "UNAUTHORIZED")
    token = client.get("/settings", headers=h).json()["csrf_token"]
    assert_error(client.post("/settings", json={"theme": "dark"}, headers=h), 403, "FORBIDDEN")
    r = client.post("/settings", json={"theme": "dark"}, headers={**h, "X-CSRF-Token": token})
    assert r.json()["settings"]["theme"] == "dark"
    assert client.get("/settings", headers=h).json()["settings"]["theme"] == "dark"


def test_xss_echo_is_escaped(client):
    r = client.get("/echo", params={"text": "<img src=x onerror=alert(1)>"})
    assert "<img" not in r.text and "&lt;img" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_security_headers_on_every_response(client):
    r = client.get("/health")
    for h in ("x-content-type-options", "x-frame-options", "referrer-policy", "content-security-policy",
              "x-request-id"):
        assert h in r.headers


def test_cors_allows_only_configured_origins(client):
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Authorization"}
    ok = client.options("/users", headers={"Origin": "https://library.local", **pre})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "https://library.local"
    bad = client.options("/users", headers={"Origin": "https://evil.com", **pre})
    assert "access-control-allow-origin" not in bad.headers

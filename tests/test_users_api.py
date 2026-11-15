"""API пользователей (лабы 2, 3, 8): статус-коды, структура ответа и ошибок, права доступа."""
import pytest

from app.models import User
from tests.conftest import user_payload

pytestmark = pytest.mark.api


def assert_error(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()["error"]
    assert body["code"] == code
    assert body["request_id"] == r.headers["x-request-id"]
    return body


def test_create_user_201_location_and_no_password_in_response(client):
    data = user_payload(email="New@Example.com")
    r = client.post("/users", json=data)
    assert r.status_code == 201
    body = r.json()
    assert r.headers["location"] == f"/users/{body['id']}"
    assert set(body) == {"id", "name", "email", "age", "role", "created_at"}
    assert body["email"] == "new@example.com" and body["role"] == "user"
    # проверяем не только ответ, но и БД: пароль хранится только в виде хеша
    with client.app.state.session_factory() as s:
        saved = s.get(User, body["id"])
        assert saved.password_hash.startswith("$2b$") and "secret123" not in saved.password_hash


def test_create_user_validation_error_lists_every_field(client):
    r = client.post("/users", json={"name": "J", "email": "invalid-email", "age": 15, "password": "123"})
    err = assert_error(r, 400, "VALIDATION_ERROR")
    assert {d["field"] for d in err["details"]} == {"name", "email", "age", "password"}


@pytest.mark.parametrize("body", [
    {"name": "John", "email": "j@x.io", "age": "abc", "password": "secret123"},
    {"name": "John", "email": "j@x.io", "password": "secret123"},
    {"name": "John", "email": "j@x.io", "age": 30, "password": "secret123", "role": "admin"},
])
def test_create_user_bad_types_missing_and_extra_fields(client, body):
    assert_error(client.post("/users", json=body), 400, "VALIDATION_ERROR")


def test_create_user_invalid_json(client):
    r = client.post("/users", content=b"{not json", headers={"Content-Type": "application/json"})
    assert_error(r, 400, "VALIDATION_ERROR")


def test_duplicate_email_409_case_insensitive(client):
    client.post("/users", json=user_payload(email="dup@example.com"))
    r = client.post("/users", json=user_payload(email="DUP@example.com"))
    err = assert_error(r, 409, "CONFLICT")
    assert err["details"][0]["field"] == "email"


def test_get_user_and_404(client, register):
    user, _ = register()
    assert client.get(f"/users/{user['id']}").json()["email"] == user["email"]
    assert_error(client.get("/users/9999"), 404, "NOT_FOUND")
    assert_error(client.get("/users/abc"), 400, "VALIDATION_ERROR")


def test_list_pagination_sort_and_search(client):
    for i, age in enumerate([40, 20, 30]):
        client.post("/users", json=user_payload(name=f"Person {chr(65 + i)}", age=age))
    page = client.get("/users", params={"page": 1, "size": 2, "sort": "age", "order": "desc"}).json()
    assert page["total"] == 3 and page["pages"] == 2 and len(page["items"]) == 2
    assert [u["age"] for u in page["items"]] == [40, 30]
    assert client.get("/users", params={"search": "person b"}).json()["total"] == 1
    assert client.get("/users", params={"min_age": 35}).json()["total"] == 1
    assert_error(client.get("/users", params={"size": 1000}), 400, "VALIDATION_ERROR")
    assert_error(client.get("/users", params={"sort": "password_hash"}), 400, "VALIDATION_ERROR")


def test_update_requires_auth_and_ownership(client, register):
    owner, owner_h = register()
    _, other_h = register()
    _, admin_h = register("admin")
    new = {"name": "Renamed", "email": owner["email"], "age": 33}
    assert_error(client.put(f"/users/{owner['id']}", json=new), 401, "UNAUTHORIZED")
    assert_error(client.put(f"/users/{owner['id']}", json=new, headers=other_h), 403, "FORBIDDEN")
    assert client.put(f"/users/{owner['id']}", json=new, headers=owner_h).json()["name"] == "Renamed"
    assert client.patch(f"/users/{owner['id']}", json={"age": 50}, headers=admin_h).json()["age"] == 50


def test_put_requires_all_fields_patch_accepts_partial(client, register):
    user, h = register()
    assert_error(client.put(f"/users/{user['id']}", json={"age": 30}, headers=h), 400, "VALIDATION_ERROR")
    r = client.patch(f"/users/{user['id']}", json={"age": 31}, headers=h)
    assert r.status_code == 200 and r.json()["age"] == 31 and r.json()["name"] == user["name"]
    assert_error(client.patch(f"/users/{user['id']}", json={"email": None}, headers=h), 400, "VALIDATION_ERROR")
    assert_error(client.patch(f"/users/{user['id']}", json={}, headers=h), 400, "VALIDATION_ERROR")


def test_change_email_to_taken_one_409(client, register):
    a, _ = register()
    b, b_h = register()
    assert_error(client.patch(f"/users/{b['id']}", json={"email": a["email"]}, headers=b_h), 409, "CONFLICT")


def test_delete_user_204_then_404(client, register):
    user, h = register()
    r = client.delete(f"/users/{user['id']}", headers=h)
    assert r.status_code == 204 and r.content == b""
    assert_error(client.get(f"/users/{user['id']}"), 404, "NOT_FOUND")


def test_change_role_only_admin(client, register):
    user, h = register()
    admin, admin_h = register("admin")
    assert_error(client.put(f"/users/{user['id']}/role", params={"role": "admin"}, headers=h), 403, "FORBIDDEN")
    r = client.put(f"/users/{user['id']}/role", params={"role": "librarian"}, headers=admin_h)
    assert r.json()["role"] == "librarian"
    r = client.put(f"/users/{admin['id']}/role", params={"role": "user"}, headers=admin_h)
    assert_error(r, 422, "BUSINESS_RULE_VIOLATION")


def test_unknown_route_and_method(client):
    assert_error(client.get("/nope"), 404, "NOT_FOUND")
    assert_error(client.patch("/users"), 405, "METHOD_NOT_ALLOWED")

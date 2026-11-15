"""Книги, выдачи, отзывы (лабы 2, 3, 7, 8, 10)."""
import pytest

from tests.conftest import book_payload
from tests.test_users_api import assert_error

pytestmark = pytest.mark.api


def test_only_librarian_can_create_books(client, register, librarian):
    _, reader = register()
    assert_error(client.post("/books", json=book_payload()), 401, "UNAUTHORIZED")
    assert_error(client.post("/books", json=book_payload(), headers=reader), 403, "FORBIDDEN")
    r = client.post("/books", json=book_payload(isbn="978-5-17-090000-1", copies_total=3), headers=librarian)
    assert r.status_code == 201
    assert r.json()["isbn"] == "9785170900001" and r.json()["copies_available"] == 3


def test_book_validation_and_duplicate_isbn(client, librarian, make_book):
    err = assert_error(client.post("/books", json=book_payload(isbn="12", year=3000, price="-1"), headers=librarian),
                       400, "VALIDATION_ERROR")
    assert {d["field"] for d in err["details"]} == {"isbn", "year", "price"}
    book = make_book()
    assert_error(client.post("/books", json=book_payload(isbn=book["isbn"]), headers=librarian), 409, "CONFLICT")


def test_catalog_filters_sort_pagination(client, make_book):
    make_book(title="Alpha", author="A. Author", price="100.00", genre="poetry")
    make_book(title="Beta", author="B. Author", price="300.00", genre="novel")
    make_book(title="Gamma", author="B. Author", price="200.00", genre="novel", copies_total=0)
    get = lambda **p: client.get("/books", params=p).json()  # noqa: E731
    assert get(genre="novel")["total"] == 2
    assert get(author="B. Author", available=True)["total"] == 1
    assert [b["title"] for b in get(sort="price", order="desc")["items"]] == ["Beta", "Gamma", "Alpha"]
    assert get(min_price=150, max_price=250)["items"][0]["title"] == "Gamma"
    assert get(q="alp")["total"] == 1
    assert get(size=2, page=2)["items"][0]["title"] == "Gamma"
    assert_error(client.get("/books", params={"min_price": 500, "max_price": 100}), 422, "BUSINESS_RULE_VIOLATION")


def test_borrow_and_return_flow(client, register, make_book):
    book = make_book(copies_total=1)
    user, h = register()
    _, other = register()
    r = client.post(f"/books/{book['id']}/loans", headers=h)
    assert r.status_code == 201
    loan = r.json()
    assert client.get(f"/books/{book['id']}").json()["copies_available"] == 0
    assert_error(client.post(f"/books/{book['id']}/loans", headers=h), 409, "CONFLICT")       # уже на руках
    assert_error(client.post(f"/books/{book['id']}/loans", headers=other), 409, "CONFLICT")   # нет экземпляров
    assert_error(client.post(f"/loans/{loan['id']}/return", headers=other), 403, "FORBIDDEN")  # чужая выдача
    r = client.post(f"/loans/{loan['id']}/return", headers=h)
    assert r.status_code == 200 and r.json()["returned_at"]
    assert client.get(f"/books/{book['id']}").json()["copies_available"] == 1  # кэш сброшен после возврата
    assert_error(client.post(f"/loans/{loan['id']}/return", headers=h), 409, "CONFLICT")
    loans = client.get(f"/users/{user['id']}/loans", headers=h).json()
    assert len(loans) == 1 and client.get(f"/users/{user['id']}/loans?active=true", headers=h).json() == []
    assert_error(client.get(f"/users/{user['id']}/loans", headers=other), 403, "FORBIDDEN")


def test_borrow_limit_business_rule(client, register, make_book):
    client.app.state.settings.max_active_loans = 2
    _, h = register()
    for _ in range(2):
        assert client.post(f"/books/{make_book()['id']}/loans", headers=h).status_code == 201
    assert_error(client.post(f"/books/{make_book()['id']}/loans", headers=h), 422, "BUSINESS_RULE_VIOLATION")


def test_borrow_missing_book_and_anonymous(client, register):
    _, h = register()
    assert_error(client.post("/books/999/loans", headers=h), 404, "NOT_FOUND")
    assert_error(client.post("/books/999/loans"), 401, "UNAUTHORIZED")


def test_idempotency_key_prevents_double_borrow(client, register, make_book):
    book = make_book(copies_total=5)
    _, h = register()
    headers = {**h, "Idempotency-Key": "a1b2c3"}
    first = client.post(f"/books/{book['id']}/loans", headers=headers)
    second = client.post(f"/books/{book['id']}/loans", headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json() and second.headers["idempotent-replay"] == "true"
    assert client.get(f"/books/{book['id']}").json()["copies_available"] == 4  # списали один раз


def test_book_cache_aside_and_invalidation(client, librarian, make_book):
    book = make_book()
    cache = client.app.state.cache
    client.get(f"/books/{book['id']}")
    client.get(f"/books/{book['id']}")
    assert (cache.misses, cache.hits) == (1, 1)
    assert 0 < client.app.state.redis.ttl(f"book:{book['id']}") <= 60
    client.patch(f"/books/{book['id']}", json={"title": "New title"}, headers=librarian)
    assert client.get(f"/books/{book['id']}").json()["title"] == "New title"
    assert_error(client.get("/books/12345"), 404, "NOT_FOUND")


def test_patch_copies_and_delete_book_rules(client, register, librarian, make_book):
    book = make_book(copies_total=2)
    _, h = register()
    client.post(f"/books/{book['id']}/loans", headers=h)
    r = client.patch(f"/books/{book['id']}", json={"copies_total": 0}, headers=librarian)
    assert_error(r, 422, "BUSINESS_RULE_VIOLATION")
    assert client.patch(f"/books/{book['id']}", json={"copies_total": 4}, headers=librarian).json()[
        "copies_available"] == 3
    assert_error(client.delete(f"/books/{book['id']}", headers=librarian), 409, "CONFLICT")
    free = make_book()
    assert client.delete(f"/books/{free['id']}", headers=librarian).status_code == 204
    assert_error(client.get(f"/books/{free['id']}"), 404, "NOT_FOUND")


def test_user_with_books_cannot_delete_account(client, register, make_book):
    user, h = register()
    client.post(f"/books/{make_book()['id']}/loans", headers=h)
    assert_error(client.delete(f"/users/{user['id']}", headers=h), 422, "BUSINESS_RULE_VIOLATION")


def test_reviews_ownership(client, register, make_book):
    book = make_book()
    _, author = register()
    _, stranger = register()
    _, admin = register("admin")
    r = client.post(f"/books/{book['id']}/reviews", json={"rating": 5, "text": "Отлично"}, headers=author)
    assert r.status_code == 201
    rid = r.json()["id"]
    assert_error(client.post(f"/books/{book['id']}/reviews", json={"rating": 4, "text": "ещё"}, headers=author),
                 409, "CONFLICT")
    assert_error(client.post(f"/books/{book['id']}/reviews", json={"rating": 9, "text": "x"}, headers=author),
                 400, "VALIDATION_ERROR")
    # подмена id в URL: чужой пользователь не может менять отзыв
    assert_error(client.put(f"/reviews/{rid}", json={"rating": 1, "text": "плохо"}, headers=stranger),
                 403, "FORBIDDEN")
    assert client.put(f"/reviews/{rid}", json={"rating": 4, "text": "Хорошо"}, headers=author).json()["rating"] == 4
    assert client.get(f"/books/{book['id']}/reviews").json()[0]["text"] == "Хорошо"
    assert_error(client.delete(f"/reviews/{rid}", headers=stranger), 403, "FORBIDDEN")
    assert client.delete(f"/reviews/{rid}", headers=admin).status_code == 204
    assert_error(client.delete(f"/reviews/{rid}", headers=admin), 404, "NOT_FOUND")
    assert_error(client.get("/books/999/reviews"), 404, "NOT_FOUND")

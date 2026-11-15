"""Unit-тесты сервисов с моками (лаба 5): ни БД, ни HTTP."""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import fakeredis
import pytest

from app.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    TooManyRequestsError,
    UnauthorizedError,
)
from app.schemas import BookPatch, UserCreate
from app.security import TokenService, hash_password
from app.services.auth import AuthService
from app.services.books import BookService, LoanService
from app.services.users import UserService, ensure_self_or_admin

pytestmark = pytest.mark.unit


def make_user_service(email_exists=False, active_loans=0):
    session, users, loans, queue = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    users.email_exists.return_value = email_exists
    users.add.side_effect = lambda u: setattr(u, "id", 7) or u
    loans.count_active.return_value = active_loans
    return UserService(session, users, loans, queue), session, users, loans, queue


def test_create_user_saves_and_queues_welcome_email():
    # Arrange
    svc, session, users, _, queue = make_user_service()
    data = UserCreate(name="Anna", email="Anna@Example.com", age=30, password="secret123")
    # Act
    user = svc.create(data)
    # Assert
    assert (user.id, user.email, user.role) == (7, "anna@example.com", "user")
    assert user.password_hash != "secret123" and user.password_hash.startswith("$2b$")
    users.email_exists.assert_called_once_with("anna@example.com")
    users.add.assert_called_once()
    session.commit.assert_called_once()
    queue.enqueue.assert_called_once()
    assert queue.enqueue.call_args.args[0] == "anna@example.com"
    assert queue.enqueue.call_args.kwargs["job_id"] == "welcome-7"


def test_create_user_duplicate_email_raises_conflict_and_does_not_save():
    svc, session, users, _, queue = make_user_service(email_exists=True)
    with pytest.raises(ConflictError) as e:
        svc.create(UserCreate(name="Anna", email="anna@example.com", age=30, password="secret123"))
    assert e.value.details[0]["field"] == "email"
    users.add.assert_not_called()
    session.commit.assert_not_called()
    queue.enqueue.assert_not_called()


def test_password_equal_to_login_is_business_rule_violation():
    svc, _, users, _, _ = make_user_service()
    with pytest.raises(BusinessRuleError):
        svc.create(UserCreate(name="Anna", email="anna1234@example.com", age=30, password="Anna1234"))
    users.add.assert_not_called()


def test_get_missing_user_raises_not_found():
    svc, _, users, *_ = make_user_service()
    users.get.return_value = None
    with pytest.raises(NotFoundError):
        svc.get(1)


def test_delete_user_with_books_on_hands_is_forbidden_by_business_rule():
    svc, session, users, _, _ = make_user_service(active_loans=2)
    owner = SimpleNamespace(id=1, role="user")
    users.get.return_value = owner
    with pytest.raises(BusinessRuleError):
        svc.delete(1, owner)
    users.delete.assert_not_called()


@pytest.mark.parametrize("actor,owner_id,allowed", [
    (SimpleNamespace(id=1, role="user"), 1, True),
    (SimpleNamespace(id=2, role="user"), 1, False),
    (SimpleNamespace(id=2, role="admin"), 1, True),
    (None, 1, False),
])
def test_ensure_self_or_admin(actor, owner_id, allowed):
    if allowed:
        ensure_self_or_admin(actor, owner_id, "test")
    else:
        with pytest.raises(ForbiddenError):
            ensure_self_or_admin(actor, owner_id, "test")


# ---------- выдача книг ----------

def make_loan_service(*, book=True, active=0, already=False, copy_ok=True):
    session, books, loans, users = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    books.get.return_value = SimpleNamespace(id=5) if book else None
    books.take_copy.return_value = copy_ok
    loans.count_active.return_value = active
    loans.active_for.return_value = object() if already else None
    loans.add.side_effect = lambda loan: setattr(loan, "id", 11) or loan
    return LoanService(session, books, loans, users, max_active=3), session, books, loans


READER = SimpleNamespace(id=1, role="user")


def test_borrow_success_commits_once():
    svc, session, books, loans = make_loan_service()
    loan = svc.borrow(READER, 5)
    assert (loan.id, loan.user_id, loan.book_id) == (11, 1, 5)
    assert loan.due_at > loan.taken_at
    books.take_copy.assert_called_once_with(5)
    session.commit.assert_called_once()


@pytest.mark.parametrize("kwargs,error", [
    ({"book": False}, NotFoundError),
    ({"active": 3}, BusinessRuleError),
    ({"already": True}, ConflictError),
    ({"copy_ok": False}, ConflictError),
])
def test_borrow_errors(kwargs, error):
    svc, session, _, loans = make_loan_service(**kwargs)
    with pytest.raises(error):
        svc.borrow(READER, 5)
    session.commit.assert_not_called()
    loans.add.assert_not_called()


def test_book_patch_cannot_shrink_below_books_on_hands():
    session, books, loans = MagicMock(), MagicMock(), MagicMock()
    books.get.return_value = SimpleNamespace(id=1, copies_total=5, copies_available=2)  # 3 на руках
    svc = BookService(session, books, loans, cache=Mock())
    with pytest.raises(BusinessRuleError):
        svc.patch(1, BookPatch(copies_total=2))
    book = svc.patch(1, BookPatch(copies_total=4))
    assert (book.copies_total, book.copies_available) == (4, 1)


# ---------- аутентификация ----------

@pytest.fixture
def auth():
    users = MagicMock()
    user = SimpleNamespace(id=3, email="a@b.io", role="user", password_hash=hash_password("secret123"))
    users.get_by_email.side_effect = lambda e: user if e == "a@b.io" else None
    users.get.side_effect = lambda i: user if i == 3 else None
    r = fakeredis.FakeRedis(decode_responses=True)
    r.flushall()
    return AuthService(users, TokenService("k" * 32), r, max_attempts=3), r


def test_login_lockout_after_max_failed_attempts(auth):
    svc, r = auth
    for _ in range(3):
        with pytest.raises(UnauthorizedError):
            svc.login("a@b.io", "wrong-pass1")
    with pytest.raises(TooManyRequestsError) as e:
        svc.login("a@b.io", "secret123")  # даже верный пароль не пускает, пока стоит блокировка
    assert int(e.value.headers["Retry-After"]) > 0


def test_unknown_email_and_wrong_password_look_the_same(auth):
    svc, _ = auth
    with pytest.raises(UnauthorizedError) as e1:
        svc.login("nobody@b.io", "secret123")
    with pytest.raises(UnauthorizedError) as e2:
        svc.login("a@b.io", "bad-pass11")
    assert e1.value.message == e2.value.message


def test_refresh_rotation_old_token_cannot_be_reused(auth):
    svc, _ = auth
    pair = svc.login("a@b.io", "secret123")
    new_pair = svc.refresh(pair["refresh_token"])
    assert new_pair["refresh_token"] != pair["refresh_token"]
    with pytest.raises(UnauthorizedError):
        svc.refresh(pair["refresh_token"])
    svc.refresh(new_pair["refresh_token"])


def test_logout_blacklists_access_token(auth):
    svc, r = auth
    pair = svc.login("a@b.io", "secret123")
    assert svc.authenticate(pair["access_token"]).id == 3
    svc.logout(pair["access_token"], pair["refresh_token"])
    with pytest.raises(UnauthorizedError):
        svc.authenticate(pair["access_token"])
    with pytest.raises(UnauthorizedError):
        svc.refresh(pair["refresh_token"])
    assert any(k.startswith("blacklist:") and r.ttl(k) > 0 for k in r.keys())


def test_session_lifecycle(auth):
    svc, r = auth
    user = svc.verify_credentials("a@b.io", "secret123")
    sid = svc.create_session(user)
    assert 0 < r.ttl(f"session:{sid}") <= 24 * 3600
    assert svc.session_user(sid).id == 3
    svc.destroy_session(sid)
    with pytest.raises(UnauthorizedError):
        svc.session_user(sid)

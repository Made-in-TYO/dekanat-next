"""Проверки требований безопасности из задания:
студент видит только свои данные, пароли не хранятся в открытом виде, ключи не лежат в коде."""
import pathlib
import re

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuthSession, Notification, Student, StudentRequest, User
from conftest import csrf, login, make_client


def _request_id(number: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(StudentRequest.id).where(StudentRequest.number == number))


# ---------- Пароли и сеансы ----------

def test_passwords_are_hashed_with_argon2():
    with SessionLocal() as db:
        for u in db.scalars(select(User)):
            assert u.password_hash.startswith("$argon2id$")
            assert "2026" not in u.password_hash


def test_session_token_is_not_stored_in_plain_text(kira):
    token = kira.cookies.get("dn_session")
    assert token
    with SessionLocal() as db:
        hashes = [s.token_hash for s in db.scalars(select(AuthSession))]
    assert token not in hashes
    assert all(len(h) == 64 for h in hashes)


def test_session_cookie_flags():
    c = make_client()
    r = c.post("/login", data={"login": "kira", "password": "Kira-2026"}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_wrong_password_gives_generic_error():
    c = make_client()
    r1 = c.post("/login", data={"login": "kira", "password": "wrong"})
    r2 = c.post("/login", data={"login": "nobody", "password": "wrong"})
    assert r1.status_code == r2.status_code == 401
    assert "Неверный логин или пароль" in r1.text and "Неверный логин или пароль" in r2.text


def test_lockout_after_five_failed_attempts():
    c = make_client()
    for _ in range(5):
        c.post("/login", data={"login": "kira", "password": "wrong"})
    r = c.post("/login", data={"login": "kira", "password": "Kira-2026"}, follow_redirects=False)
    assert r.status_code == 401
    assert "заблокирован" in r.text


def test_pages_require_login():
    c = make_client()
    for path in ["/home", "/schedule", "/grades", "/requests", "/notifications", "/messages", "/profile", "/staff"]:
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), path


def test_logout_invalidates_session(kira):
    token = csrf(kira)
    kira.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert kira.get("/home", follow_redirects=False).status_code == 303


# ---------- Студент видит только свои данные ----------

def test_student_cannot_open_foreign_request_by_url(kira):
    foreign = _request_id("СП-2026-0461")  # заявка Тимура
    assert kira.get(f"/requests/{foreign}").status_code == 404
    assert kira.get(f"/requests/{foreign}/document").status_code == 404


def test_student_cannot_change_foreign_request(kira):
    foreign = _request_id("СП-2026-0470")  # заявка Тимура в статусе "отправлена"
    token = csrf(kira)
    r = kira.post(f"/requests/{foreign}/cancel", data={"csrf_token": token})
    assert r.status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(StudentRequest.status).where(StudentRequest.id == foreign)) == "submitted"


def test_student_cannot_open_foreign_notification(kira):
    with SessionLocal() as db:
        timur_user = db.scalar(select(User.id).where(User.login == "timur"))
        nid = db.scalar(select(Notification.id).where(Notification.user_id == timur_user).limit(1))
    token = csrf(kira)
    assert kira.post(f"/notifications/{nid}/open", data={"csrf_token": token}).status_code == 404


def test_student_lists_show_only_own_data(kira, timur):
    page = kira.get("/requests").text
    assert "СП-2026-0412" in page and "СП-2026-0461" not in page
    assert "Галиев" not in kira.get("/profile").text
    assert "Воронцова" not in timur.get("/messages").text


def test_student_cannot_use_staff_pages(kira):
    assert kira.get("/staff").status_code == 403
    assert kira.get("/staff/requests").status_code == 403
    token = csrf(kira)
    rid = _request_id("СП-2026-0457")
    r = kira.post(f"/staff/requests/{rid}/status", data={"new_status": "ready", "csrf_token": token})
    assert r.status_code == 403


def test_staff_has_no_student_pages(staff):
    assert staff.get("/home").status_code == 403


# ---------- Подделка запросов ----------

def test_forms_require_csrf_token(kira):
    r = kira.post("/requests", data={"type_code": "study", "purpose": "Банк", "fmt": "electronic"})
    assert r.status_code == 403
    r = kira.post("/requests", data={"type_code": "study", "purpose": "Банк", "fmt": "electronic", "csrf_token": "fake"})
    assert r.status_code == 403


def test_cross_site_post_is_rejected(kira):
    token = csrf(kira)
    r = kira.post("/messages", data={"body": "hi", "csrf_token": token}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_login_redirect_does_not_go_to_other_sites():
    c = make_client()
    r = c.post("/login", data={"login": "kira", "password": "Kira-2026", "next": "//evil.example/x"},
               follow_redirects=False)
    assert r.headers["location"] == "/home"


def test_user_text_is_escaped(kira):
    token = csrf(kira, "/messages")
    kira.post("/messages", data={"body": "<script>alert(1)</script>", "csrf_token": token})
    html = kira.get("/messages").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_security_headers(kira):
    r = kira.get("/home")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


def test_password_change(kira):
    other = login("kira", "Kira-2026")
    token = csrf(kira)
    bad = kira.post("/profile/password", data={"current": "nope", "new": "NewPass-2026", "repeat": "NewPass-2026",
                                               "csrf_token": token})
    assert bad.status_code == 400
    ok = kira.post("/profile/password", data={"current": "Kira-2026", "new": "NewPass-2026", "repeat": "NewPass-2026",
                                              "csrf_token": token}, follow_redirects=False)
    assert ok.status_code == 303
    assert other.get("/home", follow_redirects=False).status_code == 303  # второй сеанс завершен
    assert kira.get("/home").status_code == 200                           # текущий сеанс работает
    login("kira", "NewPass-2026")


# ---------- В коде нет секретов ----------

def test_no_secrets_in_repository():
    root = pathlib.Path(__file__).resolve().parent.parent
    pattern = re.compile(r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{30,})")
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".html", ".js", ".css", ".md", ".yml", ".yaml", ".txt", ".toml"} \
                and "node_modules" not in path.parts:
            assert not pattern.search(path.read_text(errors="ignore")), path

"""Тесты на находки четвертой независимой проверки (номера как в отчете, docs/PROCESS.md, раздел 11)."""
import threading
import time
from datetime import datetime

from sqlalchemy import select

from app import security
from app.db import SessionLocal
from app.models import LessonTemplate, StudentRequest
from app.services import timeutil
from conftest import csrf, login, make_client


# 2. Проверки пароля argon2id (64 МБ каждая) не идут больше двух одновременно
def test_argon2_runs_at_most_two_at_once(monkeypatch):
    active, peak, lock = 0, 0, threading.Lock()

    def slow_verify(_hash, _password):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return False

    class SlowHasher:  # у PasswordHasher методы только для чтения, поэтому подменяем объект целиком
        verify = staticmethod(slow_verify)

    monkeypatch.setattr(security, "_ph", SlowHasher())
    threads = [threading.Thread(target=security.verify_password, args=("h", "p")) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == security.ARGON2_SLOTS == 2


# 4. Подпись в профиле не рвется посреди слова
def test_profile_label_is_short(kira):
    html = kira.get("/profile").text
    assert "<dt>Программа</dt>" in html and "Образовательная программа" not in html


# 5. Ошибка «занятие уже началось» не стирает введенные данные
def test_started_lesson_error_keeps_input(staff):
    timeutil.freeze(datetime(2026, 11, 23, 15, 0))
    try:
        from app.security import reset_login_limits
        from app.seed import init_db
        init_db(reset=True)
        reset_login_limits()
        c = login("dekanat", "Dekanat-2026")
        with SessionLocal() as db:
            t = db.scalar(select(LessonTemplate).where(LessonTemplate.group_code == "P3214",
                                                       LessonTemplate.weekday == 0, LessonTemplate.pair_no == 3))
        token = csrf(c, "/staff/schedule")
        r = c.post("/staff/schedule", data={"template_id": t.id, "day": "2026-11-23", "change_type": "room",
                                            "new_room": "3401", "note": "Проектор не работает", "csrf_token": token})
        assert r.status_code == 400 and 'value="3401"' in r.text and "Проектор не работает" in r.text
    finally:
        timeutil.freeze(None)


# 7. Если деканат ждет ответа студента, срок зависит от студента, а не «прошел»
def test_eta_waits_for_student_answer(timur):
    with SessionLocal() as db:
        rid = db.scalar(select(StudentRequest.id).where(StudentRequest.status == "need_info"))
    assert "Срок зависит от вашего ответа" in timur.get(f"/requests/{rid}").text
    lst = timur.get("/requests").text
    assert "срок зависит от вашего ответа" in lst


# 11. Вход в один клик возвращает туда, куда человек шел
def test_one_click_login_keeps_next():
    c = make_client()
    html = c.get("/login?next=/grades").text
    assert html.count('name="next" value="/grades"') >= 4  # основная форма и три кнопки демо-входа
    r = c.post("/login", data={"login": "kira", "password": "Kira-2026", "next": "/grades"}, follow_redirects=False)
    assert r.headers["location"] == "/grades"


# 13. Текст о переносе без повтора слова «пара»
def test_move_notice_wording():
    from datetime import date

    from app.models import LessonChange
    from app.services.schedule import change_notice
    c = LessonChange(change_type="moved", date=date(2026, 11, 24), new_date=date(2026, 11, 27), new_pair_no=5)
    _title, body = change_notice("Базы данных", 2, c)
    assert body == "Пара перенесена со вторника, 24 ноября (2-я пара) на пятницу, 27 ноября (5-я пара)."


# 14. Ответы сжимаются, если браузер это поддерживает
def test_responses_are_gzipped(kira):
    r = kira.get("/home", headers={"accept-encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    assert "default-src 'self'" in r.headers.get("content-security-policy", "")

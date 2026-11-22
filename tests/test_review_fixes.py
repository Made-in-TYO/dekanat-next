"""Тесты на находки независимой проверки (Claude Code): каждая исправленная проблема закреплена тестом."""
import re
from datetime import date, timedelta

from sqlalchemy import select

import app.seed as seed_module
from app.db import SessionLocal
from app.models import Announcement, Deadline, LessonChange, LessonTemplate, Meta, StudentRequest, User
from app.services import schedule as S
from app.services.timeutil import today
from conftest import csrf, make_client


def _req(number):
    with SessionLocal() as db:
        return db.scalar(select(StudentRequest).where(StudentRequest.number == number))


# ---------- Демо-режим ----------

def test_demo_data_is_recreated_on_a_new_day(staff):
    token = csrf(staff, "/staff/announcements")
    staff.post("/staff/announcements", data={"title": "Временное объявление", "body": "Текст", "audience": "all",
                                             "csrf_token": token})
    with SessionLocal() as db:
        assert db.scalar(select(Announcement).where(Announcement.title == "Временное объявление")) is not None
        db.merge(Meta(key="seeded_on", value=(today() - timedelta(days=1)).isoformat()))
        db.commit()
    seed_module._checked_on = None
    make_client().get("/login")
    with SessionLocal() as db:
        assert db.scalar(select(Announcement).where(Announcement.title == "Временное объявление")) is None
        assert db.get(Meta, "seeded_on").value == today().isoformat()


def test_demo_role_switch(kira):
    token = csrf(kira)
    r = kira.post("/demo/switch", data={"to": "dekanat", "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/staff"
    assert kira.get("/staff").status_code == 200
    token = csrf(kira, "/staff")
    r = kira.post("/demo/switch", data={"to": "timur", "csrf_token": token}, follow_redirects=False)
    assert r.headers["location"] == "/home"
    assert "Галиев" in kira.get("/profile").text


def test_demo_switch_requires_csrf(kira):
    assert kira.post("/demo/switch", data={"to": "dekanat"}).status_code == 403


# ---------- Табло, расписание и календарь ----------

def test_schedule_handles_extreme_dates(kira):
    for q in ["9999-12-31", "0001-01-01", "abc"]:
        assert kira.get(f"/schedule?date={q}").status_code == 200
        assert kira.get(f"/schedule/week.ics?date={q}").status_code == 200


def test_session_period_shows_exams_instead_of_classes(kira):
    html = kira.get("/schedule?date=2027-01-13").text
    assert "Экзамен" in html and "Стратегическое обновление" in html
    assert "сессия" in html
    assert "Английский язык" not in html  # обычных пар в сессию нет


def test_holiday_has_no_classes(kira):
    html = kira.get("/schedule?date=2026-11-04").text
    assert "Праздничный день" in html


def test_ics_is_valid(kira):
    body = kira.get("/schedule/week.ics").text
    assert "BEGIN:VTIMEZONE" in body and "TZID:Europe/Moscow" in body
    for line in body.split("\r\n"):
        assert len(line.encode()) <= 75, line


def test_change_phrases_are_grammatical():
    v = S.LessonView(day=today(), pair_no=2, discipline="Физика", kind="lecture", room="1", building="b",
                     teacher="t", online=False, status="cancelled")
    assert v.change_phrase == "пара отменена"


# ---------- Заявки ----------

def test_ready_electronic_certificate_moves_to_archive(kira):
    html = kira.get("/requests").text
    active, archive = html.split("Архив", 1) if "Архив" in html else (html, "")
    assert "СП-2026-0412" not in active.split("В работе", 1)[1]
    assert "СП-2026-0412" in archive


def test_request_numbers_are_unique(kira):
    token = csrf(kira, "/requests/new?type=study")
    numbers = set()
    for i in range(3):
        r = kira.post("/requests", data={"type_code": "study", "purpose": f"Банк {i}", "fmt": "electronic",
                                         "csrf_token": token}, follow_redirects=False)
        rid = int(re.search(r"/requests/(\d+)", r.headers["location"]).group(1))
        with SessionLocal() as db:
            numbers.add(db.get(StudentRequest, rid).number)
    assert len(numbers) == 3 and all(n.startswith("СП-") for n in numbers)


def test_application_gets_decision_statuses_and_no_qr(kira, staff):
    token = csrf(kira, "/requests/new?type=aid")
    r = kira.post("/requests", data={"type_code": "aid", "purpose": "Сложная ситуация", "csrf_token": token},
                  follow_redirects=False)
    rid = int(re.search(r"/requests/(\d+)", r.headers["location"]).group(1))
    st = csrf(staff, f"/staff/requests/{rid}")
    staff.post(f"/staff/requests/{rid}/status", data={"new_status": "in_progress", "csrf_token": st})
    staff.post(f"/staff/requests/{rid}/status", data={"new_status": "ready", "csrf_token": st})
    html = kira.get(f"/requests/{rid}").text
    assert "Одобрено" in html and "Открыть справку" not in html
    with SessionLocal() as db:
        assert db.get(StudentRequest, rid).verify_code is None
    assert kira.get(f"/requests/{rid}/document").status_code == 404
    # После решения заявление закрыто: сотруднику больше нечего менять
    assert "Заявка закрыта" in staff.get(f"/staff/requests/{rid}").text


def test_stipend_certificate_only_for_budget_students(timur, kira):
    assert timur.get("/requests/new?type=stipend").status_code == 404
    token = csrf(timur, "/messages")
    assert timur.post("/requests", data={"type_code": "stipend", "purpose": "x" * 5, "fmt": "electronic",
                                         "csrf_token": token}).status_code == 404
    assert kira.get("/requests/new?type=stipend").status_code == 200


def test_csrf_with_non_ascii_token_is_rejected_not_crashing(kira):
    assert kira.post("/messages", data={"body": "привет", "csrf_token": "й"}).status_code == 403


# ---------- Кабинет сотрудника ----------

def test_unanswered_counter_counts_threads_waiting_for_reply(staff):
    with SessionLocal() as db:
        timur_student = db.scalar(select(User).where(User.login == "timur")).student.id
    assert re.search(r"<b>1</b><span>вопрос без ответа", staff.get("/staff").text)
    staff.get(f"/staff/messages/{timur_student}")  # просто открыть переписку
    assert re.search(r"<b>1</b><span>вопрос без ответа", staff.get("/staff").text)
    token = csrf(staff, f"/staff/messages/{timur_student}")
    staff.post(f"/staff/messages/{timur_student}", data={"body": "Больше ничего не нужно", "csrf_token": token})
    assert re.search(r"<b>0</b><span>вопросов без ответа", staff.get("/staff").text)


def _template_on(group: str, d: date) -> LessonTemplate:
    with SessionLocal() as db:
        ts = S.templates_for(db, group, d)
        assert ts, f"на {d} у группы {group} нет пар"
        return ts[0]


def _future_class_day(group: str) -> date:
    d = today() + timedelta(days=1)
    with SessionLocal() as db:
        for _ in range(30):
            if S.is_class_day(d) and S.templates_for(db, group, d):
                return d
            d += timedelta(days=1)
    raise AssertionError("нет дня с парами")


def test_staff_can_revert_schedule_change(staff, timur):
    d = _future_class_day("P3214")
    t = _template_on("P3214", d)
    token = csrf(staff, "/staff/schedule")
    staff.post("/staff/schedule", data={"template_id": t.id, "day": d.isoformat(), "change_type": "cancelled",
                                        "csrf_token": token})
    with SessionLocal() as db:
        assert db.scalar(select(LessonChange).where(LessonChange.template_id == t.id, LessonChange.date == d))
    r = staff.post("/staff/schedule", data={"template_id": t.id, "day": d.isoformat(), "change_type": "revert",
                                            "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.scalar(select(LessonChange).where(LessonChange.template_id == t.id, LessonChange.date == d)) is None
    assert "Как было" in timur.get("/notifications?kind=schedule").text


def test_move_validation(staff):
    d = _future_class_day("P3214")
    t = _template_on("P3214", d)
    token = csrf(staff, "/staff/schedule")
    base = {"template_id": t.id, "day": d.isoformat(), "change_type": "moved", "csrf_token": token}
    # в прошлое
    r = staff.post("/staff/schedule", data={**base, "new_date": (today() - timedelta(days=3)).isoformat(), "new_pair_no": 3})
    assert r.status_code == 400 and "в прошлое" in r.text
    # на воскресенье
    sunday = today() + timedelta(days=(6 - today().weekday()) % 7 or 7)
    r = staff.post("/staff/schedule", data={**base, "new_date": sunday.isoformat(), "new_pair_no": 3})
    assert r.status_code == 400
    # на занятый слот: сама эта пара в тот же день
    other = _template_on("P3214", d)
    r = staff.post("/staff/schedule", data={**base, "new_date": d.isoformat(), "new_pair_no": other.pair_no})
    assert r.status_code == 400
    # пустая аудитория: ошибка в форме, а не общая страница ошибки
    r = staff.post("/staff/schedule", data={**base, "change_type": "room", "new_room": ""})
    assert r.status_code == 400 and "Укажите новую аудиторию" in r.text


def test_change_for_a_day_without_that_lesson_is_rejected(staff):
    d = _future_class_day("P3214")
    t = _template_on("P3214", d)
    wrong_day = d + timedelta(days=1)
    while wrong_day.weekday() == t.weekday:
        wrong_day += timedelta(days=1)
    token = csrf(staff, "/staff/schedule")
    r = staff.post("/staff/schedule", data={"template_id": t.id, "day": wrong_day.isoformat(),
                                            "change_type": "cancelled", "csrf_token": token})
    assert r.status_code == 400


# ---------- Дедлайны ----------

def test_deadline_can_be_marked_done_only_by_owner(kira, timur):
    with SessionLocal() as db:
        kira_student = db.scalar(select(User).where(User.login == "kira")).student.id
        kira_dl = db.scalar(select(Deadline).where(Deadline.student_id == kira_student).limit(1))
    token = csrf(kira)
    assert kira.post(f"/deadlines/{kira_dl.id}/toggle", data={"csrf_token": token}, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.get(Deadline, kira_dl.id).done is True
    ttoken = csrf(timur)
    assert timur.post(f"/deadlines/{kira_dl.id}/toggle", data={"csrf_token": ttoken}).status_code == 404


def test_grade_texts_use_correct_plurals(kira):
    html = kira.get("/home").text
    assert not re.search(r"\b(2[1-4]|3[1-4]|4[1-4])\s+баллов\b", html)


# ---------- Вторая проверка ----------

def test_staff_request_filters_work(staff):
    for st in ["open", "submitted", "in_progress", "need_info", "ready", "issued", "rejected", "all", "nonsense"]:
        assert staff.get(f"/staff/requests?status={st}").status_code == 200, st


def test_huge_or_invalid_ids_give_404(kira, staff):
    for path in ["/requests/99999999999999999999999", "/requests/abc", "/requests/0"]:
        assert kira.get(path).status_code == 404, path
    for path in ["/staff/requests/99999999999999999999999", "/staff/messages/99999999999999999999999"]:
        assert staff.get(path).status_code == 404, path


def test_overdue_deadline_stays_visible(kira):
    from datetime import datetime
    with SessionLocal() as db:
        kira_student = db.scalar(select(User).where(User.login == "kira")).student.id
        db.add(Deadline(student_id=kira_student, title="Просроченное эссе", source="Тест",
                        due_at=datetime.combine(today() - timedelta(days=2), datetime.min.time())))
        db.commit()
    html = kira.get("/home").text
    assert "Просроченное эссе" in html and "просрочено на 2 дня" in html


def test_deadline_double_click_keeps_done(kira):
    with SessionLocal() as db:
        kira_student = db.scalar(select(User).where(User.login == "kira")).student.id
        dl = db.scalar(select(Deadline).where(Deadline.student_id == kira_student).limit(1))
    token = csrf(kira)
    for _ in range(2):
        kira.post(f"/deadlines/{dl.id}/toggle", data={"done": "1", "csrf_token": token})
    with SessionLocal() as db:
        assert db.get(Deadline, dl.id).done is True
    kira.post(f"/deadlines/{dl.id}/toggle", data={"done": "0", "csrf_token": token})
    with SessionLocal() as db:
        assert db.get(Deadline, dl.id).done is False


def test_demo_switch_returns_to_same_student(timur):
    token = csrf(timur)
    timur.post("/demo/switch", data={"to": "dekanat", "csrf_token": token})
    html = timur.get("/staff").text
    m = re.search(r'name="to" value="(\w+)"', html)
    assert m and m.group(1) == "timur"


def test_student_reply_is_visible_to_staff(timur, staff):
    rid = _req("СП-2026-0461").id
    token = csrf(timur, f"/requests/{rid}")
    r = timur.post(f"/requests/{rid}/reply", data={"answer": "   ", "csrf_token": token})
    assert r.status_code == 400 and "Напишите ответ" in r.text
    timur.post(f"/requests/{rid}/reply", data={"answer": "Нужна на английском", "csrf_token": token})
    assert "Ответ студента" in timur.get(f"/requests/{rid}").text
    assert "Ответ студента" in staff.get("/staff/requests").text
    assert "Ответ студента" in staff.get(f"/staff/requests/{rid}").text


def test_verify_link_uses_https_behind_proxy(kira):
    rid = _req("СП-2026-0412").id
    html = kira.get(f"/requests/{rid}/document", headers={"X-Forwarded-Proto": "https"}).text
    assert "https://testserver/verify/" in html


def test_verify_code_is_case_insensitive(kira):
    code = _req("СП-2026-0412").verify_code
    assert kira.get(f"/verify/{code.upper()}").status_code == 200


def test_staff_cannot_change_past_lessons(staff):
    d = today() - timedelta(days=1)
    for _ in range(14):
        with SessionLocal() as db:
            ts = S.templates_for(db, "P3214", d)
        if ts:
            break
        d -= timedelta(days=1)
    token = csrf(staff, "/staff/schedule")
    r = staff.post("/staff/schedule", data={"template_id": ts[0].id, "day": d.isoformat(), "change_type": "cancelled",
                                            "csrf_token": token})
    assert r.status_code == 400 and "уже прошло" in r.text


def test_grammar_so_sredy():
    from app.services.timeutil import from_day
    wed = today() + timedelta(days=(2 - today().weekday()) % 7)
    assert from_day(wed).startswith("со среды")
    tue = today() + timedelta(days=(1 - today().weekday()) % 7)
    assert from_day(tue).startswith("со вторника")
    thu = today() + timedelta(days=(3 - today().weekday()) % 7)
    assert from_day(thu).startswith("с четверга")


def test_null_origin_post_is_rejected():
    r = make_client().post("/login", data={"login": "kira", "password": "Kira-2026"}, headers={"Origin": "null"})
    assert r.status_code == 403


def test_head_requests_for_monitoring():
    c = make_client()
    assert c.head("/healthz").status_code == 200
    assert c.head("/", follow_redirects=False).status_code == 303


def test_instant_electronic_study_certificate(kira):
    token = csrf(kira, "/requests/new?type=study")
    r = kira.post("/requests", data={"type_code": "study", "purpose": "Для банка", "fmt": "electronic",
                                     "csrf_token": token}, follow_redirects=False)
    rid = int(re.search(r"/requests/(\d+)", r.headers["location"]).group(1))
    req = _req_by_id(rid)
    assert req.status == "ready" and req.verify_code
    assert kira.get(f"/requests/{rid}/document").status_code == 200
    assert "автоматически" in kira.get(f"/requests/{rid}").text


def test_paper_study_certificate_still_goes_to_staff(kira):
    token = csrf(kira, "/requests/new?type=study")
    r = kira.post("/requests", data={"type_code": "study", "purpose": "Для визы", "fmt": "paper", "copies": 2,
                                     "csrf_token": token}, follow_redirects=False)
    rid = int(re.search(r"/requests/(\d+)", r.headers["location"]).group(1))
    assert _req_by_id(rid).status == "submitted"
    assert "Готово будет примерно" in kira.get(f"/requests/{rid}").text


def _req_by_id(rid):
    with SessionLocal() as db:
        return db.get(StudentRequest, rid)


def test_ready_eta_counts_working_days():
    from app.services.catalog import ready_eta
    friday = date(2026, 11, 20)
    assert ready_eta(friday, 1) == date(2026, 11, 23)       # пятница + 1 рабочий день = понедельник
    assert ready_eta(date(2026, 11, 3), 1) == date(2026, 11, 5)  # 4 ноября праздник


def test_board_hints():
    from app.services.schedule import LessonView
    import app.services.schedule as sched
    d = today()
    first = LessonView(day=d, pair_no=1, discipline="A", kind="lecture", room="1", building="X", teacher="t",
                       online=False, status="cancelled")
    second = LessonView(day=d, pair_no=2, discipline="B", kind="lecture", room="2", building="X", teacher="t",
                        online=False)
    third = LessonView(day=d, pair_no=5, discipline="C", kind="lecture", room="3", building="Y", teacher="t",
                       online=False)
    orig = sched.lessons_for_date
    sched.lessons_for_date = lambda db, g, day: [first, second, third]
    try:
        hints = sched.board_hints(None, "G", None, second)
        assert any("можно прийти к 10:00" in h for h in hints)
        hints = sched.board_hints(None, "G", second, third)
        assert any("окно" in h for h in hints) and any("другом корпусе" in h for h in hints)
    finally:
        sched.lessons_for_date = orig


def test_idle_reset_keeps_open_sessions(kira, staff):
    import time
    token = csrf(staff, "/staff/announcements")
    staff.post("/staff/announcements", data={"title": "Пока кто-то смотрел", "body": "Текст", "audience": "all",
                                             "csrf_token": token})
    seed_module._last_activity = time.monotonic() - 31 * 60  # полчаса без посетителей
    html = kira.get("/home").text                              # тот же сеанс, без повторного входа
    assert "Привет, Кира" in html
    assert "Пока кто-то смотрел" not in kira.get("/notifications").text


def test_seed_during_session_has_no_phantom_lessons(monkeypatch):
    """Если демо-данные создаются в сессию, переносы на каникулы не появляются."""
    from datetime import datetime
    import app.seed as sd
    monkeypatch.setattr(sd, "now_local", lambda: datetime(2026, 12, 30, 12, 0))
    sd.init_db(reset=True)
    with SessionLocal() as db:
        for d in [date(2027, 1, 1), date(2027, 1, 4), date(2027, 1, 12)]:
            for v in S.lessons_for_date(db, "U4227", d):
                assert v.is_exam, (d, v.discipline, v.status)


def test_one_click_jury_login():
    c = make_client()
    html = c.get("/login").text
    assert html.count('action="/login"') >= 4  # основная форма и три кнопки демо-входа
    r = c.post("/login", data={"login": "dekanat", "password": "Dekanat-2026"}, follow_redirects=False)
    assert r.headers["location"] == "/staff"

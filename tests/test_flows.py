"""Сквозные сценарии из задания: войти, посмотреть расписание и оценки, подать заявку и увидеть ее статус."""
import re
from datetime import date, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import LessonTemplate, Notification, StudentRequest, User
from app.services import grades as G
from conftest import csrf


def test_core_pages_open(kira):
    for path in ["/home", "/schedule", "/schedule?view=week", "/grades", "/grades?tab=session", "/grades?tab=history",
                 "/requests", "/requests/new?type=study", "/notifications", "/messages", "/profile"]:
        assert kira.get(path).status_code == 200, path


def test_profile_has_required_fields(kira):
    html = kira.get("/profile").text
    for text in ["Воронцова Кира Андреевна", "Факультет технологического менеджмента и инноваций",
                 "27.04.05 Инноватика", "2 курс", "U4227"]:
        assert text in html


def test_request_full_cycle(kira, staff):
    # 1. Студент заказывает справку
    token = csrf(kira, "/requests/new?type=study")
    r = kira.post("/requests", data={"type_code": "study", "purpose": "Для банка", "fmt": "electronic",
                                     "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303
    rid = int(re.search(r"/requests/(\d+)", r.headers["location"]).group(1))
    assert "Отправлена" in kira.get(f"/requests/{rid}").text

    # 2. Сотрудник берет в работу и выдает электронную справку
    stoken = csrf(staff, f"/staff/requests/{rid}")
    assert staff.post(f"/staff/requests/{rid}/status", data={"new_status": "in_progress", "csrf_token": stoken},
                      follow_redirects=False).status_code == 303
    assert staff.post(f"/staff/requests/{rid}/status", data={"new_status": "ready", "csrf_token": stoken},
                      follow_redirects=False).status_code == 303
    # Недопустимый переход
    assert staff.post(f"/staff/requests/{rid}/status", data={"new_status": "submitted", "csrf_token": stoken}).status_code == 409

    # 3. Студент видит статус, уведомление и справку с QR-кодом
    assert "Готова" in kira.get(f"/requests/{rid}").text
    assert "готово" in kira.get("/notifications").text
    doc = kira.get(f"/requests/{rid}/document").text
    assert "<svg" in doc and "/verify/" in doc

    # 4. Публичная проверка показывает только инициалы
    with SessionLocal() as db:
        code = db.scalar(select(StudentRequest.verify_code).where(StudentRequest.id == rid))
    v = kira.get(f"/verify/{code}").text
    assert "Справка действительна" in v and "В********" in v and "Воронцова" not in v
    assert kira.get("/verify/abc123").status_code == 404


def test_need_info_and_student_reply(timur, staff):
    with SessionLocal() as db:
        rid = db.scalar(select(StudentRequest.id).where(StudentRequest.number == "СП-2026-0461"))
    token = csrf(timur, f"/requests/{rid}")
    r = timur.post(f"/requests/{rid}/reply", data={"answer": "Нужна на английском", "csrf_token": token},
                   follow_redirects=False)
    assert r.status_code == 303
    assert "В работе" in timur.get(f"/requests/{rid}").text
    assert "Нужна на английском" in staff.get(f"/staff/requests/{rid}").text


def test_request_validation(kira):
    token = csrf(kira, "/requests/new?type=study")
    r = kira.post("/requests", data={"type_code": "study", "purpose": "", "fmt": "electronic", "csrf_token": token})
    assert r.status_code == 400 and "Укажите" in r.text
    r = kira.post("/requests", data={"type_code": "transcript", "purpose": "Стажировка", "fmt": "electronic",
                                     "csrf_token": token})
    assert r.status_code == 400


def test_schedule_change_notifies_only_that_group(staff):
    with SessionLocal() as db:
        t = db.scalar(select(LessonTemplate).where(LessonTemplate.group_code == "P3214").limit(1))
        before_kira = db.query(Notification).join(User).filter(User.login == "kira").count()
    day = date.today() + timedelta(days=(t.weekday - date.today().weekday()) % 7 + 7)
    token = csrf(staff, "/staff/schedule")
    r = staff.post("/staff/schedule", data={"template_id": t.id, "day": day.isoformat(), "change_type": "room",
                                            "new_room": "4105", "note": "Тест", "csrf_token": token},
                   follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        timur_n = db.query(Notification).join(User).filter(User.login == "timur", Notification.title.like("Другая аудитория%")).count()
        after_kira = db.query(Notification).join(User).filter(User.login == "kira").count()
    assert timur_n == 1 and after_kira == before_kira


def test_announcement_reaches_students(staff, kira):
    token = csrf(staff, "/staff/announcements")
    staff.post("/staff/announcements", data={"title": "Тестовое объявление", "body": "Текст", "audience": "all",
                                             "csrf_token": token})
    assert "Тестовое объявление" in kira.get("/notifications").text


def test_messages_roundtrip(kira, staff):
    token = csrf(kira, "/messages")
    kira.post("/messages", data={"body": "Вопрос про сессию", "csrf_token": token})
    assert "Вопрос про сессию" in staff.get("/staff/messages").text


def test_ics_export(kira):
    r = kira.get("/schedule/week.ics")
    assert r.status_code == 200 and r.text.startswith("BEGIN:VCALENDAR")


def test_mobile_viewport_meta(kira):
    assert 'name="viewport"' in kira.get("/home").text


def test_grade_scale_matches_itmo_document():
    assert G.letter(59) == "FX"
    assert G.letter(60) == "E" and G.letter(67) == "E"
    assert G.letter(68) == "D" and G.letter(74) == "D"
    assert G.letter(75) == "C" and G.letter(83) == "C"
    assert G.letter(84) == "B" and G.letter(90) == "B"
    assert G.letter(91) == "A" and G.letter(103) == "A"
    assert G.grade_label(60, "credit") == "зачтено"


def test_deadline_reminders_are_created_once(kira):
    from app.models import Deadline
    kira.get("/home")
    kira.get("/home")
    with SessionLocal() as db:
        n = db.query(Notification).join(User).filter(User.login == "kira", Notification.kind == "deadline").count()
    # У Киры в демо-данных один дедлайн в ближайшие 3 дня (тест через 3 дня)
    assert n == 1
    assert "тест 2" in kira.get("/notifications?kind=deadline").text.lower()

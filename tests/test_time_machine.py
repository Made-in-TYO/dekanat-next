"""Машина времени: кабинет проверяется в разные моменты учебного года (третья независимая проверка).

Демо-данные строятся от «сейчас», поэтому ошибки прячутся в моментах, которых нет в день разработки:
выходные, праздники, вечер, сессия. Для каждого момента база создается заново и проверяются инварианты:
- каждое уведомление о расписании соответствует реальному изменению и пришло до начала пары;
- события демо-данных идут по порядку, в рабочие дни и часы и не позже «сейчас»;
- пустые состояния не противоречат табло;
- ни одна страница под тремя ролями не падает.
"""
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import config
from app.db import SessionLocal
from app.models import (Announcement, LessonChange, LessonTemplate, Message, Notification, RequestEvent, Student,
                        StudentRequest, User)
from app.security import reset_login_limits
from app.seed import init_db
from app.services import schedule as S
from app.services import timeutil
from app.services.timeutil import utc_to_local
from conftest import csrf, login
from test_crawl import crawl

HOLIDAYS = {datetime.fromisoformat(k).date() for k in config.HOLIDAYS}

MOMENTS = [
    datetime(2026, 9, 1, 8, 0),     # первый день семестра, до пар
    datetime(2026, 11, 4, 12, 0),   # праздник (среда)
    datetime(2026, 11, 5, 9, 0),    # день после праздника
    datetime(2026, 11, 23, 7, 30),  # понедельник до пар
    datetime(2026, 11, 23, 10, 30), # во время пары
    datetime(2026, 11, 23, 11, 35), # перемена
    datetime(2026, 11, 23, 21, 0),  # вечер
    datetime(2026, 11, 28, 12, 0),  # суббота
    datetime(2026, 11, 29, 12, 0),  # воскресенье
    datetime(2026, 12, 26, 12, 0),  # последний день занятий
    datetime(2026, 12, 28, 12, 0),  # сессия
    datetime(2027, 1, 5, 12, 0),    # новогодние каникулы
    datetime(2027, 1, 13, 9, 0),    # день экзамена
    datetime(2027, 2, 10, 12, 0),   # после сессии
]


@pytest.fixture
def at():
    """at(момент): «сейчас» = момент, демо-база создана заново в этот момент."""
    def go(moment: datetime):
        timeutil.freeze(moment)
        init_db(reset=True)
        reset_login_limits()
    yield go
    timeutil.freeze(None)


def _workday(d) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def check_invariants(now: datetime) -> list[str]:
    bad: list[str] = []
    with SessionLocal() as db:
        groups = {s.user_id: s.group_code for s in db.scalars(select(Student))}
        changes = db.scalars(select(LessonChange).options(selectinload(LessonChange.template)
                                                         .selectinload(LessonTemplate.discipline))).all()
        # Изменения стоят только на занятиях, которые реально есть в этот день
        for c in changes:
            if c.template_id not in {t.id for t in S.templates_for(db, c.template.group_code, c.date)}:
                bad.append(f"изменение на дне без этой пары: {c.date} {c.template.discipline.name}")
            if c.change_type == "moved" and not S.is_class_day(c.new_date):
                bad.append(f"перенос на неучебный день: {c.new_date}")
        # Каждое уведомление о расписании описывает реальное изменение и пришло до начала пары
        for n in db.scalars(select(Notification).where(Notification.kind == "schedule")):
            day = n.link.split("date=")[1][:10] if n.link and "date=" in n.link else None
            match = [c for c in changes if c.date.isoformat() == day and c.template.group_code == groups.get(n.user_id)
                     and c.template.discipline.name in n.title]
            if not match:
                bad.append(f"уведомление без изменения: {n.title} ({day})")
            elif utc_to_local(n.created_at) >= S.lesson_start(match[0].date, match[0].template.pair_no):
                bad.append(f"уведомление после начала пары: {n.title}")
        # Время событий: по порядку, в рабочие дни и часы, не в будущем
        stamps = []
        for r in db.scalars(select(StudentRequest).options(selectinload(StudentRequest.events))):
            stamps.append(("заявка", r.created_at))
            for e in r.events:
                stamps.append((f"событие {e.status}", e.created_at))
                if e.created_at < r.created_at:
                    bad.append(f"событие раньше заявки: {r.number}")
            if r.issued_at and r.issued_at < r.created_at:
                bad.append(f"выдана раньше подачи: {r.number}")
        for n in db.scalars(select(Notification)):
            stamps.append((f"уведомление {n.title}", n.created_at))
            if n.link and n.link.startswith("/requests/"):
                r = db.get(StudentRequest, int(n.link.rsplit("/", 1)[1]))
                if r is None or n.created_at < r.created_at:
                    bad.append(f"уведомление раньше заявки или без нее: {n.title}")
        stamps += [("сообщение", m.created_at) for m in db.scalars(select(Message))]
        stamps += [("объявление", a.created_at) for a in db.scalars(select(Announcement))]
        stamps += [("событие", e.created_at) for e in db.scalars(select(RequestEvent))]
        for what, utc in stamps:
            t = utc_to_local(utc)
            if t > now + timedelta(seconds=1):
                bad.append(f"{what} в будущем: {t}")
            if not (time(9, 0) <= t.time() <= time(18, 0)) or not _workday(t.date()):
                bad.append(f"{what} вне рабочего времени: {t:%a %d.%m %H:%M}")
        assert db.scalar(select(User).where(User.login == "kira")) is not None
    return bad


@pytest.mark.parametrize("moment", MOMENTS, ids=lambda m: m.strftime("%Y-%m-%d_%H%M"))
def test_demo_data_is_consistent_at_any_moment(at, moment):
    at(moment)
    assert check_invariants(moment) == []


@pytest.mark.parametrize("moment", MOMENTS, ids=lambda m: m.strftime("%Y-%m-%d_%H%M"))
def test_pages_work_and_agree_at_any_moment(at, moment):
    at(moment)
    for login_, password, start in [("kira", "Kira-2026", "/home"), ("timur", "Timur-2026", "/home"),
                                    ("dekanat", "Dekanat-2026", "/staff")]:
        c = login(login_, password)
        pages = crawl(c, start, limit=60)
        assert all(s < 500 for s in pages.values()), pages
        if start == "/home":
            html = c.get("/home").text
            if "Ближайших занятий нет" in html:
                assert "Ближайшее занятие показано на табло выше" not in html, moment


# ---------- Конкретные находки третьей проверки, которые видны только в определенное время ----------

def test_staff_cannot_change_lesson_that_already_started_today(at):
    """Находка 6: сравнение не только по дате. Понедельник 15:00, пара 11:40-13:10 уже прошла."""
    at(datetime(2026, 11, 23, 15, 0))
    with SessionLocal() as db:
        t = db.scalar(select(LessonTemplate).where(LessonTemplate.group_code == "P3214", LessonTemplate.weekday == 0,
                                                   LessonTemplate.pair_no == 3))
    c = login("dekanat", "Dekanat-2026")
    token = csrf(c, "/staff/schedule")
    r = c.post("/staff/schedule", data={"template_id": t.id, "day": "2026-11-23", "change_type": "cancelled",
                                        "csrf_token": token})
    assert r.status_code == 400 and "уже началось или прошло" in r.text
    # и перенос будущей пары на уже прошедшее сегодня время тоже нельзя
    with SessionLocal() as db:
        later = db.scalar(select(LessonTemplate).where(LessonTemplate.group_code == "P3214", LessonTemplate.weekday == 1,
                                                       LessonTemplate.pair_no == 4))
    r = c.post("/staff/schedule", data={"template_id": later.id, "day": "2026-11-24", "change_type": "moved",
                                        "new_date": "2026-11-23", "new_pair_no": 4, "csrf_token": token})
    assert r.status_code == 400 and "в прошлое" in r.text
    # в интерфейсе у прошедшей пары нет формы изменения
    html = c.get("/staff/schedule?group=P3214&date=2026-11-23").text
    assert "Занятие уже началось или прошло" in html


def test_board_hints_work_on_a_break(at):
    """Находка 16: на перемене подсказка о другом корпусе не пропадает."""
    at(datetime(2026, 11, 23, 11, 35))  # у P3214 физика в Кронверкском закончилась, дальше алгоритмы на Ломоносова
    html = login("timur", "Timur-2026").get("/home").text
    assert "Следующая пара в другом корпусе" in html

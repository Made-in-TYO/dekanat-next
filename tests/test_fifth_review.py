"""Тесты на находки пятой независимой проверки (Codex, номера как в отчете, docs/PROCESS.md, раздел 12)."""
import threading
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app import config
from app.db import SessionLocal
from app.models import AuthSession, Deadline, LessonTemplate, Notification, Student, StudentRequest, User
from app.security import reset_login_limits
from app.seed import init_db
from app.services import notify as notify_mod
from app.services import schedule as S
from app.services import timeutil
from conftest import csrf, login


def _at(moment: datetime):
    timeutil.freeze(moment)
    init_db(reset=True)
    reset_login_limits()


def _tpl(db, group: str, d: date, pair_no: int) -> LessonTemplate:
    for t in S.templates_for(db, group, d):
        if t.pair_no == pair_no:
            return t
    raise AssertionError(f"нет занятия {group} {d} {pair_no}-я пара")


def _change(c, token, t_id, day, change_type, **extra):
    data = {"template_id": t_id, "day": day, "change_type": change_type, "csrf_token": token, **extra}
    return c.post("/staff/schedule", data=data, follow_redirects=False)


# 1. «Вернуть как было» не ставит группе две пары на одно время
def test_revert_does_not_double_book():
    _at(datetime(2026, 11, 23, 7, 30))
    try:
        c = login("dekanat", "Dekanat-2026")
        token = csrf(c, "/staff/schedule")
        day = date(2026, 11, 23)
        with SessionLocal() as db:
            first = _tpl(db, "P3214", day, 1)
            third = _tpl(db, "P3214", day, 3)
        assert _change(c, token, first.id, day.isoformat(), "cancelled").status_code == 303
        r = _change(c, token, third.id, day.isoformat(), "moved", new_date=day.isoformat(), new_pair_no=1)
        assert r.status_code == 303
        for back in ("revert", "teacher"):
            r = _change(c, token, first.id, day.isoformat(), back, new_teacher="Иванов И. И.")
            assert r.status_code == 400 and "уже стоит другая пара" in r.text
        with SessionLocal() as db:
            at_first = [v for v in S.lessons_for_date(db, "P3214", day) if v.happens and v.pair_no == 1]
        assert len(at_first) == 1
    finally:
        timeutil.freeze(None)


# 2. Перенесенную пару, которая уже идет на новом месте, нельзя отменить через исходный день
def test_running_moved_lesson_is_locked():
    _at(datetime(2026, 11, 23, 15, 0))
    try:
        c = login("dekanat", "Dekanat-2026")
        token = csrf(c, "/staff/schedule")
        src, dst = date(2026, 11, 24), date(2026, 11, 23)
        with SessionLocal() as db:
            t = S.templates_for(db, "P3214", src)[0]
            pair = next(n for n in (5, 6, 7, 8) if not S.slot_taken(db, "P3214", dst, n))
        r = _change(c, token, t.id, src.isoformat(), "moved", new_date=dst.isoformat(), new_pair_no=pair)
        assert r.status_code == 303
        timeutil.freeze(S.lesson_start(dst, pair) + timedelta(minutes=5))
        r = _change(c, token, t.id, src.isoformat(), "cancelled")
        assert r.status_code == 400 and "уже началось" in r.text
        page = c.get(f"/staff/schedule?group=P3214&date={src.isoformat()}").text
        assert "изменить его нельзя" in page
    finally:
        timeutil.freeze(None)


# 3. Онлайн-паре не назначают аудиторию: иначе уведомление и табло говорят разное
def test_online_lesson_gets_no_room():
    _at(datetime(2026, 11, 24, 9, 0))
    try:
        c = login("dekanat", "Dekanat-2026")
        token = csrf(c, "/staff/schedule")
        day = date(2026, 11, 24)
        with SessionLocal() as db:
            t = next(x for x in S.templates_for(db, "P3214", day) if x.online)
            before = db.scalar(select(func.count(Notification.id)))
        r = _change(c, token, t.id, day.isoformat(), "room", new_room="3401")
        assert r.status_code == 400 and "онлайн-пара" in r.text
        with SessionLocal() as db:
            assert db.scalar(select(func.count(Notification.id))) == before
    finally:
        timeutil.freeze(None)


# 5. Длинное объявление студент читает целиком
def test_long_announcement_is_kept_whole(staff, timur):
    body = "Подробности о практике. " * 20 + "Встреча в 15:00 в ауд. 2337."
    token = csrf(staff, "/staff/announcements")
    staff.post("/staff/announcements", data={"title": "Практика", "body": body, "audience": "all", "csrf_token": token})
    assert "Встреча в 15:00 в ауд. 2337." in timur.get("/notifications?kind=news").text


# 7. У кнопок чата есть имя и на телефоне, где подпись скрыта
def test_chat_buttons_have_names(kira, staff):
    assert 'aria-label="Отправить"' in kira.get("/messages").text
    with SessionLocal() as db:
        sid = db.scalar(select(Student.id))
    assert 'aria-label="Ответить"' in staff.get(f"/staff/messages/{sid}").text


# 8. Дробное количество экземпляров: подсказка у поля, заполненное не теряется
def test_fractional_copies_keep_form(timur):
    token = csrf(timur, "/requests")
    r = timur.post("/requests", data={"type_code": "transcript", "purpose": "Для стажировки", "copies": "1.5",
                                      "fmt": "paper", "comment": "Нужен английский перевод", "csrf_token": token})
    assert r.status_code == 400
    assert "Введите целое число от 1 до 5" in r.text and "Для стажировки" in r.text and "Нужен английский перевод" in r.text


# 9. В тексте одобрения не повторяется прежний вопрос деканата
def test_approval_does_not_repeat_question(kira, staff):
    token = csrf(kira, "/requests")
    kira.post("/requests", data={"type_code": "schedule", "purpose": "Олимпиада по экономике", "csrf_token": token})
    with SessionLocal() as db:
        rid = db.scalar(select(StudentRequest.id).where(StudentRequest.type_code == "schedule")
                        .order_by(StudentRequest.id.desc()))
    st = csrf(staff, f"/staff/requests/{rid}")
    staff.post(f"/staff/requests/{rid}/status", data={"new_status": "need_info", "comment": "В какой день олимпиада?",
                                                       "csrf_token": st})
    kira.post(f"/requests/{rid}/reply", data={"answer": "15 января", "csrf_token": token})
    staff.post(f"/staff/requests/{rid}/status", data={"new_status": "ready", "comment": "", "csrf_token": st})
    html = kira.get(f"/requests/{rid}").text
    note = html.split('class="ready-note"', 1)[1].split("</p>", 1)[0]
    assert "Заявление одобрено" in note and "В какой день олимпиада" not in note
    assert "Электронное заявление" in staff.get(f"/staff/requests/{rid}").text


# Мелочь: напоминание о дедлайне приходит один раз, даже если две вкладки открылись одновременно
def test_deadline_reminder_once_under_race(monkeypatch):
    _at(datetime(2026, 11, 23, 9, 0))
    try:
        with SessionLocal() as db:
            sid = db.scalar(select(Student.id).join(User).where(User.login == "kira"))
            due = db.scalar(select(func.count(Deadline.id)).where(Deadline.student_id == sid, Deadline.reminded.is_(False),
                                                                  Deadline.done.is_(False)))
        n = 4
        barrier = threading.Barrier(n, timeout=5)
        real_update = notify_mod.update

        def update_after_everyone_read(*a, **kw):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return real_update(*a, **kw)

        monkeypatch.setattr(notify_mod, "update", update_after_everyone_read)

        def run():
            with SessionLocal() as db:
                notify_mod.remind_deadlines(db, db.get(Student, sid))

        threads = [threading.Thread(target=run) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with SessionLocal() as db:
            uid = db.get(Student, sid).user_id
            titles = db.scalars(select(Notification.title).where(Notification.user_id == uid,
                                                                 Notification.kind == "deadline")).all()
        assert due > 0
        assert len(titles) == len(set(titles)), titles
    finally:
        timeutil.freeze(None)


# Мелочь: в очереди вопросов первыми те, где ждут ответа, а не просто непрочитанные
def test_waiting_threads_first(staff):
    with SessionLocal() as db:
        kira_id = db.scalar(select(Student.id).join(User).where(User.login == "kira"))
        timur_id = db.scalar(select(Student.id).join(User).where(User.login == "timur"))
    t = login("timur", "Timur-2026")
    t.post("/messages", data={"body": "Когда пересдача?", "csrf_token": csrf(t, "/messages")})
    staff.get(f"/staff/messages/{timur_id}")  # открыли, но еще не ответили
    token = csrf(staff, f"/staff/messages/{kira_id}")
    staff.post(f"/staff/messages/{kira_id}", data={"body": "Дополнение к ответу", "csrf_token": token})
    html = staff.get("/staff/messages").text
    assert html.index("Галиев") < html.index("Воронцова")


# Мелочи: ссылка на неделю, текст справки о стипендии, подписи после сессии
def test_small_texts(kira, staff):
    assert 'href="/schedule?view=week">Вся неделя' in kira.get("/home").text
    from app.services.catalog import TYPES
    assert "суммами" not in TYPES["stipend"].description
    assert S.day_note(date.fromisoformat(config.SESSION_END) + timedelta(days=10)) == "Сессия закончилась, занятий нет"
    assert timeutil.week_label(date.fromisoformat(config.SESSION_END) + timedelta(days=10)) == "после сессии"


def test_exam_day_message_for_staff():
    _at(datetime(2027, 1, 12, 9, 0))
    try:
        c = login("dekanat", "Dekanat-2026")
        html = c.get("/staff/schedule?group=U4227&date=2027-01-13").text
        assert "Обычных пар для изменения нет" in html and "нет занятий" not in html
    finally:
        timeutil.freeze(None)


# Мелочь: клиент, явно отказавшийся от gzip, получает несжатый ответ
def test_gzip_refused_by_client(kira):
    from app.main import gzip_refused
    assert gzip_refused("gzip;q=0, identity;q=1") and gzip_refused("GZIP; q=0.0")
    assert not gzip_refused("gzip, deflate, br") and not gzip_refused("gzip;q=0.5")
    r = kira.get("/home", headers={"accept-encoding": "gzip;q=0, identity;q=1"})
    assert "content-encoding" not in r.headers
    assert kira.get("/home", headers={"accept-encoding": "gzip"}).headers.get("content-encoding") == "gzip"


# 10. Origin сравнивается целиком: схема и порт, а у отказа есть заголовки безопасности
def test_origin_checks_scheme_and_port(kira):
    token = csrf(kira, "/messages")
    https_proxy = {"x-forwarded-proto": "https", "x-forwarded-host": "dekanat.example"}
    for bad in ("http://dekanat.example", "https://dekanat.example:8443", "https://evil.example"):
        r = kira.post("/messages", data={"body": "x", "csrf_token": token},
                      headers={"origin": bad, **https_proxy}, follow_redirects=False)
        assert r.status_code == 403, bad
        assert "default-src 'self'" in r.headers.get("content-security-policy", "")
    ok = kira.post("/messages", data={"body": "Свой сайт", "csrf_token": token},
                   headers={"origin": "https://dekanat.example", **https_proxy}, follow_redirects=False)
    assert ok.status_code == 303


# Мелочь: истекшие сеансы не переживают сброс демо-данных
def test_expired_sessions_dropped_on_reset():
    from app.seed import reset_demo_keep_sessions
    login("kira", "Kira-2026")
    with SessionLocal() as db:
        s = db.scalar(select(AuthSession))
        s.expires_at = timeutil.utcnow() - timedelta(hours=1)
        db.commit()
        before = db.scalar(select(func.count(AuthSession.id)))
    reset_demo_keep_sessions()
    with SessionLocal() as db:
        assert db.scalar(select(func.count(AuthSession.id))) == before - 1

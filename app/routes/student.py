"""Кабинет студента. Каждый запрос к данным фильтруется по текущему студенту из серверной сессии:
идентификатор студента никогда не берется из адреса или формы."""
import secrets
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from .common import IdPath
from ..models import (Announcement, AuthSession, Deadline, Enrollment, ExamEvent, Message, Notification, PastResult,
                      RequestEvent, StudentRequest)
from ..security import (check_csrf, destroy_other_sessions, hash_password, public_base_url, require_student,
                        verify_password)
from ..services import grades as G
from ..services import schedule as S
from ..services.catalog import REQUEST_TYPES, TYPES, is_active, is_instant
from ..services.timeutil import (WEEKDAYS, WEEKDAYS_SHORT, now_local, semester_week, today, utcnow,
                                 week_monday, week_parity)
from .. import config
from ..web import render

router = APIRouter()


def _student(s: AuthSession):
    return s.user.student


def _parse_date(value: str | None, default: date | None = None) -> date:
    """Дата из адреса. Неверная или слишком далекая (больше 2 лет) заменяется значением по умолчанию."""
    default = default or today()
    try:
        d = date.fromisoformat(value) if value else default
    except ValueError:
        return default
    if abs((d - today()).days) > config.DATE_RANGE_DAYS:
        return default
    return d


def _default_schedule_day() -> date:
    """В воскресенье пар нет, поэтому расписание по умолчанию открывается на понедельнике."""
    t = today()
    return t + timedelta(days=1) if t.weekday() == 6 else t


def _own_request(db: Session, s: AuthSession, request_id: int) -> StudentRequest:
    r = db.scalar(select(StudentRequest).options(selectinload(StudentRequest.events))
                  .where(StudentRequest.id == request_id, StudentRequest.student_id == _student(s).id))
    if r is None:
        # Одинаковый ответ для "нет такой заявки" и "заявка чужая": не раскрываем, что она существует
        raise HTTPException(status_code=404)
    return r


def _announcements_for(db: Session, group_code: str, limit: int | None = None):
    q = (select(Announcement).where(or_(Announcement.audience == "all", Announcement.audience == f"group:{group_code}"))
         .order_by(Announcement.created_at.desc()))
    if limit:
        q = q.limit(limit)
    return db.scalars(q).all()


@router.get("/home")
def home(request: Request, s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    st = _student(s)
    now = now_local()
    current, nxt = S.current_and_next(db, st.group_code)
    lessons_today = S.lessons_for_date(db, st.group_code, now.date())
    changes = S.upcoming_changes(db, st.group_code, days=7)
    upcoming = db.scalars(select(Deadline).where(Deadline.student_id == st.id,
                                                 Deadline.due_at >= now, Deadline.due_at <= now + timedelta(days=14))
                          .order_by(Deadline.done, Deadline.due_at)).all()
    # Просроченные и не сделанные не пропадают, а стоят первыми с пометкой "просрочено"
    overdue = db.scalars(select(Deadline).where(Deadline.student_id == st.id, Deadline.done.is_(False),
                                                Deadline.due_at < now, Deadline.due_at >= now - timedelta(days=30))
                         .order_by(Deadline.due_at)).all()
    deadlines = list(overdue) + [d for d in upcoming if not d.done] + [d for d in upcoming if d.done]
    all_reqs = db.scalars(select(StudentRequest).where(StudentRequest.student_id == st.id)
                          .order_by(StudentRequest.updated_at.desc())).all()
    active_reqs = [r for r in all_reqs if is_active(r)][:3]
    enrollments = db.scalars(select(Enrollment).options(selectinload(Enrollment.items), selectinload(Enrollment.discipline))
                             .where(Enrollment.student_id == st.id)).all()
    progress = [G.build_progress(e) for e in enrollments]
    attention = [p for p in progress if p.state in ("risk", "fail", "watch")]
    past = db.scalars(select(PastResult).where(PastResult.student_id == st.id)).all()
    unread_msgs = db.scalar(select(func.count(Message.id)).where(Message.student_id == st.id, Message.from_staff.is_(True),
                                                                 Message.read_by_student.is_(False))) or 0
    return render(request, "student/home.html", {
        "nav": "home", "st": st, "now": now, "current": current, "next": nxt, "lessons_today": lessons_today,
        "changes": changes, "deadlines": deadlines, "active_reqs": active_reqs, "progress": progress,
        "attention": attention, "avg": G.average_five(past), "unread_msgs": unread_msgs,
        "today_note": S.day_note(now.date()), "hints": S.board_hints(db, st.group_code, current, nxt),
        "news": _announcements_for(db, st.group_code, 2),
    }, db=db)


@router.get("/schedule")
def schedule(request: Request, view: str = "day", s: AuthSession = Depends(require_student),
             db: Session = Depends(get_db)):
    st = _student(s)
    d = _parse_date(request.query_params.get("date"), _default_schedule_day())
    monday = week_monday(d)
    ctx = {"nav": "schedule", "st": st, "view": "week" if view == "week" else "day", "d": d, "monday": monday,
           "week_no": semester_week(d), "parity": week_parity(d), "WEEKDAYS": WEEKDAYS, "WEEKDAYS_SHORT": WEEKDAYS_SHORT,
           "now": now_local(), "PAIRS": S.PAIRS, "timedelta": timedelta, "day_note": S.day_note,
           "default_day": _default_schedule_day()}
    if ctx["view"] == "week":
        ctx["week"] = S.week_lessons(db, st.group_code, monday)
    else:
        ctx["lessons"] = S.lessons_for_date(db, st.group_code, d)
        ctx["strip"] = [(monday + timedelta(days=i)) for i in range(6)]
        ctx["counts"] = {x: sum(1 for v in S.lessons_for_date(db, st.group_code, x) if v.happens) for x in ctx["strip"]}
    return render(request, "student/schedule.html", ctx, db=db)


@router.get("/schedule/week.ics")
def schedule_ics(request: Request, s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    st = _student(s)
    monday = week_monday(_parse_date(request.query_params.get("date"), _default_schedule_day()))
    body = S.week_ics(db, st.group_code, monday)
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="raspisanie-{monday.isoformat()}.ics"'})


@router.get("/grades")
def grades(request: Request, tab: str = "current", s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    st = _student(s)
    tab = tab if tab in ("current", "session", "history") else "current"
    enrollments = db.scalars(select(Enrollment).options(selectinload(Enrollment.items), selectinload(Enrollment.discipline))
                             .where(Enrollment.student_id == st.id)).all()
    progress = sorted((G.build_progress(e) for e in enrollments),
                      key=lambda p: ["fail", "risk", "watch", "ok", "done"].index(p.state))
    exams = db.scalars(select(ExamEvent).options(selectinload(ExamEvent.discipline))
                       .where(ExamEvent.group_code == st.group_code).order_by(ExamEvent.starts_at)).all()
    past = db.scalars(select(PastResult).where(PastResult.student_id == st.id).order_by(PastResult.id)).all()
    semesters: dict[str, list] = {}
    for r in past:
        semesters.setdefault(r.semester, []).append(r)
    return render(request, "student/grades.html", {
        "nav": "grades", "st": st, "tab": tab, "progress": progress, "exams": exams, "semesters": semesters,
        "avg": G.average_five(past), "G": G, "credits": sum(p.credits for p in progress),
        "debts": sum(1 for r in past if r.points < G.PASS),
        "counts": {k: sum(1 for p in progress if p.state == k) for k in ("ok", "watch", "risk", "fail")},
    }, db=db)


# ---------------------------- Заявки ----------------------------

@router.get("/requests")
def requests_list(request: Request, s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    st = _student(s)
    reqs = db.scalars(select(StudentRequest).where(StudentRequest.student_id == st.id)
                      .order_by(StudentRequest.created_at.desc())).all()
    return render(request, "student/requests.html", {
        "nav": "requests", "active": [r for r in reqs if is_active(r)],
        "archive": [r for r in reqs if not is_active(r)], "catalog": REQUEST_TYPES, "st": st,
    }, db=db)


@router.get("/requests/new")
def request_new(request: Request, type: str = "study", s: AuthSession = Depends(require_student),
                db: Session = Depends(get_db)):
    t = TYPES.get(type)
    if t is None or not _available(t, _student(s)):
        raise HTTPException(status_code=404)
    return render(request, "student/request_new.html", {"nav": "requests", "t": t, "form": {}}, db=db)


def _available(t, st) -> bool:
    """Справка о стипендии доступна только студентам на бюджете."""
    return not t.budget_only or st.funding == "Бюджет"


def _number_for(request_id: int) -> str:
    """Номер заявки строится из ее идентификатора в базе, поэтому две заявки не получат один номер."""
    return f"СП-{today().year}-{request_id + 470:04d}"


@router.post("/requests")
def request_create(request: Request, type_code: str = Form(...), purpose: str = Form(""), copies: str = Form("1"),
                   fmt: str = Form("electronic"), comment: str = Form(""), csrf_token: str = Form(""),
                   s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    t = TYPES.get(type_code)
    if t is None or not _available(t, _student(s)):
        raise HTTPException(status_code=404)
    purpose, comment = purpose.strip()[:200], comment.strip()[:1000]
    if len(t.formats) == 1:
        fmt = t.formats[0]
    errors = {}
    if t.needs_purpose and len(purpose) < 3:
        errors["purpose"] = "Укажите, для чего нужен документ" if t.kind == "certificate" else "Укажите причину"
    if fmt not in t.formats:
        errors["fmt"] = "Выберите формат из предложенных"
    # Количество разбираем здесь, а не в типе параметра: при "1.5" или "два" студент получает
    # подсказку у поля и не теряет уже заполненную цель и комментарий
    copies_raw = copies.strip()[:32]
    n_copies = int(copies_raw) if copies_raw.isascii() and copies_raw.isdigit() else 0
    if fmt == "paper" and t.kind == "certificate" and not 1 <= n_copies <= 5:
        errors["copies"] = "Введите целое число от 1 до 5"
    if errors:
        return render(request, "student/request_new.html", {
            "nav": "requests", "t": t, "errors": errors,
            "form": {"purpose": purpose, "copies": copies_raw, "fmt": fmt, "comment": comment}}, status_code=400, db=db)
    st = _student(s)
    r = StudentRequest(number=f"tmp-{secrets.token_hex(8)}", student_id=st.id, type_code=t.code,
                       purpose=purpose or None, copies=n_copies if (fmt == "paper" and t.kind == "certificate") else 1,
                       fmt=fmt, comment=comment or None, status="submitted")
    db.add(r)
    db.flush()
    r.number = _number_for(r.id)
    db.add(RequestEvent(request_id=r.id, status="submitted", actor="student", comment=comment or None))
    if is_instant(t.code, fmt):
        # Электронная справка об обучении собирается из данных учетной системы сразу, без очереди
        r.status = "ready"
        r.issued_at = utcnow()
        r.verify_code = secrets.token_hex(8)
        db.add(RequestEvent(request_id=r.id, status="ready", actor="system",
                            comment="Справка сформирована автоматически по данным учетной системы и подписана электронной подписью"))
        db.commit()
        return RedirectResponse(f"/requests/{r.id}?ok=instant", status_code=303)
    db.commit()
    return RedirectResponse(f"/requests/{r.id}?ok=created", status_code=303)


@router.get("/requests/{request_id}")
def request_detail(request: Request, request_id: IdPath, s: AuthSession = Depends(require_student),
                   db: Session = Depends(get_db)):
    r = _own_request(db, s, request_id)
    return render(request, "student/request_detail.html", {"nav": "requests", "r": r, "t": TYPES[r.type_code]}, db=db)


@router.post("/requests/{request_id}/cancel")
def request_cancel(request: Request, request_id: IdPath, csrf_token: str = Form(""),
                   s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    r = _own_request(db, s, request_id)
    if r.status not in ("submitted", "need_info"):
        raise HTTPException(status_code=409)
    r.status = "cancelled"
    r.updated_at = utcnow()
    db.add(RequestEvent(request_id=r.id, status="cancelled", actor="student"))
    db.commit()
    return RedirectResponse(f"/requests/{r.id}?ok=cancelled", status_code=303)


@router.post("/requests/{request_id}/reply")
def request_reply(request: Request, request_id: IdPath, answer: str = Form(""), csrf_token: str = Form(""),
                  s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    r = _own_request(db, s, request_id)
    answer = answer.strip()[:1000]
    if r.status != "need_info":
        raise HTTPException(status_code=409)
    if not answer:
        return render(request, "student/request_detail.html", {"nav": "requests", "r": r, "t": TYPES[r.type_code],
                                                               "reply_error": "Напишите ответ на вопрос деканата"},
                      status_code=400, db=db)
    r.status = "in_progress"
    r.updated_at = utcnow()
    # Отдельное событие "ответ студента": деканат видит его в очереди и в истории заявки
    db.add(RequestEvent(request_id=r.id, status="reply", actor="student", comment=answer))
    db.commit()
    return RedirectResponse(f"/requests/{r.id}?ok=replied", status_code=303)


@router.get("/requests/{request_id}/document")
def request_document(request: Request, request_id: IdPath, s: AuthSession = Depends(require_student),
                     db: Session = Depends(get_db)):
    r = _own_request(db, s, request_id)
    if r.fmt != "electronic" or r.status not in ("ready", "issued") or not r.verify_code or TYPES[r.type_code].kind != "certificate":
        raise HTTPException(status_code=404)
    from ..services.qr import qr_svg
    verify_url = public_base_url(request) + f"/verify/{r.verify_code}"
    return render(request, "student/document.html", {"nav": "requests", "r": r, "t": TYPES[r.type_code],
                                                     "st": _student(s), "qr": qr_svg(verify_url),
                                                     "verify_url": verify_url}, db=db)


# ---------------------------- Дедлайны ----------------------------

@router.post("/deadlines/{deadline_id}/toggle")
def deadline_toggle(request: Request, deadline_id: IdPath, done: str = Form("1"), csrf_token: str = Form(""),
                    s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    """Ставит явное состояние (done=1 или 0), а не переключает: двойной клик не отменяет отметку."""
    check_csrf(s, csrf_token)
    d = db.scalar(select(Deadline).where(Deadline.id == deadline_id, Deadline.student_id == _student(s).id))
    if d is None:
        raise HTTPException(status_code=404)
    d.done = done == "1"
    db.commit()
    return RedirectResponse("/home#deadlines", status_code=303)


# ---------------------------- Уведомления ----------------------------

@router.get("/notifications")
def notifications(request: Request, kind: str = "all", s: AuthSession = Depends(require_student),
                  db: Session = Depends(get_db)):
    q = select(Notification).where(Notification.user_id == s.user_id)
    if kind in ("schedule", "request", "deadline", "news", "message"):
        q = q.where(Notification.kind == kind)
    else:
        kind = "all"
    items = db.scalars(q.order_by(Notification.created_at.desc()).limit(100)).all()
    return render(request, "student/notifications.html", {"nav": "notifications", "items": items, "kind": kind}, db=db)


@router.post("/notifications/read-all")
def notifications_read_all(request: Request, csrf_token: str = Form(""), s: AuthSession = Depends(require_student),
                           db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    for n in db.scalars(select(Notification).where(Notification.user_id == s.user_id, Notification.read_at.is_(None))):
        n.read_at = utcnow()
    db.commit()
    return RedirectResponse("/notifications?ok=readall", status_code=303)


@router.post("/notifications/{notif_id}/open")
def notification_open(request: Request, notif_id: IdPath, csrf_token: str = Form(""),
                      s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    n = db.scalar(select(Notification).where(Notification.id == notif_id, Notification.user_id == s.user_id))
    if n is None:
        raise HTTPException(status_code=404)
    if n.read_at is None:
        n.read_at = utcnow()
        db.commit()
    link = n.link if (n.link and n.link.startswith("/") and not n.link.startswith("//")) else "/notifications"
    return RedirectResponse(link, status_code=303)


# ---------------------------- Связь с деканатом ----------------------------

@router.get("/messages")
def messages(request: Request, s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    st = _student(s)
    items = db.scalars(select(Message).options(selectinload(Message.author)).where(Message.student_id == st.id)
                       .order_by(Message.created_at)).all()
    changed = False
    for m in items:
        if m.from_staff and not m.read_by_student:
            m.read_by_student = True
            changed = True
    if changed:
        db.commit()
    return render(request, "student/messages.html", {"nav": "messages", "items": items}, db=db)


@router.post("/messages")
def message_send(request: Request, body: str = Form(""), csrf_token: str = Form(""),
                 s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    body = body.strip()[:2000]
    if not body:
        items = db.scalars(select(Message).options(selectinload(Message.author)).where(Message.student_id == _student(s).id)
                           .order_by(Message.created_at)).all()
        return render(request, "student/messages.html", {"nav": "messages", "items": items,
                                                         "chat_error": "Напишите текст вопроса"}, status_code=400, db=db)
    db.add(Message(student_id=_student(s).id, author_id=s.user_id, from_staff=False, body=body, read_by_student=True))
    db.commit()
    return RedirectResponse("/messages?ok=sent#last", status_code=303)


# ---------------------------- Профиль ----------------------------

@router.get("/profile")
def profile(request: Request, s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    return render(request, "student/profile.html", {"nav": "profile", "st": _student(s)}, db=db)


@router.post("/profile/password")
def change_password(request: Request, current: str = Form(""), new: str = Form(""), repeat: str = Form(""),
                    csrf_token: str = Form(""), s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    error = None
    if s.user.is_demo:
        # Общие тестовые аккаунты нельзя "угнать" сменой пароля: иначе следующий член жюри не войдет
        error = "В тестовых аккаунтах смена пароля отключена, чтобы кабинет оставался доступен всем. В настоящем кабинете здесь меняется пароль"
    elif not verify_password(s.user.password_hash, current):
        error = "Текущий пароль указан неверно"
    elif len(new) < 8:
        error = "Новый пароль должен быть не короче 8 символов"
    elif new != repeat:
        error = "Пароли не совпадают"
    elif new == current:
        error = "Новый пароль совпадает с текущим"
    if error:
        return render(request, "student/profile.html", {"nav": "profile", "st": _student(s), "pw_error": error},
                      status_code=400, db=db)
    s.user.password_hash = hash_password(new)
    db.commit()
    destroy_other_sessions(db, s.user_id, request.cookies.get(config.SESSION_COOKIE))
    return RedirectResponse("/profile?ok=password", status_code=303)

"""Кабинет студента. Каждый запрос к данным фильтруется по текущему студенту из серверной сессии:
идентификатор студента никогда не берется из адреса или формы."""
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import (Announcement, AuthSession, Deadline, Enrollment, ExamEvent, Message, Notification, PastResult,
                      RequestEvent, StudentRequest)
from ..security import check_csrf, destroy_other_sessions, hash_password, require_student, verify_password
from ..services import grades as G
from ..services import schedule as S
from ..services.catalog import ACTIVE, TYPES, REQUEST_TYPES
from ..services.notify import notify
from ..services.timeutil import (WEEKDAYS, WEEKDAYS_SHORT, now_local, semester_week, today, utcnow,
                                 week_monday, week_parity)
from .. import config
from ..web import render

router = APIRouter()


def _student(s: AuthSession):
    return s.user.student


def _parse_date(value: str | None) -> date:
    try:
        return date.fromisoformat(value) if value else today()
    except ValueError:
        return today()


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
    deadlines = db.scalars(select(Deadline).where(Deadline.student_id == st.id, Deadline.done.is_(False),
                                                  Deadline.due_at >= now, Deadline.due_at <= now + timedelta(days=14))
                           .order_by(Deadline.due_at)).all()
    active_reqs = db.scalars(select(StudentRequest).where(StudentRequest.student_id == st.id,
                                                          StudentRequest.status.in_(ACTIVE))
                             .order_by(StudentRequest.updated_at.desc()).limit(3)).all()
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
        "week_no": semester_week(now.date()), "parity": week_parity(now.date()),
        "news": _announcements_for(db, st.group_code, 2),
    }, db=db)


@router.get("/schedule")
def schedule(request: Request, view: str = "day", s: AuthSession = Depends(require_student),
             db: Session = Depends(get_db)):
    st = _student(s)
    d = _parse_date(request.query_params.get("date"))
    monday = week_monday(d)
    ctx = {"nav": "schedule", "st": st, "view": "week" if view == "week" else "day", "d": d, "monday": monday,
           "week_no": semester_week(d), "parity": week_parity(d), "WEEKDAYS": WEEKDAYS, "WEEKDAYS_SHORT": WEEKDAYS_SHORT,
           "now": now_local(), "PAIRS": S.PAIRS, "timedelta": timedelta}
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
    monday = week_monday(_parse_date(request.query_params.get("date")))
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
        "counts": {k: sum(1 for p in progress if p.state == k) for k in ("ok", "watch", "risk", "fail")},
    }, db=db)


# ---------------------------- Заявки ----------------------------

@router.get("/requests")
def requests_list(request: Request, s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    st = _student(s)
    reqs = db.scalars(select(StudentRequest).where(StudentRequest.student_id == st.id)
                      .order_by(StudentRequest.created_at.desc())).all()
    return render(request, "student/requests.html", {
        "nav": "requests", "active": [r for r in reqs if r.status in ACTIVE],
        "archive": [r for r in reqs if r.status not in ACTIVE], "catalog": REQUEST_TYPES,
    }, db=db)


@router.get("/requests/new")
def request_new(request: Request, type: str = "study", s: AuthSession = Depends(require_student),
                db: Session = Depends(get_db)):
    t = TYPES.get(type)
    if t is None:
        raise HTTPException(status_code=404)
    return render(request, "student/request_new.html", {"nav": "requests", "t": t, "form": {}}, db=db)


def _next_number(db: Session) -> str:
    n = (db.scalar(select(func.max(StudentRequest.id))) or 0) + 470
    return f"СП-{today().year}-{n:04d}"


@router.post("/requests")
def request_create(request: Request, type_code: str = Form(...), purpose: str = Form(""), copies: int = Form(1),
                   fmt: str = Form("electronic"), comment: str = Form(""), csrf_token: str = Form(""),
                   s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    t = TYPES.get(type_code)
    if t is None:
        raise HTTPException(status_code=404)
    purpose, comment = purpose.strip()[:200], comment.strip()[:1000]
    errors = {}
    if t.needs_purpose and len(purpose) < 3:
        errors["purpose"] = "Укажите, для чего нужен документ" if t.kind == "certificate" else "Укажите причину"
    if fmt not in t.formats:
        errors["fmt"] = "Выберите формат из предложенных"
    if not 1 <= copies <= 5:
        errors["copies"] = "Можно заказать от 1 до 5 экземпляров"
    if errors:
        return render(request, "student/request_new.html", {
            "nav": "requests", "t": t, "errors": errors,
            "form": {"purpose": purpose, "copies": copies, "fmt": fmt, "comment": comment}}, status_code=400, db=db)
    st = _student(s)
    r = StudentRequest(number=_next_number(db), student_id=st.id, type_code=t.code, purpose=purpose or None,
                       copies=copies if fmt == "paper" else 1, fmt=fmt, comment=comment or None, status="submitted")
    db.add(r)
    db.flush()
    db.add(RequestEvent(request_id=r.id, status="submitted", actor="student", comment=comment or None))
    db.commit()
    return RedirectResponse(f"/requests/{r.id}?ok=created", status_code=303)


@router.get("/requests/{request_id}")
def request_detail(request: Request, request_id: int, s: AuthSession = Depends(require_student),
                   db: Session = Depends(get_db)):
    r = _own_request(db, s, request_id)
    return render(request, "student/request_detail.html", {"nav": "requests", "r": r, "t": TYPES[r.type_code]}, db=db)


@router.post("/requests/{request_id}/cancel")
def request_cancel(request: Request, request_id: int, csrf_token: str = Form(""),
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
def request_reply(request: Request, request_id: int, answer: str = Form(""), csrf_token: str = Form(""),
                  s: AuthSession = Depends(require_student), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    r = _own_request(db, s, request_id)
    answer = answer.strip()[:1000]
    if r.status != "need_info" or not answer:
        raise HTTPException(status_code=409)
    r.status = "in_progress"
    r.updated_at = utcnow()
    db.add(RequestEvent(request_id=r.id, status="in_progress", actor="student", comment=answer))
    db.commit()
    return RedirectResponse(f"/requests/{r.id}?ok=replied", status_code=303)


@router.get("/requests/{request_id}/document")
def request_document(request: Request, request_id: int, s: AuthSession = Depends(require_student),
                     db: Session = Depends(get_db)):
    r = _own_request(db, s, request_id)
    if r.fmt != "electronic" or r.status not in ("ready", "issued") or not r.verify_code or TYPES[r.type_code].kind != "certificate":
        raise HTTPException(status_code=404)
    from ..services.qr import qr_svg
    verify_url = str(request.base_url).rstrip("/") + f"/verify/{r.verify_code}"
    return render(request, "student/document.html", {"nav": "requests", "r": r, "t": TYPES[r.type_code],
                                                     "st": _student(s), "qr": qr_svg(verify_url),
                                                     "verify_url": verify_url}, db=db)


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
    st = _student(s)
    return render(request, "student/notifications.html", {"nav": "notifications", "items": items, "kind": kind,
                                                           "news": _announcements_for(db, st.group_code)}, db=db)


@router.post("/notifications/read-all")
def notifications_read_all(request: Request, csrf_token: str = Form(""), s: AuthSession = Depends(require_student),
                           db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    for n in db.scalars(select(Notification).where(Notification.user_id == s.user_id, Notification.read_at.is_(None))):
        n.read_at = utcnow()
    db.commit()
    return RedirectResponse("/notifications?ok=readall", status_code=303)


@router.post("/notifications/{notif_id}/open")
def notification_open(request: Request, notif_id: int, csrf_token: str = Form(""),
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
        return RedirectResponse("/messages", status_code=303)
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
    if not verify_password(s.user.password_hash, current):
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

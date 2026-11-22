"""Кабинет сотрудника деканата: заявки, вопросы студентов, объявления, изменения расписания."""
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import (Announcement, AuthSession, LessonChange, LessonTemplate, Message, RequestEvent, Student,
                      StudentRequest)
from ..security import check_csrf, require_staff
from ..services import schedule as S
from ..services.catalog import ACTIVE, DEKANAT_PLACE, STAFF_TRANSITIONS, STATUS, TYPES
from ..services.notify import notify, notify_group
from ..services.timeutil import fmt_date, from_day, on_day, today, utcnow
from ..web import render

router = APIRouter(prefix="/staff")


def _groups(db: Session) -> list[str]:
    return list(db.scalars(select(Student.group_code).distinct().order_by(Student.group_code)))


def _unanswered(db: Session) -> int:
    return db.scalar(select(func.count(Message.id)).where(Message.from_staff.is_(False), Message.read_by_staff.is_(False))) or 0


@router.get("")
def dashboard(request: Request, s: AuthSession = Depends(require_staff), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(StudentRequest.status, func.count()).group_by(StudentRequest.status)).all())
    fresh = db.scalars(select(StudentRequest).options(selectinload(StudentRequest.student).selectinload(Student.user))
                       .where(StudentRequest.status.in_(["submitted", "need_info", "in_progress"]))
                       .order_by(StudentRequest.created_at).limit(6)).all()
    return render(request, "staff/dashboard.html", {"nav": "dash", "counts": counts, "fresh": fresh,
                                                    "unanswered": _unanswered(db)}, db=db)


@router.get("/requests")
def requests_list(request: Request, status: str = "open", s: AuthSession = Depends(require_staff),
                  db: Session = Depends(get_db)):
    q = select(StudentRequest).options(selectinload(StudentRequest.student).selectinload(Student.user))
    if status == "open":
        q = q.where(StudentRequest.status.in_(["submitted", "in_progress", "need_info"]))
    elif status in STATUS:
        q = q.where(StudentRequest.status == status)
    else:
        status = "all"
    items = db.scalars(q.order_by(StudentRequest.created_at.desc())).all()
    return render(request, "staff/requests.html", {"nav": "requests", "items": items, "status": status,
                                                   "unanswered": _unanswered(db)}, db=db)


def _get_request(db: Session, rid: int) -> StudentRequest:
    r = db.scalar(select(StudentRequest).options(selectinload(StudentRequest.events),
                                                 selectinload(StudentRequest.student).selectinload(Student.user))
                  .where(StudentRequest.id == rid))
    if r is None:
        raise HTTPException(status_code=404)
    return r


@router.get("/requests/{rid}")
def request_detail(request: Request, rid: int, s: AuthSession = Depends(require_staff), db: Session = Depends(get_db)):
    r = _get_request(db, rid)
    return render(request, "staff/request_detail.html", {
        "nav": "requests", "r": r, "t": TYPES[r.type_code], "transitions": STAFF_TRANSITIONS.get(r.status, []),
        "unanswered": _unanswered(db)}, db=db)


@router.post("/requests/{rid}/status")
def request_status(request: Request, rid: int, new_status: str = Form(...), comment: str = Form(""),
                   csrf_token: str = Form(""), s: AuthSession = Depends(require_staff), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    r = _get_request(db, rid)
    comment = comment.strip()[:1000]
    if new_status not in STAFF_TRANSITIONS.get(r.status, []):
        raise HTTPException(status_code=409)
    if new_status in ("need_info", "rejected") and not comment:
        return render(request, "staff/request_detail.html", {
            "nav": "requests", "r": r, "t": TYPES[r.type_code], "transitions": STAFF_TRANSITIONS.get(r.status, []),
            "error": "Напишите студенту вопрос или причину отказа", "unanswered": _unanswered(db)},
            status_code=400, db=db)
    r.status = new_status
    r.updated_at = utcnow()
    if comment:
        r.staff_comment = comment
    if new_status == "ready":
        r.issued_at = utcnow()
        if r.fmt == "electronic":
            r.verify_code = r.verify_code or secrets.token_hex(8)
        else:
            r.pickup_place = DEKANAT_PLACE
    db.add(RequestEvent(request_id=r.id, status=new_status, actor="staff", comment=comment or None))
    t = TYPES[r.type_code]
    titles = {
        "in_progress": f"{t.title}: заявка в работе",
        "need_info": f"Нужно уточнение по заявке {r.number}",
        "ready": f"{t.title}: готово",
        "issued": f"{t.title}: выдано",
        "rejected": f"Заявка {r.number} отклонена",
    }
    body = comment or STATUS[new_status][1]
    if new_status == "ready" and r.fmt == "paper":
        body = f"Забрать: {DEKANAT_PLACE}"
    notify(db, r.student.user_id, "request", titles[new_status], body, f"/requests/{r.id}")
    db.commit()
    return RedirectResponse(f"/staff/requests/{r.id}?ok=status", status_code=303)


# ---------------------------- Вопросы студентов ----------------------------

@router.get("/messages")
def threads(request: Request, s: AuthSession = Depends(require_staff), db: Session = Depends(get_db)):
    students = db.scalars(select(Student).options(selectinload(Student.user))).all()
    rows = []
    for st in students:
        last = db.scalar(select(Message).where(Message.student_id == st.id).order_by(Message.created_at.desc()).limit(1))
        if last is None:
            continue
        unread = db.scalar(select(func.count(Message.id)).where(Message.student_id == st.id, Message.from_staff.is_(False),
                                                               Message.read_by_staff.is_(False))) or 0
        rows.append((st, last, unread))
    rows.sort(key=lambda x: (x[2] == 0, -x[1].created_at.timestamp()))
    return render(request, "staff/threads.html", {"nav": "messages", "rows": rows, "unanswered": _unanswered(db)}, db=db)


@router.get("/messages/{student_id}")
def thread(request: Request, student_id: int, s: AuthSession = Depends(require_staff), db: Session = Depends(get_db)):
    st = db.scalar(select(Student).options(selectinload(Student.user)).where(Student.id == student_id))
    if st is None:
        raise HTTPException(status_code=404)
    items = db.scalars(select(Message).options(selectinload(Message.author)).where(Message.student_id == st.id)
                       .order_by(Message.created_at)).all()
    for m in items:
        if not m.from_staff:
            m.read_by_staff = True
    db.commit()
    return render(request, "staff/thread.html", {"nav": "messages", "st": st, "items": items,
                                                 "unanswered": _unanswered(db)}, db=db)


@router.post("/messages/{student_id}")
def thread_reply(request: Request, student_id: int, body: str = Form(""), csrf_token: str = Form(""),
                 s: AuthSession = Depends(require_staff), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    st = db.get(Student, student_id)
    if st is None:
        raise HTTPException(status_code=404)
    body = body.strip()[:2000]
    if body:
        db.add(Message(student_id=st.id, author_id=s.user_id, from_staff=True, body=body, read_by_staff=True))
        notify(db, st.user_id, "message", "Деканат ответил на ваш вопрос", body[:140], "/messages")
        db.commit()
    return RedirectResponse(f"/staff/messages/{st.id}?ok=sent#last", status_code=303)


# ---------------------------- Объявления ----------------------------

@router.get("/announcements")
def announcements(request: Request, s: AuthSession = Depends(require_staff), db: Session = Depends(get_db)):
    items = db.scalars(select(Announcement).order_by(Announcement.created_at.desc())).all()
    return render(request, "staff/announcements.html", {"nav": "news", "items": items, "groups": _groups(db),
                                                        "form": {}, "unanswered": _unanswered(db)}, db=db)


@router.post("/announcements")
def announcement_create(request: Request, title: str = Form(""), body: str = Form(""), audience: str = Form("all"),
                        csrf_token: str = Form(""), s: AuthSession = Depends(require_staff), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    title, body = title.strip()[:200], body.strip()[:3000]
    groups = _groups(db)
    if audience != "all" and audience.removeprefix("group:") not in groups:
        audience = "all"
    if len(title) < 3 or len(body) < 3:
        items = db.scalars(select(Announcement).order_by(Announcement.created_at.desc())).all()
        return render(request, "staff/announcements.html", {
            "nav": "news", "items": items, "groups": groups, "error": "Заполните заголовок и текст объявления",
            "form": {"title": title, "body": body, "audience": audience}, "unanswered": _unanswered(db)},
            status_code=400, db=db)
    db.add(Announcement(title=title, body=body, audience=audience, author_id=s.user_id))
    group = audience.removeprefix("group:") if audience != "all" else None
    notify_group(db, group, "news", title, body[:200], "/notifications?kind=news")
    db.commit()
    return RedirectResponse("/staff/announcements?ok=announced", status_code=303)


# ---------------------------- Изменения расписания ----------------------------

@router.get("/schedule")
def schedule(request: Request, group: str | None = None, s: AuthSession = Depends(require_staff),
             db: Session = Depends(get_db)):
    groups = _groups(db)
    group = group if group in groups else groups[0]
    try:
        d = date.fromisoformat(request.query_params.get("date", ""))
    except ValueError:
        d = today()
    lessons = S.lessons_for_date(db, group, d)
    return render(request, "staff/schedule.html", {"nav": "schedule", "groups": groups, "group": group, "d": d,
                                                   "lessons": lessons, "PAIRS": S.PAIRS, "timedelta": timedelta,
                                                   "unanswered": _unanswered(db)}, db=db)


@router.post("/schedule")
def schedule_change(request: Request, template_id: int = Form(...), day: str = Form(...), change_type: str = Form(...),
                    new_room: str = Form(""), new_teacher: str = Form(""), new_date: str = Form(""),
                    new_pair_no: int = Form(0), note: str = Form(""), csrf_token: str = Form(""),
                    s: AuthSession = Depends(require_staff), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    t = db.get(LessonTemplate, template_id)
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400)
    if t is None or change_type not in ("cancelled", "room", "teacher", "online", "moved"):
        raise HTTPException(status_code=400)
    existing = db.scalar(select(LessonChange).where(LessonChange.template_id == t.id, LessonChange.date == d))
    if existing:
        db.delete(existing)
        db.flush()
    c = LessonChange(template_id=t.id, date=d, change_type=change_type, note=note.strip()[:255] or None)
    if change_type == "room":
        if not new_room.strip():
            raise HTTPException(status_code=400)
        c.new_room = new_room.strip()[:32]
    if change_type == "teacher":
        if not new_teacher.strip():
            raise HTTPException(status_code=400)
        c.new_teacher = new_teacher.strip()[:128]
    if change_type == "moved":
        try:
            c.new_date = date.fromisoformat(new_date)
        except ValueError:
            raise HTTPException(status_code=400)
        if new_pair_no not in S.PAIRS:
            raise HTTPException(status_code=400)
        c.new_pair_no = new_pair_no
    db.add(c)
    name = t.discipline.name
    when = f"{fmt_date(d, with_weekday=True)}, {t.pair_no} пара"
    if change_type == "cancelled":
        title, body = f"Отмена: {name}", f"Занятие {when} отменено."
    elif change_type == "room":
        title, body = f"Другая аудитория: {name}", f"{when}: ауд. {c.new_room}."
    elif change_type == "teacher":
        title, body = f"Замена преподавателя: {name}", f"{when}: ведет {c.new_teacher}."
    elif change_type == "online":
        title, body = f"Онлайн: {name}", f"{when}: занятие пройдет онлайн."
    else:
        title = f"Перенос: {name}"
        body = f"Занятие {from_day(d)}, {t.pair_no} пара, перенесено {on_day(c.new_date)}, {c.new_pair_no} пара."
    if c.note:
        body += f" {c.note}"
    notify_group(db, t.group_code, "schedule", title, body, f"/schedule?date={d.isoformat()}")
    db.commit()
    return RedirectResponse(f"/staff/schedule?group={t.group_code}&date={d.isoformat()}&ok=change", status_code=303)

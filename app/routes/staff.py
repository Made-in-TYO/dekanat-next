"""Кабинет сотрудника деканата: заявки, вопросы студентов, объявления, изменения расписания."""
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import config
from ..db import get_db
from ..models import (Announcement, AuthSession, LessonChange, LessonTemplate, Message, RequestEvent, Student,
                      StudentRequest)
from ..security import check_csrf, require_staff
from ..services import schedule as S
from ..services.catalog import DEKANAT_PLACE, TYPES, is_final_ready, staff_transitions, status_info
from ..services.notify import notify, notify_group
from ..services.timeutil import fmt_date, from_day, on_day, today, utcnow
from ..web import render

router = APIRouter(prefix="/staff")


def _groups(db: Session) -> list[str]:
    return list(db.scalars(select(Student.group_code).distinct().order_by(Student.group_code)))


def _unanswered(db: Session) -> int:
    """Сколько переписок ждут ответа: последнее сообщение в ветке от студента.
    Просто открыть переписку недостаточно, счетчик уменьшится только после ответа."""
    last = (select(Message.student_id, func.max(Message.id).label("mid")).group_by(Message.student_id).subquery())
    return db.scalar(select(func.count()).select_from(last).join(Message, Message.id == last.c.mid)
                     .where(Message.from_staff.is_(False))) or 0


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
        "nav": "requests", "r": r, "t": TYPES[r.type_code], "transitions": staff_transitions(r),
        "unanswered": _unanswered(db)}, db=db)


@router.post("/requests/{rid}/status")
def request_status(request: Request, rid: int, new_status: str = Form(...), comment: str = Form(""),
                   csrf_token: str = Form(""), s: AuthSession = Depends(require_staff), db: Session = Depends(get_db)):
    check_csrf(s, csrf_token)
    r = _get_request(db, rid)
    comment = comment.strip()[:1000]
    if new_status not in staff_transitions(r):
        raise HTTPException(status_code=409)
    if new_status in ("need_info", "rejected") and not comment:
        return render(request, "staff/request_detail.html", {
            "nav": "requests", "r": r, "t": TYPES[r.type_code], "transitions": staff_transitions(r),
            "error": "Напишите студенту вопрос или причину отказа", "unanswered": _unanswered(db)},
            status_code=400, db=db)
    r.status = new_status
    r.updated_at = utcnow()
    if comment:
        r.staff_comment = comment
    t = TYPES[r.type_code]
    if new_status == "ready":
        r.issued_at = utcnow()
        if r.fmt == "electronic" and t.kind == "certificate":
            # Код проверки по QR есть только у электронных справок
            r.verify_code = r.verify_code or secrets.token_hex(8)
        elif r.fmt == "paper":
            r.pickup_place = DEKANAT_PLACE
    db.add(RequestEvent(request_id=r.id, status=new_status, actor="staff", comment=comment or None))
    if t.kind == "application":
        titles = {
            "in_progress": f"{t.title}: заявление на рассмотрении",
            "need_info": f"Нужно уточнение по заявлению {r.number}",
            "ready": f"{t.title}: заявление одобрено",
            "rejected": f"{t.title}: в заявлении отказано",
        }
    else:
        titles = {
            "in_progress": f"{t.title}: заявка в работе",
            "need_info": f"Нужно уточнение по заявке {r.number}",
            "ready": f"{t.title}: {'можно забрать' if r.fmt == 'paper' else 'готово'}",
            "issued": f"{t.title}: выдано",
            "rejected": f"Заявка {r.number} отклонена",
        }
    body = comment or status_info(new_status, r.type_code, r.fmt)[1]
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

def _schedule_ctx(db: Session, group: str, d: date, **extra) -> dict:
    groups = _groups(db)
    ctx = {"nav": "schedule", "groups": groups, "group": group, "d": d, "lessons": S.lessons_for_date(db, group, d),
           "PAIRS": S.PAIRS, "timedelta": timedelta, "day_note": S.day_note(d), "unanswered": _unanswered(db),
           "errors": {}, "form": {}}
    ctx.update(extra)
    return ctx


@router.get("/schedule")
def schedule(request: Request, group: str | None = None, s: AuthSession = Depends(require_staff),
             db: Session = Depends(get_db)):
    groups = _groups(db)
    group = group if group in groups else groups[0]
    try:
        d = date.fromisoformat(request.query_params.get("date", ""))
    except ValueError:
        d = today()
    if abs((d - today()).days) > config.DATE_RANGE_DAYS:
        d = today()
    return render(request, "staff/schedule.html", _schedule_ctx(db, group, d), db=db)


def _when(d: date, pair_no: int) -> str:
    return f"{fmt_date(d, with_weekday=True)}, {S.pair_name(pair_no)}"


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
    if t is None or change_type not in ("cancelled", "room", "teacher", "online", "moved", "revert"):
        raise HTTPException(status_code=400)
    # Изменение возможно только для занятия, которое действительно стоит в этот день по сетке
    if t.id not in {x.id for x in S.templates_for(db, t.group_code, d)}:
        raise HTTPException(status_code=400)
    existing = db.scalar(select(LessonChange).where(LessonChange.template_id == t.id, LessonChange.date == d))
    name = t.discipline.name
    when = _when(d, t.pair_no)

    if change_type == "revert":
        # "Вернуть как было": удаляем изменение и сообщаем группе
        if existing is None:
            return RedirectResponse(f"/staff/schedule?group={t.group_code}&date={d.isoformat()}", status_code=303)
        db.delete(existing)
        notify_group(db, t.group_code, "schedule", f"Как было: {name}",
                     f"{when}: пара проходит по обычному расписанию, предыдущее изменение отменено.",
                     f"/schedule?date={d.isoformat()}")
        db.commit()
        return RedirectResponse(f"/staff/schedule?group={t.group_code}&date={d.isoformat()}&ok=reverted", status_code=303)

    errors: dict[str, str] = {}
    room, teacher = new_room.strip()[:32], new_teacher.strip()[:128]
    nd = None
    if change_type == "room" and not room:
        errors["new_room"] = "Укажите новую аудиторию"
    if change_type == "teacher" and not teacher:
        errors["new_teacher"] = "Укажите, кто ведет занятие"
    if change_type == "moved":
        try:
            nd = date.fromisoformat(new_date)
        except ValueError:
            nd = None
        if nd is None:
            errors["new_date"] = "Укажите новую дату"
        elif new_pair_no not in S.PAIRS:
            errors["new_date"] = "Выберите пару"
        elif nd < today():
            errors["new_date"] = "Нельзя перенести занятие в прошлое"
        elif not S.is_class_day(nd):
            errors["new_date"] = S.day_note(nd) or "В этот день занятий нет (воскресенье)"
        elif nd == d and new_pair_no == t.pair_no:
            errors["new_date"] = "Новая дата и пара совпадают с текущими"
        elif S.slot_taken(db, t.group_code, nd, new_pair_no):
            errors["new_date"] = f"У группы {t.group_code} в это время уже есть пара"
    if errors:
        ctx = _schedule_ctx(db, t.group_code, d, errors=errors, error_template=t.id,
                            form={"change_type": change_type, "new_room": room, "new_teacher": teacher,
                                  "new_date": new_date, "new_pair_no": new_pair_no, "note": note.strip()[:255]})
        return render(request, "staff/schedule.html", ctx, status_code=400, db=db)

    if existing:
        db.delete(existing)
        db.flush()
    c = LessonChange(template_id=t.id, date=d, change_type=change_type, note=note.strip()[:255] or None)
    if change_type == "room":
        c.new_room = room
    elif change_type == "teacher":
        c.new_teacher = teacher
    elif change_type == "moved":
        c.new_date, c.new_pair_no = nd, new_pair_no
    db.add(c)
    if change_type == "cancelled":
        title, body = f"Отмена: {name}", f"{when}: пара отменена."
    elif change_type == "room":
        title, body = f"Другая аудитория: {name}", f"{when}: пара в ауд. {c.new_room}."
    elif change_type == "teacher":
        title, body = f"Замена преподавателя: {name}", f"{when}: пару ведет {c.new_teacher}."
    elif change_type == "online":
        title, body = f"Онлайн: {name}", f"{when}: пара пройдет онлайн."
    else:
        title = f"Перенос: {name}"
        body = (f"Пара {from_day(d)}, {S.pair_name(t.pair_no)}, перенесена "
                f"{on_day(c.new_date)}, {S.pair_name(c.new_pair_no)}.")
    if c.note:
        body += f" {c.note}"
    notify_group(db, t.group_code, "schedule", title, body, f"/schedule?date={d.isoformat()}")
    db.commit()
    return RedirectResponse(f"/staff/schedule?group={t.group_code}&date={d.isoformat()}&ok=change", status_code=303)

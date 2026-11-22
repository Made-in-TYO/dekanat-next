"""Создание уведомлений."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from datetime import datetime, time, timedelta

from ..models import Deadline, Notification, Student, User
from .timeutil import fmt_dt, now_local, plural


def notify(db: Session, user_id: int, kind: str, title: str, body: str | None = None, link: str | None = None):
    db.add(Notification(user_id=user_id, kind=kind, title=title, body=body, link=link))


def notify_group(db: Session, group_code: str | None, kind: str, title: str, body: str | None = None,
                 link: str | None = None) -> int:
    """group_code=None: всем студентам."""
    q = select(User.id).join(Student, Student.user_id == User.id)
    if group_code:
        q = q.where(Student.group_code == group_code)
    ids = db.scalars(q).all()
    for uid in ids:
        notify(db, uid, kind, title, body, link)
    return len(ids)


def unread_count(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count(Notification.id)).where(Notification.user_id == user_id,
                                                               Notification.read_at.is_(None))) or 0


def remind_deadlines(db: Session, student: Student, days: int = 3) -> int:
    """Напоминание о дедлайнах, до которых осталось не больше трех календарных дней. Каждое приходит один раз."""
    now = now_local()
    due = db.scalars(select(Deadline).where(Deadline.student_id == student.id, Deadline.done.is_(False),
                                            Deadline.reminded.is_(False), Deadline.due_at >= now,
                                            Deadline.due_at < datetime.combine(now.date() + timedelta(days=days + 1), time.min))).all()
    for d in due:
        left = (d.due_at.date() - now.date()).days
        when = "Сегодня" if left <= 0 else ("Завтра" if left == 1 else f"Через {left} {plural(left, 'день', 'дня', 'дней')}")
        notify(db, student.user_id, "deadline", f"{when}: {d.title}", f"{d.source}, срок {fmt_dt(d.due_at)}", "/home")
        d.reminded = True
    if due:
        db.commit()
    return len(due)

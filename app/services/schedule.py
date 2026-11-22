"""Расписание: постоянная сетка группы + изменения на конкретные даты."""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..models import LessonChange, LessonTemplate
from .timeutil import fmt_date, from_day, now_local, on_day, utcnow, week_parity

# Время пар (вымышленная сетка в стиле петербургских вузов, первая пара в 8:20)
PAIRS = {
    1: (time(8, 20), time(9, 50)),
    2: (time(10, 0), time(11, 30)),
    3: (time(11, 40), time(13, 10)),
    4: (time(13, 30), time(15, 0)),
    5: (time(15, 20), time(16, 50)),
    6: (time(17, 0), time(18, 30)),
    7: (time(18, 40), time(20, 10)),
    8: (time(20, 20), time(21, 50)),
}

KIND_LABELS = {"lecture": "Лекция", "practice": "Практика", "lab": "Лабораторная"}


@dataclass
class LessonView:
    day: date
    pair_no: int
    discipline: str
    kind: str
    room: str
    building: str
    teacher: str
    online: bool
    status: str = "normal"  # normal | moved_away | moved_in | cancelled | room | teacher | online
    note: str | None = None
    old_room: str | None = None
    old_teacher: str | None = None
    moved_text: str | None = None
    template_id: int | None = None
    change_id: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def start(self) -> time:
        return PAIRS[self.pair_no][0]

    @property
    def end(self) -> time:
        return PAIRS[self.pair_no][1]

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.day, self.start)

    @property
    def end_dt(self) -> datetime:
        return datetime.combine(self.day, self.end)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def happens(self) -> bool:
        """Занятие состоится в этот день и в это время."""
        return self.status not in ("moved_away", "cancelled")

    @property
    def changed(self) -> bool:
        return self.status != "normal"


def _view(t: LessonTemplate, d: date) -> LessonView:
    return LessonView(day=d, pair_no=t.pair_no, discipline=t.discipline.name, kind=t.kind, room=t.room,
                      building=t.building, teacher=t.teacher, online=t.online, template_id=t.id)


def lessons_for_date(db: Session, group_code: str, d: date) -> list[LessonView]:
    parity = week_parity(d)
    templates = db.scalars(
        select(LessonTemplate).options(joinedload(LessonTemplate.discipline))
        .where(LessonTemplate.group_code == group_code, LessonTemplate.weekday == d.weekday(),
               LessonTemplate.parity.in_(["any", parity]))
    ).all()
    ids = [t.id for t in templates]
    changes = {}
    if ids:
        for c in db.scalars(select(LessonChange).where(LessonChange.template_id.in_(ids), LessonChange.date == d)):
            changes[c.template_id] = c

    result: list[LessonView] = []
    for t in templates:
        v = _view(t, d)
        c = changes.get(t.id)
        if c:
            v.change_id = c.id
            v.note = c.note
            if c.change_type == "moved" and c.new_date:
                v.status = "moved_away"
                v.moved_text = f"Перенесена {on_day(c.new_date)}, {c.new_pair_no} пара"
            elif c.change_type == "cancelled":
                v.status = "cancelled"
            elif c.change_type == "room" and c.new_room:
                v.status = "room"
                v.old_room = v.room
                v.room = c.new_room
                if c.new_building:
                    v.building = c.new_building
            elif c.change_type == "teacher" and c.new_teacher:
                v.status = "teacher"
                v.old_teacher = v.teacher
                v.teacher = c.new_teacher
            elif c.change_type == "online":
                v.status = "online"
                v.online = True
        result.append(v)

    # Занятия, перенесенные НА этот день с других дат
    moved_in = db.scalars(
        select(LessonChange).join(LessonTemplate).options(joinedload(LessonChange.template).joinedload(LessonTemplate.discipline))
        .where(LessonTemplate.group_code == group_code, LessonChange.change_type == "moved", LessonChange.new_date == d)
    ).all()
    for c in moved_in:
        v = _view(c.template, d)
        v.pair_no = c.new_pair_no or v.pair_no
        if c.new_room:
            v.room = c.new_room
        v.status = "moved_in"
        v.change_id = c.id
        v.note = c.note
        v.moved_text = f"Перенос {from_day(c.date)}, {c.template.pair_no} пара"
        result.append(v)

    result.sort(key=lambda v: (v.pair_no, 0 if v.happens else 1))
    return result


def week_lessons(db: Session, group_code: str, monday: date) -> list[tuple[date, list[LessonView]]]:
    return [(monday + timedelta(days=i), lessons_for_date(db, group_code, monday + timedelta(days=i))) for i in range(6)]


def current_and_next(db: Session, group_code: str, horizon_days: int = 14):
    """(текущее занятие или None, следующее занятие или None) относительно текущего времени вуза."""
    now = now_local()
    current = None
    for i in range(horizon_days):
        d = now.date() + timedelta(days=i)
        for v in lessons_for_date(db, group_code, d):
            if not v.happens:
                continue
            if v.start_dt <= now < v.end_dt:
                current = v
                continue
            if v.start_dt > now:
                return current, v
    return current, None


def upcoming_changes(db: Session, group_code: str, days: int = 7) -> list[LessonView]:
    start = now_local().date()
    out = []
    for i in range(days):
        for v in lessons_for_date(db, group_code, start + timedelta(days=i)):
            if v.changed:
                out.append(v)
    return out


def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def week_ics(db: Session, group_code: str, monday: date) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Dekanat Next concept//RU", "CALSCALE:GREGORIAN",
             "X-WR-TIMEZONE:Europe/Moscow"]
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    for d, lessons in week_lessons(db, group_code, monday):
        for v in lessons:
            if not v.happens:
                continue
            where = "Онлайн" if v.online else f"ауд. {v.room}, {v.building}"
            lines += [
                "BEGIN:VEVENT",
                f"UID:{d:%Y%m%d}-{v.pair_no}-{v.template_id}@dekanat-next",
                f"DTSTAMP:{stamp}",
                f"DTSTART;TZID=Europe/Moscow:{v.start_dt:%Y%m%dT%H%M%S}",
                f"DTEND;TZID=Europe/Moscow:{v.end_dt:%Y%m%dT%H%M%S}",
                f"SUMMARY:{_ics_escape(v.discipline + ' (' + v.kind_label.lower() + ')')}",
                f"LOCATION:{_ics_escape(where)}",
                f"DESCRIPTION:{_ics_escape('Преподаватель: ' + v.teacher)}",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

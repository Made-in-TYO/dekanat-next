"""Расписание: постоянная сетка группы + изменения на конкретные даты + сессия.

Учебный календарь концепта: занятия идут с начала семестра до SEMESTER_END, кроме праздников.
После окончания занятий в расписании и на табло показываются консультации и экзамены сессии.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .. import config
from ..models import ExamEvent, LessonChange, LessonTemplate
from .timeutil import from_day, now_local, on_day, utcnow, week_parity

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

KIND_LABELS = {"lecture": "Лекция", "practice": "Практика", "lab": "Лабораторная",
               "exam": "Экзамен", "consultation": "Консультация", "credit": "Зачет"}
EXAM_DURATION = {"exam": timedelta(hours=3), "consultation": timedelta(minutes=90), "credit": timedelta(hours=2)}

SEMESTER_START = date.fromisoformat(config.SEMESTER_START)
SEMESTER_END = date.fromisoformat(config.SEMESTER_END)
SESSION_END = date.fromisoformat(config.SESSION_END)
HOLIDAYS = {date.fromisoformat(k): v for k, v in config.HOLIDAYS.items()}


def pair_name(n: int) -> str:
    return f"{n}-я пара"


def short_name(fio: str) -> str:
    parts = fio.split()
    return parts[0] + " " + " ".join(p[0] + "." for p in parts[1:3]) if len(parts) > 1 else fio


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
    status: str = "normal"  # normal | moved_away | moved_in | cancelled | room | teacher | online | exam
    note: str | None = None
    old_room: str | None = None
    old_teacher: str | None = None
    moved_text: str | None = None
    template_id: int | None = None
    change_id: int | None = None
    custom_start: time | None = None   # для экзаменов: время не по сетке пар
    custom_end: time | None = None
    moved_start: datetime | None = None  # для перенесенной пары: когда она пройдет на новом месте
    extra: dict = field(default_factory=dict)

    @property
    def start(self) -> time:
        return self.custom_start or PAIRS[self.pair_no][0]

    @property
    def end(self) -> time:
        return self.custom_end or PAIRS[self.pair_no][1]

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.day, self.start)

    @property
    def end_dt(self) -> datetime:
        return datetime.combine(self.day, self.end)

    def locked(self, now: datetime) -> bool:
        """Менять поздно: пара уже началась в исходное время или на новом месте после переноса."""
        return self.start_dt <= now or (self.moved_start is not None and self.moved_start <= now)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def happens(self) -> bool:
        """Занятие состоится в этот день и в это время."""
        return self.status not in ("moved_away", "cancelled")

    @property
    def changed(self) -> bool:
        return self.status not in ("normal", "exam")

    @property
    def is_exam(self) -> bool:
        return self.status == "exam"

    @property
    def change_phrase(self) -> str:
        """Короткая фраза об изменении, согласованная со словом "пара"."""
        return {
            "cancelled": "пара отменена",
            "moved_away": (self.moved_text or "пара перенесена").lower(),
            "moved_in": (self.moved_text or "пара перенесена").lower(),
            "room": f"пара в ауд. {self.room} вместо {self.old_room}",
            "teacher": f"пару ведет {self.teacher}",
            "online": "пара пройдет онлайн",
        }.get(self.status, "")


def is_class_day(d: date) -> bool:
    """Идут ли в этот день обычные занятия по сетке."""
    return SEMESTER_START <= d <= SEMESTER_END and d not in HOLIDAYS and d.weekday() != 6


def day_note(d: date) -> str | None:
    """Пояснение для дня без занятий: праздник, сессия или каникулы."""
    if d in HOLIDAYS:
        return f"Праздничный день: {HOLIDAYS[d]}. Занятий нет"
    if d > SESSION_END:
        return "Сессия закончилась, занятий нет"
    if d > SEMESTER_END:
        return "Занятия семестра закончились, идет сессия"
    if d < SEMESTER_START:
        return "Семестр еще не начался"
    return None


def _view(t: LessonTemplate, d: date) -> LessonView:
    return LessonView(day=d, pair_no=t.pair_no, discipline=t.discipline.name, kind=t.kind, room=t.room,
                      building=t.building, teacher=t.teacher, online=t.online, template_id=t.id)


def templates_for(db: Session, group_code: str, d: date) -> list[LessonTemplate]:
    """Занятия сетки, которые стоят в этот день (с учетом дня недели и четности недели)."""
    if not is_class_day(d):
        return []
    return list(db.scalars(
        select(LessonTemplate).options(joinedload(LessonTemplate.discipline))
        .where(LessonTemplate.group_code == group_code, LessonTemplate.weekday == d.weekday(),
               LessonTemplate.parity.in_(["any", week_parity(d)]))
    ))


def _exams_for(db: Session, group_code: str, d: date) -> list[LessonView]:
    start = datetime.combine(d, time.min)
    events = db.scalars(select(ExamEvent).options(joinedload(ExamEvent.discipline))
                        .where(ExamEvent.group_code == group_code, ExamEvent.starts_at >= start,
                               ExamEvent.starts_at < start + timedelta(days=1))).all()
    out = []
    for e in events:
        end = (e.starts_at + EXAM_DURATION.get(e.kind, timedelta(hours=2))).time()
        out.append(LessonView(day=d, pair_no=0, discipline=e.discipline.name, kind=e.kind, room=e.room,
                              building=e.building, teacher=short_name(e.discipline.teacher), online=False,
                              status="exam", custom_start=e.starts_at.time(), custom_end=end))
    return out


def lessons_for_date(db: Session, group_code: str, d: date) -> list[LessonView]:
    templates = templates_for(db, group_code, d)
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
                v.moved_start = lesson_start(c.new_date, c.new_pair_no)
                v.moved_text = f"Пара перенесена {on_day(c.new_date)}, {pair_name(c.new_pair_no)}"
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
        select(LessonChange).join(LessonTemplate)
        .options(joinedload(LessonChange.template).joinedload(LessonTemplate.discipline))
        .where(LessonTemplate.group_code == group_code, LessonChange.change_type == "moved", LessonChange.new_date == d)
    ).all()
    for c in moved_in:
        if not is_class_day(d):
            continue  # на неучебный день перенос не показываем (праздник, сессия, воскресенье)
        v = _view(c.template, d)
        v.pair_no = c.new_pair_no or v.pair_no
        if c.new_room:
            v.room = c.new_room
            if c.new_room.lower() == "онлайн":
                v.online = True
        v.status = "moved_in"
        v.change_id = c.id
        v.note = c.note
        v.moved_text = f"Пара перенесена сюда {from_day(c.date)}, {pair_name(c.template.pair_no)}"
        result.append(v)

    result.extend(_exams_for(db, group_code, d))
    result.sort(key=lambda v: (v.start, 0 if v.happens else 1))
    return result


def week_lessons(db: Session, group_code: str, monday: date) -> list[tuple[date, list[LessonView]]]:
    return [(monday + timedelta(days=i), lessons_for_date(db, group_code, monday + timedelta(days=i))) for i in range(6)]


def current_and_next(db: Session, group_code: str, horizon_days: int = 21):
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


def slot_holder(db: Session, group_code: str, d: date, pair_no: int,
                ignore_change_id: int | None = None) -> LessonView | None:
    """Занятие, которое уже стоит у группы в этот день на эту пару и состоится, или None.
    ignore_change_id: перенос, который сейчас заменяется (повторный перенос на тот же слот не конфликт)."""
    for v in lessons_for_date(db, group_code, d):
        if (v.happens and not v.is_exam and v.pair_no == pair_no
                and not (ignore_change_id is not None and v.status == "moved_in" and v.change_id == ignore_change_id)):
            return v
    return None


def slot_taken(db: Session, group_code: str, d: date, pair_no: int, ignore_change_id: int | None = None) -> bool:
    return slot_holder(db, group_code, d, pair_no, ignore_change_id) is not None


def lesson_start(d: date, pair_no: int) -> datetime:
    return datetime.combine(d, PAIRS[pair_no][0])


def change_notice(discipline: str, pair_no: int, c: LessonChange) -> tuple[str, str]:
    """Заголовок и текст уведомления группе об изменении занятия (одинаковые в кабинете деканата и в демо-данных)."""
    from .timeutil import fmt_date
    when = f"{fmt_date(c.date, with_weekday=True)}, {pair_name(pair_no)}"
    if c.change_type == "cancelled":
        title, body = f"Отмена: {discipline}", f"{when}: пара отменена."
    elif c.change_type == "room":
        title, body = f"Другая аудитория: {discipline}", f"{when}: пара в ауд. {c.new_room}."
    elif c.change_type == "teacher":
        title, body = f"Замена преподавателя: {discipline}", f"{when}: пару ведет {c.new_teacher}."
    elif c.change_type == "online":
        title, body = f"Онлайн: {discipline}", f"{when}: пара пройдет онлайн."
    else:
        title = f"Перенос: {discipline}"
        body = (f"Пара перенесена {from_day(c.date)} ({pair_name(pair_no)}) "
                f"{on_day(c.new_date)} ({pair_name(c.new_pair_no)}).")
    if c.note:
        body += f" {c.note}"
    return title, body[:1].upper() + body[1:]


# ---------------------------- Экспорт в календарь (RFC 5545) ----------------------------

def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Строки длиннее 75 байт переносятся, продолжение начинается с пробела (RFC 5545, раздел 3.1)."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode()
        limit = 75 if not out else 74  # у строк-продолжений первый байт занимает пробел
        if len(cur) + len(b) > limit:
            out.append(cur.decode())
            cur = b""
        cur += b
    out.append(cur.decode())
    return "\r\n ".join(out)


VTIMEZONE = [
    "BEGIN:VTIMEZONE", "TZID:Europe/Moscow",
    "BEGIN:STANDARD", "DTSTART:19700101T000000", "TZOFFSETFROM:+0300", "TZOFFSETTO:+0300", "TZNAME:MSK",
    "END:STANDARD", "END:VTIMEZONE",
]


def week_ics(db: Session, group_code: str, monday: date) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Dekanat Next concept//RU", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-TIMEZONE:Europe/Moscow", *VTIMEZONE]
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    for d, lessons in week_lessons(db, group_code, monday):
        for v in lessons:
            if not v.happens:
                continue
            where = "Онлайн" if v.online else f"ауд. {v.room}, {v.building}"
            uid = f"{d:%Y%m%d}-{v.start:%H%M}-{v.template_id or v.kind}@dekanat-next"
            lines += [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{stamp}",
                f"DTSTART;TZID=Europe/Moscow:{v.start_dt:%Y%m%dT%H%M%S}",
                f"DTEND;TZID=Europe/Moscow:{v.end_dt:%Y%m%dT%H%M%S}",
                f"SUMMARY:{_ics_escape(v.discipline + ' (' + v.kind_label.lower() + ')')}",
                f"LOCATION:{_ics_escape(where)}",
                f"DESCRIPTION:{_ics_escape('Преподаватель: ' + v.teacher)}",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


# ---------------------------- Подсказки табло ----------------------------

def _dur(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} ч {m} мин"
    return f"{h} ч" if h else f"{m} мин"


def board_hints(db: Session, group_code: str, current: "LessonView | None", nxt: "LessonView | None") -> list[str]:
    """Человеческие подсказки к табло: когда выходить из дома, где окно, куда идти дальше."""
    hints: list[str] = []
    focus = current or nxt
    if focus is None or focus.is_exam:
        return hints
    day = lessons_for_date(db, group_code, focus.day)
    regular = [v for v in day if not v.is_exam]
    happening = [v for v in regular if v.happens]
    # Первая пара дня отменена или перенесена: можно прийти позже
    if not current and happening and focus is not None and happening[0].start == focus.start:
        dropped = [v for v in regular if not v.happens and v.start < focus.start]
        if dropped:
            hints.append(f"Первая пара отменена или перенесена: можно прийти к {focus.start:%H:%M}")
    # Все пары дня онлайн
    if happening and all(v.online for v in happening):
        hints.append("В этот день все пары онлайн, ехать в университет не нужно")
    now = now_local()
    # Пара, с которой студент идет: та, что идет сейчас, или (на перемене) та, что уже закончилась сегодня
    prev = current
    if prev is None and nxt is not None and nxt.day == now.date():
        ended = [v for v in happening if v.end_dt <= now]
        prev = ended[-1] if ended else None
    if focus.day == now.date():
        later = [v for v in happening if v.start >= focus.end]
        if not later:
            hints.append("Это последняя пара сегодня" if len(happening) > 1 else "Сегодня это единственная пара")
        elif current and nxt is not None and nxt.day == current.day:
            gap = int((nxt.start_dt - current.end_dt).total_seconds() // 60)
            if gap >= 60:
                hints.append(f"После этой пары окно {_dur(gap)}")
    if prev is not None and nxt is not None and nxt.day == prev.day and nxt is not prev:
        if not nxt.online and not prev.online and nxt.building != prev.building:
            hints.append(f"Следующая пара в другом корпусе: {nxt.building}")
    return hints

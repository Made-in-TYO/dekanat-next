"""Время и даты по-русски. "Сейчас" всегда считается в часовом поясе вуза."""
from datetime import date, datetime, timedelta

from ..config import SEMESTER_START, TZ

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAYS_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]  # на ...
WEEKDAYS_GEN = ["понедельника", "вторника", "среды", "четверга", "пятницы", "субботы", "воскресенья"]  # с ...


def utcnow() -> datetime:
    """Текущее время UTC без tzinfo: так служебные отметки хранятся в базе."""
    from datetime import timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_local() -> datetime:
    """Текущее время вуза без tzinfo (наивное), чтобы сравнивать с датами из базы."""
    return datetime.now(TZ).replace(tzinfo=None)


def today() -> date:
    return now_local().date()


def utc_to_local(dt: datetime | None) -> datetime | None:
    """В базе служебные отметки времени хранятся в UTC; показываем по Москве."""
    if dt is None:
        return None
    from datetime import timezone
    return dt.replace(tzinfo=timezone.utc).astimezone(TZ).replace(tzinfo=None)


def fmt_date(d: date, with_weekday: bool = False, with_year: bool = False) -> str:
    s = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    if with_year:
        s += f" {d.year}"
    if with_weekday:
        s = f"{WEEKDAYS[d.weekday()].lower()}, {s}"
    return s


def on_day(d: date) -> str:
    """'на пятницу, 27 ноября'"""
    return f"на {WEEKDAYS_ACC[d.weekday()]}, {fmt_date(d)}"


def from_day(d: date) -> str:
    """'с вторника, 24 ноября'"""
    prep = "со" if d.weekday() == 1 else "с"
    return f"{prep} {WEEKDAYS_GEN[d.weekday()]}, {fmt_date(d)}"


def human_day(d: date) -> str:
    t = today()
    if d == t:
        return "сегодня"
    if d == t + timedelta(days=1):
        return "завтра"
    if d == t - timedelta(days=1):
        return "вчера"
    return fmt_date(d, with_year=d.year != t.year)


def fmt_dt(dt: datetime) -> str:
    return f"{human_day(dt.date())}, {dt:%H:%M}"


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def semester_week(d: date) -> int:
    start = datetime.strptime(SEMESTER_START, "%Y-%m-%d").date()
    return (week_monday(d) - week_monday(start)).days // 7 + 1


def week_parity(d: date) -> str:
    """odd / even: четность учебной недели, от которой зависит сетка расписания."""
    return "odd" if semester_week(d) % 2 == 1 else "even"


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def until_text(target: datetime) -> str:
    """'через 25 мин', 'через 2 ч 10 мин', 'через 3 дня'."""
    delta = target - now_local()
    mins = int(delta.total_seconds() // 60)
    if mins < 0:
        return "уже идет"
    if mins < 60:
        return f"через {mins} мин"
    if mins < 24 * 60:
        h, m = divmod(mins, 60)
        return f"через {h} ч {m} мин" if m else f"через {h} ч"
    days = (target.date() - today()).days
    if days == 1:
        return "завтра"
    if days == 2:
        return "послезавтра"
    return f"через {days} {plural(days, 'день', 'дня', 'дней')}"

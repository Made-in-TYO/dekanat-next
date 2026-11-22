"""Каталог справок и заявлений, статусы заявок.

Три вида заявок:
- certificate: справка (электронная с QR-кодом или бумажная);
- document: документ, который забирают в деканате (дубликат студенческого билета);
- application: заявление, по которому деканат принимает решение (одобрено или отказано).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestType:
    code: str
    title: str
    kind: str          # certificate | document | application
    description: str
    days: int          # обычный срок подготовки, рабочих дней
    formats: tuple     # electronic, paper
    needs_purpose: bool
    purpose_hint: str = ""
    budget_only: bool = False


REQUEST_TYPES = [
    RequestType("study", "Справка об обучении", "certificate",
                "Подтверждает, что вы учитесь в вузе. Нужна для работы, банка, визы, пенсионного фонда.",
                1, ("electronic", "paper"), True, "Например: для банка"),
    RequestType("stipend", "Справка о получении стипендии", "certificate",
                "Подтверждает, что вам начисляется государственная академическая стипендия, с суммами за последние 6 месяцев.",
                2, ("electronic", "paper"), True, "Например: для оформления субсидии", budget_only=True),
    RequestType("transcript", "Выписка оценок", "certificate",
                "Все итоговые оценки за пройденные семестры. Выдается на бланке с печатью.",
                5, ("paper",), True, "Например: для стажировки в компании"),
    RequestType("card", "Дубликат студенческого билета", "document",
                "Если билет потерян или испорчен. Готовый дубликат забирают лично в деканате.",
                3, ("paper",), False),
    RequestType("schedule", "Индивидуальный график сессии", "application",
                "Заявление на сдачу сессии в другие даты по уважительной причине. Решение придет в уведомления.",
                5, ("electronic",), True, "Причина: например, участие в олимпиаде или конференции"),
    RequestType("aid", "Материальная помощь", "application",
                "Заявление на единовременную выплату. Рассматривается стипендиальной комиссией, решение придет в уведомления.",
                10, ("electronic",), True, "Кратко опишите ситуацию"),
]
TYPES = {t.code: t for t in REQUEST_TYPES}

FORMAT_LABELS = {"electronic": "Электронная, с QR-кодом", "paper": "Бумажная, забрать в деканате"}

# Подписи статусов. Для заявлений решение называется иначе, чем для справок.
STATUS = {
    "submitted": ("Отправлена", "Ждет, когда деканат возьмет в работу"),
    "in_progress": ("В работе", "Сотрудник деканата готовит документ"),
    "need_info": ("Нужно уточнение", "Деканат задал вопрос, ответьте в заявке"),
    "ready": ("Готова", "Документ готов"),
    "issued": ("Выдана", "Документ получен"),
    "rejected": ("Отклонена", "Причина указана в заявке"),
    "cancelled": ("Отменена", "Вы отменили заявку"),
    "reply": ("Ответ студента", "Студент ответил на вопрос деканата"),
}
STATUS_APPLICATION = {
    "in_progress": ("На рассмотрении", "Деканат рассматривает заявление"),
    "ready": ("Одобрено", "Заявление одобрено"),
    "rejected": ("Отказано", "Причина указана в заявке"),
}
STATUS_PAPER_READY = ("Готова", "Можно забрать в деканате")

DEKANAT_PLACE = "Деканат, Кронверкский пр., 49, ауд. 1105 (пн-пт, 10:00-17:00)"


def kind_of(type_code: str) -> str:
    t = TYPES.get(type_code)
    return t.kind if t else "certificate"


def status_info(code: str, type_code: str | None = None, fmt: str | None = None) -> tuple[str, str]:
    kind = kind_of(type_code) if type_code else "certificate"
    if kind == "application" and code in STATUS_APPLICATION:
        return STATUS_APPLICATION[code]
    if code == "ready" and fmt == "paper":
        return STATUS_PAPER_READY
    if code == "in_progress" and kind == "document":
        return ("В работе", "Деканат готовит дубликат")
    return STATUS.get(code, (code, ""))


def status_label(code: str, type_code: str | None = None, fmt: str | None = None) -> str:
    return status_info(code, type_code, fmt)[0]


def is_final_ready(type_code: str, fmt: str) -> bool:
    """Электронная справка и решение по заявлению завершают заявку сразу: выдавать нечего."""
    return kind_of(type_code) == "application" or fmt == "electronic"


def is_active(r) -> bool:
    """Заявка еще требует внимания студента или деканата."""
    if r.status in ("submitted", "in_progress", "need_info"):
        return True
    return r.status == "ready" and not is_final_ready(r.type_code, r.fmt)


def staff_transitions(r) -> list[str]:
    """Какие статусы сотрудник может поставить из текущего."""
    if r.status == "submitted":
        return ["in_progress", "need_info", "rejected"]
    if r.status == "in_progress":
        return ["ready", "need_info", "rejected"]
    if r.status == "need_info":
        return ["in_progress", "rejected"]
    if r.status == "ready" and not is_final_ready(r.type_code, r.fmt):
        return ["issued"]
    return []


def track_labels(type_code: str) -> list[str]:
    if kind_of(type_code) == "application":
        return ["Отправлена", "На рассмотрении", "Решение"]
    return ["Отправлена", "В работе", "Готова"]


# Справка об обучении в электронном виде выдается сразу: все данные для нее уже есть в учетной системе
INSTANT_TYPES = {"study"}


def is_instant(type_code: str, fmt: str) -> bool:
    return type_code in INSTANT_TYPES and fmt == "electronic"


def ready_eta(created_local, days: int):
    """Примерная дата готовности: через N рабочих дней (без выходных и праздников из учебного календаря)."""
    from datetime import date, timedelta

    from .. import config
    holidays = {date.fromisoformat(k) for k in config.HOLIDAYS}
    d = created_local.date() if hasattr(created_local, "date") else created_local
    left = days
    while left > 0:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in holidays:
            left -= 1
    return d

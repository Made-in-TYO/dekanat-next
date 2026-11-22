"""Каталог справок и заявлений, статусы заявок."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestType:
    code: str
    title: str
    kind: str          # certificate | application
    description: str
    days: int          # обычный срок подготовки, рабочих дней
    formats: tuple     # electronic, paper
    needs_purpose: bool
    purpose_hint: str = ""


REQUEST_TYPES = [
    RequestType("study", "Справка об обучении", "certificate",
                "Подтверждает, что вы учитесь в вузе. Нужна для работы, банка, визы, пенсионного фонда.",
                1, ("electronic", "paper"), True, "Например: по месту работы родителей"),
    RequestType("stipend", "Справка о размере стипендии", "certificate",
                "Сумма начисленной стипендии за выбранный период.",
                2, ("electronic", "paper"), True, "Например: для оформления субсидии"),
    RequestType("transcript", "Выписка оценок", "certificate",
                "Все итоговые оценки за пройденные семестры. Выдается на бланке с печатью.",
                5, ("paper",), True, "Например: для стажировки в компании"),
    RequestType("schedule", "Индивидуальный график сессии", "application",
                "Заявление на сдачу сессии в другие даты по уважительной причине.",
                5, ("electronic",), True, "Причина: например, участие в олимпиаде или конференции"),
    RequestType("aid", "Материальная помощь", "application",
                "Заявление на единовременную выплату. Рассматривается стипендиальной комиссией.",
                10, ("electronic",), True, "Кратко опишите ситуацию"),
    RequestType("card", "Дубликат студенческого билета", "application",
                "Если билет потерян или испорчен. Забирать лично в деканате.",
                3, ("paper",), False),
]
TYPES = {t.code: t for t in REQUEST_TYPES}

FORMAT_LABELS = {"electronic": "Электронная, с QR-кодом", "paper": "Бумажная, забрать в деканате"}

STATUS = {
    "submitted": ("Отправлена", "Ждет, когда деканат возьмет в работу"),
    "in_progress": ("В работе", "Сотрудник деканата готовит документ"),
    "need_info": ("Нужно уточнение", "Деканат задал вопрос, ответьте в заявке"),
    "ready": ("Готова", "Документ готов"),
    "issued": ("Выдана", "Документ получен"),
    "rejected": ("Отклонена", "Причина указана в заявке"),
    "cancelled": ("Отменена", "Вы отменили заявку"),
}
# Шаги трекера, как у доставки
TRACK = ["submitted", "in_progress", "ready"]
ACTIVE = ("submitted", "in_progress", "need_info", "ready")
FINAL = ("issued", "rejected", "cancelled")

# Какие переходы может сделать сотрудник из каждого статуса
STAFF_TRANSITIONS = {
    "submitted": ["in_progress", "need_info", "rejected"],
    "in_progress": ["ready", "need_info", "rejected"],
    "need_info": ["in_progress", "rejected"],
    "ready": ["issued"],
    "issued": [],
    "rejected": [],
    "cancelled": [],
}

DEKANAT_PLACE = "Деканат, Кронверкский пр., 49, ауд. 1105 (пн-пт, 10:00-17:00)"


def status_label(code: str) -> str:
    return STATUS.get(code, (code, ""))[0]

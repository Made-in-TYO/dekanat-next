"""Балльно-рейтинговая система.

Шкала перевода баллов в оценку взята из открытого документа ИТМО (edu.itmo.ru/files/23):
минимум для аттестации 60 баллов, максимум 103 (100 основных + 3 дополнительных);
60-67 E "удовлетворительно", 67-74 D "удовлетворительно", 74-83 C "хорошо",
83-90 B "хорошо", 90-103 A "отлично" (левая граница не включается, кроме 60).
"""
from dataclasses import dataclass

CONTROL_LABELS = {"exam": "Экзамен", "credit": "Зачет", "diff_credit": "Дифзачет"}
PASS = 60
EXCELLENT = 91  # "более 90"
MAX_POINTS = 103


def letter(points: float) -> str:
    if points < 60:
        return "FX"
    if points <= 67:
        return "E"
    if points <= 74:
        return "D"
    if points <= 83:
        return "C"
    if points <= 90:
        return "B"
    return "A"


def grade_label(points: float, control: str) -> str:
    if control == "credit":
        return "зачтено" if points >= PASS else "не зачтено"
    lt = letter(points)
    return {"A": "отлично", "B": "хорошо", "C": "хорошо", "D": "удовлетворительно",
            "E": "удовлетворительно", "FX": "неудовлетворительно"}[lt]


def five_scale(points: float) -> int:
    lt = letter(points)
    return {"A": 5, "B": 4, "C": 4, "D": 3, "E": 3, "FX": 2}[lt]


@dataclass
class DisciplineProgress:
    enrollment_id: int
    name: str
    control: str
    credits: int
    teacher: str
    points: float          # уже набрано
    assessed_max: float    # максимум по уже оцененным элементам
    remaining_max: float   # еще можно набрать
    items: list
    final_points: float | None

    @property
    def control_label(self) -> str:
        return CONTROL_LABELS.get(self.control, self.control)

    @property
    def possible_max(self) -> float:
        return min(MAX_POINTS, self.points + self.remaining_max)

    @property
    def to_pass(self) -> float:
        return max(0.0, PASS - self.points)

    @property
    def to_excellent(self) -> float:
        return max(0.0, EXCELLENT - self.points)

    @property
    def pace(self) -> float:
        return self.points / self.assessed_max if self.assessed_max else 1.0

    @property
    def state(self) -> str:
        """ok | watch | risk | fail | done"""
        if self.final_points is not None:
            return "done"
        if self.possible_max < PASS:
            return "fail"
        if self.assessed_max >= 10 and self.pace < 0.6:
            return "risk"
        if self.assessed_max >= 10 and self.pace < 0.75:
            return "watch"
        return "ok"

    @property
    def state_text(self) -> str:
        return {
            "done": "Итог выставлен",
            "fail": "Минимум 60 баллов уже не набрать, нужна пересдача",
            "risk": "Есть риск не набрать минимум",
            "watch": "Стоит подтянуть",
            "ok": "Идет хорошо",
        }[self.state]

    @property
    def forecast_points(self) -> float:
        """Если дальше работать в том же темпе."""
        return round(min(MAX_POINTS, self.points + self.remaining_max * self.pace))

    @property
    def bar_points(self) -> int:
        return int(min(100, self.points))


def build_progress(enrollment) -> DisciplineProgress:
    pts = sum(i.points for i in enrollment.items if i.points is not None)
    assessed = sum(i.max_points for i in enrollment.items if i.points is not None)
    remaining = sum(i.max_points for i in enrollment.items if i.points is None)
    d = enrollment.discipline
    return DisciplineProgress(
        enrollment_id=enrollment.id, name=d.name, control=d.control, credits=d.credits, teacher=d.teacher,
        points=pts, assessed_max=assessed, remaining_max=remaining, items=list(enrollment.items),
        final_points=enrollment.final_points,
    )


def average_five(past_results) -> float | None:
    graded = [five_scale(r.points) for r in past_results if r.control in ("exam", "diff_credit")]
    if not graded:
        return None
    return round(sum(graded) / len(graded), 2)

"""Модели данных. Все персональные данные в демо-базе вымышлены."""
from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .services.timeutil import utcnow


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))  # argon2id, пароль в открытом виде не хранится
    role: Mapped[str] = mapped_column(String(16))  # student | staff
    full_name: Mapped[str] = mapped_column(String(128))
    position: Mapped[str | None] = mapped_column(String(128), nullable=True)  # для сотрудников
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)  # общий тестовый аккаунт для жюри
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    student: Mapped["Student | None"] = relationship(back_populates="user", uselist=False)

    @property
    def first_name(self) -> str:
        parts = self.full_name.split()
        return parts[1] if len(parts) > 1 else self.full_name

    @property
    def initials(self) -> str:
        parts = self.full_name.split()
        return "".join(p[0] for p in parts[:2]).upper()


class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    faculty: Mapped[str] = mapped_column(String(160))
    faculty_short: Mapped[str] = mapped_column(String(16))
    level: Mapped[str] = mapped_column(String(32))  # Бакалавриат / Магистратура
    program_code: Mapped[str] = mapped_column(String(16))
    program_name: Mapped[str] = mapped_column(String(160))
    profile_name: Mapped[str] = mapped_column(String(160))
    course: Mapped[int] = mapped_column(Integer)
    group_code: Mapped[str] = mapped_column(String(16), index=True)
    study_form: Mapped[str] = mapped_column(String(32))
    funding: Mapped[str] = mapped_column(String(32))
    record_book: Mapped[str] = mapped_column(String(32))  # номер зачетки (вымышленный)
    admission_year: Mapped[int] = mapped_column(Integer)
    curator: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(128))

    user: Mapped[User] = relationship(back_populates="student")


class Discipline(Base):
    __tablename__ = "disciplines"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    control: Mapped[str] = mapped_column(String(16))  # exam | credit | diff_credit
    credits: Mapped[int] = mapped_column(Integer)  # зачетные единицы
    teacher: Mapped[str] = mapped_column(String(128))
    semester: Mapped[str] = mapped_column(String(32))  # "Осень 2026"


class LessonTemplate(Base):
    """Постоянная сетка расписания группы: день недели, номер пары, четность недели."""
    __tablename__ = "lesson_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_code: Mapped[str] = mapped_column(String(16), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = понедельник
    pair_no: Mapped[int] = mapped_column(Integer)
    parity: Mapped[str] = mapped_column(String(8), default="any")  # any | odd | even
    discipline_id: Mapped[int] = mapped_column(ForeignKey("disciplines.id"))
    kind: Mapped[str] = mapped_column(String(16))  # lecture | practice | lab
    room: Mapped[str] = mapped_column(String(32))
    building: Mapped[str] = mapped_column(String(128))
    teacher: Mapped[str] = mapped_column(String(128))
    online: Mapped[bool] = mapped_column(Boolean, default=False)

    discipline: Mapped[Discipline] = relationship()


class LessonChange(Base):
    """Изменение конкретного занятия в конкретный день: перенос, отмена, замена аудитории или преподавателя."""
    __tablename__ = "lesson_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("lesson_templates.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date, index=True)
    change_type: Mapped[str] = mapped_column(String(16))  # moved | cancelled | room | teacher | online
    new_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    new_pair_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_room: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_building: Mapped[str | None] = mapped_column(String(128), nullable=True)
    new_teacher: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    template: Mapped[LessonTemplate] = relationship()


class Enrollment(Base):
    """Дисциплина в учебном плане студента и текущие баллы по ней."""
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("student_id", "discipline_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    discipline_id: Mapped[int] = mapped_column(ForeignKey("disciplines.id"))
    final_points: Mapped[float | None] = mapped_column(Float, nullable=True)  # итог, если дисциплина закрыта

    discipline: Mapped[Discipline] = relationship()
    items: Mapped[list["ScoreItem"]] = relationship(order_by="ScoreItem.position", cascade="all, delete-orphan")


class ScoreItem(Base):
    """Элемент балльно-рейтинговой системы: контрольная, лабораторная, экзамен и т.д."""
    __tablename__ = "score_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(160))
    max_points: Mapped[float] = mapped_column(Float)
    points: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = еще не оценено
    due: Mapped[date | None] = mapped_column(Date, nullable=True)


class PastResult(Base):
    """Итоги прошлых семестров (для общей картины и среднего балла)."""
    __tablename__ = "past_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    semester: Mapped[str] = mapped_column(String(32))
    discipline: Mapped[str] = mapped_column(String(200))
    control: Mapped[str] = mapped_column(String(16))
    points: Mapped[float] = mapped_column(Float)


class ExamEvent(Base):
    """Консультации и экзамены сессии для группы."""
    __tablename__ = "exam_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_code: Mapped[str] = mapped_column(String(16), index=True)
    discipline_id: Mapped[int] = mapped_column(ForeignKey("disciplines.id"))
    kind: Mapped[str] = mapped_column(String(16))  # consultation | exam | credit
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    room: Mapped[str] = mapped_column(String(32))
    building: Mapped[str] = mapped_column(String(128))

    discipline: Mapped[Discipline] = relationship()


class Deadline(Base):
    __tablename__ = "deadlines"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    due_at: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(64))  # название дисциплины или "Деканат"
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)  # напоминание уже отправлено


class StudentRequest(Base):
    """Заявка на справку или заявление."""
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    type_code: Mapped[str] = mapped_column(String(32))
    purpose: Mapped[str | None] = mapped_column(String(200), nullable=True)
    copies: Mapped[int] = mapped_column(Integer, default=1)
    fmt: Mapped[str] = mapped_column(String(16))  # electronic | paper
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="submitted")
    staff_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    pickup_place: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verify_code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    student: Mapped[Student] = relationship()
    events: Mapped[list["RequestEvent"]] = relationship(order_by="RequestEvent.created_at", cascade="all, delete-orphan")


class RequestEvent(Base):
    __tablename__ = "request_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(16))  # student | staff
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # schedule | request | deadline | news | message
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Announcement(Base):
    __tablename__ = "announcements"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(32))  # all | group:<код>
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Message(Base):
    """Переписка студента с деканатом: одна ветка на студента."""
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    from_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    read_by_student: Mapped[bool] = mapped_column(Boolean, default=False)
    read_by_staff: Mapped[bool] = mapped_column(Boolean, default=False)

    author: Mapped[User] = relationship()


class AuthSession(Base):
    """Серверная сессия. В cookie лежит случайный токен, в базе только его хеш."""
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped[User] = relationship()


class Meta(Base):
    """Служебные значения: например, дата, когда были созданы демо-данные."""
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))

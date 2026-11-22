"""Демо-данные. Все люди, оценки, номера и заявки вымышлены.

Даты считаются от дня запуска, чтобы в расписании и дедлайнах всегда было что показать.
Запуск вручную: python -m app.seed --reset
"""
import secrets
import sys
import threading
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .models import (Announcement, Deadline, Discipline, Enrollment, ExamEvent, LessonChange, LessonTemplate,
                     Message, Meta, Notification, PastResult, RequestEvent, ScoreItem, Student, StudentRequest, User)
from .security import hash_password
from .services.catalog import DEKANAT_PLACE
from .services.timeutil import now_local, week_monday

# Тестовые аккаунты для жюри (пароли хранятся в базе только как хеш argon2id)
DEMO_ACCOUNTS = [
    ("kira", "Kira-2026", "Студентка магистратуры"),
    ("timur", "Timur-2026", "Студент бакалавриата"),
    ("dekanat", "Dekanat-2026", "Сотрудник деканата"),
]

KRONV = "Кронверкский пр., 49"
LOMO = "ул. Ломоносова, 9"
BIRZH = "Биржевая линия, 14"


def _dt(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m))


def _to_utc(local_dt: datetime) -> datetime:
    """Служебные отметки времени храним в UTC (Москва = UTC+3)."""
    return local_dt - timedelta(hours=3)


def seed(db) -> None:
    now = now_local()
    today = now.date()
    base = today if today.weekday() < 5 else today + timedelta(days=7 - today.weekday())
    mon = week_monday(base)

    # ---------------- Пользователи ----------------
    staff = User(login="dekanat", password_hash=hash_password("Dekanat-2026"), role="staff", is_demo=True,
                 full_name="Мельникова Ольга Сергеевна", position="Специалист по учебной работе")
    u_kira = User(login="kira", password_hash=hash_password("Kira-2026"), role="student", is_demo=True,
                  full_name="Воронцова Кира Андреевна")
    u_timur = User(login="timur", password_hash=hash_password("Timur-2026"), role="student", is_demo=True,
                   full_name="Галиев Тимур Ринатович")
    db.add_all([staff, u_kira, u_timur])
    db.flush()

    kira = Student(user_id=u_kira.id, faculty="Факультет технологического менеджмента и инноваций",
                   faculty_short="ФТМИ", level="Магистратура", program_code="27.04.05", program_name="Инноватика",
                   profile_name="Управление высокотехнологичным бизнесом", course=2, group_code="U4227",
                   study_form="Очная", funding="Бюджет", record_book="250417", admission_year=2025,
                   curator="Соболев Игорь Павлович", email="kira.vorontsova@student.example")
    timur = Student(user_id=u_timur.id, faculty="Факультет программной инженерии и компьютерной техники",
                    faculty_short="ПИиКТ", level="Бакалавриат", program_code="09.03.04",
                    program_name="Программная инженерия", profile_name="Системное и прикладное программное обеспечение",
                    course=2, group_code="P3214", study_form="Очная", funding="Контракт", record_book="251093",
                    admission_year=2025, curator="Гусев Артем Валерьевич", email="timur.galiev@student.example")
    db.add_all([kira, timur])
    db.flush()

    # ---------------- Дисциплины ----------------
    sem = "Осень 2026"

    def disc(name, control, credits, teacher):
        d = Discipline(name=name, control=control, credits=credits, teacher=teacher, semester=sem)
        db.add(d)
        return d

    k_strat = disc("Стратегическое обновление и управление изменениями", "exam", 4, "Ершова Наталья Андреевна")
    k_bp = disc("Бизнес-планирование (на английском языке)", "diff_credit", 3, "Литвинов Марк Олегович")
    k_tt = disc("Трансфер технологий", "credit", 3, "Карпова Елена Викторовна")
    k_nis = disc("Научно-исследовательский семинар", "credit", 2, "Соболев Игорь Павлович")
    k_uip = disc("Управление инновационными проектами", "exam", 4, "Белова Анна Сергеевна")
    k_eng = disc("Английский язык в профессиональной деятельности", "credit", 2, "Морозова Дарья Игоревна")

    t_alg = disc("Алгоритмы и структуры данных", "exam", 5, "Гусев Артем Валерьевич")
    t_db = disc("Базы данных", "exam", 4, "Никитина Ольга Романовна")
    t_os = disc("Операционные системы", "diff_credit", 4, "Захаров Денис Ильич")
    t_phys = disc("Физика", "exam", 4, "Полякова Ирина Геннадьевна")
    t_eng = disc("Иностранный язык", "credit", 2, "Морозова Дарья Игоревна")
    t_pe = disc("Физическая культура и спорт", "credit", 2, "Ковалев Сергей Петрович")
    db.flush()

    def short(fio: str) -> str:
        f, i, o = fio.split()
        return f"{f} {i[0]}. {o[0]}."

    # ---------------- Сетка расписания ----------------
    def lt(group, wd, pair, d, kind, room, building, parity="any", online=False):
        t = LessonTemplate(group_code=group, weekday=wd, pair_no=pair, parity=parity, discipline_id=d.id, kind=kind,
                           room=room, building=building, teacher=short(d.teacher), online=online)
        db.add(t)
        return t

    K = "U4227"
    k_mon_lec = lt(K, 0, 3, k_strat, "lecture", "2207", KRONV)
    lt(K, 0, 4, k_strat, "practice", "2207", KRONV, parity="odd")
    lt(K, 0, 5, k_eng, "practice", "1410", LOMO)
    k_tue_lec = lt(K, 1, 2, k_bp, "lecture", "Онлайн", "Zoom-конференция курса", online=True)
    lt(K, 1, 3, k_bp, "practice", "1216", LOMO, parity="even")
    lt(K, 2, 4, k_uip, "lecture", "1404", LOMO)
    k_wed_pr = lt(K, 2, 5, k_uip, "practice", "1404", LOMO)
    k_thu_pr = lt(K, 3, 3, k_tt, "practice", "2310", BIRZH)
    lt(K, 3, 4, k_nis, "practice", "Онлайн", "Teams, канал семинара", parity="odd", online=True)
    lt(K, 4, 3, k_tt, "lecture", "2310", BIRZH, parity="even")
    lt(K, 4, 4, k_strat, "practice", "2207", KRONV, parity="even")

    T = "P3214"
    t_mon_phys = lt(T, 0, 1, t_phys, "lecture", "2337", KRONV)
    lt(T, 0, 2, t_phys, "practice", "2337", KRONV, parity="odd")
    lt(T, 0, 3, t_alg, "lecture", "1404", LOMO)
    lt(T, 1, 2, t_db, "lecture", "Онлайн", "Zoom-конференция курса", online=True)
    lt(T, 1, 3, t_db, "lab", "2209", KRONV, parity="even")
    lt(T, 1, 4, t_pe, "practice", "Спортзал", LOMO)
    lt(T, 2, 1, t_os, "lecture", "2337", KRONV)
    t_wed_os = lt(T, 2, 2, t_os, "lab", "3214", KRONV)
    lt(T, 2, 3, t_eng, "practice", "1410", LOMO)
    t_thu_alg = lt(T, 3, 2, t_alg, "practice", "2209", KRONV)
    lt(T, 3, 3, t_alg, "lab", "2209", KRONV, parity="odd")
    lt(T, 4, 3, t_phys, "lab", "2416", KRONV, parity="even")
    lt(T, 4, 4, t_pe, "practice", "Спортзал", LOMO)
    lt(T, 5, 2, t_db, "practice", "Онлайн", "Zoom-конференция курса", parity="odd", online=True)
    db.flush()

    # ---------------- Изменения в расписании (на ближайшие две недели) ----------------
    changes = [
        LessonChange(template_id=k_mon_lec.id, date=mon, change_type="room", new_room="1229", new_building=KRONV,
                     note="В аудитории 2207 ремонт проектора"),
        LessonChange(template_id=k_tue_lec.id, date=mon + timedelta(days=1), change_type="moved",
                     new_date=mon + timedelta(days=4), new_pair_no=5, new_room="Онлайн",
                     note="Преподаватель на конференции"),
        LessonChange(template_id=k_wed_pr.id, date=mon + timedelta(days=2), change_type="cancelled",
                     note="Задание на самостоятельную работу выложено в курсе"),
        LessonChange(template_id=k_thu_pr.id, date=mon + timedelta(days=3), change_type="teacher",
                     new_teacher="Соболев И. П.", note="Замена на одно занятие"),
        LessonChange(template_id=k_mon_lec.id, date=mon + timedelta(days=7), change_type="online",
                     note="Лекция пройдет онлайн, ссылка в курсе"),
        LessonChange(template_id=t_mon_phys.id, date=mon, change_type="cancelled", note="Преподаватель заболел"),
        LessonChange(template_id=t_wed_os.id, date=mon + timedelta(days=2), change_type="room", new_room="3302",
                     new_building=KRONV, note="Компьютерный класс 3214 занят под олимпиаду"),
        LessonChange(template_id=t_thu_alg.id, date=mon + timedelta(days=3), change_type="moved",
                     new_date=mon + timedelta(days=5), new_pair_no=3, new_room="2209",
                     note="Перенос из-за праздничного концерта в актовом зале"),
    ]
    db.add_all(changes)

    # ---------------- Баллы текущего семестра ----------------
    def enroll(student, d, items, final=None):
        e = Enrollment(student_id=student.id, discipline_id=d.id, final_points=final)
        db.add(e)
        db.flush()
        for pos, (title, mx, pts, due_days) in enumerate(items):
            db.add(ScoreItem(enrollment_id=e.id, position=pos, title=title, max_points=mx, points=pts,
                             due=(today + timedelta(days=due_days)) if due_days is not None else None))
        return e

    enroll(kira, k_strat, [("Посещение и работа на семинарах", 10, 9, None), ("Кейс 1: диагностика изменений", 15, 14, None),
                           ("Кейс 2: карта стейкхолдеров", 15, 12, None), ("Групповой проект", 20, None, 9),
                           ("Экзамен", 40, None, 50)])
    enroll(kira, k_bp, [("Market analysis", 20, 17, None), ("Financial model", 25, 19, None),
                        ("Final business plan", 30, None, 5), ("Pitch defense", 25, None, 12)])
    enroll(kira, k_tt, [("Практика 3.1", 20, 18, None), ("Практика 3.2", 20, 15, None), ("Практика 3.3", 20, 11, None),
                        ("Итоговый проект", 40, None, 14)])
    enroll(kira, k_nis, [("Обзор литературы", 30, 27, None), ("Доклад на семинаре", 30, None, 7),
                         ("Статья, черновик", 40, None, 21)])
    enroll(kira, k_uip, [("Тест 1", 10, 8, None), ("Проектный устав", 25, 23, None), ("Тест 2", 10, None, 3),
                         ("Курсовой проект", 15, None, 18), ("Экзамен", 40, None, 55)])
    enroll(kira, k_eng, [("Speaking", 30, 26, None), ("Writing", 30, 25, None), ("Final test", 40, None, 16)])

    enroll(timur, t_alg, [("Лабораторная 1", 10, 10, None), ("Лабораторная 2", 10, 9, None), ("Контрольная 1", 10, 8, None),
                          ("Лабораторная 3", 10, None, 4), ("Контрольная 2", 20, None, 15), ("Экзамен", 40, None, 52)])
    enroll(timur, t_db, [("Лабораторная 1", 10, 9, None), ("Лабораторная 2", 10, 8, None),
                         ("Курсовой проект: этап 1", 10, 7, None), ("Курсовой проект: этап 2", 15, None, 6),
                         ("Курсовой проект: защита", 15, None, 19), ("Экзамен", 40, None, 54)])
    enroll(timur, t_os, [("Лабораторная 1", 15, 13, None), ("Лабораторная 2", 15, 11, None), ("Лабораторная 3", 15, 9, None),
                         ("Лабораторная 4", 15, None, 2), ("Итоговый тест", 40, None, 17)])
    enroll(timur, t_phys, [("Лабораторная 1", 10, 5, None), ("Лабораторная 2", 10, 4, None), ("Контрольная 1", 20, 9, None),
                           ("Лабораторная 3", 10, None, 8), ("Экзамен", 50, None, 58)])
    enroll(timur, t_eng, [("Speaking", 30, 24, None), ("Writing", 30, 22, None), ("Final test", 40, None, 16)])
    enroll(timur, t_pe, [("Посещаемость", 60, 41, None), ("Нормативы", 40, None, 11)])

    # ---------------- Прошлые семестры ----------------
    past = {
        kira: [("Осень 2025", "Методология научных исследований", "exam", 92),
               ("Осень 2025", "Экономика инноваций", "exam", 88),
               ("Осень 2025", "Маркетинг инноваций", "diff_credit", 95),
               ("Осень 2025", "Деловой английский", "credit", 81),
               ("Весна 2026", "Управление интеллектуальной собственностью", "exam", 79),
               ("Весна 2026", "Венчурное финансирование", "exam", 91),
               ("Весна 2026", "Цифровые платформы", "diff_credit", 86),
               ("Весна 2026", "Производственная практика", "diff_credit", 94)],
        timur: [("Осень 2025", "Математический анализ", "exam", 71),
                ("Осень 2025", "Программирование", "exam", 93),
                ("Осень 2025", "Дискретная математика", "exam", 78),
                ("Осень 2025", "Информатика", "diff_credit", 88),
                ("Весна 2026", "Линейная алгебра", "exam", 64),
                ("Весна 2026", "Программирование (продолжение)", "exam", 96),
                ("Весна 2026", "Вычислительная математика", "diff_credit", 82),
                ("Весна 2026", "Физическая культура и спорт", "credit", 75)],
    }
    for st, rows in past.items():
        for s, name, ctrl, pts in rows:
            db.add(PastResult(student_id=st.id, semester=s, discipline=name, control=ctrl, points=pts))

    # ---------------- Сессия ----------------
    for group, items in {
        K: [(k_strat, "consultation", date(2027, 1, 12), 12, "2207", KRONV), (k_strat, "exam", date(2027, 1, 13), 10, "2207", KRONV),
            (k_uip, "consultation", date(2027, 1, 18), 14, "1404", LOMO), (k_uip, "exam", date(2027, 1, 19), 10, "1404", LOMO)],
        T: [(t_alg, "consultation", date(2027, 1, 11), 12, "1404", LOMO), (t_alg, "exam", date(2027, 1, 12), 10, "1404", LOMO),
            (t_db, "consultation", date(2027, 1, 15), 15, "2209", KRONV), (t_db, "exam", date(2027, 1, 16), 10, "2209", KRONV),
            (t_phys, "consultation", date(2027, 1, 20), 12, "2337", KRONV), (t_phys, "exam", date(2027, 1, 21), 10, "2337", KRONV)],
    }.items():
        for d, kind, day, hour, room, b in items:
            db.add(ExamEvent(group_code=group, discipline_id=d.id, kind=kind, starts_at=_dt(day, hour), room=room, building=b))

    # ---------------- Дедлайны ----------------
    def dl(st, title, days, source, hour=23, minute=59):
        db.add(Deadline(student_id=st.id, title=title, due_at=_dt(today + timedelta(days=days), hour, minute), source=source))

    dl(kira, "Тест 2 по управлению инновационными проектами", 3, "Управление инновационными проектами")
    dl(kira, "Финальная версия бизнес-плана", 5, "Бизнес-планирование")
    dl(kira, "Доклад на научно-исследовательском семинаре", 7, "Научно-исследовательский семинар", 15, 20)
    dl(kira, "Групповой проект по управлению изменениями", 9, "Стратегическое обновление")
    dl(kira, "Подтвердить тему ВКР у руководителя", 12, "Деканат", 18, 0)
    dl(timur, "Лабораторная 4 по операционным системам", 2, "Операционные системы")
    dl(timur, "Лабораторная 3 по алгоритмам", 4, "Алгоритмы и структуры данных")
    dl(timur, "Курсовой проект по базам данных: этап 2", 6, "Базы данных")
    dl(timur, "Лабораторная 3 по физике", 8, "Физика", 15, 0)
    dl(timur, "Сдать нормативы по физкультуре", 11, "Физическая культура и спорт", 17, 0)
    dl(timur, "Оплатить обучение за весенний семестр", 20, "Деканат", 18, 0)

    # ---------------- Заявки ----------------
    def req(st, number, type_code, fmt, status, purpose, created_days_ago, events, staff_comment=None, ready=False):
        created_local = datetime.combine(today - timedelta(days=created_days_ago), time(10, 15))
        if created_local > now - timedelta(minutes=20):
            created_local = now - timedelta(minutes=20)
        created = _to_utc(created_local)
        r = StudentRequest(number=number, student_id=st.id, type_code=type_code, fmt=fmt, status=status, purpose=purpose,
                           copies=1, created_at=created, updated_at=created, staff_comment=staff_comment)
        if ready:
            r.verify_code = secrets.token_hex(8)
            r.issued_at = created + timedelta(days=1)
            if fmt == "paper":
                r.pickup_place = DEKANAT_PLACE
        db.add(r)
        db.flush()
        t = created
        for status_, actor, comment, hours in events:
            t = t + timedelta(hours=hours)
            db.add(RequestEvent(request_id=r.id, status=status_, actor=actor, comment=comment, created_at=t))
            r.updated_at = t
        return r

    r1 = req(kira, "СП-2026-0412", "study", "electronic", "ready", "По месту работы", 4,
             [("submitted", "student", None, 0), ("in_progress", "staff", None, 2),
              ("ready", "staff", "Справка подписана электронной подписью", 20)], ready=True)
    req(kira, "СП-2026-0457", "stipend", "electronic", "in_progress", "Для оформления субсидии", 1,
        [("submitted", "student", None, 0), ("in_progress", "staff", "Запросили данные в бухгалтерии", 5)])
    req(timur, "СП-2026-0461", "transcript", "paper", "need_info", "Для стажировки в IT-компании", 2,
        [("submitted", "student", None, 0),
         ("need_info", "staff", "Уточните, нужна ли выписка на английском языке или достаточно русской версии?", 6)],
        staff_comment="Уточните, нужна ли выписка на английском языке или достаточно русской версии?")
    req(timur, "СП-2026-0470", "card", "paper", "submitted", None, 0, [("submitted", "student", None, 0)])

    # ---------------- Объявления ----------------
    ann = [
        Announcement(title="Опубликовано расписание зимней сессии",
                     body="Даты консультаций и экзаменов уже в разделе Учеба, вкладка Сессия. "
                          "Если экзамены совпадают по времени, напишите в деканат до 15 декабря.",
                     audience="all", author_id=staff.id, created_at=_to_utc(now - timedelta(days=2, hours=4))),
        Announcement(title="Продление студенческих билетов на весенний семестр",
                     body="Наклейки на студенческие билеты выдаем с 1 по 26 декабря в деканате. Возьмите с собой билет.",
                     audience="all", author_id=staff.id, created_at=_to_utc(now - timedelta(days=5))),
        Announcement(title="Встреча с научными руководителями",
                     body="В четверг в 18:40 в ауд. 1216 обсуждаем темы ВКР. Можно подключиться онлайн, ссылка в курсе.",
                     audience="group:U4227", author_id=staff.id, created_at=_to_utc(now - timedelta(days=1, hours=2))),
    ]
    db.add_all(ann)

    # ---------------- Уведомления ----------------
    def n(user, kind, title, body, link, hours_ago, read=False):
        created = _to_utc(now - timedelta(hours=hours_ago))
        db.add(Notification(user_id=user.id, kind=kind, title=title, body=body, link=link, created_at=created,
                            read_at=created if read else None))

    sched_link = f"/schedule?view=week&date={mon.isoformat()}"
    n(u_kira, "schedule", "Перенос: Бизнес-планирование", "Лекция со вторника перенесена на пятницу, 5-я пара, онлайн.", sched_link, 2)
    n(u_kira, "schedule", "Другая аудитория в понедельник", "Стратегическое обновление: пара в ауд. 1229 вместо 2207.", sched_link, 3)
    n(u_kira, "request", "Справка об обучении готова", "Заявка СП-2026-0412. Электронная справка с QR-кодом доступна в заявке.",
      f"/requests/{r1.id}", 70, read=True)
    n(u_kira, "message", "Деканат ответил на ваш вопрос", "Про перезачет дисциплины.", "/messages", 30, read=True)

    n(u_timur, "schedule", "Отмена: Физика в понедельник", "Лекция в 8:20 отменена, преподаватель заболел.", sched_link, 1)
    n(u_timur, "schedule", "Перенос: Алгоритмы и структуры данных", "Практика с четверга перенесена на субботу, 3-я пара.", sched_link, 4)
    n(u_timur, "request", "Нужно уточнение по заявке СП-2026-0461", "Деканат задал вопрос по выписке оценок.", None, 40)
    # Каждое объявление приходит уведомлением всем, кому оно адресовано (как при публикации из кабинета деканата)
    for a in ann:
        for u, st in ((u_kira, kira), (u_timur, timur)):
            if a.audience == "all" or a.audience == f"group:{st.group_code}":
                hours = (now - (a.created_at + timedelta(hours=3))).total_seconds() / 3600
                n(u, "news", a.title, a.body[:200], "/notifications?kind=news", hours, read=hours > 48)
    db.flush()
    # ссылка на заявку Тимура
    timur_req = db.scalar(select(StudentRequest).where(StudentRequest.number == "СП-2026-0461"))
    for notif in db.scalars(select(Notification).where(Notification.user_id == u_timur.id, Notification.kind == "request")):
        notif.link = f"/requests/{timur_req.id}"

    # ---------------- Переписка с деканатом ----------------
    db.add_all([
        Message(student_id=kira.id, author_id=u_kira.id, from_staff=False, read_by_student=True, read_by_staff=True,
                body="Здравствуйте! Можно ли перезачесть английский из бакалавриата?",
                created_at=_to_utc(now - timedelta(hours=34))),
        Message(student_id=kira.id, author_id=staff.id, from_staff=True, read_by_student=True, read_by_staff=True,
                body="Здравствуйте, Кира! В магистратуре перезачет возможен, если совпадает объем часов. "
                     "Напишите сюда, в каком году и с каким количеством часов сдавали английский, мы сверим "
                     "с учебным планом и ответим за 5 рабочих дней.",
                created_at=_to_utc(now - timedelta(hours=30))),
        Message(student_id=timur.id, author_id=u_timur.id, from_staff=False, read_by_student=True, read_by_staff=False,
                body="Добрый день! Я потерял студенческий, заявку на дубликат отправил. Нужно ли что-то еще?",
                created_at=_to_utc(now - timedelta(hours=1))),
    ])
    db.commit()


def init_db(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is None:
            seed(db)
            db.merge(Meta(key="seeded_on", value=now_local().date().isoformat()))
            db.commit()


_fresh_lock = threading.Lock()
_checked_on: str | None = None


def ensure_demo_fresh() -> bool:
    """Пересоздает демо-базу, если она создана не сегодня (по Москве).
    Так даты в расписании, дедлайнах и заявках всегда актуальны, а все, что посетители
    поменяли в общих демо-аккаунтах, исчезает на следующий день. Возвращает True, если был сброс."""
    global _checked_on
    from . import config
    today = now_local().date().isoformat()
    if not config.DEMO_DAILY_RESET or _checked_on == today:
        return False
    with _fresh_lock:
        if _checked_on == today:
            return False
        with SessionLocal() as db:
            try:
                seeded = db.get(Meta, "seeded_on")
            except Exception:
                seeded = None
            fresh = seeded is not None and seeded.value == today
        if not fresh:
            init_db(reset=True)
            from .security import reset_login_limits
            reset_login_limits()
        _checked_on = today
        return not fresh


if __name__ == "__main__":
    init_db(reset="--reset" in sys.argv)
    print("База готова. Тестовые аккаунты:")
    for login, pwd, role in DEMO_ACCOUNTS:
        print(f"  {role}: {login} / {pwd}")

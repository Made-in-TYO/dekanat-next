"""Демо-данные. Все люди, оценки, номера и заявки вымышлены.

Даты считаются от дня запуска, чтобы в расписании и дедлайнах всегда было что показать.
Запуск вручную: python -m app.seed --reset
"""
import secrets
import sys
import threading
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from sqlalchemy.orm import selectinload

from .config import DEMO_ACCOUNTS, HOLIDAYS
from .db import Base, SessionLocal, engine
from .models import (Announcement, AuthSession, Deadline, Discipline, Enrollment, ExamEvent, LessonChange,
                     LessonTemplate, Message, Meta, Notification, PastResult, RequestEvent, ScoreItem, Student,
                     StudentRequest, User)
from .security import hash_password
from .services.catalog import DEKANAT_PLACE
from .services.timeutil import now_local, utcnow, week_monday

__all__ = ["DEMO_ACCOUNTS", "init_db", "seed", "ensure_demo_fresh", "reset_demo_keep_sessions"]

_hash_cache: dict[str, str] = {}


def _demo_hash(password: str) -> str:
    """argon2id специально медленный, поэтому хеш демо-пароля считаем один раз на процесс, а не при каждом сбросе."""
    if password not in _hash_cache:
        _hash_cache[password] = hash_password(password)
    return _hash_cache[password]


_HOLIDAYS = {date.fromisoformat(k) for k in HOLIDAYS}


def _is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in _HOLIDAYS

KRONV = "Кронверкский пр., 49"
LOMO = "ул. Ломоносова, 9"
BIRZH = "Биржевая линия, 14"


def _dt(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m))


def _to_utc(local_dt: datetime) -> datetime:
    """Служебные отметки времени храним в UTC (Москва = UTC+3)."""
    return local_dt - timedelta(hours=3)


def seed(db, commit: bool = True) -> None:
    now = now_local()
    today = now.date()
    # Опорное время для событий в демо-данных: внутри рабочего дня деканата (будни, не праздник, 10:00-17:40),
    # а не ночью и не в выходной. Все события строятся от него назад, поэтому никогда не оказываются в будущем.
    if _is_workday(today) and 10 <= now.hour and now.time() < time(17, 40):
        ref = now.replace(second=0, microsecond=0)
    else:
        d = today if (_is_workday(today) and now.time() >= time(17, 40)) else today - timedelta(days=1)
        while not _is_workday(d):
            d -= timedelta(days=1)
        ref = _dt(d, 17, 40)

    def wd(k: int, h: int, m: int = 0) -> datetime:
        """k-й рабочий день назад от опорного (0: день опорного времени) в заданное время, но не позже опорного."""
        d = ref.date()
        while k > 0:
            d -= timedelta(days=1)
            if _is_workday(d):
                k -= 1
        return min(_dt(d, h, m), ref)

    base = today if today.weekday() < 5 else today + timedelta(days=7 - today.weekday())
    mon = week_monday(base)

    # ---------------- Пользователи ----------------
    staff = User(login="dekanat", password_hash=_demo_hash("Dekanat-2026"), role="staff", is_demo=True,
                 full_name="Мельникова Ольга Сергеевна", position="Специалист по учебной работе")
    u_kira = User(login="kira", password_hash=_demo_hash("Kira-2026"), role="student", is_demo=True,
                  full_name="Воронцова Кира Андреевна")
    u_timur = User(login="timur", password_hash=_demo_hash("Timur-2026"), role="student", is_demo=True,
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
    # Оставляем только изменения, которые попадают на учебные дни и на реально стоящие в этот день пары
    # (например, если демо-данные создаются во время сессии, переносов не будет).
    from .services import schedule as S
    valid = []
    for c in changes:
        day_ids = {t.id for t in S.templates_for(db, db.get(LessonTemplate, c.template_id).group_code, c.date)}
        if c.template_id in day_ids and (c.new_date is None or S.is_class_day(c.new_date)):
            valid.append(c)
    db.add_all(valid)

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
    # Время каждого события задано явно: события идут по порядку, в рабочие часы и не позже опорного времени.
    def req(st, number, type_code, fmt, status, purpose, events, staff_comment=None):
        """events: [(статус, кто, комментарий, местное время)], первое событие = подача заявки."""
        created = _to_utc(events[0][3])
        r = StudentRequest(number=number, student_id=st.id, type_code=type_code, fmt=fmt, status=status, purpose=purpose,
                           copies=1, created_at=created, updated_at=created, staff_comment=staff_comment)
        db.add(r)
        db.flush()
        for status_, actor, comment, local_t in events:
            t = _to_utc(local_t)
            db.add(RequestEvent(request_id=r.id, status=status_, actor=actor, comment=comment, created_at=t))
            r.updated_at = t
            if status_ == "ready":
                r.issued_at = t
                r.verify_code = secrets.token_hex(8) if fmt == "electronic" else None
                if fmt == "paper":
                    r.pickup_place = DEKANAT_PLACE
        return r

    # Электронная справка об обучении выдается сразу, как и при заказе из кабинета
    t_r1 = wd(4, 10, 15)
    req(kira, "СП-2026-0412", "study", "electronic", "ready", "По месту работы",
        [("submitted", "student", None, t_r1),
         ("ready", "system", "Справка сформирована автоматически по данным учетной системы и подписана "
                             "электронной подписью", t_r1)])
    req(kira, "СП-2026-0457", "stipend", "electronic", "in_progress", "Для оформления субсидии",
        [("submitted", "student", None, wd(1, 10, 15)),
         ("in_progress", "staff", "Запросили данные в бухгалтерии", wd(1, 15, 5))])
    t_ask = wd(2, 16, 15)
    ask = "Уточните, нужна ли выписка на английском языке или достаточно русской версии?"
    timur_req = req(timur, "СП-2026-0461", "transcript", "paper", "need_info", "Для стажировки в IT-компании",
                    [("submitted", "student", None, wd(2, 10, 15)), ("need_info", "staff", ask, t_ask)],
                    staff_comment=ask)
    # Заявка "сегодня": если рабочий день только начался, она подана накануне вечером
    t_card = wd(0, 10, 15) if ref >= _dt(ref.date(), 10, 35) else wd(1, 16, 50)
    req(timur, "СП-2026-0470", "card", "paper", "submitted", None, [("submitted", "student", None, t_card)])

    # ---------------- Объявления ----------------
    ann = [
        Announcement(title="Опубликовано расписание зимней сессии",
                     body="Даты консультаций и экзаменов уже в разделе Учеба, вкладка Сессия. "
                          "Если экзамены совпадают по времени, напишите в деканат до 15 декабря.",
                     audience="all", author_id=staff.id, created_at=_to_utc(wd(2, 13, 40))),
        Announcement(title="Продление студенческих билетов на весенний семестр",
                     body="Наклейки на студенческие билеты выдаем с 1 по 26 декабря в деканате. Возьмите с собой билет.",
                     audience="all", author_id=staff.id, created_at=_to_utc(wd(5, 11, 0))),
        Announcement(title="Встреча с научными руководителями",
                     body="В четверг в 18:40 в ауд. 1216 обсуждаем темы ВКР. Можно подключиться онлайн, ссылка в курсе.",
                     audience="group:U4227", author_id=staff.id, created_at=_to_utc(wd(1, 15, 40))),
    ]
    db.add_all(ann)

    # ---------------- Уведомления ----------------
    # Уведомления строятся из реальных событий демо-данных теми же функциями, что и в кабинете деканата:
    # нет изменения в расписании, нет и уведомления о нем; время уведомления равно времени события.
    def n(user, kind, title, body, link, local_t, read=False):
        created = _to_utc(local_t)
        db.add(Notification(user_id=user.id, kind=kind, title=title, body=body, link=link, created_at=created,
                            read_at=created if read else None))

    users = {kira.group_code: u_kira, timur.group_code: u_timur}
    db.flush()
    for i, c in enumerate(valid):
        t = db.get(LessonTemplate, c.template_id)
        title, body = S.change_notice(t.discipline.name, t.pair_no, c)
        # Деканат сообщает об изменении заранее: в рабочий день до начала пары
        k, sent = 1, wd(1, 16, 30 + i)
        while sent >= S.lesson_start(c.date, t.pair_no) and k < 15:
            k += 1
            sent = wd(k, 16, 30 + i)
        n(users[t.group_code], "schedule", title, body, f"/schedule?date={c.date.isoformat()}", sent)
    n(u_kira, "message", "Деканат ответил на ваш вопрос", "Про перезачет дисциплины.", "/messages", wd(1, 12, 10), read=True)
    n(u_timur, "request", "Нужно уточнение по заявке СП-2026-0461", ask, f"/requests/{timur_req.id}", t_ask)
    # Каждое объявление приходит уведомлением всем, кому оно адресовано (как при публикации из кабинета деканата)
    for a in ann:
        for u, st in ((u_kira, kira), (u_timur, timur)):
            if a.audience == "all" or a.audience == f"group:{st.group_code}":
                local_t = a.created_at + timedelta(hours=3)
                n(u, "news", a.title, a.body, "/notifications?kind=news", local_t, read=(ref - local_t).days >= 2)
    db.flush()

    # ---------------- Переписка с деканатом ----------------
    db.add_all([
        Message(student_id=kira.id, author_id=u_kira.id, from_staff=False, read_by_student=True, read_by_staff=True,
                body="Здравствуйте! Можно ли перезачесть английский из бакалавриата?",
                created_at=_to_utc(wd(1, 11, 40))),
        Message(student_id=kira.id, author_id=staff.id, from_staff=True, read_by_student=True, read_by_staff=True,
                body="Здравствуйте, Кира! В магистратуре перезачет возможен, если совпадает объем часов. "
                     "Напишите сюда, в каком году и с каким количеством часов сдавали английский, мы сверим "
                     "с учебным планом и ответим за 5 рабочих дней.",
                created_at=_to_utc(wd(1, 12, 10))),
        Message(student_id=timur.id, author_id=u_timur.id, from_staff=False, read_by_student=True, read_by_staff=False,
                body="Добрый день! Я потерял студенческий, заявку на дубликат отправил. Нужно ли что-то еще?",
                created_at=_to_utc(t_card + timedelta(minutes=10))),
    ])
    if commit:
        db.commit()


def init_db(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is None:
            seed(db, commit=False)
            _meta_set(db, "seeded_on", now_local().date().isoformat())
            db.commit()


def _meta_set(db, key: str, value: str) -> None:
    """Записать служебное значение одной командой (INSERT ... ON CONFLICT): параллельные запросы
    и сброс демо не спотыкаются об уникальность ключа, как при чтении и последующей вставке."""
    name = db.get_bind().dialect.name
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        db.merge(Meta(key=key, value=value))
        return
    stmt = insert(Meta).values(key=key, value=value)
    db.execute(stmt.on_conflict_do_update(index_elements=[Meta.key], set_={"value": value}))


_fresh_lock = threading.Lock()
_checked_on: str | None = None
ACTIVITY_WRITE_EVERY = timedelta(seconds=60)


def reset_demo_keep_sessions() -> None:
    """Пересоздает демо-данные, сохраняя открытые сеансы: тот, кто сейчас в кабинете, не вылетит.

    Таблицы не удаляются: строки стираются и создаются заново в одной транзакции. Пока она идет,
    другие запросы видят прежние данные (SQLite в режиме WAL), а после фиксации сразу новые.
    Ошибок вида "нет такой таблицы" у параллельных запросов не бывает."""
    with SessionLocal() as db:
        # Сохраняем только действующие сеансы: истекшие все равно не дают входа и только копились бы
        saved = [(s.token_hash, s.csrf_token, s.user.login, s.created_at, s.expires_at)
                 for s in db.scalars(select(AuthSession).options(selectinload(AuthSession.user))
                                     .where(AuthSession.expires_at > utcnow()))]
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        seed(db, commit=False)
        db.flush()
        ids = {u.login: u.id for u in db.scalars(select(User))}
        for token_hash, csrf, login, created, expires in saved:
            if login in ids:
                db.add(AuthSession(token_hash=token_hash, csrf_token=csrf, user_id=ids[login],
                                   created_at=created, expires_at=expires))
        _meta_set(db, "seeded_on", now_local().date().isoformat())
        _meta_set(db, "last_activity", utcnow().isoformat())
        db.commit()
    from .security import reset_login_limits
    reset_login_limits()


def ensure_demo_fresh(active: bool = False) -> bool:
    """Держит демо-данные свежими для общих тестовых аккаунтов. База пересоздается:
    - раз в сутки (по Москве): даты в расписании, дедлайнах и заявках всегда актуальны;
    - после DEMO_IDLE_RESET_MINUTES минут тишины: следующий член жюри приходит на чистый кабинет,
      не видя изменений предыдущего, а тот, кто работает прямо сейчас, ничего не теряет.

    active: запрос от посетителя с сеансом. Только такие запросы считаются активностью, поэтому
    проверки мониторинга (HEAD /healthz) не мешают сбросу. Отметка активности хранится в базе,
    а не в памяти процесса, поэтому сброс работает одинаково при нескольких процессах.
    Возвращает True, если был сброс."""
    global _checked_on
    from . import config
    if not config.DEMO_DAILY_RESET:
        return False
    today = now_local().date().isoformat()
    if _checked_on == today and not active:
        return False
    with _fresh_lock:
        now = utcnow()
        with SessionLocal() as db:
            try:
                meta = {m.key: m.value for m in db.scalars(select(Meta).where(Meta.key.in_(("seeded_on", "last_activity"))))}
            except Exception:
                meta = {}
            last = datetime.fromisoformat(meta["last_activity"]) if meta.get("last_activity") else None
            idle = (active and last is not None and config.DEMO_IDLE_RESET_MINUTES > 0
                    and now - last > timedelta(minutes=config.DEMO_IDLE_RESET_MINUTES))
            reset = meta.get("seeded_on") != today or idle
            if not reset and active and (last is None or now - last >= ACTIVITY_WRITE_EVERY):
                try:
                    _meta_set(db, "last_activity", now.isoformat())
                    db.commit()
                except Exception:  # отметка активности не должна ломать запрос посетителя
                    db.rollback()
        if reset:
            reset_demo_keep_sessions()
        _checked_on = today
        return reset


if __name__ == "__main__":
    init_db(reset="--reset" in sys.argv)
    print("База готова. Тестовые аккаунты:")
    for login, pwd, role in DEMO_ACCOUNTS:
        print(f"  {role}: {login} / {pwd}")

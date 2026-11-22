"""Верстка с реальным текстом в настоящем браузере (третья независимая проверка, находка 1).

Сначала в кабинет добавляются длинные ссылки и слова без пробелов: в сообщения, объявление, комментарий
к заявке, пояснение к изменению пары. Потом Chromium обходит все страницы трех ролей на ширине 360 px и
проверяет, что нет горизонтальной прокрутки, ошибок в консоли и нарушений CSP.

Нужен Playwright: pip install playwright && python -m playwright install chromium.
Без него тест пропускается (остальные тесты работают и без браузера).
"""
import socket
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from urllib.parse import urldefrag, urljoin, urlparse

import pytest

from app.db import SessionLocal
from app.main import app
from app.models import LessonTemplate, StudentRequest
from app.security import reset_login_limits
from app.seed import init_db
from app.services import schedule as S
from app.services import timeutil
from conftest import csrf, login

sync_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

LONG_URL = "https://itmo.zoom.us/j/84512345678?pwd=Q2xhdWRlQ29kZVJldmlld0RlbW9UZXN0TG9uZ1Bhc3N3b3JkMTIz&from=deanery"
LONG_WORD = "Сверхдлинноесловобезпробелов" * 6
NOW = datetime(2026, 11, 23, 9, 0)  # понедельник учебной недели


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fill_with_long_text():
    """Длинный текст в каждое место, куда его могут ввести студент или сотрудник."""
    kira, timur, staff = login("kira", "Kira-2026"), login("timur", "Timur-2026"), login("dekanat", "Dekanat-2026")
    t = csrf(kira, "/messages")
    kira.post("/messages", data={"body": f"Ссылка на консультацию: {LONG_URL} {LONG_WORD}", "csrf_token": t})
    kira.post("/requests", data={"type_code": "transcript", "purpose": LONG_WORD[:200], "comment": LONG_URL,
                                 "fmt": "paper", "copies": 1, "csrf_token": t})
    t = csrf(staff, "/staff")
    staff.post("/staff/announcements", data={"title": LONG_WORD[:200], "body": f"Подробности: {LONG_URL}",
                                             "audience": "all", "csrf_token": t})
    with SessionLocal() as db:
        rid = db.query(StudentRequest.id).filter(StudentRequest.status == "submitted").first()[0]
        lesson = next(v for d in range(1, 6) for v in S.lessons_for_date(
            db, "U4227", NOW.date().replace(day=NOW.day + d)) if v.status == "normal")
        tpl = db.get(LessonTemplate, lesson.template_id)
    staff.post(f"/staff/requests/{rid}/status", data={"new_status": "need_info", "comment": f"Посмотрите {LONG_URL}",
                                                      "csrf_token": t})
    staff.post("/staff/schedule", data={"template_id": tpl.id, "day": lesson.day.isoformat(), "change_type": "online",
                                        "note": LONG_URL, "csrf_token": t})
    staff.post("/staff/messages/2", data={"body": f"Ответ со ссылкой {LONG_URL}", "csrf_token": t})
    # длинная аудитория и длинное имя преподавателя: видны на табло, в расписании и в изменениях
    with SessionLocal() as db:
        lessons = [v for d in range(0, 14) for v in S.lessons_for_date(db, "U4227", NOW.date() + timedelta(days=d))
                   if v.status == "normal" and v.start_dt > NOW]
    for v, field, change in ((lessons[1], "new_room", "room"), (lessons[2], "new_teacher", "teacher")):
        staff.post("/staff/schedule", data={"template_id": v.template_id, "day": v.day.isoformat(), "change_type": change,
                                            field: LONG_WORD[:32 if change == "room" else 128], "csrf_token": t})
    t2 = csrf(timur, "/messages")
    timur.post("/messages", data={"body": LONG_WORD, "csrf_token": t2})


@pytest.fixture
def live_server():
    timeutil.freeze(NOW)
    init_db(reset=True)
    reset_login_limits()
    _fill_with_long_text()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(timeout=5)
    timeutil.freeze(None)


def _crawl_in_browser(page, base: str, start: str, limit: int) -> dict[str, list[str]]:
    problems: dict[str, list[str]] = {}
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    seen, queue = set(), deque([start])
    while queue and len(seen) < limit:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        errors.clear()
        page.goto(base + path)
        page.wait_for_load_state("load")
        wide = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        found = ([f"горизонтальная прокрутка на {wide} px"] if wide > 1 else []) + list(errors)
        if found:
            problems[path] = found
        for href in page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))"):
            href = urldefrag(href)[0]
            if not href or href.startswith(("http", "mailto:", "tel:", "#", "//", "/static/", "/logout")) or ".ics" in href:
                continue
            full = urljoin(base + path, href)
            p = urlparse(full).path + (("?" + urlparse(full).query) if urlparse(full).query else "")
            if p not in seen:
                queue.append(p)
    return problems


def test_no_horizontal_scroll_or_console_errors_at_360px(live_server):
    try:
        pw = sync_api.sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as exc:  # браузер не установлен
        pytest.skip(f"Chromium для Playwright недоступен: {exc}")
    problems = {}
    try:
        for login_, password, start in [("kira", "Kira-2026", "/home"), ("timur", "Timur-2026", "/home"),
                                        ("dekanat", "Dekanat-2026", "/staff")]:
            ctx = browser.new_context(viewport={"width": 360, "height": 780})
            page = ctx.new_page()
            page.goto(live_server + "/login")
            page.fill("#login", login_)
            page.fill("#password", password)
            page.click(".login-card [type=submit]")
            page.wait_for_load_state("load")
            for path, found in _crawl_in_browser(page, live_server, start, limit=45).items():
                problems[f"{login_} {path}"] = found
            if login_ == "kira":
                # Находка 7: у выписки (только бумажная) поле количества экземпляров видно
                page.goto(live_server + "/requests/new?type=transcript")
                assert page.is_visible("#copies")
            ctx.close()
    finally:
        browser.close()
        pw.stop()
    assert not problems, problems


# ---------- Табло на телефоне (четвертая проверка, находка 1) ----------
# Обход выше проверяет прокрутку всей страницы, а у табло overflow: hidden, и обрезанный текст
# прокрутки не дает. Поэтому табло проверяется отдельно: в моменты, когда на нем «Онлайн»,
# «Спортзал» и длинная аудитория, которую ввел сотрудник, на ширине 360, 390 и 1440 px.
BOARD_CASES = [
    (datetime(2026, 11, 24, 9, 0), "timur", None),          # вторник до 10:00: онлайн-лекция
    (datetime(2026, 11, 24, 12, 0), "timur", None),         # вторник днем: Спортзал
    (datetime(2026, 11, 27, 12, 0), "timur", None),         # пятница: Спортзал
    (datetime(2026, 11, 26, 14, 0), "kira", None),          # четверг после 13:10
    (datetime(2026, 11, 27, 10, 0), "kira", None),          # пятница
    (datetime(2026, 11, 23, 9, 0), "timur", "Конференц-зал"),
    (datetime(2026, 11, 23, 9, 0), "timur", LONG_WORD[:32]),
]
BOARD_CHECK = """() => {
  const b = document.querySelector('.board'); if (!b) return {bad: ['нет табло'], text: ''};
  const br = b.getBoundingClientRect(); const bad = [];
  for (const el of b.querySelectorAll('.board-main, .board-main *, .board-title, .board-meta, .board-kicker')) {
    const r = el.getBoundingClientRect();
    if (r.width && r.right > br.right + 0.5) bad.push(el.className + ': выходит за табло на ' + Math.round(r.right - br.right) + ' px');
    if (el.clientWidth && el.scrollWidth > el.clientWidth + 1) bad.push(el.className + ': обрезано ' + el.scrollWidth + ' > ' + el.clientWidth);
  }
  const t = b.querySelector('.board-time');
  if (t && t.getBoundingClientRect().height > parseFloat(getComputedStyle(t).fontSize) * 1.6) bad.push('время на табло разорвано на строки');
  return {bad, text: b.querySelector('.board-room') ? b.querySelector('.board-room').textContent.trim() : ''};
}"""


@pytest.fixture
def board_server():
    timeutil.freeze(NOW)
    init_db(reset=True)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(timeout=5)
    timeutil.freeze(None)


def test_board_fits_on_phone_with_text_rooms(board_server):
    try:
        pw = sync_api.sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as exc:
        pytest.skip(f"Chromium для Playwright недоступен: {exc}")
    problems, seen_rooms = {}, set()
    try:
        for moment, user, long_room in BOARD_CASES:
            timeutil.freeze(moment)
            init_db(reset=True)
            reset_login_limits()
            group = "P3214" if user == "timur" else "U4227"
            if long_room:
                from app.models import LessonChange
                with SessionLocal() as db:
                    cur, nxt = S.current_and_next(db, group)
                    focus = cur or nxt
                    db.add(LessonChange(template_id=focus.template_id, date=focus.day, change_type="room", new_room=long_room))
                    db.commit()
            for width in (360, 390, 1440):
                ctx = browser.new_context(viewport={"width": width, "height": 800}, timezone_id="Europe/Moscow")
                ctx.clock.set_fixed_time(moment.isoformat() + "+03:00")
                page = ctx.new_page()
                page.goto(board_server + "/login")
                page.fill("#login", user)
                page.fill("#password", "Timur-2026" if user == "timur" else "Kira-2026")
                page.click(".login-card [type=submit]")
                page.wait_for_load_state("load")
                res = page.evaluate(BOARD_CHECK)
                seen_rooms.add(res["text"])
                if res["bad"]:
                    problems[f"{user} {moment:%a %H:%M} {width}px {long_room or ''}"] = res["bad"]
                ctx.close()
    finally:
        browser.close()
        pw.stop()
    assert not problems, problems
    # тест действительно видел трудные случаи, а не только номера аудиторий
    for room in ("Онлайн", "Спортзал", "Конференц-зал", LONG_WORD[:32]):
        assert any(room in r for r in seen_rooms), (room, seen_rooms)

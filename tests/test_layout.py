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

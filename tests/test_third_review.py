"""Тесты на находки третьей независимой проверки (номера как в отчете, docs/PROCESS.md, раздел 9)."""
import re
import threading
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import LessonChange, LessonTemplate, Message, Meta, StudentRequest
from app.seed import ensure_demo_fresh, reset_demo_keep_sessions
from app.services import timeutil
from app.services.catalog import eta_for
from app.services.timeutil import by_day, utcnow
from app.web import ASSET_VERSION, asset_version
from conftest import csrf, login, make_client


def _staff_req(status="need_info"):
    with SessionLocal() as db:
        return db.scalar(select(StudentRequest).where(StudentRequest.status == status).limit(1))


# 1. Длинные слова и ссылки переносятся на всех страницах (в браузере это проверяет tests/test_layout.py)
def test_long_words_wrap_everywhere():
    css = make_client().get(f"/static/css/app.css?v={ASSET_VERSION}").text
    body = re.search(r"\nbody \{[^}]*\}", css).group(0)
    # мягкий перенос: обычные слова не рвутся, длинные ссылки переносятся, если не помещаются
    assert "overflow-wrap: break-word" in body
    # списки с пользовательским текстом не растягиваются под длинное слово
    for sel in (".news", ".chat-list", ".history", ".notifs", ".req-status", ".form"):
        rule = re.search(re.escape(sel) + r" \{[^}]*\}", css).group(0)
        assert "minmax(0, 1fr)" in rule, sel


# 2. После ошибки выбранный статус и комментарий сохраняются
def test_staff_status_choice_kept_after_error(staff):
    r0 = _staff_req()
    token = csrf(staff, f"/staff/requests/{r0.id}")
    r = staff.post(f"/staff/requests/{r0.id}/status", data={"new_status": "rejected", "comment": "  ", "csrf_token": token})
    assert r.status_code == 400
    assert re.search(r'value="rejected"\s+checked', r.text)
    assert _staff_req().id == r0.id  # статус не изменился


# 3. Сброс демо во время запросов других посетителей не дает ошибок
def test_reset_during_requests_has_no_errors():
    errors: list = []
    stop = threading.Event()
    clients = [login("kira", "Kira-2026"), login("timur", "Timur-2026"), login("dekanat", "Dekanat-2026")]
    paths = ["/home", "/schedule", "/requests", "/notifications", "/messages", "/grades"]

    def visitor(c, ps):
        # Любая ошибка в потоке записывается: упавший поток не должен молча «пройти» тест
        try:
            token = csrf(c, ps[0])
            while not stop.is_set():
                for p in ps:
                    r = c.get(p)
                    if r.status_code >= 500:
                        errors.append((p, r.status_code))
                    if p == "/messages":
                        r = c.post("/messages", data={"body": "Вопрос во время сброса", "csrf_token": token})
                        if r.status_code >= 500:
                            errors.append((p, r.status_code))
        except Exception as exc:  # тестовый клиент пробрасывает ошибки сервера
            errors.append(("поток", repr(exc)[:300]))

    threads = [threading.Thread(target=visitor, args=(clients[0], paths)),
               threading.Thread(target=visitor, args=(clients[1], paths)),
               threading.Thread(target=visitor, args=(clients[2], ["/staff", "/staff/requests", "/staff/messages"]))]
    for t in threads:
        t.start()
    try:
        for _ in range(6):
            reset_demo_keep_sessions()
    finally:
        stop.set()
        for t in threads:
            t.join()
    assert not errors, errors[:5]
    assert all(not t.is_alive() for t in threads)
    assert "Привет, Кира" in clients[0].get("/home").text  # сеанс пережил сбросы


# 4. Проверки мониторинга не считаются активностью и не мешают сбросу после простоя
def _set_last_activity(minutes_ago: int):
    with SessionLocal() as db:
        db.merge(Meta(key="last_activity", value=(utcnow() - timedelta(minutes=minutes_ago)).isoformat()))
        db.commit()


def test_monitoring_does_not_block_idle_reset(kira, staff):
    token = csrf(staff, "/staff/announcements")
    staff.post("/staff/announcements", data={"title": "След прошлого гостя", "body": "Текст", "audience": "all",
                                             "csrf_token": token})
    _set_last_activity(31)
    anon = make_client()
    for _ in range(3):  # мониторинг и файлы приложения без сеанса
        anon.head("/healthz")
        anon.head("/")
        anon.get("/sw.js")
    with SessionLocal() as db:
        last = datetime.fromisoformat(db.get(Meta, "last_activity").value)
    assert utcnow() - last > timedelta(minutes=30)  # отметка активности не сдвинулась
    # первый посетитель с сеансом после тишины получает чистые демо-данные
    assert "След прошлого гостя" not in kira.get("/notifications").text


def test_visitor_with_session_counts_as_activity(kira):
    _set_last_activity(10)
    kira.get("/home")
    with SessionLocal() as db:
        last = datetime.fromisoformat(db.get(Meta, "last_activity").value)
    assert utcnow() - last < timedelta(seconds=5)
    assert ensure_demo_fresh(active=True) is False  # активность свежая, сброса нет


# 7. У выписки, которая бывает только бумажной, поле количества экземпляров не прячется
def test_copies_field_for_paper_only_certificate(kira):
    html = kira.get("/requests/new?type=transcript").text
    assert 'id="copies"' in html and "Количество экземпляров</label>" in html
    js = make_client().get(f"/static/js/app.js?v={ASSET_VERSION}").text
    assert "radios.length > 0" in js


# 9. Прогноз готовности: «к среде», «ко вторнику», честно про прошедший срок
def test_eta_is_grammatical_and_honest():
    assert by_day(date(2026, 11, 25)) == "к среде, 25 ноября"
    assert by_day(date(2026, 11, 24)) == "ко вторнику, 24 ноября"
    timeutil.freeze(datetime(2026, 11, 23, 10, 0))
    try:
        assert eta_for(datetime(2026, 11, 23, 9, 0), 2, "certificate").text == "Будет готово к среде, 25 ноября"
        assert eta_for(datetime(2026, 11, 20, 9, 0), 1, "certificate").text == "Будет готово сегодня"
        assert eta_for(datetime(2026, 11, 23, 9, 0), 1, "application").text == "Решение будет завтра, 24 ноября"
        late = eta_for(datetime(2026, 11, 2, 9, 0), 3, "certificate")
        assert late.overdue and late.text.startswith("Обычный срок прошел")
    finally:
        timeutil.freeze(None)


# 10. Частые вопросы не противоречат мгновенной справке
def test_faq_matches_instant_certificate(kira):
    html = kira.get("/messages").text
    assert "готова сразу после заказа" in html and "готова за 1 рабочий день, ее" not in html


# 11. Пустой ответ в переписке не выдается за отправленный (и у сотрудника, и у студента)
def test_blank_chat_message_shows_error(kira, staff):
    with SessionLocal() as db:
        before = db.query(Message).count()
    token = csrf(staff, "/staff/messages/1")
    r = staff.post("/staff/messages/1", data={"body": "   ", "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 400 and "Напишите текст ответа" in r.text
    token = csrf(kira, "/messages")
    r = kira.post("/messages", data={"body": "\n \t", "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 400 and "Напишите текст вопроса" in r.text
    with SessionLocal() as db:
        assert db.query(Message).count() == before


# 12. После выдачи документа не пишем «Где забрать»
def test_issued_document_has_no_pickup_hint(timur, staff):
    with SessionLocal() as db:
        rid = db.scalar(select(StudentRequest.id).where(StudentRequest.type_code == "card"))
    token = csrf(staff, f"/staff/requests/{rid}")
    for st in ("in_progress", "ready", "issued"):
        staff.post(f"/staff/requests/{rid}/status", data={"new_status": st, "comment": "", "csrf_token": token})
    html = timur.get(f"/requests/{rid}").text
    assert "Где забрать" not in html and "Документ выдан в деканате" in html


# 13. Повторный перенос на тот же слот не считается конфликтом с самим собой
def test_repeat_move_to_same_slot(staff):
    with SessionLocal() as db:
        c = db.scalar(select(LessonChange).where(LessonChange.change_type == "moved",
                                                 LessonChange.date > timeutil.today()))
        if c is None:
            return  # в этот день демо-данных переносов вперед нет (например, сессия)
        t = db.get(LessonTemplate, c.template_id)
        new_date, new_pair, day = c.new_date, c.new_pair_no, c.date
    token = csrf(staff, "/staff/schedule")
    r = staff.post("/staff/schedule", data={"template_id": t.id, "day": day.isoformat(), "change_type": "moved",
                                            "new_date": new_date.isoformat(), "new_pair_no": new_pair,
                                            "note": "Уточнили причину", "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]


# 15. Страница проверки объясняет, почему демо-справка могла пропасть
def test_verify_explains_demo_reset():
    html = make_client().get("/verify/0000000000000000").text
    assert "данные обновляются каждый день" in html


# 17. X-Forwarded-Host от постороннего клиента не расширяет список своих сайтов
def test_forwarded_host_trusted_only_from_proxy(kira):
    token = csrf(kira, "/messages")
    direct = kira.post("/messages", data={"body": "x", "csrf_token": token},
                       headers={"origin": "https://evil.example", "x-forwarded-host": "evil.example",
                                "x-test-peer": "8.8.8.8"}, follow_redirects=False)
    assert direct.status_code == 403
    via_proxy = kira.post("/messages", data={"body": "Через прокси", "csrf_token": token},
                          headers={"origin": "https://dekanat.example", "x-forwarded-host": "dekanat.example"},
                          follow_redirects=False)
    assert via_proxy.status_code == 303


# 19. Блокировка демо-аккаунта подсказывает войти под другим
def test_demo_lockout_suggests_other_account():
    c = make_client()
    for _ in range(5):
        c.post("/login", data={"login": "kira", "password": "wrong"})
    r = c.post("/login", data={"login": "kira", "password": "Kira-2026"})
    assert "другим тестовым аккаунтом" in r.text
    assert c.post("/login", data={"login": "timur", "password": "Timur-2026"}, follow_redirects=False).status_code == 303


# Версия оформления считается по содержимому: забыть поднять ее невозможно
def test_asset_version_follows_file_contents():
    assert ASSET_VERSION == asset_version()
    sw = make_client().get("/sw.js").text
    assert f'var V = "{ASSET_VERSION}"' in sw and "__ASSET_VERSION__" not in sw
    html = make_client().get("/login").text
    assert f"app.css?v={ASSET_VERSION}" in html


# Страница 500 тоже приходит с заголовками безопасности
def test_server_error_page_has_security_headers():
    from fastapi.testclient import TestClient

    def boom():
        raise RuntimeError("проверка")

    app.add_api_route("/__boom", boom)
    try:
        r = TestClient(app, raise_server_exceptions=False).get("/__boom")
    finally:
        app.router.routes = [rt for rt in app.router.routes if getattr(rt, "path", "") != "/__boom"]
    assert r.status_code == 500
    assert "default-src 'self'" in r.headers.get("content-security-policy", "")
    assert r.headers.get("x-frame-options") == "DENY"


# Диагностика для настройки за прокси: /healthz показывает, какой адрес и схему видит приложение
def test_healthz_shows_what_app_sees_behind_proxy():
    c = make_client()
    assert c.get("/healthz", headers={"x-forwarded-for": "203.0.113.7", "x-forwarded-proto": "https"}).json() == {
        "status": "ok", "your_ip": "203.0.113.7", "https": True}
    direct = c.get("/healthz", headers={"x-forwarded-for": "1.2.3.4", "x-test-peer": "8.8.8.8"}).json()
    assert direct["your_ip"] == "8.8.8.8"  # прямому клиенту подделать адрес нельзя
    spoofed = c.get("/healthz", headers={"x-forwarded-proto": "https", "x-test-peer": "8.8.8.8"}).json()
    assert spoofed["https"] is False  # и схему тоже

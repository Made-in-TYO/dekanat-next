"""Проверки целых классов ошибок во всех формах сразу (третья независимая проверка).

Обычные тесты проверяют конкретные случаи, а ломается то, что между ними. Здесь обходчик собирает
все формы, до которых можно дойти кликами под каждой ролью, и:
1) отправляет каждую с «злыми» значениями в каждом поле: пусто, пробелы, 5000 символов, HTML, эмодзи,
   огромные и отрицательные числа, неверные даты. Ни одна отправка не должна дать ошибку сервера;
2) проверяет, что после ошибки в форме сохраняется выбор пользователя (радиокнопки, списки, текст).
Новая форма в кабинете попадает в эти проверки автоматически.
"""
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import pytest

from app.security import reset_login_limits
from app.seed import init_db
from app.services import timeutil
from conftest import csrf, login, make_client
from formkit import form_key, parse_forms
from test_crawl import crawl


@pytest.fixture(autouse=True)
def school_monday():
    """Проверки не зависят от дня запуска: «сейчас» понедельник учебной недели, 9:00."""
    timeutil.freeze(datetime(2026, 11, 23, 9, 0))
    init_db(reset=True)
    reset_login_limits()
    yield
    timeutil.freeze(None)

EVIL = [
    "", "   ", "x" * 5000, "<script>alert(1)</script><b>жирный</b> 😀 ‮", "9" * 40, "-1", "0",
    "2026-02-30", "../../../etc/passwd", "строка\x00с нулем", "'; DROP TABLE users; --",
]
SKIP_ACTIONS = ("/logout", "/demo/switch")
ROLES = [("kira", "Kira-2026", "/home"), ("timur", "Timur-2026", "/home"), ("dekanat", "Dekanat-2026", "/staff")]
MIN_FORMS = {"kira": 6, "timur": 6, "dekanat": 4}  # защита от того, что обходчик перестал находить формы


def _get_form_urls(f, base_day: date) -> list[str]:
    """Как пользователь пользуется фильтром или выбором даты: каждый вариант списка и даты на неделю вперед."""
    selects = f.selects()
    dates = [t.name for t in f.text_fields() if t.type == "date"]
    urls = []
    for name, options in (selects.items() or [(None, [(None, True)])]):
        for value, _ in options:
            for i in range(8 if dates else 1):
                params = f.defaults()
                if name:
                    params[name] = value
                for dn in dates:
                    params[dn] = (base_day + timedelta(days=i)).isoformat()
                urls.append(f"{f.action}?{urlencode(params)}")
    return urls


def collect_forms(client, start: str, limit: int = 250) -> list:
    """Все уникальные POST-формы на страницах, до которых можно дойти по ссылкам и через GET-формы (фильтры, даты)."""
    forms, seen, pages = [], set(), set()
    urls = [u for u, status in crawl(client, start, limit=limit).items() if status == 200]
    while urls:
        url = urls.pop(0)
        if url in pages:
            continue
        pages.add(url)
        r = client.get(url)
        if "text/html" not in r.headers.get("content-type", ""):
            continue
        for f in parse_forms(r.text):
            if f.method == "get" and f.action and len(pages) < limit + 100:
                urls.extend(u for u in _get_form_urls(f, timeutil.today()) if u not in pages)
                continue
            if f.method != "post" or not f.action or f.action.startswith(SKIP_ACTIONS):
                continue
            k = form_key(f)
            if k not in seen:
                seen.add(k)
                forms.append(f)
    return forms


@pytest.mark.parametrize("login_, password, start", ROLES)
def test_every_form_survives_evil_input(login_, password, start):
    c = login(login_, password)
    forms = collect_forms(c, start)
    assert len(forms) >= MIN_FORMS[login_], [f.action for f in forms]
    token = csrf(c, start)
    errors = []
    for f in forms:
        for name in f.names():
            if name == "csrf_token":
                continue
            for value in EVIL:
                data = f.defaults()
                data["csrf_token"] = token
                data[name] = value
                r = c.post(f.action, data=data, follow_redirects=False)
                if r.status_code >= 500:
                    errors.append((f.action, name, value[:30], r.status_code))
    assert not errors, errors


def test_public_forms_survive_evil_input():
    c = make_client()
    r = c.get("/login")
    for f in parse_forms(r.text):
        for name in f.names():
            for value in EVIL:
                data = f.defaults()
                data[name] = value
                assert c.post(f.action, data=data, follow_redirects=False).status_code < 500, (name, value[:30])


def _same_form(html: str, f):
    """Та же форма в ответе: тот же адрес и метод, те же скрытые поля (например, номер занятия и дата)."""
    hidden = {x.name: x.value for x in f.fields if x.type == "hidden" and x.name != "csrf_token"}
    for g in parse_forms(html):
        if g.action == f.action and g.method == f.method:
            if all(g.defaults().get(k) == v for k, v in hidden.items()):
                return g
    return None


@pytest.mark.parametrize("login_, password, start", ROLES)
def test_choice_is_kept_after_form_error(login_, password, start):
    """Выбор в радиокнопках и списках, а также введенный текст не сбрасываются, если форма вернулась с ошибкой."""
    c = login(login_, password)
    forms = collect_forms(c, start)
    token = csrf(c, start)
    checked = 0
    problems = []
    for f in forms:
        choices = {**f.radios(), **f.selects()}
        texts = [t.name for t in f.text_fields() if t.name != "csrf_token"]
        # 1) каждый вариант каждого выбора с пустыми текстовыми полями (обычная причина ошибки)
        for name, options in choices.items():
            for value, _ in options:
                data = f.defaults()
                data.update({t: "" for t in texts})
                data.update(csrf_token=token, **{name: value})
                r = c.post(f.action, data=data, follow_redirects=False)
                g = _same_form(r.text, f) if r.status_code in (200, 400) else None
                if g is not None:
                    checked += 1
                    if g.chosen(name) != value:
                        problems.append((f.action, name, value, g.chosen(name)))
        # 2) введенный текст сохраняется, когда ошибка в другом поле
        for empty in texts:
            data = f.defaults()
            data.update({t: "Проверка 123" for t in texts if t != empty})
            data.update(csrf_token=token, **{empty: ""})
            if any(t.type == "password" for t in f.fields):
                continue  # пароли после ошибки намеренно не показываем заново
            r = c.post(f.action, data=data, follow_redirects=False)
            g = _same_form(r.text, f) if r.status_code in (200, 400) else None
            if g is not None:
                for t in texts:
                    if t != empty and g.text_value(t) is not None and g.text_value(t) != "Проверка 123":
                        problems.append((f.action, "текст", t, g.text_value(t)))
                checked += 1
    assert not problems, problems
    assert checked > 0

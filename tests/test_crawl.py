"""Обходчик ссылок и линтер: защита от регрессий вида «страница падает с ошибкой 500».

Вторая независимая проверка нашла, что после исправлений фильтры заявок у сотрудника отдавали 500:
обычные тесты не открывали эти ссылки. Этот тест под каждой ролью обходит все внутренние ссылки,
до которых можно дойти кликами, и требует, чтобы ни одна не вернула ошибку сервера.
"""
import pathlib
import re
import subprocess
import sys
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

import pytest

from conftest import login

LINK = re.compile(r'href="([^"]+)"')
SKIP = ("/static/", "/logout", "/sw.js", "/manifest.webmanifest")


def crawl(client, start: str, limit: int = 400) -> dict[str, int]:
    seen: dict[str, int] = {}
    queue = deque([start])
    while queue and len(seen) < limit:
        url = queue.popleft()
        if url in seen:
            continue
        r = client.get(url, follow_redirects=True)
        seen[url] = r.status_code
        if "text/html" not in r.headers.get("content-type", ""):
            continue
        for href in LINK.findall(r.text):
            href = urldefrag(href.replace("&amp;", "&"))[0]
            if not href or href.startswith(("http", "mailto:", "tel:", "#", "//")) or href.startswith(SKIP):
                continue
            full = urljoin(url, href)
            path = urlparse(full).path + (("?" + urlparse(full).query) if urlparse(full).query else "")
            if path not in seen:
                queue.append(path)
    return seen


@pytest.mark.parametrize("login_, password, start", [
    ("kira", "Kira-2026", "/home"),
    ("timur", "Timur-2026", "/home"),
    ("dekanat", "Dekanat-2026", "/staff"),
])
def test_no_server_errors_on_any_reachable_page(login_, password, start):
    c = login(login_, password)
    pages = crawl(c, start)
    assert len(pages) > 15, pages
    errors = {u: s for u, s in pages.items() if s >= 500}
    assert not errors, errors


def test_public_pages_have_no_server_errors():
    from conftest import make_client
    pages = crawl(make_client(), "/login")
    assert all(s < 500 for s in pages.values()), pages


def test_code_has_no_undefined_names_or_unused_imports():
    """pyflakes: неопределенные имена (как удаленный импорт STATUS) ловятся до запуска."""
    pytest.importorskip("pyflakes")
    root = pathlib.Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-m", "pyflakes", "app", "tests", "scripts"], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

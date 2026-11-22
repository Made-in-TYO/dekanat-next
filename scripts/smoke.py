"""Смоук-тест по боевому адресу: python scripts/smoke.py https://адрес-сайта

Проверяет то, что локальные тесты проверить не могут, потому что зависит от прокси хостинга:
- сайт отвечает, заголовки безопасности на месте, HTTPS виден приложению;
- адрес посетителя определяется правильно (не внутренний адрес прокси), иначе неудачные входы
  одного человека блокировали бы всех (настройка TRUSTED_PROXY_HOPS);
- под тремя тестовыми аккаунтами все страницы открываются без ошибок сервера;
- ссылка в QR-коде электронной справки начинается с https и ведет на действующую проверку.
Ничего не создает и не отправляет, кроме входа и выхода. Но открытие страниц, как у живого посетителя, отмечает
переписку прочитанной, создает напоминания о дедлайнах и может запустить сброс демо после простоя.
Нужен httpx (есть в requirements-dev.txt).
"""
import ipaddress
import re
import sys
from collections import deque
from urllib.parse import urlparse

import httpx

ACCOUNTS = [("kira", "Kira-2026", "/home"), ("timur", "Timur-2026", "/home"), ("dekanat", "Dekanat-2026", "/staff")]
problems: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        problems.append(what)


def crawl(c: httpx.Client, base: str, start: str, limit: int = 60) -> dict[str, int]:
    seen: dict[str, int] = {}
    queue = deque([start])
    while queue and len(seen) < limit:
        path = queue.popleft()
        if path in seen:
            continue
        r = c.get(base + path)
        seen[path] = r.status_code
        if "text/html" not in r.headers.get("content-type", ""):
            continue
        for href in re.findall(r'href="([^"]+)"', r.text):
            href = href.replace("&amp;", "&").split("#")[0]
            if not href.startswith("/") or href.startswith(("//", "/static/", "/logout")) or ".ics" in href:
                continue
            if href not in seen:
                queue.append(href)
    return seen


def main(base: str) -> int:
    base = base.rstrip("/")
    https = base.startswith("https://")
    with httpx.Client(timeout=20, follow_redirects=True) as c:
        print("Сайт и заголовки")
        h = c.get(base + "/healthz")
        check(h.status_code == 200, f"/healthz отвечает ({h.status_code})")
        info = h.json() if h.status_code == 200 else {}
        ip = info.get("your_ip", "")
        try:
            internal = ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback
        except ValueError:
            internal = True
        check(not internal, f"приложение видит ваш внешний адрес, а не адрес прокси: {ip}")
        if https:
            check(info.get("https") is True, "приложение понимает, что запрос пришел по HTTPS")
        r = c.get(base + "/login")
        for name in ("content-security-policy", "x-frame-options", "x-content-type-options"):
            check(name in r.headers, f"заголовок {name}")
        if https:
            check("strict-transport-security" in r.headers, "заголовок strict-transport-security")
    for login, password, start in ACCOUNTS:
        print(f"Аккаунт {login}")
        with httpx.Client(timeout=20, follow_redirects=True) as c:
            r = c.post(base + "/login", data={"login": login, "password": password})
            check(urlparse(str(r.url)).path == start, f"вход открывает {start}")
            pages = crawl(c, base, start)
            bad = {p: s for p, s in pages.items() if s >= 500}
            check(len(pages) > 10 and not bad, f"открыто {len(pages)} страниц, ошибок сервера: {len(bad)} {bad or ''}")
            if login == "kira":
                links = re.findall(r'href="(/requests/\d+/document)"', "".join(c.get(base + p).text for p in
                                   [p for p in pages if re.fullmatch(r"/requests/\d+", p)]))
                if links:
                    doc = c.get(base + links[0]).text
                    m = re.search(r'(https?://[^"<\s]+/verify/[0-9a-f]+)', doc)
                    check(bool(m), "на справке есть ссылка проверки")
                    if m:
                        check(m.group(1).startswith("https://") or not https, f"ссылка в QR: {m.group(1)}")
                        check("Справка действительна" in c.get(m.group(1)).text, "проверка по QR подтверждает справку")
                else:
                    check(False, "у Киры есть готовая электронная справка")
            tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get(base + start).text)
            c.post(base + "/logout", data={"csrf_token": tok.group(1) if tok else ""})
    print("\nИтог:", "все проверки пройдены" if not problems else f"проблем: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

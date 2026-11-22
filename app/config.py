"""Настройки приложения. Все секреты и пути берутся из переменных окружения."""
import os
from zoneinfo import ZoneInfo

# Часовой пояс вуза: все "сегодня", "сейчас" и расписание считаются по Москве/Петербургу,
# независимо от часового пояса сервера.
TZ = ZoneInfo("Europe/Moscow")

# Строка подключения к базе. По умолчанию SQLite-файл в папке data/.
# Для масштабирования достаточно указать PostgreSQL:
#   DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dekanat
# На хостинге Amvera постоянное хранилище подключено в /data (см. amvera.yml): база переживает перезапуски.
_default_data = "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else \
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DATA_DIR = os.environ.get("DATA_DIR", _default_data)
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(DATA_DIR, 'dekanat.db')}")

# Cookie с флагом Secure: включается автоматически, если сайт открыт по HTTPS (или принудительно переменной).
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
# Дополнительные доверенные адреса сайта для проверки Origin (через запятую), если хостинг меняет заголовок Host
TRUSTED_HOSTS = {h.strip() for h in os.environ.get("TRUSTED_HOSTS", "").split(",") if h.strip()}
# Сколько прокси хостинга дописывают адрес в X-Forwarded-For (адрес клиента берется на этой позиции справа)
TRUSTED_PROXY_HOPS = max(1, int(os.environ.get("TRUSTED_PROXY_HOPS", "1")))
# Публичный адрес сайта для ссылок в QR-кодах (например, https://dekanat.example). Если пусто, берется из запроса.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
SESSION_COOKIE = "dn_session"
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "12"))

# Защита от подбора пароля. Блокируется пара "логин + IP": посторонний не может
# заблокировать общий демо-аккаунт для всех, а перебор с одного адреса останавливается.
MAX_FAILED_LOGINS = 5          # неудачных попыток подряд для пары логин + IP
LOCK_MINUTES = 5               # на сколько блокируем вход
IP_MAX_FAILS = 30              # неудачных попыток с одного IP по любым логинам
IP_WINDOW_MINUTES = 10

# Учебный календарь концепта (вымышленный): начало занятий, конец занятий, праздники.
# От начала семестра считается номер учебной недели и четность. После конца занятий идет сессия.
SEMESTER_START = os.environ.get("SEMESTER_START", "2026-09-01")
SEMESTER_END = os.environ.get("SEMESTER_END", "2026-12-26")
HOLIDAYS = {"2026-11-04": "День народного единства",
            **{f"2027-01-0{d}": "Новогодние каникулы" for d in range(1, 9)}}

# Демо-режим: тестовые аккаунты общие для всех посетителей, поэтому база с демо-данными
# пересоздается каждый день (по московскому времени), а даты в ней считаются от сегодняшнего дня.
DEMO_DAILY_RESET = os.environ.get("DEMO_DAILY_RESET", "1") == "1"
# После скольких минут без посетителей демо-данные сбрасываются (0 = только раз в сутки)
DEMO_IDLE_RESET_MINUTES = int(os.environ.get("DEMO_IDLE_RESET_MINUTES", "30"))

# Допустимый диапазон дат в адресах (защита от ошибок на заведомо неверных датах)
DATE_RANGE_DAYS = 730

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
SESSION_COOKIE = "dn_session"
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "12"))

# Защита от подбора пароля
MAX_FAILED_LOGINS = 5          # неудачных попыток подряд для одного логина
LOCK_MINUTES = 5               # на сколько блокируем вход
IP_MAX_FAILS = 20              # неудачных попыток с одного IP
IP_WINDOW_MINUTES = 10

# Начало осеннего семестра: от него считается номер учебной недели и четность.
SEMESTER_START = os.environ.get("SEMESTER_START", "2026-09-01")

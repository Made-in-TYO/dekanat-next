"""Шаблоны и общий контекст страниц."""
import hashlib
import os
from datetime import datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import config
from .services import timeutil
from .services.catalog import FORMAT_LABELS, TYPES, eta_for, is_instant, status_info, status_label, track_labels
from .services.notify import unread_count

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
env = templates.env
env.autoescape = True

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
VERSIONED_ASSETS = ("css/app.css", "js/app.js", "js/theme.js", "js/sw.js", "icons/sprite.svg", "img/logo.svg")


def asset_version() -> str:
    """Версия оформления считается по содержимому файлов: после любой правки CSS или JS браузеры
    и service worker сами возьмут новые файлы, вручную поднимать номер не нужно."""
    h = hashlib.sha256()
    for rel in VERSIONED_ASSETS:
        with open(os.path.join(STATIC_DIR, rel), "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:10]


ASSET_VERSION = asset_version()


def icon(name: str, cls: str = "ic") -> Markup:
    # name берется только из шаблонов (не из пользовательского ввода)
    return Markup(f'<svg class="{cls}" aria-hidden="true" focusable="false">'
                  f'<use href="/static/icons/sprite.svg?v={ASSET_VERSION}#i-{name}"></use></svg>')


def _local(dt: datetime | None) -> datetime | None:
    return timeutil.utc_to_local(dt)


def _ago(dt_utc: datetime | None) -> str:
    if dt_utc is None:
        return ""
    dt = timeutil.utc_to_local(dt_utc)
    delta = timeutil.now_local() - dt
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "только что"
    if mins < 60:
        return f"{mins} мин назад"
    hours = mins // 60
    if hours < 24 and dt.date() == timeutil.today():
        return f"{hours} {timeutil.plural(hours, 'час', 'часа', 'часов')} назад"
    return timeutil.fmt_dt(dt)


env.globals.update(demo_idle_minutes=config.DEMO_IDLE_RESET_MINUTES, demo_daily_reset=config.DEMO_DAILY_RESET, week_label=timeutil.week_label, greeting=timeutil.greeting, icon=icon, TYPES=TYPES, FORMAT_LABELS=FORMAT_LABELS, status_label=status_label, status_info=status_info,
                   track_labels=track_labels, eta_for=eta_for, is_instant=is_instant,
                   asset_v=ASSET_VERSION, plural=timeutil.plural)
env.filters.update(
    local=_local,
    ago=_ago,
    fdate=lambda d, **kw: timeutil.fmt_date(d, **kw) if d else "",
    human_day=timeutil.human_day,
    fdt=lambda dt: timeutil.fmt_dt(dt) if dt else "",
    until=timeutil.until_text,
    hm=lambda t: t.strftime("%H:%M"),
    num=lambda x: (f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",") if isinstance(x, float) else str(x)),
)


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200, db=None):
    ctx = dict(ctx or {})
    auth = getattr(request.state, "auth", None)
    ctx.setdefault("request", request)
    ctx["auth"] = auth
    ctx["user"] = auth.user if auth else None
    ctx["csrf"] = auth.csrf_token if auth else ""
    ctx["today"] = timeutil.today()
    back = request.cookies.get("dn_demo_back", "kira")
    ctx["demo_back"] = back if back in ("kira", "timur") else "kira"
    if auth and db is not None and "unread" not in ctx:
        ctx["unread"] = unread_count(db, auth.user_id)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


# Сообщения после действий: только фиксированные тексты по ключу (никакого отражения ввода в URL)
FLASH = {
    "created": "Заявка отправлена в деканат. Статус будет обновляться здесь и в уведомлениях",
    "instant": "Справка готова: данные взяты из учетной системы, очередь не понадобилась",
    "cancelled": "Заявка отменена",
    "replied": "Ответ отправлен в деканат",
    "password": "Пароль изменен. Остальные сеансы завершены",
    "sent": "Сообщение отправлено",
    "status": "Статус заявки обновлен, студент получил уведомление",
    "announced": "Объявление опубликовано и разослано студентам",
    "change": "Изменение в расписании сохранено, группа получила уведомление",
    "reverted": "Занятие возвращено к обычному расписанию, группа получила уведомление",
    "readall": "Все уведомления прочитаны",
}


def flash_text(request: Request) -> str | None:
    return FLASH.get(request.query_params.get("ok", ""))


env.globals["flash_text"] = flash_text

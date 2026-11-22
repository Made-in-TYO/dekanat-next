"""Деканат Next: концепт личного кабинета студента для конкурса MGIMO AI Challenge.

Запуск локально:  uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .routes import auth, public, staff, student
from .security import LoginRequired, WrongRole, _is_internal, is_https
from .seed import ensure_demo_fresh, init_db
from .web import render

log = logging.getLogger("dekanat")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


# Документация API отключена: в продакшене она не нужна и раскрывала бы внутреннее устройство
app = FastAPI(title="Деканат Next", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class CachedStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp


app.mount("/static", CachedStatic(directory=__file__.rsplit("/", 1)[0] + "/static"), name="static")

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; font-src 'self'; "
       "connect-src 'self'; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
       "form-action 'self'; object-src 'none'")


def _allowed_hosts(request: Request) -> set[str]:
    """Адреса нашего сайта. X-Forwarded-Host учитываем, только если запрос пришел от прокси хостинга."""
    hosts = {(request.headers.get("host") or "").split(":")[0]} | config.TRUSTED_HOSTS
    peer = request.client.host if request.client else ""
    if _is_internal(peer) and request.headers.get("x-forwarded-host"):
        hosts.add(request.headers["x-forwarded-host"].split(",")[0].strip().split(":")[0])
    return hosts


def apply_security_headers(request: Request, response) -> None:
    h = response.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if is_https(request):
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    # Личные страницы не кешируем в браузере и прокси
    if not request.url.path.startswith("/static"):
        h.setdefault("Cache-Control", "no-store")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Демо-данные: раз в сутки и после простоя база пересоздается. Активностью считаются только
    # запросы посетителей с сеансом, а не проверки мониторинга. Работа с базой идет вне цикла событий.
    if not request.url.path.startswith("/static"):
        await run_in_threadpool(ensure_demo_fresh, config.SESSION_COOKIE in request.cookies)
    # Запросы, меняющие данные, принимаем только со своего сайта (защита от CSRF вдобавок к токену в формах)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:  # "null" тоже чужой: так браузер помечает запросы из песочниц и после перенаправлений
            if urlparse(origin).hostname not in _allowed_hosts(request):
                return PlainTextResponse("Запрос с чужого сайта отклонен", status_code=403)
    response = await call_next(request)
    apply_security_headers(request, response)
    return response


@app.exception_handler(WrongRole)
async def wrong_role_handler(_request: Request, exc: WrongRole):
    return RedirectResponse(exc.home, status_code=303)


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, _exc: LoginRequired):
    nxt = request.url.path
    if request.url.query:
        nxt += "?" + request.url.query
    if request.method != "GET":
        nxt = "/"
    return RedirectResponse(f"/login?next={quote(nxt, safe='/')}" if nxt not in ("/", "/home") else "/login", status_code=303)


ERROR_TEXT = {
    400: ("Не получилось обработать запрос", "Проверьте данные в форме и попробуйте еще раз."),
    403: ("Нет доступа", "Эта страница вам недоступна. Если вы отправляли форму, обновите страницу и повторите действие."),
    404: ("Страница не найдена", "Такой страницы нет или у вас нет к ней доступа."),
    409: ("Действие уже недоступно", "Статус изменился, пока страница была открыта. Обновите ее."),
    405: ("Действие недоступно", "Так открыть эту страницу нельзя."),
}


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    title, text = ERROR_TEXT.get(exc.status_code, ("Что-то пошло не так", "Попробуйте обновить страницу."))
    return render(request, "error.html", {"code": exc.status_code, "title": title, "text": text},
                  status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Неверный номер в адресе (не число, слишком большое) значит "такой страницы нет"
    if any(err.get("loc", ("",))[0] == "path" for err in exc.errors()):
        title, text = ERROR_TEXT[404]
        return render(request, "error.html", {"code": 404, "title": title, "text": text}, status_code=404)
    title, text = ERROR_TEXT[400]
    return render(request, "error.html", {"code": 400, "title": title, "text": text}, status_code=400)


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    log.exception("Unhandled error", exc_info=exc)
    response = render(request, "error.html", {"code": 500, "title": "Ошибка на сервере",
                                              "text": "Мы уже видим ее в журнале. Попробуйте обновить страницу через минуту."},
                      status_code=500)
    # Страница 500 формируется в обход middleware, поэтому заголовки безопасности ставим здесь
    apply_security_headers(request, response)
    return response


app.include_router(public.router)
app.include_router(auth.router)
app.include_router(student.router)
app.include_router(staff.router)

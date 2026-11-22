"""Деканат Next: концепт личного кабинета студента для конкурса MGIMO AI Challenge.

Запуск локально:  uvicorn app.main:app --reload
"""
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .routes import auth, public, staff, student
from .security import LoginRequired
from .seed import init_db
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


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Запросы, меняющие данные, принимаем только со своего сайта (защита от CSRF вдобавок к токену в формах)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin != "null":
            allowed = {(request.headers.get(h) or "").split(":")[0] for h in ("host", "x-forwarded-host")} | config.TRUSTED_HOSTS
            if urlparse(origin).hostname not in allowed:
                return PlainTextResponse("Запрос с чужого сайта отклонен", status_code=403)
    response = await call_next(request)
    h = response.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if config.COOKIE_SECURE or request.url.scheme == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    # Личные страницы не кешируем в браузере и прокси
    if not request.url.path.startswith("/static"):
        h.setdefault("Cache-Control", "no-store")
    return response


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
async def validation_error(request: Request, _exc: RequestValidationError):
    title, text = ERROR_TEXT[400]
    return render(request, "error.html", {"code": 400, "title": title, "text": text}, status_code=400)


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    log.exception("Unhandled error", exc_info=exc)
    return render(request, "error.html", {"code": 500, "title": "Ошибка на сервере",
                                          "text": "Мы уже видим ее в журнале. Попробуйте обновить страницу через минуту."},
                  status_code=500)


app.include_router(public.router)
app.include_router(auth.router)
app.include_router(student.router)
app.include_router(staff.router)

"""Вход и выход."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..models import User
from ..security import (authenticate, check_csrf, client_ip, create_session, destroy_session, get_session,
                        is_https)
from ..seed import DEMO_ACCOUNTS
from ..web import render

router = APIRouter()
DEMO_BACK_COOKIE = "dn_demo_back"


def _safe_next(nxt: str | None) -> str | None:
    """Разрешаем только внутренние адреса, чтобы ссылку входа нельзя было использовать для перенаправления на чужой сайт."""
    if nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    return None


def _home_for(role: str) -> str:
    return "/staff" if role == "staff" else "/home"


@router.get("/login")
def login_page(request: Request, next: str | None = None, db: Session = Depends(get_db)):
    s = get_session(db, request)
    if s:
        return RedirectResponse(_home_for(s.user.role), status_code=303)
    return render(request, "login.html", {"next": _safe_next(next) or "", "demo": DEMO_ACCOUNTS,
                                          "expired": request.query_params.get("expired") == "1"})


@router.post("/login")
def login_submit(request: Request, login: str = Form(""), password: str = Form(""), next: str = Form(""),
                 db: Session = Depends(get_db)):
    login = login.strip()[:64]
    password = password[:256]
    if not login or not password:
        return render(request, "login.html", {"error": "Введите логин и пароль", "login": login, "next": _safe_next(next) or "",
                                              "demo": DEMO_ACCOUNTS}, status_code=400)
    user, err = authenticate(db, login, password, client_ip(request))
    if err:
        return render(request, "login.html", {"error": err, "login": login, "next": _safe_next(next) or "",
                                              "demo": DEMO_ACCOUNTS}, status_code=401)
    token = create_session(db, user)
    target = _safe_next(next) or _home_for(user.role)
    if user.role == "student" and target.startswith("/staff"):
        target = "/home"
    if user.role == "staff" and not target.startswith("/staff"):
        target = "/staff"
    resp = RedirectResponse(target, status_code=303)
    _set_cookie(request, resp, token)
    return resp


def _set_cookie(request: Request, resp, token: str) -> None:
    resp.set_cookie(config.SESSION_COOKIE, token, httponly=True, secure=is_https(request), samesite="lax",
                    max_age=config.SESSION_TTL_HOURS * 3600, path="/")


@router.post("/demo/switch")
def demo_switch(request: Request, to: str = Form(...), csrf_token: str = Form(""), db: Session = Depends(get_db)):
    """Быстрое переключение между тестовыми аккаунтами для жюри: студент <-> деканат без выхода.
    Работает только между общими демо-аккаунтами и только из демо-аккаунта."""
    s = get_session(db, request)
    if s is None:
        return RedirectResponse("/login", status_code=303)
    check_csrf(s, csrf_token)
    target = db.scalar(select(User).where(User.login == to))
    if not s.user.is_demo or target is None or not target.is_demo:
        raise HTTPException(status_code=403)
    came_from = s.user
    destroy_session(db, request.cookies.get(config.SESSION_COOKIE))
    token = create_session(db, target)
    resp = RedirectResponse(_home_for(target.role), status_code=303)
    _set_cookie(request, resp, token)
    if came_from.role == "student":
        # Запоминаем, из какого студента пришли, чтобы кнопка "Кабинет студента" вернула туда же
        resp.set_cookie(DEMO_BACK_COOKIE, came_from.login, httponly=True, samesite="lax", secure=is_https(request),
                        max_age=config.SESSION_TTL_HOURS * 3600, path="/")
    return resp


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)):
    s = get_session(db, request)
    if s:
        check_csrf(s, csrf_token)
        destroy_session(db, request.cookies.get(config.SESSION_COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(config.SESSION_COOKIE, path="/")
    return resp

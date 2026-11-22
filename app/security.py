"""Безопасность: хеширование паролей, серверные сессии, CSRF, защита от подбора пароля."""
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import AuthSession, User
from .services.timeutil import utcnow

_ph = PasswordHasher()  # argon2id с параметрами по умолчанию (рекомендация OWASP)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# Хеш-заглушка, чтобы время ответа не выдавало, существует ли логин
_DUMMY_HASH = _ph.hash("dummy-password-for-timing")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- Ограничение попыток входа по IP (в памяти процесса) ----------
_ip_fails: dict[str, deque] = defaultdict(deque)
_ip_lock = threading.Lock()


def ip_blocked(ip: str) -> bool:
    window = config.IP_WINDOW_MINUTES * 60
    now = time.monotonic()
    with _ip_lock:
        q = _ip_fails[ip]
        while q and now - q[0] > window:
            q.popleft()
        return len(q) >= config.IP_MAX_FAILS


def ip_register_fail(ip: str) -> None:
    with _ip_lock:
        _ip_fails[ip].append(time.monotonic())


def reset_ip_limits() -> None:  # используется в тестах
    with _ip_lock:
        _ip_fails.clear()


def authenticate(db: Session, login: str, password: str, ip: str) -> tuple[User | None, str | None]:
    """Возвращает (пользователь, None) или (None, текст ошибки)."""
    generic = "Неверный логин или пароль"
    if ip_blocked(ip):
        return None, "Слишком много попыток входа. Подождите несколько минут и попробуйте снова"
    user = db.scalar(select(User).where(User.login == login.strip().lower()))
    now = utcnow()
    if user is None:
        verify_password(_DUMMY_HASH, password)
        ip_register_fail(ip)
        return None, generic
    if user.locked_until and user.locked_until > now:
        mins = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        return None, f"Вход временно заблокирован после нескольких неудачных попыток. Попробуйте через {mins} мин"
    if not verify_password(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= config.MAX_FAILED_LOGINS:
            user.locked_until = now + timedelta(minutes=config.LOCK_MINUTES)
            user.failed_logins = 0
        db.commit()
        ip_register_fail(ip)
        return None, generic
    user.failed_logins = 0
    user.locked_until = None
    if _ph.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    db.commit()
    return user, None


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        token_hash=_token_hash(token),
        csrf_token=secrets.token_urlsafe(32),
        user_id=user.id,
        expires_at=utcnow() + timedelta(hours=config.SESSION_TTL_HOURS),
    ))
    db.commit()
    return token


def destroy_session(db: Session, token: str | None) -> None:
    if not token:
        return
    s = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if s:
        db.delete(s)
        db.commit()


def destroy_other_sessions(db: Session, user_id: int, keep_token: str | None) -> None:
    keep = _token_hash(keep_token) if keep_token else None
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user_id)):
        if s.token_hash != keep:
            db.delete(s)
    db.commit()


def get_session(db: Session, request: Request) -> AuthSession | None:
    token = request.cookies.get(config.SESSION_COOKIE)
    if not token:
        return None
    s = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if s is None:
        return None
    if s.expires_at < utcnow():
        db.delete(s)
        db.commit()
        return None
    return s


class LoginRequired(Exception):
    """Перенаправить на страницу входа."""


def current_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    s = get_session(db, request)
    if s is None:
        raise LoginRequired()
    request.state.auth = s
    return s


def require_student(s: AuthSession = Depends(current_session), db: Session = Depends(get_db)) -> AuthSession:
    if s.user.role != "student" or s.user.student is None:
        raise HTTPException(status_code=403)
    from .services.notify import remind_deadlines
    remind_deadlines(db, s.user.student)
    return s


def require_staff(s: AuthSession = Depends(current_session)) -> AuthSession:
    if s.user.role != "staff":
        raise HTTPException(status_code=403)
    return s


def check_csrf(s: AuthSession, token: str | None) -> None:
    """Каждая форма, меняющая данные, несет секретный токен сессии."""
    if not token or not hmac.compare_digest(s.csrf_token, token):
        raise HTTPException(status_code=403, detail="csrf")


def client_ip(request: Request) -> str:
    # За прокси хостинга реальный адрес приходит в X-Forwarded-For (берем первый)
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

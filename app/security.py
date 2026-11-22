"""Безопасность: хеширование паролей, серверные сессии, CSRF, защита от подбора пароля."""
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

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


# ---------- Защита от подбора пароля (в памяти процесса) ----------
# Блокируем пару "логин + IP", а не логин целиком: иначе любой посетитель мог бы
# пятью неверными паролями закрыть общий демо-аккаунт для всего жюри.
# Одинаково работает для существующих и несуществующих логинов, поэтому по ответу
# нельзя узнать, есть ли такой пользователь.
_fails: dict[tuple[str, str], list] = defaultdict(list)   # (логин, ip) -> [счетчик, заблокирован_до]
_ip_fails: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def _ip_blocked(ip: str, now: float) -> bool:
    q = _ip_fails[ip]
    while q and now - q[0] > config.IP_WINDOW_MINUTES * 60:
        q.popleft()
    return len(q) >= config.IP_MAX_FAILS


def _lock_left(login: str, ip: str, now: float) -> int:
    """Сколько минут еще действует блокировка пары логин + IP (0, если не заблокирована)."""
    rec = _fails.get((login, ip))
    if rec and rec[1] > now:
        return max(1, int((rec[1] - now) // 60) + 1)
    return 0


def _register_fail(login: str, ip: str, now: float) -> None:
    _ip_fails[ip].append(now)
    rec = _fails[(login, ip)]
    if not rec:
        rec.extend([0, 0.0])
    rec[0] += 1
    if rec[0] >= config.MAX_FAILED_LOGINS:
        rec[0] = 0
        rec[1] = now + config.LOCK_MINUTES * 60


def reset_login_limits() -> None:  # используется в тестах и при сбросе демо
    with _lock:
        _fails.clear()
        _ip_fails.clear()


reset_ip_limits = reset_login_limits  # совместимость со старым именем


def authenticate(db: Session, login: str, password: str, ip: str) -> tuple[User | None, str | None]:
    """Возвращает (пользователь, None) или (None, текст ошибки)."""
    generic = "Неверный логин или пароль"
    login = login.strip().lower()
    now = time.monotonic()
    with _lock:
        if _ip_blocked(ip, now):
            return None, "Слишком много попыток входа с этого адреса. Подождите несколько минут и попробуйте снова"
        mins = _lock_left(login, ip, now)
    if mins:
        return None, f"Вход временно заблокирован после нескольких неудачных попыток. Попробуйте через {mins} мин"
    user = db.scalar(select(User).where(User.login == login))
    ok = verify_password(user.password_hash if user else _DUMMY_HASH, password) and user is not None
    if not ok:
        with _lock:
            _register_fail(login, ip, now)
        return None, generic
    with _lock:
        _fails.pop((login, ip), None)
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
    if not token or not hmac.compare_digest(s.csrf_token.encode(), token.encode("utf-8", "replace")):
        raise HTTPException(status_code=403, detail="csrf")


def client_ip(request: Request) -> str:
    """Адрес клиента. За прокси хостинга он приходит в X-Forwarded-For.
    Берем самый правый адрес: его дописывает сам прокси, а левые части клиент может подделать."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


def is_https(request: Request) -> bool:
    return (config.COOKIE_SECURE or request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "").split(",")[-1].strip() == "https")

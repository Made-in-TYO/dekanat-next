"""Публичные страницы: проверка справки по QR, о концепте, файлы приложения для телефона."""
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import Student, StudentRequest
from ..security import get_session
from ..services.catalog import TYPES
from ..web import render

router = APIRouter()
STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


@router.api_route("/", methods=["GET", "HEAD"])
def root(request: Request, db: Session = Depends(get_db)):
    s = get_session(db, request)
    if s is None:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/staff" if s.user.role == "staff" else "/home", status_code=303)


def _mask_name(full_name: str) -> str:
    """Для публичной проверки показываем только фамилию с маской и инициалы."""
    parts = full_name.split()
    if not parts:
        return ""
    last = parts[0]
    masked = last[0] + "*" * (len(last) - 1)
    initials = " ".join(p[0] + "." for p in parts[1:3])
    return f"{masked} {initials}".strip()


@router.get("/verify/{code}")
def verify(request: Request, code: str, db: Session = Depends(get_db)):
    r = None
    code = code.strip().lower()
    if code.isalnum() and len(code) <= 32:
        r = db.scalar(select(StudentRequest).options(selectinload(StudentRequest.student).selectinload(Student.user))
                      .where(StudentRequest.verify_code == code))
    ok = (r is not None and r.status in ("ready", "issued") and r.fmt == "electronic"
          and TYPES[r.type_code].kind == "certificate")
    ctx = {"ok": ok}
    if ok:
        ctx.update({"number": r.number, "title": TYPES[r.type_code].title, "issued": r.issued_at,
                    "holder": _mask_name(r.student.user.full_name)})
    return render(request, "verify.html", ctx, status_code=200 if ok else 404)


@router.get("/about")
def about(request: Request, db: Session = Depends(get_db)):
    s = get_session(db, request)
    request.state.auth = s
    return render(request, "about.html", {"nav": "about"}, db=db)


@router.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(STATIC, "manifest.webmanifest"), media_type="application/manifest+json")


@router.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(STATIC, "js", "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@router.get("/offline")
def offline(request: Request):
    return render(request, "offline.html", {})


@router.api_route("/healthz", methods=["GET", "HEAD"])
def healthz():
    return JSONResponse({"status": "ok"})

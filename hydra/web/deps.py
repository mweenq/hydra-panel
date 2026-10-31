"""Авторизация для HTML-роутов."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse

from .. import security


def current_admin(request: Request) -> dict | None:
    return security.get_session(request.cookies.get("hydra_session"))


def require_admin(request: Request):
    """Возвращает dict админа или RedirectResponse на /login."""
    admin = current_admin(request)
    if admin:
        return admin
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

"""Шаблоны и общие фильтры (отдельный модуль, чтобы не было циклических импортов)."""
from __future__ import annotations

from fastapi.templating import Jinja2Templates

from .. import config, db
from ..services import clients as clients_svc


def _build() -> Jinja2Templates:
    t = Jinja2Templates(directory=str(config.TEMPLATE_DIR))
    t.env.globals["version"] = config.VERSION
    t.env.globals["app_name"] = config.APP_NAME
    t.env.filters["bytes"] = clients_svc.human_bytes

    def settings():
        return db.all_settings()

    def nav_counts():
        return {
            "clients": db.q1("SELECT COUNT(*) AS n FROM clients")["n"],
            "nodes": db.q1("SELECT COUNT(*) AS n FROM nodes")["n"],
            "inbounds": db.q1("SELECT COUNT(*) AS n FROM inbounds")["n"],
        }

    t.env.globals["settings"] = settings
    t.env.globals["nav_counts"] = nav_counts
    return t


TEMPLATES = _build()

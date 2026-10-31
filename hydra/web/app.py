"""Сборка FastAPI-приложения."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config, db, security
from ..services import nodes as nodes_svc
from ..services import traffic
from . import api, node_api, pub, ui
from .templating import TEMPLATES

log = logging.getLogger("hydra")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    security.ensure_ca()
    nodes_svc.start_worker()
    traffic.start()
    from ..services import backup as backup_svc

    backup_svc.start_scheduler()
    from ..services import notify as notify_svc

    notify_svc.start_scheduler()
    try:
        from ..bots.runner import start_bots

        start_bots()
    except Exception as e:  # noqa: BLE001
        log.warning("bots not started: %s", e)
    log.info("Hydra %s up", config.VERSION)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=config.APP_NAME, version=config.VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
    app.state.templates = TEMPLATES

    app.include_router(ui.router)
    app.include_router(api.router)
    app.include_router(pub.router)
    app.include_router(node_api.router)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):  # noqa: ANN001
        if request.url.path.startswith(("/api/", "/sub/", "/v1/")):
            return JSONResponse({"error": "not found"}, status_code=404)
        return TEMPLATES.TemplateResponse(
            request, "404.html", {"request": request}, status_code=404
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    return app

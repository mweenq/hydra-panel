"""JSON API панели (для ботов, скриптов и динамики в UI)."""
from __future__ import annotations

import io

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .. import db, security
from ..services import clients as clients_svc
from ..services import nodes as nodes_svc, subs, traffic

router = APIRouter(prefix="/api")


def _ok(request: Request) -> bool:
    if security.get_session(request.cookies.get("hydra_session")):
        return True
    key = request.headers.get("x-api-key") or request.query_params.get("key")
    return bool(key) and key == db.get_setting("api_key", "")


def _deny():
    return JSONResponse({"error": "unauthorized"}, status_code=401)


@router.get("/stats")
def stats(request: Request):
    if not _ok(request):
        return _deny()
    nodes = nodes_svc.list_nodes()
    return {
        "version": "0.9.0",
        "traffic": traffic.summary(),
        "nodes": [
            {"id": n["id"], "name": n["name"], "status": n["status"],
             "address": n["address"], "engine": n["engine"], "last_seen": n["last_seen"]}
            for n in nodes
        ],
        "top": traffic.top_clients(10),
    }


@router.get("/clients")
def clients(request: Request, q: str = ""):
    if not _ok(request):
        return _deny()
    return {"clients": clients_svc.list_clients(q)}


@router.post("/clients/{cid}/toggle")
def client_toggle(request: Request, cid: int):
    if not _ok(request):
        return _deny()
    c = clients_svc.get_client(cid)
    if not c:
        return JSONResponse({"error": "not found"}, status_code=404)
    clients_svc.update_client(cid, enabled=0 if c["enabled"] else 1)
    nodes_svc.push_async()
    return {"ok": True, "enabled": not c["enabled"]}


@router.post("/clients/{cid}/extend")
def client_extend(request: Request, cid: int, days: int = 30):
    if not _ok(request):
        return _deny()
    exp = clients_svc.extend(cid, days)
    nodes_svc.push_async()
    return {"ok": True, "expire_at": exp}


@router.get("/nodes")
def nodes(request: Request):
    if not _ok(request):
        return _deny()
    return {"nodes": nodes_svc.list_nodes()}


@router.post("/nodes/{nid}/push")
def node_push(request: Request, nid: int):
    if not _ok(request):
        return _deny()
    return nodes_svc.push(nid)


@router.get("/sub/{cid}/links")
def sub_links(request: Request, cid: int):
    if not _ok(request):
        return _deny()
    c = clients_svc.get_client(cid)
    if not c:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"url": clients_svc.sub_url(c["sub_token"]), "links": subs.build_links(c)}


@router.get("/qr/{cid}.png")
def qr(request: Request, cid: int, scale: int = 6):
    if not _ok(request):
        return _deny()
    c = clients_svc.get_client(cid)
    if not c:
        return JSONResponse({"error": "not found"}, status_code=404)
    import qrcode

    img = qrcode.make(clients_svc.sub_url(c["sub_token"]))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"cache-control": "no-store"})

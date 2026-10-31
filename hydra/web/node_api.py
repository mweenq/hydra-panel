"""Приём нод: /v1/join (по одноразовому токену).

Дальше мастер сам ходит на ноду по mTLS (8443) — входящих портов на панели
для управления нодами не нужно.
"""
from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import db
from ..services import nodes as nodes_svc

router = APIRouter()


class JoinIn(BaseModel):
    token: str = Field(min_length=8)
    csr: str = Field(min_length=100)
    address: str = ""
    engine: str = "xray"
    version: str = ""
    name: str = ""


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


@router.post("/v1/join")
def join(request: Request, body: JoinIn):
    address = (body.address or "").strip()
    if not address:
        address = _client_ip(request)
    try:
        ipaddress.ip_address(address)
    except ValueError:
        pass
    try:
        res = nodes_svc.join_node(body.token, address, body.csr, body.engine, body.version)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"join failed: {e}"}, status_code=400)
    db.audit("node.join.http", target=str(res["node_id"]), meta={"ip": _client_ip(request)})
    return res

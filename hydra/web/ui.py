"""HTML-интерфейс панели."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, security
from ..services import bottexts, devices
from ..services import clients as clients_svc
from ..services import nodes as nodes_svc, routing, subs, traffic
from ..services import payments as pay_svc
from .deps import require_admin
from .templating import TEMPLATES

router = APIRouter()


def render(request: Request, name: str, status_code: int = 200, **ctx):
    ctx.setdefault("admin", None)
    ctx.setdefault("path", request.url.path)
    ctx.setdefault("msg", request.query_params.get("ok", ""))
    ctx.setdefault("err", request.query_params.get("err", ""))
    return TEMPLATES.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _auth(request: Request):
    a = require_admin(request)
    return a if isinstance(a, dict) else None


# ------------------------------------------------------------------ auth
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if _auth(request):
        return redirect("/dashboard")
    return render(request, "login.html", next=next)


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          next: str = Form("/")):
    row = db.q1("SELECT * FROM admins WHERE username=?", (username.strip(),))
    if not row or not security.verify_password(password, row["password_hash"]):
        return render(request, "login.html", next=next, err="Неверный логин или пароль", status_code=401)
    sid = security.create_session(
        row["id"], ua=request.headers.get("user-agent", ""),
        ip=request.client.host if request.client else "",
    )
    db.audit("auth.login", actor=username)
    resp = redirect(next if next.startswith("/") else "/dashboard")
    resp.set_cookie("hydra_session", sid, httponly=True, samesite="lax",
                    max_age=7 * 24 * 3600, secure=request.url.scheme == "https")
    return resp


@router.post("/logout")
def logout(request: Request):
    sid = request.cookies.get("hydra_session")
    if sid:
        security.drop_session(sid)
    resp = redirect("/login")
    resp.delete_cookie("hydra_session")
    return resp


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, period: str = "month"):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    nodes = nodes_svc.list_nodes()
    for n in nodes:
        n["connections"] = db.q1(
            "SELECT COUNT(*) AS n FROM inbounds WHERE node_id=?", (n["id"],)
        )["n"]
    data = traffic.series(period)
    graph = traffic.chart([p["value"] for p in data["points"]])
    audit = db.dicts(db.q("SELECT * FROM audit ORDER BY id DESC LIMIT 8"))
    return render(
        request, "dashboard.html", admin=admin, nodes=nodes, stats=traffic.summary(),
        top=traffic.top_clients(5), data=data, graph=graph, audit=audit,
        users_total=db.q1("SELECT COUNT(*) AS n FROM clients")["n"],
        active_users=db.q1("SELECT COUNT(*) AS n FROM clients WHERE enabled=1")["n"],
        connections=db.q1("SELECT COUNT(*) AS n FROM inbounds")["n"],
        active_connections=db.q1("SELECT COUNT(*) AS n FROM inbounds WHERE enabled=1")["n"],
    )


# ------------------------------------------------------------------ users
@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, q: str = ""):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    return render(
        request, "users.html", admin=admin, users=clients_svc.list_clients(q), q=q,
        nodes=nodes_svc.list_nodes(),
        connections=db.dicts(db.q(
            """SELECT i.*, n.name AS node_name FROM inbounds i JOIN nodes n ON n.id=i.node_id
               ORDER BY n.id, i.port"""
        )),
    )


@router.post("/users/new")
def user_new(request: Request, name: str = Form(...), days: int = Form(30),
             limit_gb: float = Form(0), note: str = Form(""),
             inbound_ids: list[int] = Form(default=[]), tg_id: str = Form("")):
    if not _auth(request):
        return redirect("/login")
    if not name.strip():
        return redirect("/users?err=Пустое+имя")
    cid = clients_svc.create_client(
        name.strip(), [int(i) for i in inbound_ids], days, limit_gb, note, tg_id
    )
    nodes_svc.push_async()
    return redirect(f"/users/{cid}?ok=Пользователь+создан")


@router.get("/users/{cid}", response_class=HTMLResponse)
def user_page(request: Request, cid: int):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    c = clients_svc.get_client(cid)
    if not c:
        return redirect("/users?err=Нет+такого+пользователя")
    c["days_left"] = clients_svc.days_left(c)
    entries = subs.client_entries(c)
    link_rows = [
        {"link": subs.links.share_link(e["inbound"], e["node"], c), "inbound": e["inbound"]}
        for e in entries
    ]
    return render(
        request, "user.html", admin=admin, c=c, links=link_rows, entries=entries,
        devices=devices.list_devices(cid), dev=devices.summary(c),
        sub_url=clients_svc.sub_url(c["sub_token"]),
        all_connections=db.dicts(db.q(
            """SELECT i.*, n.name AS node_name FROM inbounds i JOIN nodes n ON n.id=i.node_id
               ORDER BY n.id, i.port"""
        )),
        daily=db.dicts(db.q(
            "SELECT * FROM traffic_daily WHERE client_id=? ORDER BY day DESC LIMIT 30", (cid,)
        )),
    )


@router.post("/users/{cid}/update")
def user_update(request: Request, cid: int, name: str = Form(...), days: int = Form(0),
                limit_gb: float = Form(0), note: str = Form(""), enabled: str = Form(""),
                inbound_ids: list[int] = Form(default=[]), device_limit: int = Form(0)):
    if not _auth(request):
        return redirect("/login")
    fields = {
        "name": name.strip(),
        "limit_bytes": int(limit_gb * 1024**3),
        "note": note,
        "enabled": 1 if enabled == "on" else 0,
        "device_limit": max(0, int(device_limit or 0)),
    }
    if days > 0:
        fields["expire_at"] = clients_svc.extend(cid, days)
    clients_svc.update_client(cid, **fields)
    clients_svc.set_inbounds(cid, [int(i) for i in inbound_ids])
    nodes_svc.push_async()
    return redirect(f"/users/{cid}?ok=Сохранено")


@router.post("/users/{cid}/devices/{did}/delete")
def device_delete(request: Request, cid: int, did: int):
    if not _auth(request):
        return redirect("/login")
    devices.unlink(did)
    return redirect(f"/users/{cid}?ok=Устройство+отвязано")


@router.post("/users/{cid}/devices/{did}/block")
def device_block(request: Request, cid: int, did: int):
    if not _auth(request):
        return redirect("/login")
    row = devices.get(did)
    if row:
        devices.block(did, not row["blocked"])
    return redirect(f"/users/{cid}?ok=Готово")


@router.post("/users/{cid}/devices/reset")
def device_reset(request: Request, cid: int):
    if not _auth(request):
        return redirect("/login")
    n = devices.unlink_all(cid)
    return redirect(f"/users/{cid}?ok=Отвязано+устройств:+{n}")


@router.post("/users/{cid}/reset")
def user_reset(request: Request, cid: int):
    if not _auth(request):
        return redirect("/login")
    clients_svc.reset_traffic(cid)
    nodes_svc.push_async()
    return redirect(f"/users/{cid}?ok=Трафик+сброшен")


@router.post("/users/{cid}/delete")
def user_delete(request: Request, cid: int):
    if not _auth(request):
        return redirect("/login")
    clients_svc.delete_client(cid)
    nodes_svc.push_async()
    return redirect("/users?ok=Удалён")


@router.get("/clients")
def clients_legacy(request: Request):
    return redirect("/users")


@router.get("/clients/{cid}")
def client_legacy(request: Request, cid: int):
    return redirect(f"/users/{cid}")


# ------------------------------------------------------------------ nodes
@router.get("/nodes", response_class=HTMLResponse)
def nodes_page(request: Request):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    nodes = nodes_svc.list_nodes()
    for n in nodes:
        n["connections"] = db.q1(
            "SELECT COUNT(*) AS n FROM inbounds WHERE node_id=?", (n["id"],)
        )["n"]
        n["users"] = db.q1(
            """SELECT COUNT(DISTINCT ci.client_id) AS n FROM client_inbounds ci
               JOIN inbounds i ON i.id=ci.inbound_id WHERE i.node_id=?""",
            (n["id"],),
        )["n"]
    return render(request, "nodes.html", admin=admin, nodes=nodes,
                  engines=db.dicts(db.q("SELECT * FROM engines WHERE enabled=1 ORDER BY id")))


@router.post("/nodes/new")
def node_new(request: Request, name: str = Form(...), address: str = Form(...),
             engine: str = Form("xray"), api_port: int = Form(8443),
             is_local: str = Form(""), note: str = Form(""), public_host: str = Form(""),
             protocol: str = Form("vless"), network: str = Form("raw"),
             security_: str = Form("reality", alias="security"), port: int = Form(2443),
             sni: str = Form(""), with_connection: str = Form("")):
    if not _auth(request):
        return redirect("/login")
    nid, token = nodes_svc.create_node(
        name.strip(), address.strip(), engine, api_port, is_local == "on", note,
        public_host=public_host.strip(),
    )
    if with_connection == "on":
        _add_connection(nid, protocol, network, security_, port, sni or "", "", "", "",
                        "reality" if security_ == "reality" else "", "yahoo.com:443", "", "", "")
        nodes_svc.push_async()
    return redirect(f"/nodes/{nid}?ok=Нода+создана&token={token}")


@router.get("/nodes/{nid}", response_class=HTMLResponse)
def node_page(request: Request, nid: int, token: str = ""):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    n = nodes_svc.get_node(nid)
    if not n:
        return redirect("/nodes?err=Нет+такой+ноды")
    connections = db.dicts(db.q("SELECT * FROM inbounds WHERE node_id=? ORDER BY port", (nid,)))
    for i in connections:
        i["clients"] = db.q1(
            "SELECT COUNT(*) AS n FROM client_inbounds WHERE inbound_id=?", (i["id"],)
        )["n"]
    panel_url = db.get_setting("panel_url") or str(request.base_url).rstrip("/")
    cmd = nodes_svc.install_command(token, panel_url, n["name"]) if token else ""
    return render(
        request, "node.html", admin=admin, n=n, connections=connections, token=token, cmd=cmd,
        health=nodes_svc.health(nid) if n.get("cert_pem") else None,
    )


@router.post("/nodes/{nid}/connections")
def node_connection_new(request: Request, nid: int, protocol: str = Form("vless"),
                        network: str = Form("raw"),
                        security_: str = Form("reality", alias="security"),
                        port: int = Form(2443), sni: str = Form(""), host: str = Form(""),
                        path: str = Form("/"), service_name: str = Form(""),
                        flow: str = Form(""), reality_dest: str = Form("yahoo.com:443"),
                        remark: str = Form(""), obfs_type: str = Form(""),
                        obfs_password: str = Form("")):
    if not _auth(request):
        return redirect("/login")
    node = nodes_svc.get_node(nid)
    if not node:
        return redirect("/nodes?err=Нет+ноды")
    _add_connection(nid, protocol, network, security_, port, sni, host, path, service_name,
                    flow, reality_dest, remark, obfs_type, obfs_password)
    nodes_svc.push_async()
    return redirect(f"/nodes/{nid}?ok=Подключение+создано")


def _add_connection(node_id: int, protocol: str, network: str, security_: str, port: int,
                    sni: str, host: str, path: str, service_name: str, flow: str,
                    reality_dest: str, remark: str, obfs_type: str, obfs_password: str) -> int:
    node = nodes_svc.get_node(node_id)
    proto = protocol.lower()
    net = "quic" if proto == "hysteria2" else (network.lower() or "raw")
    sec = "tls" if proto == "hysteria2" else (security_.lower() if security_ else "")
    if sec == "none":
        sec = ""
    tag = f"{proto}-{net}-{port}"
    if service_name:
        tag += f"-{service_name}"
    priv = pub = sid = ""
    if sec == "reality":
        priv, pub, sid = _gen_reality()
    return db.ex(
        """INSERT INTO inbounds(node_id, tag, remark, protocol, network, security, port, listen,
                               sni, host, path, service_name, reality_private, reality_public,
                               reality_short_id, reality_dest, flow, obfs_type, obfs_password,
                               enabled, created_at)
           VALUES(?,?,?,?,?,?,?,'0.0.0.0',?,?,?,?,?,?,?,?,?,?,?,1,?)""",
        (node_id, tag, remark or f"{proto.upper()} {net}", proto, net, sec, port,
         sni or (node["public_host"] or node["address"]), host, path, service_name, priv, pub,
         sid, reality_dest, flow, obfs_type, obfs_password, db.now()),
    )


def _gen_reality() -> tuple[str, str, str]:
    import base64
    import os

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    key = X25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return b64(priv), b64(pub), os.urandom(4).hex()


@router.post("/nodes/{nid}/push")
def node_push(request: Request, nid: int):
    if not _auth(request):
        return redirect("/login")
    res = nodes_svc.push(nid)
    if res.get("ok"):
        return redirect(f"/nodes/{nid}?ok=Конфиг+применён")
    return redirect(f"/nodes/{nid}?err={res.get('error', 'ошибка')[:120]}")


@router.post("/nodes/{nid}/rotate")
def node_rotate(request: Request, nid: int):
    if not _auth(request):
        return redirect("/login")
    token = nodes_svc.rotate_join(nid)
    return redirect(f"/nodes/{nid}?ok=Новый+токен&token={token}")


@router.post("/nodes/{nid}/delete")
def node_delete(request: Request, nid: int):
    if not _auth(request):
        return redirect("/login")
    nodes_svc.delete_node(nid)
    return redirect("/nodes?ok=Удалена")


@router.post("/connections/{iid}/toggle")
def connection_toggle(request: Request, iid: int):
    if not _auth(request):
        return redirect("/login")
    row = db.q1("SELECT node_id FROM inbounds WHERE id=?", (iid,))
    db.ex("UPDATE inbounds SET enabled = 1 - enabled WHERE id=?", (iid,))
    nodes_svc.push_async()
    return redirect(f"/nodes/{row['node_id']}" if row else "/nodes")


@router.post("/connections/{iid}/delete")
def connection_delete(request: Request, iid: int):
    if not _auth(request):
        return redirect("/login")
    row = db.q1("SELECT node_id FROM inbounds WHERE id=?", (iid,))
    db.ex("DELETE FROM inbounds WHERE id=?", (iid,))
    nodes_svc.push_async()
    return redirect(f"/nodes/{row['node_id']}?ok=Удалено" if row else "/nodes")


@router.get("/inbounds")
def inbounds_legacy(request: Request):
    return redirect("/nodes")


# ------------------------------------------------------------------ routing
@router.get("/routing", response_class=HTMLResponse)
def routing_page(request: Request):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    return render(request, "routing.html", admin=admin, rules=routing.list_routes(),
                  kinds=routing.KINDS, actions=routing.ACTIONS,
                  preview=routing.clash_preview())


@router.post("/routing/new")
def routing_new(request: Request, kind: str = Form(...), value: str = Form(...),
                action: str = Form("proxy"), note: str = Form("")):
    if not _auth(request):
        return redirect("/login")
    try:
        routing.add_route(kind, value, action, note)
    except ValueError:
        return redirect("/routing?err=Некорректное+правило")
    nodes_svc.push_async()
    return redirect("/routing?ok=Правило+добавлено")


@router.post("/routing/preset")
def routing_preset(request: Request, preset: str = Form(...)):
    if not _auth(request):
        return redirect("/login")
    n = routing.apply_preset(preset)
    return redirect(f"/routing?ok=Добавлено+правил:+{n}")


@router.post("/routing/{rid}/toggle")
def routing_toggle(request: Request, rid: int):
    if not _auth(request):
        return redirect("/login")
    routing.toggle_route(rid)
    return redirect("/routing")


@router.post("/routing/{rid}/delete")
def routing_delete(request: Request, rid: int):
    if not _auth(request):
        return redirect("/login")
    routing.delete_route(rid)
    return redirect("/routing?ok=Удалено")


@router.post("/routing/{rid}/move")
def routing_move(request: Request, rid: int, delta: int = Form(...)):
    if not _auth(request):
        return redirect("/login")
    routing.move_route(rid, 1 if delta > 0 else -1)
    return redirect("/routing")


# ------------------------------------------------------------------ bots
@router.get("/bots", response_class=HTMLResponse)
def bots_page(request: Request):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    return render(
        request, "bots.html", admin=admin, s=db.all_settings(),
        pay_on=pay_svc.enabled(), pay_plans=len(pay_svc.list_plans()),
        pay_stats=pay_svc.payments_stats(),
    )


@router.post("/bots/save")
def bots_save(request: Request, bot_token: str = Form(""), user_bot_token: str = Form(""),
              admin_tg_ids: str = Form(""), backup_tg_chat: str = Form(""),
              user_bot_enabled: str = Form("")):
    if not _auth(request):
        return redirect("/login")
    db.set_setting("bot_token", bot_token.strip())
    db.set_setting("user_bot_token", user_bot_token.strip())
    db.set_setting("admin_tg_ids", admin_tg_ids.strip())
    db.set_setting("backup_tg_chat", backup_tg_chat.strip())
    db.set_setting("user_bot_enabled", 1 if user_bot_enabled == "on" else 0)
    db.audit("bots.save", actor=_auth(request)["username"])
    return redirect("/bots?ok=Сохранено+—+перезапустите+службу+для+применения")


@router.post("/bots/test")
def bots_test(request: Request):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    import httpx

    token = str(db.get_setting("bot_token", "") or "")
    chat = str(db.get_setting("admin_tg_ids", "") or "").split(",")[0].strip()
    if not token or not chat:
        return redirect("/bots?err=Нужен+токен+бота+и+хотя+бы+один+Telegram+ID")
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": "Hydra: проверка связи. Всё работает."},
            timeout=10,
        )
        ok = r.status_code == 200 and r.json().get("ok")
    except Exception as e:  # noqa: BLE001
        return redirect(f"/bots?err={str(e)[:100]}")
    return redirect("/bots?ok=Сообщение+отправлено" if ok else "/bots?err=Telegram+отклонил+запрос")


# ------------------------------------------------------------------ оплата
@router.get("/bots/payments", response_class=HTMLResponse)
def bots_payments(request: Request, status: str = ""):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    return render(
        request, "bot_payments.html", admin=admin, s=db.all_settings(),
        plans=pay_svc.list_plans(), payments=pay_svc.list_payments(40, status),
        stats=pay_svc.payments_stats(), currencies=pay_svc.CURRENCIES,
        methods=pay_svc.METHODS, status=status,
    )


@router.post("/bots/payments/save")
def bots_payments_save(request: Request, pay_enabled: str = Form(""),
                       pay_currency: str = Form("RUB"), pay_provider_token: str = Form(""),
                       pay_manual_enabled: str = Form(""), pay_manual_details: str = Form(""),
                       pay_support_url: str = Form(""), pay_title: str = Form("")):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    cur = pay_currency.upper()
    if cur not in pay_svc.CURRENCIES:
        cur = "RUB"
    db.set_setting("pay_enabled", 1 if pay_enabled == "on" else 0)
    db.set_setting("pay_currency", cur)
    db.set_setting("pay_provider_token", pay_provider_token.strip())
    db.set_setting("pay_manual_enabled", 1 if pay_manual_enabled == "on" else 0)
    db.set_setting("pay_manual_details", pay_manual_details.strip())
    db.set_setting("pay_support_url", pay_support_url.strip())
    db.set_setting("pay_title", pay_title.strip() or "Продление подписки")
    db.audit("pay.save", actor=admin["username"])
    return redirect("/bots/payments?ok=Сохранено+—+перезапустите+службу")


@router.post("/bots/payments/plans/new")
def plan_new(request: Request, name: str = Form(...), days: int = Form(30),
             gb: float = Form(0), price: float = Form(0), currency: str = Form("XTR"),
             note: str = Form("")):
    if not _auth(request):
        return redirect("/login")
    if not name.strip():
        return redirect("/bots/payments?err=Пустое+название")
    try:
        pay_svc.create_plan(name, days, gb, price, currency, note)
    except ValueError:
        return redirect("/bots/payments?err=Неизвестная+валюта")
    return redirect("/bots/payments?ok=Тариф+добавлен")


@router.post("/bots/payments/plans/{pid}/update")
def plan_update(request: Request, pid: int, name: str = Form(...), days: int = Form(0),
                gb: float = Form(0), price: float = Form(0), currency: str = Form("XTR"),
                enabled: str = Form("")):
    if not _auth(request):
        return redirect("/login")
    try:
        pay_svc.update_plan(pid, name, days, gb, price, currency, 1 if enabled == "on" else 0)
    except ValueError:
        return redirect("/bots/payments?err=Неизвестная+валюта")
    return redirect("/bots/payments?ok=Тариф+обновлён")


@router.post("/bots/payments/plans/{pid}/delete")
def plan_delete(request: Request, pid: int):
    if not _auth(request):
        return redirect("/login")
    pay_svc.delete_plan(pid)
    return redirect("/bots/payments?ok=Тариф+удалён")


@router.post("/bots/payments/{pid}/approve")
def payment_approve(request: Request, pid: int):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    pay_svc.mark_paid(pid, note=f"подтвердил {admin['username']}")
    return redirect("/bots/payments?ok=Оплата+зачтена")


@router.post("/bots/payments/{pid}/cancel")
def payment_cancel(request: Request, pid: int):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    pay_svc.mark_canceled(pid, note=f"отклонил {admin['username']}")
    return redirect("/bots/payments?ok=Заявка+отклонена")


# ------------------------------------------------------------------ тексты бота
@router.get("/bots/texts", response_class=HTMLResponse)
def bots_texts(request: Request):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    sample = bottexts.sample()
    return render(
        request, "bot_texts.html", admin=admin, groups=bottexts.view(),
        vars_hint=bottexts.VARS_HINT, sample=sample,
        preview_status=bottexts.render("status", **sample),
        preview_pay=bottexts.render("pay_plan", **sample),
        preview_manual=bottexts.render("pay_manual", **sample),
    )


@router.post("/bots/texts/save")
async def bots_texts_save(request: Request):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    form = await request.form()
    values = {k[2:]: str(v) for k, v in form.items() if k.startswith("t_")}
    n = bottexts.save(values)
    db.audit("bots.texts", actor=admin["username"], meta={"changed": n})
    return redirect(f"/bots/texts?ok=Сохранено+правок:+{n}")


@router.post("/bots/texts/reset")
async def bots_texts_reset(request: Request):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    form = await request.form()
    if form.get("all"):
        bottexts.reset_all()
        db.audit("bots.texts.reset", actor=admin["username"], meta={"scope": "all"})
        return redirect("/bots/texts?ok=Все+тексты+вернулись+к+умолчанию")
    key = str(form.get("key") or "")
    bottexts.reset(key)
    db.audit("bots.texts.reset", actor=admin["username"], meta={"key": key})
    return redirect("/bots/texts?ok=Текст+сброшен")


# ------------------------------------------------------------------ subscription
@router.get("/subscription", response_class=HTMLResponse)
def subscription_page(request: Request, ua: str = "", preview: int = 0):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    result = None
    if ua:
        sample = db.q1("SELECT * FROM clients WHERE enabled=1 ORDER BY id LIMIT 1")
        if sample:
            result = subs.preview(dict(sample), ua)
        else:
            result = {"app": "—", "format": "—", "content_type": "—", "size": 0,
                      "first_line": "нет активных пользователей для примера"}
    return render(request, "subscription.html", admin=admin, s=db.all_settings(),
                  apps=subs.apps_view(), formats=subs.FORMATS, labels=subs.FORMAT_LABELS,
                  ua=ua, result=result)


@router.post("/subscription/save")
def subscription_save(request: Request, sub_title: str = Form(""),
                      sub_support_url: str = Form(""), sub_announce: str = Form(""),
                      sub_theme: str = Form("#111827"), sub_show_qr: str = Form(""),
                      sub_update_hours: int = Form(12)):
    if not _auth(request):
        return redirect("/login")
    db.set_setting("sub_title", sub_title.strip())
    db.set_setting("sub_support_url", sub_support_url.strip())
    db.set_setting("sub_announce", sub_announce.strip())
    db.set_setting("sub_theme", sub_theme.strip() or "#111827")
    db.set_setting("sub_show_qr", 1 if sub_show_qr == "on" else 0)
    db.set_setting("sub_update_hours", max(1, sub_update_hours))
    return redirect("/subscription?ok=Сохранено")


@router.post("/subscription/apps")
async def subscription_apps(request: Request):
    if not _auth(request):
        return redirect("/login")
    form = await request.form()
    for a in subs.APPS:
        fmt = str(form.get(f"fmt_{a['key']}") or a["fmt"])
        enabled = 1 if form.get(f"on_{a['key']}") else 0
        try:
            subs.set_app(a["key"], fmt, enabled)
        except ValueError:
            continue
    return redirect("/subscription?ok=Приложения+обновлены")


# ------------------------------------------------------------------ settings
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    return render(request, "settings.html", admin=admin, s=db.all_settings(),
                  admins=db.dicts(db.q("SELECT id, username, tg_id, created_at FROM admins")),
                  engines=db.dicts(db.q("SELECT * FROM engines ORDER BY id")))


@router.post("/settings")
async def settings_save(request: Request):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    form = await request.form()
    text_keys = {
        "panel_url", "fallback_dest", "decoy_title", "default_engine", "theme",
        "root_mode", "root_html", "root_redirect", "acme_email", "tls_mode",
        "api_key", "wildcard_domain",
    }
    int_keys = {"traffic_check_interval", "backup_keep", "hwid_limit", "notify_days"}
    flag_keys = {"hwid_enabled", "hwid_reset_on_pay", "notify_enabled"}
    for k in text_keys:
        if k in form:
            db.set_setting(k, str(form[k]).strip())
    for k in int_keys:
        if k in form:
            try:
                db.set_setting(k, int(str(form[k]).strip() or 0))
            except ValueError:
                pass
    for k in flag_keys:
        db.set_setting(k, 1 if form.get(k) else 0)
    db.audit("settings.save", actor=admin["username"])
    return redirect("/settings?ok=Сохранено")


@router.post("/settings/password")
def password_change(request: Request, password: str = Form(...)):
    admin = _auth(request)
    if not admin:
        return redirect("/login")
    db.ex("UPDATE admins SET password_hash=? WHERE id=?",
          (security.hash_password(password), admin["id"]))
    db.audit("auth.password", actor=admin["username"])
    return redirect("/settings?ok=Пароль+изменён")


@router.post("/settings/engines/new")
def engine_new(request: Request, name: str = Form(...), kind: str = Form("xray"),
               binary: str = Form(""), note: str = Form("")):
    if not _auth(request):
        return redirect("/login")
    try:
        db.ex("INSERT INTO engines(name, kind, binary, note) VALUES(?,?,?,?)",
              (name.strip(), kind, binary.strip(), note.strip()))
    except Exception:  # noqa: BLE001
        return redirect("/settings?err=Движок+с+таким+именем+уже+есть")
    return redirect("/settings?ok=Движок+добавлен")


@router.post("/settings/engines/{eid}/update")
def engine_update(request: Request, eid: int, kind: str = Form("xray"), binary: str = Form(""),
                  extra: str = Form(""), enabled: str = Form("")):
    if not _auth(request):
        return redirect("/login")
    db.ex("UPDATE engines SET kind=?, binary=?, extra=?, enabled=? WHERE id=?",
          (kind, binary.strip(), extra.strip(), 1 if enabled == "on" else 0, eid))
    return redirect("/settings?ok=Движок+обновлён")


@router.post("/settings/engines/{eid}/delete")
def engine_delete(request: Request, eid: int):
    if not _auth(request):
        return redirect("/login")
    db.ex("DELETE FROM engines WHERE id=?", (eid,))
    return redirect("/settings?ok=Движок+удалён")

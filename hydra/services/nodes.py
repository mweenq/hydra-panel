"""Ноды: регистрация по join-токену, mTLS-пуш конфигов, health-чек."""
from __future__ import annotations

import ssl
import threading
import time
from typing import Any

import httpx

from .. import config, db, security
from . import config_gen

_dirty = threading.Event()
_push_lock = threading.Lock()


# ------------------------------------------------------------------ CRUD
def list_nodes() -> list[dict]:
    return db.dicts(db.q("SELECT * FROM nodes ORDER BY id"))


def get_node(node_id: int) -> dict | None:
    row = db.q1("SELECT * FROM nodes WHERE id=?", (node_id,))
    return dict(row) if row else None


def create_node(name: str, address: str, engine: str = "xray", api_port: int = 8443,
                is_local: bool = False, note: str = "", public_host: str = "") -> tuple[int, str]:
    token = security.new_token(24)
    exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600 * 24))
    nid = db.ex(
        """INSERT INTO nodes(name, address, public_host, api_port, engine, status,
                             join_token_hash, join_expires, is_local, note, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (name, address, public_host, api_port, engine, "pending", security.token_hash(token), exp,
         1 if is_local else 0, note, db.now()),
    )
    db.audit("node.create", target=str(nid), meta={"name": name, "address": address})
    return nid, token


def join_node(token: str, address: str, csr_pem: str, engine: str = "xray",
              version: str = "") -> dict:
    h = security.token_hash(token)
    row = db.q1("SELECT * FROM nodes WHERE join_token_hash=?", (h,))
    if not row:
        raise PermissionError("invalid join token")
    if row["join_expires"] and row["join_expires"] < db.now():
        raise PermissionError("join token expired")
    # адрес берём из панели (домен задаёт админ), присланный — только если пусто
    effective = (row["address"] or "").strip() or (address or "").strip()
    cert_pem = security.sign_csr(csr_pem, effective)
    db.ex(
        """UPDATE nodes SET address=?, engine=?, status='online', last_seen=?, version=?,
                            cert_pem=?, cert_expires=?, join_token_hash=''
           WHERE id=?""",
        (effective, engine or row["engine"], db.now(), version,
         cert_pem, security.cert_expires(cert_pem), row["id"]),
    )
    db.audit("node.join", target=str(row["id"]), meta={"address": effective, "engine": engine})
    return {
        "node_id": row["id"],
        "name": row["name"],
        "cert_pem": cert_pem,
        "ca_pem": security.ca_pem(),
        "address": effective,
    }


def delete_node(node_id: int) -> None:
    db.ex("DELETE FROM nodes WHERE id=?", (node_id,))
    db.audit("node.delete", target=str(node_id))


def rotate_join(node_id: int) -> str:
    token = security.new_token(24)
    exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600 * 24))
    db.ex(
        "UPDATE nodes SET join_token_hash=?, join_expires=?, status='pending' WHERE id=?",
        (security.token_hash(token), exp, node_id),
    )
    return token


# ------------------------------------------------------------------ payload
def build_payload(node_id: int) -> dict:
    node = get_node(node_id)
    if not node:
        raise ValueError("no such node")
    inbounds = db.dicts(
        db.q("SELECT * FROM inbounds WHERE node_id=? AND enabled=1 ORDER BY port", (node_id,))
    )
    clients_by_inbound: dict[int, list[dict]] = {}
    for inb in inbounds:
        rows = db.dicts(
            db.q(
                """SELECT c.* FROM clients c
                   JOIN client_inbounds ci ON ci.client_id=c.id
                   WHERE ci.inbound_id=? AND c.enabled=1""",
                (inb["id"],),
            )
        )
        # лимит/срок проверяем здесь же — просроченные не попадают в конфиг
        now = db.now()
        alive = []
        for c in rows:
            if c["expire_at"] and c["expire_at"] < now:
                continue
            if c["limit_bytes"] and (c["up_bytes"] + c["down_bytes"]) >= c["limit_bytes"]:
                continue
            alive.append(c)
        clients_by_inbound[inb["id"]] = alive

    return config_gen.build_payload(node, inbounds, clients_by_inbound, _node_tls(node))


def _node_tls(node: dict) -> dict:
    """Если есть wildcard-сертификат и нода под этим доменом — отдаём его ноде.

    Так один сертификат на панели обслуживает все ноды *.example.com и продлевается
    в одном месте; нода сама acme не занимается.
    """
    wild = str(db.get_setting("wildcard_domain", "") or "").strip().lstrip("*.")
    if not wild:
        return {}
    host = (node.get("public_host") or node["address"] or "").strip()
    if not host.endswith("." + wild) and host != wild:
        return {}
    crt = config.CERT_DIR / "wildcard" / "fullchain.pem"
    key = config.CERT_DIR / "wildcard" / "privkey.pem"
    if not (crt.exists() and key.exists()):
        return {}
    try:
        return {"cert_pem": crt.read_text(encoding="utf-8"), "key_pem": key.read_text(encoding="utf-8")}
    except OSError:
        return {}


# ------------------------------------------------------------------ транспорт
def _ctx() -> ssl.SSLContext:
    security.ensure_ca()
    ctx = ssl.create_default_context(cafile=str(config.CERT_DIR / "ca.pem"))
    ctx.load_cert_chain(str(config.CERT_DIR / "master.pem"), str(config.CERT_DIR / "master.key"))
    ctx.check_hostname = False  # нода может стоять за NAT, цепочку всё равно проверяем
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _client(timeout: float = 12.0) -> httpx.Client:
    return httpx.Client(verify=_ctx(), timeout=timeout)


def push(node_id: int) -> dict:
    node = get_node(node_id)
    if not node:
        return {"ok": False, "error": "no such node"}
    if not node.get("cert_pem"):
        return {"ok": False, "error": "node not joined"}
    payload = build_payload(node_id)
    url = f"https://{node['address']}:{node['api_port']}/v1/apply"
    try:
        with _client() as c:
            r = c.post(url, json=payload)
        ok = r.status_code == 200 and r.json().get("ok", False)
        db.ex(
            "UPDATE nodes SET status=?, last_seen=?, version=? WHERE id=?",
            ("online" if ok else "error", db.now(), r.json().get("version", node.get("version") or ""), node_id),
        )
        return {"ok": ok, "response": r.json()}
    except Exception as e:  # noqa: BLE001
        db.ex("UPDATE nodes SET status='offline' WHERE id=?", (node_id,))
        return {"ok": False, "error": str(e)}


def push_all() -> None:
    for n in list_nodes():
        if n.get("cert_pem"):
            push(n["id"])


def push_async() -> None:
    _dirty.set()


def _worker() -> None:
    while True:
        _dirty.wait()
        time.sleep(1.2)  # дебаунс: пачка изменений — один пуш
        _dirty.clear()
        with _push_lock:
            push_all()


def start_worker() -> None:
    t = threading.Thread(target=_worker, name="hydra-push", daemon=True)
    t.start()


def health(node_id: int) -> dict:
    node = get_node(node_id)
    if not node or not node.get("cert_pem"):
        return {"ok": False, "error": "not joined"}
    try:
        with _client(6.0) as c:
            r = c.get(f"https://{node['address']}:{node['api_port']}/v1/status")
        data = r.json()
        db.ex("UPDATE nodes SET status='online', last_seen=?, version=? WHERE id=?",
              (db.now(), data.get("version", ""), node_id))
        return {"ok": True, **data}
    except Exception as e:  # noqa: BLE001
        db.ex("UPDATE nodes SET status='offline' WHERE id=?", (node_id,))
        return {"ok": False, "error": str(e)}


def install_command(token: str, panel_url: str, node_name: str = "") -> str:
    base = panel_url.rstrip("/") or f"http://{config.WEB_HOST}:{config.WEB_PORT}"
    return (
        f"curl -fsSL {base}/install-node.sh | bash -s -- "
        f"--master {base} --token {token}" + (f" --name {node_name}" if node_name else "")
    )

"""Клиенты: CRUD, выдача подписок, связи с инбаундами."""
from __future__ import annotations

import uuid as uuidlib
from datetime import datetime, timedelta, timezone

from .. import db, security


def list_clients(search: str = "") -> list[dict]:
    sql = "SELECT * FROM clients"
    args: list = []
    if search:
        sql += " WHERE name LIKE ? OR note LIKE ? OR uuid LIKE ?"
        like = f"%{search}%"
        args = [like, like, like]
    sql += " ORDER BY id DESC"
    rows = db.dicts(db.q(sql, args))
    for r in rows:
        r["used"] = (r["up_bytes"] or 0) + (r["down_bytes"] or 0)
        r["used_h"] = human_bytes(r["used"])
        r["limit_h"] = human_bytes(r["limit_bytes"]) if r["limit_bytes"] else "∞"
        r["expire_h"] = (r["expire_at"] or "")[:10] or "∞"
        r["status"] = client_status(r)
        r["days_left"] = days_left(r)
        r["expire_state"] = (
            "expired" if r["status"] == "expired"
            else "soon" if (r["days_left"] is not None and r["days_left"] <= 5)
            else ""
        )
        r["inbounds"] = db.q1(
            "SELECT COUNT(*) AS n FROM client_inbounds WHERE client_id=?", (r["id"],)
        )["n"]
    return rows


def client_status(c: dict) -> str:
    if not c.get("enabled"):
        return "disabled"
    if c.get("expire_at") and c["expire_at"] < db.now():
        return "expired"
    if c.get("limit_bytes") and (c["up_bytes"] + c["down_bytes"]) >= c["limit_bytes"]:
        return "limit"
    return "active"


def get_client(cid: int) -> dict | None:
    row = db.q1("SELECT * FROM clients WHERE id=?", (cid,))
    if not row:
        return None
    c = dict(row)
    c["used"] = (c["up_bytes"] or 0) + (c["down_bytes"] or 0)
    c["used_h"] = human_bytes(c["used"])
    c["limit_h"] = human_bytes(c["limit_bytes"]) if c["limit_bytes"] else "∞"
    c["status"] = client_status(c)
    c["inbound_ids"] = [
        r["inbound_id"] for r in db.q("SELECT inbound_id FROM client_inbounds WHERE client_id=?", (cid,))
    ]
    return c


def days_left(c: dict) -> int | None:
    """Сколько дней осталось до конца подписки. None — бессрочно."""
    exp = c.get("expire_at") or ""
    if not exp:
        return None
    try:
        dt = datetime.strptime(exp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (dt - datetime.now(timezone.utc)).days


def get_by_sub(token: str) -> dict | None:
    row = db.q1("SELECT * FROM clients WHERE sub_token=?", (token,))
    return dict(row) if row else None


def create_client(name: str, inbound_ids: list[int], days: int = 30, limit_gb: float = 0,
                  note: str = "", tg_id: str = "", enabled: bool = True) -> int:
    uid = str(uuidlib.uuid4())
    token = security.new_token(18)
    exp = ""
    if days > 0:
        exp = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cid = db.ex(
        """INSERT INTO clients(name, uuid, sub_token, enabled, expire_at, limit_bytes, note,
                               tg_id, created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (name, uid, token, 1 if enabled else 0, exp, int(limit_gb * 1024**3), note, tg_id, db.now()),
    )
    set_inbounds(cid, inbound_ids)
    db.audit("client.create", target=str(cid), meta={"name": name})
    return cid


def update_client(cid: int, **fields) -> None:
    allowed = {"name", "enabled", "expire_at", "limit_bytes", "note", "tg_id", "device_limit"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            args.append(v)
    if sets:
        args.append(cid)
        db.ex(f"UPDATE clients SET {', '.join(sets)} WHERE id=?", args)


def set_inbounds(cid: int, inbound_ids: list[int]) -> None:
    db.ex("DELETE FROM client_inbounds WHERE client_id=?", (cid,))
    for iid in inbound_ids:
        db.ex(
            "INSERT OR IGNORE INTO client_inbounds(client_id, inbound_id) VALUES(?,?)",
            (cid, int(iid)),
        )


def delete_client(cid: int) -> None:
    db.ex("DELETE FROM clients WHERE id=?", (cid,))
    db.audit("client.delete", target=str(cid))


def reset_traffic(cid: int) -> None:
    db.ex("UPDATE clients SET up_bytes=0, down_bytes=0 WHERE id=?", (cid,))
    db.ex("DELETE FROM traffic_daily WHERE client_id=?", (cid,))


def extend(cid: int, days: int) -> str:
    c = get_client(cid)
    if not c:
        raise ValueError("no client")
    base = datetime.now(timezone.utc)
    if c["expire_at"]:
        try:
            cur = datetime.strptime(c["expire_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if cur > base:
                base = cur
        except ValueError:
            pass
    exp = (base + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.ex("UPDATE clients SET expire_at=?, enabled=1 WHERE id=?", (exp, cid))
    return exp


def human_bytes(n: int) -> str:
    step = 1024.0
    val = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < step:
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} PB"


def sub_url(token: str, base: str = "") -> str:
    base = (base or db.get_setting("panel_url") or "").rstrip("/")
    return f"{base}/sub/{token}"

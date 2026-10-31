"""HWID: привязка устройств к подписке.

Клиенты на ядре Happ (и Remnawave-совместимые) присылают заголовок `x-hwid` —
стабильный отпечаток устройства. По нему считаем устройства и, если задан
лимит, не пускаем лишние.

Правила простые:

- `hwid_enabled = 1` — устройства записываются (видно, кто и когда заходил);
- `hwid_limit > 0` — включается ограничение: новый HWID сверх лимита получает
  403 и заголовок `x-hwid-max-devices-reached`;
- у пользователя можно переопределить лимит (`clients.device_limit`),
  0 — брать общий из настроек;
- клиенты без `x-hwid` (clash, sing-box, браузер) не блокируются, пока лимит
  не включён явно; с включённым лимитом подписка требует HWID-заголовок.
"""
from __future__ import annotations

from .. import db

HEADER = "x-hwid"


def enabled() -> bool:
    return bool(int(db.get_setting("hwid_enabled", 1) or 0))


def default_limit() -> int:
    try:
        return max(0, int(db.get_setting("hwid_limit", 0) or 0))
    except (TypeError, ValueError):
        return 0


def limit_for(client: dict) -> int:
    own = int(client.get("device_limit") or 0)
    return own if own > 0 else default_limit()


def list_devices(client_id: int) -> list[dict]:
    rows = db.dicts(db.q(
        "SELECT * FROM devices WHERE client_id=? ORDER BY last_seen DESC", (client_id,)
    ))
    for r in rows:
        r["title"] = r["model"] or r["platform"] or "устройство"
        r["hwid_short"] = (r["hwid"][:8] + "…") if len(r["hwid"]) > 9 else r["hwid"]
    return rows


def count(client_id: int) -> int:
    return db.q1("SELECT COUNT(*) AS n FROM devices WHERE client_id=?", (client_id,))["n"]


def get(device_id: int) -> dict | None:
    row = db.q1("SELECT * FROM devices WHERE id=?", (device_id,))
    return dict(row) if row else None


def summary(client: dict) -> dict:
    return {"count": count(client["id"]), "limit": limit_for(client), "enabled": enabled()}


def touch(client: dict, hwid: str, platform: str = "", model: str = "",
          ver_os: str = "", ua: str = "", ip: str = "") -> dict:
    """Регистрирует устройство. Возвращает статус: new / known / limit / blocked."""
    limit = limit_for(client)
    hwid = (hwid or "").strip()[:128]
    if not hwid:
        return {"status": "none", "device": None, "count": count(client["id"]), "limit": limit}

    row = db.q1("SELECT * FROM devices WHERE client_id=? AND hwid=?", (client["id"], hwid))
    now = db.now()
    if row:
        db.ex(
            """UPDATE devices SET last_seen=?, platform=?, model=?, ver_os=?, user_agent=?, ip=?
               WHERE id=?""",
            (now, (platform or row["platform"])[:64], (model or row["model"])[:64],
             (ver_os or row["ver_os"])[:32], (ua or row["user_agent"])[:300], ip, row["id"]),
        )
        dev = get(row["id"])
        return {
            "status": "blocked" if dev["blocked"] else "known",
            "device": dev,
            "count": count(client["id"]),
            "limit": limit,
        }

    used = count(client["id"])
    if limit and used >= limit:
        db.audit("device.limit", target=str(client["id"]),
                 meta={"hwid": hwid[:12], "used": used, "limit": limit})
        return {"status": "limit", "device": None, "count": used, "limit": limit}

    did = db.ex(
        """INSERT INTO devices(client_id, hwid, platform, model, ver_os, user_agent, ip,
                               first_seen, last_seen)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (client["id"], hwid, platform[:64], model[:64], ver_os[:32], ua[:300], ip, now, now),
    )
    db.audit("device.bind", target=str(client["id"]), meta={"hwid": hwid[:12]})
    return {"status": "new", "device": get(did), "count": used + 1, "limit": limit}


def unlink(device_id: int) -> None:
    dev = get(device_id)
    db.ex("DELETE FROM devices WHERE id=?", (device_id,))
    if dev:
        db.audit("device.unlink", target=str(dev["client_id"]), meta={"hwid": dev["hwid"][:12]})


def unlink_all(client_id: int) -> int:
    n = count(client_id)
    db.ex("DELETE FROM devices WHERE client_id=?", (client_id,))
    if n:
        db.audit("device.unlink_all", target=str(client_id), meta={"count": n})
    return n


def block(device_id: int, blocked: bool = True) -> None:
    db.ex("UPDATE devices SET blocked=? WHERE id=?", (1 if blocked else 0, device_id))

"""Учёт трафика: опрос нод, накопление по дням и часам, автоотключение."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from .. import db
from . import nodes as nodes_svc


def _fmt_bytes(n: int) -> str:
    step = 1024.0
    val = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < step:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= step
    return f"{val:.1f} PB"


_last_cleanup = 0.0


def apply_stats(node_id: int, users: dict[str, dict]) -> int:
    """users: {"uuid|tag": {"up":..,"down":..}} — дельты с прошлого опроса."""
    global _last_cleanup
    updated = 0
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    hour = now.strftime("%Y-%m-%dT%H")
    for email, delta in (users or {}).items():
        uuid = email.split("|", 1)[0]
        up = int(delta.get("up") or 0)
        down = int(delta.get("down") or 0)
        if up <= 0 and down <= 0:
            continue
        row = db.q1("SELECT id FROM clients WHERE uuid=?", (uuid,))
        if not row:
            continue
        cid = row["id"]
        db.ex(
            "UPDATE clients SET up_bytes=up_bytes+?, down_bytes=down_bytes+?, last_seen=? WHERE id=?",
            (up, down, db.now(), cid),
        )
        db.ex(
            """INSERT INTO traffic_daily(client_id, day, up, down) VALUES(?,?,?,?)
               ON CONFLICT(client_id, day) DO UPDATE SET up=up+excluded.up, down=down+excluded.down""",
            (cid, day, up, down),
        )
        db.ex(
            """INSERT INTO traffic_hourly(client_id, hour, up, down) VALUES(?,?,?,?)
               ON CONFLICT(client_id, hour) DO UPDATE SET up=up+excluded.up, down=down+excluded.down""",
            (cid, hour, up, down),
        )
        updated += 1
    if updated:
        db.ex("UPDATE nodes SET last_seen=? WHERE id=?", (db.now(), node_id))
    if time.time() - _last_cleanup > 3600:
        _last_cleanup = time.time()
        keep = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H")
        db.ex("DELETE FROM traffic_hourly WHERE hour < ?", (keep,))
    return updated


def poll_node(node_id: int) -> dict:
    node = nodes_svc.get_node(node_id)
    if not node or not node.get("cert_pem"):
        return {"ok": False, "error": "not joined"}
    try:
        with nodes_svc._client(10.0) as c:  # noqa: SLF001
            r = c.get(f"https://{node['address']}:{node['api_port']}/v1/stats")
        data = r.json()
        n = apply_stats(node_id, data.get("users") or {})
        return {"ok": True, "updated": n}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def poll_all() -> None:
    for n in nodes_svc.list_nodes():
        if n.get("cert_pem") and n.get("status") != "pending":
            poll_node(n["id"])


def enforce_limits() -> list[str]:
    """Отключает просроченных и превысивших лимит. Возвращает имена отключённых."""
    now = db.now()
    disabled: list[str] = []
    for c in db.q("SELECT * FROM clients WHERE enabled=1"):
        used = (c["up_bytes"] or 0) + (c["down_bytes"] or 0)
        reason = ""
        if c["expire_at"] and c["expire_at"] < now:
            reason = "expired"
        elif c["limit_bytes"] and used >= c["limit_bytes"]:
            reason = "limit"
        if reason:
            db.ex(
                "UPDATE clients SET enabled=0, note=trim(COALESCE(note,'') || ?) WHERE id=?",
                (f" [auto-off: {reason}]", c["id"]),
            )
            db.audit("client.autodisable", target=str(c["id"]), meta={"reason": reason})
            disabled.append(c["name"])
    if disabled:
        nodes_svc.push_async()
    return disabled


# ------------------------------------------------------------------ графики
PERIODS = {
    "day": {"title": "за сутки", "prev": "за предыдущие сутки", "points": 24},
    "month": {"title": "за 30 дней", "prev": "за предыдущие 30 дней", "points": 30},
    "year": {"title": "за год", "prev": "за прошлый год", "points": 12},
}


def _sum(rows, key: str = "v") -> int:
    return sum(int(r[key] or 0) for r in rows)


def series(period: str = "month") -> dict:
    """Данные для графика: точки, сумма периода, сумма предыдущего, дельта."""
    period = period if period in PERIODS else "month"
    now = datetime.now(timezone.utc)
    points: list[dict] = []
    prev_total = 0

    if period == "day":
        start = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)
        keys = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H") for i in range(24)]
        rows = db.dicts(db.q(
            "SELECT hour AS k, SUM(up+down) AS v FROM traffic_hourly WHERE hour >= ? GROUP BY hour",
            (keys[0],),
        ))
        prev_from = (start - timedelta(hours=24)).strftime("%Y-%m-%dT%H")
        prev_rows = db.q(
            "SELECT SUM(up+down) AS v FROM traffic_hourly WHERE hour >= ? AND hour < ?",
            (prev_from, keys[0]),
        )
        prev_total = int(prev_rows[0]["v"] or 0) if prev_rows else 0
        labels = [k[11:13] + ":00" for k in keys]
        values = {r["k"]: int(r["v"] or 0) for r in rows}
        points = [{"label": labels[i], "value": values.get(k, 0)} for i, k in enumerate(keys)]

    elif period == "year":
        start = (now.replace(day=1) - timedelta(days=365)).replace(day=1)
        keys = []
        for i in range(11, -1, -1):
            y = now.year + (now.month - 1 - i) // 12
            m = (now.month - 1 - i) % 12 + 1
            keys.append(f"{y:04d}-{m:02d}")
        rows = db.dicts(db.q(
            "SELECT substr(day,1,7) AS k, SUM(up+down) AS v FROM traffic_daily WHERE day >= ? GROUP BY k",
            (keys[0] + "-01",),
        ))
        prev_rows = db.q(
            "SELECT SUM(up+down) AS v FROM traffic_daily WHERE day >= ? AND day < ?",
            (start.strftime("%Y-%m-%d"), keys[0] + "-01"),
        )
        prev_total = int(prev_rows[0]["v"] or 0) if prev_rows else 0
        values = {r["k"]: int(r["v"] or 0) for r in rows}
        points = [{"label": k[5:] + "." + k[2:4], "value": values.get(k, 0)} for k in keys]

    else:
        keys = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
        rows = db.dicts(db.q(
            "SELECT day AS k, SUM(up+down) AS v FROM traffic_daily WHERE day >= ? GROUP BY day",
            (keys[0],),
        ))
        prev_rows = db.q(
            "SELECT SUM(up+down) AS v FROM traffic_daily WHERE day >= ? AND day < ?",
            ((now - timedelta(days=59)).strftime("%Y-%m-%d"), keys[0]),
        )
        prev_total = int(prev_rows[0]["v"] or 0) if prev_rows else 0
        values = {r["k"]: int(r["v"] or 0) for r in rows}
        points = [{"label": k[8:10] + "." + k[5:7], "value": values.get(k, 0)} for k in keys]

    total = sum(p["value"] for p in points)
    delta = None
    if prev_total > 0:
        delta = round((total - prev_total) * 100 / prev_total)
    return {
        "period": period,
        "points": points,
        "total": total,
        "total_h": _fmt_bytes(total),
        "prev_total_h": _fmt_bytes(prev_total),
        "delta": delta,
        "title": PERIODS[period]["title"],
        "prev_title": PERIODS[period]["prev"],
        "peak": max([p["value"] for p in points] + [1]),
        "peak_h": _fmt_bytes(max([p["value"] for p in points] + [0])),
    }


def chart(values: list[int], width: int = 640, height: int = 170, pad: int = 8) -> dict:
    """Сглаженная линия (Catmull-Rom → bezier) и залитая область под ней."""
    n = len(values)
    if n == 0:
        return {"line": "", "area": "", "dots": []}
    peak = max(values) or 1
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    step = inner_w / max(n - 1, 1)
    pts = [
        (pad + i * step, pad + inner_h - (v / peak) * inner_h)
        for i, v in enumerate(values)
    ]
    if n == 1:
        d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"
        area = f"{d} L {pts[0][0]:.1f} {height - pad:.1f} Z"
        return {"line": d, "area": area, "dots": pts}

    def cp(i: int) -> tuple[float, float, float, float]:
        p0 = pts[max(i - 1, 0)]
        p1 = pts[i]
        p2 = pts[min(i + 1, n - 1)]
        p3 = pts[min(i + 2, n - 1)]
        c1x = p1[0] + (p2[0] - p0[0]) / 6
        c1y = p1[1] + (p2[1] - p0[1]) / 6
        c2x = p2[0] - (p3[0] - p1[0]) / 6
        c2y = p2[1] - (p3[1] - p1[1]) / 6
        return (c1x, c1y, c2x, c2y)

    parts = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"]
    for i in range(n - 1):
        c1x, c1y, c2x, c2y = cp(i)
        parts.append(
            f"C {c1x:.1f} {c1y:.1f} {c2x:.1f} {c2y:.1f} {pts[i + 1][0]:.1f} {pts[i + 1][1]:.1f}"
        )
    line = " ".join(parts)
    area = f"{line} L {pts[-1][0]:.1f} {height - pad:.1f} L {pts[0][0]:.1f} {height - pad:.1f} Z"
    return {"line": line, "area": area, "dots": pts, "width": width, "height": height}


def summary() -> dict:
    row = db.q1(
        """SELECT COUNT(*) AS clients,
                  COALESCE(SUM(up_bytes),0) AS up,
                  COALESCE(SUM(down_bytes),0) AS down,
                  COALESCE(SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END),0) AS active
           FROM clients"""
    )
    return {
        "clients": row["clients"],
        "active": row["active"],
        "up": row["up"],
        "down": row["down"],
        "total": row["up"] + row["down"],
        "total_h": _fmt_bytes(row["up"] + row["down"]),
    }


def top_clients(limit: int = 5) -> list[dict]:
    rows = db.dicts(db.q(
        "SELECT name, up_bytes+down_bytes AS used FROM clients ORDER BY used DESC LIMIT ?",
        (limit,),
    ))
    for r in rows:
        r["used_h"] = _fmt_bytes(r["used"])
    return rows


_stop = threading.Event()


def _loop() -> None:
    while not _stop.wait(int(db.get_setting("traffic_check_interval", 60) or 60)):
        try:
            poll_all()
            enforce_limits()
        except Exception:  # noqa: BLE001
            pass


def start() -> None:
    threading.Thread(target=_loop, name="hydra-traffic", daemon=True).start()

"""Демо-данные для локального просмотра интерфейса.

Запуск:  python tools/demo_seed.py
Панель при этом должна смотреть в тот же HYDRA_HOME (по умолчанию ./data).
Скрипт идемпотентен: если демо-нода уже есть, ничего не дублирует.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hydra import db  # noqa: E402
from hydra.services import clients as clients_svc  # noqa: E402
from hydra.services import config_gen, nodes as nodes_svc  # noqa: E402
from hydra.web.ui import _gen_reality  # noqa: E402


def main() -> None:
    db.init_db()
    if db.q1("SELECT id FROM nodes WHERE name='de-fra-1'"):
        print("демо-данные уже есть")
        return

    nid, _ = nodes_svc.create_node("de-fra-1", "de1.example.com", "xray", 8443, False, "Hetzner CX22")
    db.ex(
        "UPDATE nodes SET status='online', last_seen=?, version='0.9.0', cert_expires=? WHERE id=?",
        (db.now(), (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"), nid),
    )
    nid2, _ = nodes_svc.create_node("nl-ams-1", "ams1.example.com", "xray", 8443, False, "Aeza")
    db.ex("UPDATE nodes SET status='offline', last_seen=? WHERE id=?", (db.now(), nid2))

    priv, pub, sid = _gen_reality()
    inbounds = [
        (nid, "vless-raw-443", "DE Reality", "vless", "raw", "reality", 443,
         "yahoo.com", "", "/", "", priv, pub, sid, "yahoo.com:443", "xtls-rprx-vision", "", ""),
        (nid, "vless-xhttp-8443", "DE XHTTP", "vless", "xhttp", "tls", 8443,
         "de1.example.com", "de1.example.com", "/api/v1", "", "", "", "", "", "", "", ""),
        (nid, "trojan-grpc-8444", "DE Trojan", "trojan", "grpc", "tls", 8444,
         "de1.example.com", "", "/", "grpc", "", "", "", "", "", "", ""),
        (nid, "hysteria2-quic-8445", "DE Hysteria", "hysteria2", "quic", "tls", 8445,
         "de1.example.com", "", "/", "", "", "", "", "", "", "salamander", "s3cret-obfs"),
    ]
    ids = []
    for row in inbounds:
        ids.append(db.ex(
            """INSERT INTO inbounds(node_id, tag, remark, protocol, network, security, port, listen,
                   sni, host, path, service_name, reality_private, reality_public, reality_short_id,
                   reality_dest, flow, obfs_type, obfs_password, enabled, created_at)
               VALUES(?,?,?,?,?,?,?,'0.0.0.0',?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            (*row, db.now()),
        ))

    people = [
        ("Иван Петров", 30, 100, "личный ноутбук"),
        ("Мария Соколова", 3, 50, "iPhone + MacBook"),
        ("Алексей", 90, 0, "роутер"),
        ("Тест-драйв", 7, 10, "проверка лимита"),
    ]
    for i, (name, days, gb, note) in enumerate(people):
        cid = clients_svc.create_client(name, ids, days=days, limit_gb=gb, note=note)
        db.ex(
            "UPDATE clients SET created_at=?, last_seen=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(days=40 - i * 7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             db.now(), cid),
        )

    # трафик за две недели — чтобы график не был пустым
    random.seed(7)
    rows = db.q("SELECT id FROM clients")
    for day_offset in range(14):
        day = (datetime.now(timezone.utc) - timedelta(days=13 - day_offset)).strftime("%Y-%m-%d")
        for r in rows:
            base = random.randint(200, 900) * 1024**2
            db.ex(
                """INSERT INTO traffic_daily(client_id, day, up, down) VALUES(?,?,?,?)
                   ON CONFLICT(client_id, day) DO UPDATE SET up=up+excluded.up, down=down+excluded.down""",
                (r["id"], day, base // 4, base),
            )
    for r in rows:
        total = db.q1("SELECT SUM(up) AS u, SUM(down) AS d FROM traffic_daily WHERE client_id=?", (r["id"],))
        db.ex("UPDATE clients SET up_bytes=?, down_bytes=? WHERE id=?",
              (total["u"] or 0, total["d"] or 0, r["id"]))

    # почасовой трафик за сутки — для графика «День»
    for h in range(24):
        hour = (datetime.now(timezone.utc) - timedelta(hours=23 - h)).strftime("%Y-%m-%dT%H")
        base = int((0.35 + 0.65 * abs((h - 12) / 12)) * random.randint(180, 620)) * 1024**2
        for r in rows:
            db.ex(
                """INSERT INTO traffic_hourly(client_id, hour, up, down) VALUES(?,?,?,?)
                   ON CONFLICT(client_id, hour) DO UPDATE SET up=up+excluded.up, down=down+excluded.down""",
                (r["id"], hour, base // 5, base),
            )

    # один клиент «упёрся в лимит»
    last = db.q1("SELECT id FROM clients ORDER BY id DESC LIMIT 1")["id"]
    db.ex("UPDATE clients SET limit_bytes=?, enabled=0, note=trim(note || ' [auto-off: limit]') WHERE id=?",
          (db.q1("SELECT up_bytes+down_bytes AS s FROM clients WHERE id=?", (last,))["s"], last))

    for action, target in (
        ("node.create", str(nid)), ("inbound.create", str(ids[0])),
        ("client.create", "1"), ("settings.save", ""), ("auth.login", "admin"),
    ):
        db.ex("INSERT INTO audit(ts, actor, action, target, meta) VALUES(?,?,?,?,?)",
              (db.now(), "admin", action, target, ""))

    db.set_setting("panel_url", "https://panel.example.com")
    db.set_setting("sub_title", "Hydra VPN")
    db.set_setting("sub_support_url", "https://t.me/hydra_support")
    db.set_setting("sub_announce", "В субботу с 02:00 до 03:00 — профилактика в Амстердаме.")
    print("готово: 2 ноды, 4 инбаунда, 4 клиента, трафик за 14 дней")


if __name__ == "__main__":
    main()

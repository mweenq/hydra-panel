"""SQLite-слой. Один коннект + блокировка: панель — это десятки запросов в минуту,
этого более чем достаточно. WAL включён, бэкап делается через VACUUM INTO."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

SCHEMA_VERSION = 1

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    tg_id         TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    admin_id   INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ua         TEXT DEFAULT '',
    ip         TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS nodes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    address          TEXT NOT NULL,
    public_host      TEXT DEFAULT '',
    api_port         INTEGER DEFAULT 8443,
    engine           TEXT DEFAULT 'xray',
    status           TEXT DEFAULT 'pending',
    last_seen        TEXT DEFAULT '',
    version          TEXT DEFAULT '',
    join_token_hash  TEXT DEFAULT '',
    join_expires     TEXT DEFAULT '',
    cert_pem         TEXT DEFAULT '',
    cert_expires     TEXT DEFAULT '',
    is_local         INTEGER DEFAULT 0,
    note             TEXT DEFAULT '',
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbounds (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id          INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    tag              TEXT NOT NULL,
    remark           TEXT DEFAULT '',
    protocol         TEXT NOT NULL,
    network          TEXT DEFAULT '',
    security         TEXT DEFAULT '',
    port             INTEGER NOT NULL,
    listen           TEXT DEFAULT '0.0.0.0',
    sni              TEXT DEFAULT '',
    host             TEXT DEFAULT '',
    path             TEXT DEFAULT '',
    service_name     TEXT DEFAULT '',
    reality_private  TEXT DEFAULT '',
    reality_public   TEXT DEFAULT '',
    reality_short_id TEXT DEFAULT '',
    reality_dest     TEXT DEFAULT '',
    flow             TEXT DEFAULT '',
    obfs_type        TEXT DEFAULT '',
    obfs_password    TEXT DEFAULT '',
    enabled          INTEGER DEFAULT 1,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    uuid        TEXT UNIQUE NOT NULL,
    sub_token   TEXT UNIQUE NOT NULL,
    enabled     INTEGER DEFAULT 1,
    expire_at   TEXT DEFAULT '',
    limit_bytes INTEGER DEFAULT 0,
    up_bytes    INTEGER DEFAULT 0,
    down_bytes  INTEGER DEFAULT 0,
    note        TEXT DEFAULT '',
    tg_id       TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    last_seen   TEXT DEFAULT '',
    device_limit INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS devices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    hwid       TEXT NOT NULL,
    platform   TEXT DEFAULT '',
    model      TEXT DEFAULT '',
    ver_os     TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    ip         TEXT DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    blocked    INTEGER DEFAULT 0,
    UNIQUE (client_id, hwid)
);

CREATE TABLE IF NOT EXISTS client_inbounds (
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    inbound_id INTEGER NOT NULL REFERENCES inbounds(id) ON DELETE CASCADE,
    PRIMARY KEY (client_id, inbound_id)
);

CREATE TABLE IF NOT EXISTS traffic_daily (
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    day       TEXT NOT NULL,
    up        INTEGER DEFAULT 0,
    down      INTEGER DEFAULT 0,
    PRIMARY KEY (client_id, day)
);

CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    actor  TEXT DEFAULT '',
    action TEXT NOT NULL,
    target TEXT DEFAULT '',
    meta   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS traffic_hourly (
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    hour      TEXT NOT NULL,
    up        INTEGER DEFAULT 0,
    down      INTEGER DEFAULT 0,
    PRIMARY KEY (client_id, hour)
);

CREATE TABLE IF NOT EXISTS routes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT NOT NULL,
    value    TEXT NOT NULL,
    action   TEXT NOT NULL DEFAULT 'proxy',
    enabled  INTEGER DEFAULT 1,
    position INTEGER DEFAULT 0,
    note     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS engines (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT UNIQUE NOT NULL,
    kind     TEXT DEFAULT 'xray',
    binary   TEXT DEFAULT '',
    version  TEXT DEFAULT '',
    enabled  INTEGER DEFAULT 1,
    extra    TEXT DEFAULT '',
    note     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS plans (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    days     INTEGER DEFAULT 30,
    gb       REAL DEFAULT 0,
    price    REAL DEFAULT 0,
    currency TEXT DEFAULT 'XTR',
    enabled  INTEGER DEFAULT 1,
    position INTEGER DEFAULT 0,
    note     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL,
    plan_id    INTEGER DEFAULT 0,
    plan_name  TEXT DEFAULT '',
    days       INTEGER DEFAULT 0,
    gb         REAL DEFAULT 0,
    amount     REAL DEFAULT 0,
    currency   TEXT DEFAULT 'XTR',
    method     TEXT DEFAULT 'stars',
    status     TEXT DEFAULT 'pending',
    charge_id  TEXT DEFAULT '',
    tg_id      TEXT DEFAULT '',
    note       TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    paid_at    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inbounds_node ON inbounds(node_id);
CREATE INDEX IF NOT EXISTS idx_traffic_day ON traffic_daily(day);
CREATE INDEX IF NOT EXISTS idx_traffic_hour ON traffic_hourly(hour);
CREATE INDEX IF NOT EXISTS idx_routes_pos ON routes(position);
CREATE INDEX IF NOT EXISTS idx_payments_client ON payments(client_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_devices_client ON devices(client_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.ensure_dirs()
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def close() -> None:
    """Закрывает соединение (нужно перед восстановлением файла базы)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
            _conn = None


def close() -> None:
    """Закрывает соединение (нужно перед восстановлением файла базы)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
            _conn = None


def init_db() -> None:
    with _lock:
        c = conn()
        c.executescript(SCHEMA)
        c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        c.commit()
        _migrate()
        _seed_settings()
        _seed_admin()
        _seed_engines()


def _migrate() -> None:
    """Догоняем схему для баз, созданных предыдущими версиями."""
    cols = {r["name"] for r in conn().execute("PRAGMA table_info(nodes)").fetchall()}
    if "public_host" not in cols:
        conn().execute("ALTER TABLE nodes ADD COLUMN public_host TEXT DEFAULT ''")
        conn().commit()
    ccols = {r["name"] for r in conn().execute("PRAGMA table_info(clients)").fetchall()}
    if "device_limit" not in ccols:
        conn().execute("ALTER TABLE clients ADD COLUMN device_limit INTEGER DEFAULT 0")
        conn().commit()


def _seed_settings() -> None:
    for k, v in config.DEFAULT_SETTINGS.items():
        conn().execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)", (k, json.dumps(v)))
    conn().commit()


def _seed_engines() -> None:
    if conn().execute("SELECT COUNT(*) AS n FROM engines").fetchone()["n"]:
        return
    rows = [
        ("xray", "xray", "/usr/local/bin/xray", "ядро, VLESS/Trojan + REALITY/XHTTP"),
        ("openflux", "xray", "/opt/hydra-node/xray/openflux", "форк xray, обход БС"),
        ("wt", "xray", "/opt/hydra-node/xray/wt", "форк wt, конфиг как у xray"),
        ("qwdtt", "xray", "/opt/hydra-node/xray/qwdtt", "уточните формат конфига в настройках движка"),
    ]
    conn().executemany(
        "INSERT INTO engines(name, kind, binary, note) VALUES(?,?,?,?)", rows
    )
    conn().commit()


def _seed_admin() -> None:
    import os
    import secrets

    from .security import hash_password

    row = conn().execute("SELECT COUNT(*) AS n FROM admins").fetchone()
    if row["n"]:
        return
    pw = os.environ.get("HYDRA_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
    conn().execute(
        "INSERT INTO admins(username, password_hash, created_at) VALUES(?,?,?)",
        ("admin", hash_password(pw), now()),
    )
    conn().commit()
    if not os.environ.get("HYDRA_ADMIN_PASSWORD"):
        p = config.DATA_DIR / "initial_password.txt"
        p.write_text(pw, encoding="utf-8")
        try:
            p.chmod(0o600)
        except OSError:
            pass


# ---------------------------------------------------------------- helpers
def q(sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with _lock:
        return conn().execute(sql, tuple(args)).fetchall()


def q1(sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
    with _lock:
        return conn().execute(sql, tuple(args)).fetchone()


def ex(sql: str, args: Iterable[Any] = ()) -> int:
    with _lock:
        cur = conn().execute(sql, tuple(args))
        conn().commit()
        return cur.lastrowid or 0


def exmany(sql: str, rows: Iterable[Iterable[Any]]) -> None:
    with _lock:
        conn().executemany(sql, [tuple(r) for r in rows])
        conn().commit()


def dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- settings
def get_setting(key: str, default: Any = None) -> Any:
    row = q1("SELECT value FROM settings WHERE key=?", (key,))
    if not row:
        return config.DEFAULT_SETTINGS.get(key, default)
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    ex(
        "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def all_settings() -> dict[str, Any]:
    out = dict(config.DEFAULT_SETTINGS)
    for row in q("SELECT key, value FROM settings"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except (TypeError, ValueError):
            out[row["key"]] = row["value"]
    return out


def audit(action: str, actor: str = "", target: str = "", meta: Any = "") -> None:
    if meta and not isinstance(meta, str):
        meta = json.dumps(meta, ensure_ascii=False)
    ex(
        "INSERT INTO audit(ts, actor, action, target, meta) VALUES(?,?,?,?,?)",
        (now(), actor, action, target, meta or ""),
    )

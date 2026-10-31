"""Маршрутизация: правила, которые уходят клиентам в профиль подписки."""
from __future__ import annotations

from .. import db

KINDS = {
    "domain": "домен",
    "suffix": "суффикс домена",
    "ip": "IP / подсеть",
    "geosite": "geosite",
    "geoip": "geoip",
}
ACTIONS = {"proxy": "через VPN", "direct": "напрямую", "block": "блокировать"}

CLASH_ACTION = {"proxy": "PROXY", "direct": "DIRECT", "block": "REJECT"}


def list_routes(only_enabled: bool = False) -> list[dict]:
    sql = "SELECT * FROM routes"
    if only_enabled:
        sql += " WHERE enabled=1"
    sql += " ORDER BY position, id"
    rows = db.dicts(db.q(sql))
    for r in rows:
        r["kind_h"] = KINDS.get(r["kind"], r["kind"])
        r["action_h"] = ACTIONS.get(r["action"], r["action"])
    return rows


def add_route(kind: str, value: str, action: str = "proxy", note: str = "") -> int:
    if kind not in KINDS or action not in ACTIONS:
        raise ValueError("bad rule")
    pos = db.q1("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM routes")["p"]
    rid = db.ex(
        "INSERT INTO routes(kind, value, action, position, note) VALUES(?,?,?,?,?)",
        (kind, value.strip(), action, pos, note),
    )
    db.audit("route.create", target=str(rid), meta={"value": value, "action": action})
    return rid


def toggle_route(rid: int) -> None:
    db.ex("UPDATE routes SET enabled = 1 - enabled WHERE id=?", (rid,))


def delete_route(rid: int) -> None:
    db.ex("DELETE FROM routes WHERE id=?", (rid,))


def move_route(rid: int, delta: int) -> None:
    row = db.q1("SELECT position FROM routes WHERE id=?", (rid,))
    if not row:
        return
    target = db.q1(
        "SELECT id, position FROM routes WHERE position {} ? ORDER BY position {} LIMIT 1".format(
            ">" if delta > 0 else "<", "ASC" if delta > 0 else "DESC"
        ),
        (row["position"],),
    )
    if not target:
        return
    db.ex("UPDATE routes SET position=? WHERE id=?", (target["position"], rid))
    db.ex("UPDATE routes SET position=? WHERE id=?", (row["position"], target["id"]))


PRESETS = {
    "lan": [
        ("ip", "192.168.0.0/16", "direct"),
        ("ip", "10.0.0.0/8", "direct"),
        ("ip", "172.16.0.0/12", "direct"),
        ("ip", "127.0.0.0/8", "direct"),
    ],
    "ads": [("geosite", "category-ads-all", "block")],
    "ru": [("geoip", "ru", "direct"), ("geosite", "category-ru", "direct")],
}


def apply_preset(key: str) -> int:
    rows = PRESETS.get(key) or []
    for kind, value, action in rows:
        add_route(kind, value, action, note=f"пресет {key}")
    return len(rows)


# ------------------------------------------------------------------ экспорт
def clash_rules() -> list[str]:
    out = []
    for r in list_routes(only_enabled=True):
        act = CLASH_ACTION.get(r["action"], "PROXY")
        if r["kind"] == "domain":
            out.append(f"DOMAIN,{r['value']},{act}")
        elif r["kind"] == "suffix":
            out.append(f"DOMAIN-SUFFIX,{r['value']},{act}")
        elif r["kind"] == "ip":
            out.append(f"IP-CIDR,{r['value']},{act},no-resolve")
        elif r["kind"] == "geosite":
            out.append(f"GEOSITE,{r['value']},{act}")
        elif r["kind"] == "geoip":
            out.append(f"GEOIP,{r['value']},{act}")
    return out


def clash_preview() -> str:
    lines = clash_rules() + ["MATCH,PROXY"]
    return "\n".join(lines)


def singbox_rules() -> tuple[list[dict], bool]:
    """Возвращает (правила, нужен ли outbound block)."""
    rules: list[dict] = []
    need_block = False
    for r in list_routes(only_enabled=True):
        action = {"proxy": "proxy", "direct": "direct", "block": "block"}[r["action"]]
        if action == "block":
            need_block = True
        item: dict = {"outbound": action}
        if r["kind"] == "domain":
            item["domain"] = [r["value"]]
        elif r["kind"] == "suffix":
            item["domain_suffix"] = [r["value"]]
        elif r["kind"] == "ip":
            item["ip_cidr"] = [r["value"]]
        else:
            # geosite/geoip в sing-box требуют rule_set с внешним списком — пропускаем
            continue
        rules.append(item)
    return rules, need_block


def xray_rules() -> list[dict]:
    """Правила для полного клиентского конфига xray (домены, IP, geo-списки)."""
    rules = []
    for r in list_routes(only_enabled=True):
        tag = {"proxy": "proxy", "direct": "direct", "block": "block"}[r["action"]]
        if r["kind"] in ("domain", "suffix"):
            # "full:" — ровно этот домен, "domain:" — он и поддомены
            prefix = "full:" if r["kind"] == "domain" else "domain:"
            rules.append({"type": "field", "domain": [prefix + r["value"]], "outboundTag": tag})
        elif r["kind"] == "geosite":
            rules.append({"type": "field", "domain": [f"geosite:{r['value']}"], "outboundTag": tag})
        elif r["kind"] == "ip":
            rules.append({"type": "field", "ip": [r["value"]], "outboundTag": tag})
        elif r["kind"] == "geoip":
            rules.append({"type": "field", "ip": [f"geoip:{r['value']}"], "outboundTag": tag})
    return rules


# ------------------------------------------------------------------ Happ
PRIVATE_IP = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "224.0.0.0/4",
    "255.255.255.255",
]

GEO_IP_URL = "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat"
GEO_SITE_URL = "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat"


def happ_profile(name: str = "Hydra") -> dict:
    """Профиль маршрутизации Happ (свой JSON, не xray).

    Happ принимает его ссылкой happ://routing/onadd/<base64> — заголовком
    `routing` или строкой в теле подписки.
    """
    out: dict = {
        "Name": name,
        "GlobalProxy": "true",
        "RemoteDNSType": "DoH",
        "RemoteDNSDomain": "https://cloudflare-dns.com/dns-query",
        "RemoteDNSIP": "1.1.1.1",
        "DomesticDNSType": "DoH",
        "DomesticDNSDomain": "https://dns.google/dns-query",
        "DomesticDNSIP": "8.8.8.8",
        "Geoipurl": GEO_IP_URL,
        "Geositeurl": GEO_SITE_URL,
        "DnsHosts": {"cloudflare-dns.com": "1.1.1.1", "dns.google": "8.8.8.8"},
        "DirectSites": [],
        "DirectIp": list(PRIVATE_IP),
        "ProxySites": [],
        "ProxyIp": [],
        "BlockSites": [],
        "BlockIp": [],
        "DomainStrategy": "IPIfNonMatch",
        "FakeDNS": "false",
    }
    bucket = {
        ("proxy", "site"): "ProxySites",
        ("proxy", "ip"): "ProxyIp",
        ("direct", "site"): "DirectSites",
        ("direct", "ip"): "DirectIp",
        ("block", "site"): "BlockSites",
        ("block", "ip"): "BlockIp",
    }
    for r in list_routes(only_enabled=True):
        action = r["action"]
        if action not in ("proxy", "direct", "block"):
            continue
        if r["kind"] == "domain":
            out[bucket[(action, "site")]].append(f"full:{r['value']}")
        elif r["kind"] == "suffix":
            out[bucket[(action, "site")]].append(f"domain:{r['value']}")
        elif r["kind"] == "geosite":
            out[bucket[(action, "site")]].append(f"geosite:{r['value']}")
        elif r["kind"] == "ip":
            out[bucket[(action, "ip")]].append(r["value"])
        elif r["kind"] == "geoip":
            out[bucket[(action, "ip")]].append(f"geoip:{r['value']}")
    return out


def happ_link(name: str = "Hydra") -> str:
    """Ссылка на профиль маршрутизации для Happ."""
    import base64
    import json

    raw = json.dumps(happ_profile(name), ensure_ascii=False).encode()
    return "happ://routing/onadd/" + base64.b64encode(raw).decode()

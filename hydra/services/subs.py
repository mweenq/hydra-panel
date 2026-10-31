"""Подписки: автоопределение клиента по User-Agent и сборка форматов."""
from __future__ import annotations

import base64
import copy
import json

import yaml

from .. import db
from . import clients as clients_svc
from . import links, routing

FORMATS = ("v2ray", "clash", "singbox", "happ", "links")

FORMAT_LABELS = {
    "v2ray": "base64 (v2ray)",
    "clash": "YAML (clash)",
    "singbox": "JSON (sing-box)",
    "happ": "JSON (Xray / Happ)",
    "links": "plain-ссылки",
}

# приложения, под которые отдаём профиль. format можно переопределить в «Подписке»
APPS: list[dict] = [
    {"key": "v2raytun", "name": "v2RayTun", "fmt": "v2ray", "short": "VT",
     "ua": ("v2raytun",), "hint": "iOS, Android, macOS"},
    {"key": "happ", "name": "Happ", "fmt": "happ", "short": "H",
     "ua": ("happ",), "hint": "iOS, Android, macOS, Windows"},
    {"key": "mihomo", "name": "mihomo / Clash", "fmt": "clash", "short": "M",
     "ua": ("mihomo", "clash", "flclash", "stash", "verge"), "hint": "FlClash, FlClashX, Stash, Verge"},
    {"key": "singbox", "name": "sing-box", "fmt": "singbox", "short": "SB",
     "ua": ("sing-box", "sfa", "sfi", "sft", "hiddify", "sagernet"), "hint": "SFA, SFI, Hiddify"},
    {"key": "shadowrocket", "name": "Shadowrocket", "fmt": "v2ray", "short": "SR",
     "ua": ("shadowrocket",), "hint": "iOS"},
    {"key": "streisand", "name": "Streisand", "fmt": "v2ray", "short": "ST",
     "ua": ("streisand",), "hint": "iOS, macOS"},
    {"key": "v2rayng", "name": "v2rayNG / NekoBox", "fmt": "v2ray", "short": "NG",
     "ua": ("v2rayng", "nekobox"), "hint": "Android, Windows"},
    {"key": "karing", "name": "Karing", "fmt": "v2ray", "short": "K",
     "ua": ("karing",), "hint": "iOS, Android, Windows"},
]


def app_settings() -> dict[str, dict]:
    saved = db.get_setting("sub_apps", {}) or {}
    out = {}
    for a in APPS:
        conf = saved.get(a["key"]) or {}
        fmt = conf.get("format") or a["fmt"]
        # Happ не разбирает JSON сам — он отдаёт файл ядру Xray как есть.
        # Сохранённый «sing-box JSON» для Happ заведомо нерабочий (ловили это
        # на живой установке), поэтому читаем его как наш xray-формат.
        if a["key"] == "happ" and fmt == "singbox":
            fmt = "happ"
        out[a["key"]] = {
            "format": fmt,
            "enabled": int(conf.get("enabled", 1)),
        }
    return out


def set_app(key: str, fmt: str, enabled: int) -> None:
    if key not in {a["key"] for a in APPS}:
        raise ValueError("unknown app")
    if fmt not in FORMATS:
        raise ValueError("unknown format")
    saved = db.get_setting("sub_apps", {}) or {}
    saved[key] = {"format": fmt, "enabled": int(enabled)}
    db.set_setting("sub_apps", saved)


def apps_view() -> list[dict]:
    conf = app_settings()
    out = []
    for a in APPS:
        item = dict(a)
        item.update(conf[a["key"]])
        out.append(item)
    return out


def detect_app(ua: str) -> dict | None:
    u = (ua or "").lower()
    if not u:
        return None
    conf = app_settings()
    for a in APPS:
        if not conf[a["key"]]["enabled"]:
            continue
        if any(k in u for k in a["ua"]):
            return a
    return None


def detect_format(ua: str, override: str = "") -> str:
    if override in FORMATS:
        return override
    app = detect_app(ua)
    if app:
        return app_settings()[app["key"]]["format"]
    return "v2ray"


def client_entries(client: dict) -> list[dict]:
    """Все подключения клиента вместе с нодой."""
    rows = db.dicts(db.q(
        """SELECT i.*, COALESCE(NULLIF(n.public_host,''), n.address) AS node_address,
                  n.name AS node_name, n.engine AS node_engine
           FROM inbounds i
           JOIN client_inbounds ci ON ci.inbound_id=i.id
           JOIN nodes n ON n.id=i.node_id
           WHERE ci.client_id=? AND i.enabled=1
           ORDER BY i.port""",
        (client["id"],),
    ))
    out = []
    for r in rows:
        node = {"address": r["node_address"], "name": r["node_name"], "engine": r["node_engine"]}
        out.append({"inbound": r, "node": node})
    return out


def build_links(client: dict) -> list[str]:
    res = []
    for e in client_entries(client):
        link = links.share_link(e["inbound"], e["node"], client)
        if link:
            res.append(link)
    return res


def build_v2ray(client: dict) -> str:
    return base64.b64encode("\n".join(build_links(client)).encode()).decode()


def build_links_text(client: dict) -> str:
    return "\n".join(build_links(client))


def build_clash(client: dict, title: str) -> str:
    proxies, names = [], []
    for e in client_entries(client):
        p = links.clash_proxy(e["inbound"], e["node"], client)
        proxies.append(p)
        names.append(p["name"])
    rules = routing.clash_rules()
    doc = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "external-controller": "127.0.0.1:9090",
        "proxies": proxies,
        "proxy-groups": [
            {"name": "PROXY", "type": "select", "proxies": (names + ["DIRECT"]) if names else ["DIRECT"]},
            ({"name": "AUTO", "type": "url-test", "url": "https://www.gstatic.com/generate_204",
              "interval": 300, "proxies": names} if names
             else {"name": "AUTO", "type": "select", "proxies": ["DIRECT"]}),
        ],
        "rules": rules + ["MATCH,PROXY"],
    }
    return f"# {title}\n" + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def build_singbox(client: dict) -> str:
    outs, tags = [], []
    for e in client_entries(client):
        o = links.singbox_outbound(e["inbound"], e["node"], client)
        outs.append(o)
        tags.append(o["tag"])
    rules, need_block = routing.singbox_rules()
    outbounds = [
        {"type": "selector", "tag": "proxy", "outbounds": tags + ["direct"],
         "default": tags[0] if tags else "direct"},
    ] + outs + [{"type": "direct", "tag": "direct"}]
    if need_block:
        outbounds.append({"type": "block", "tag": "block"})
    doc = {
        "log": {"level": "info"},
        "inbounds": [{"type": "tun", "tag": "tun-in", "address": ["172.19.0.1/30"],
                      "auto_route": True, "strict_route": True, "stack": "mixed"}],
        "outbounds": outbounds,
        "route": {
            "rules": [{"ip_is_private": True, "outbound": "direct"}] + rules,
            "final": "proxy",
        },
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


HAPP_DNS = {
    "queryStrategy": "UseIP",
    "servers": ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"],
}

HAPP_INBOUNDS = [
    {"tag": "socks-in", "listen": "127.0.0.1", "port": 10808, "protocol": "socks",
     "settings": {"udp": True}},
    {"tag": "http-in", "listen": "127.0.0.1", "port": 10809, "protocol": "http",
     "settings": {}},
]

# базовые правила: торренты в блок, проверки связи и локалка — напрямую
HAPP_BASE_RULES = [
    {"type": "field", "protocol": ["bittorrent"], "outboundTag": "block"},
    {"type": "field", "domain": [
        "domain:connectivitycheck.gstatic.com",
        "domain:connectivitycheck.android.com",
        "domain:captive.apple.com",
        "domain:www.msftconnecttest.com",
        "domain:detectportal.firefox.com",
    ], "outboundTag": "direct"},
    {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"},
]


def build_happ(client: dict) -> str:
    """Профиль для Happ: **массив** клиентских конфигов Xray.

    Happ не разбирает JSON сам — каждый элемент массива уходит ядру как есть.
    Поэтому на каждое подключение отдаём отдельный конфиг с одним outbound
    `proxy`, а первым элементом — «Автовыбор»: все серверы как `proxy-1…N`
    плюс balancer `auto` (leastLoad) и observatory для замеров.
    """
    title = db.get_setting("sub_title", "Hydra VPN") or "Hydra VPN"
    entries: list[dict] = []
    for e in client_entries(client):
        ob = links.happ_outbound(e["inbound"], e["node"], client)
        if ob:
            entries.append(ob)
    if not entries:
        return "[]"

    rules = routing.xray_rules()
    direct = {"tag": "direct", "protocol": "freedom", "settings": {}}
    block = {"tag": "block", "protocol": "blackhole", "settings": {}}

    # ---------------------------------------------------------- автовыбор
    auto_outs = []
    costs = []
    for i, ob in enumerate(entries, start=1):
        item = dict(ob)
        item["tag"] = f"proxy-{i}"
        auto_outs.append(item)
        costs.append({"regexp": False, "match": f"proxy-{i}", "value": float((i - 1) * 10)})
    auto_rules = copy.deepcopy(HAPP_BASE_RULES)
    for r in rules:
        if r.get("outboundTag") == "proxy":
            auto_rules.append({"type": "field",
                               **{k: v for k, v in r.items() if k not in ("type", "outboundTag")},
                               "balancerTag": "auto"})
        else:
            auto_rules.append(r)

    auto = {
        "remarks": f"{title} · Автовыбор",
        "log": {"loglevel": "warning"},
        "dns": copy.deepcopy(HAPP_DNS),
        "inbounds": copy.deepcopy(HAPP_INBOUNDS),
        "outbounds": auto_outs + [direct, block],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": auto_rules,
            "balancers": [{
                "tag": "auto",
                "selector": ["proxy-"],
                "fallbackTag": "proxy-1",
                "strategy": {"type": "leastLoad", "settings": {
                    "expected": 1, "maxRTT": "3s", "tolerance": 0.5, "costs": costs,
                }},
            }],
        },
        "observatory": {
            "subjectSelector": ["proxy-"],
            "probeUrl": "https://www.gstatic.com/generate_204",
            "probeInterval": "15s",
            "enableConcurrency": True,
        },
    }

    # ---------------------------------------------------------- по серверу
    items = [auto]
    for ob in entries:
        item = dict(ob)
        item["tag"] = "proxy"
        items.append({
            "remarks": f"{title} · {ob['tag']}",
            "log": {"loglevel": "warning"},
            "dns": copy.deepcopy(HAPP_DNS),
            "inbounds": copy.deepcopy(HAPP_INBOUNDS),
            "outbounds": [item, dict(direct), dict(block)],
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": copy.deepcopy(HAPP_BASE_RULES) + rules,
            },
        })
    return json.dumps(items, ensure_ascii=False, indent=2)


def render(client: dict, fmt: str) -> tuple[str, str]:
    title = db.get_setting("sub_title", "Hydra VPN") or "Hydra VPN"
    if fmt == "clash":
        return build_clash(client, title), "text/yaml; charset=utf-8"
    if fmt == "singbox":
        return build_singbox(client), "application/json; charset=utf-8"
    if fmt == "happ":
        return build_happ(client), "application/json; charset=utf-8"
    if fmt == "links":
        return build_links_text(client), "text/plain; charset=utf-8"
    return build_v2ray(client), "text/plain; charset=utf-8"


def userinfo_header(client: dict) -> str:
    used_up = client.get("up_bytes") or 0
    used_down = client.get("down_bytes") or 0
    total = client.get("limit_bytes") or 0
    exp = client.get("expire_at") or ""
    exp_ts = 0
    if exp:
        from datetime import datetime, timezone

        try:
            exp_ts = int(
                datetime.strptime(exp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            )
        except ValueError:
            exp_ts = 0
    return f"upload={used_up}; download={used_down}; total={total}; expire={exp_ts}"


def sub_headers(client: dict, fmt: str, ua: str = "") -> dict[str, str]:
    title = db.get_setting("sub_title", "Hydra VPN") or "Hydra VPN"
    hours = int(db.get_setting("sub_update_hours", 12) or 12)
    headers = {
        "subscription-userinfo": userinfo_header(client),
        "profile-title": title,
        "profile-update-interval": str(hours),
        "profile-web-page-url": clients_svc.sub_url(client["sub_token"]),
        "cache-control": "no-store",
    }
    # Happ умеет забирать профиль маршрутизации заголовком. В JSON-режиме
    # правила и так лежат в конфиге, поэтому заголовок нужен только для
    # base64/clash/sing-box форматов.
    app = detect_app(ua)
    if fmt != "happ" and app and app["key"] == "happ":
        headers["routing"] = routing.happ_link(
            str(db.get_setting("panel_name", "Hydra") or "Hydra")
        )
    return headers


def preview(client: dict, ua: str) -> dict:
    """Что получит клиент с таким User-Agent — для страницы «Подписка»."""
    app = detect_app(ua)
    fmt = detect_format(ua)
    body, ctype = render(client, fmt)
    if fmt == "v2ray":
        try:
            first = base64.b64decode(body).decode().splitlines()[0]
        except Exception:  # noqa: BLE001
            first = ""
    elif fmt in ("singbox", "happ"):
        try:
            doc = json.loads(body)
            if isinstance(doc, list):
                names = [str(x.get("remarks") or "") for x in doc[:3]]
                first = f"JSON, профилей: {len(doc)} — " + ", ".join(names)
            else:
                outs = doc.get("outbounds") or []
                first = f"JSON, outbounds: {len(outs)} — " + ", ".join(
                    str(o.get("tag") or o.get("type") or "") for o in outs[:4]
                )
        except ValueError:
            first = body.splitlines()[0] if body else ""
    else:
        first = body.splitlines()[0] if body else ""
    return {
        "app": app["name"] if app else "неизвестный клиент",
        "key": app["key"] if app else "",
        "format": fmt,
        "content_type": ctype,
        "size": len(body),
        "first_line": first,
    }

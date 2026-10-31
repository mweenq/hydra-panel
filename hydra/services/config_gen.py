"""Сборка конфигов демонов для ноды: xray (и его форки), hysteria2, sing-box."""
from __future__ import annotations

import json
from typing import Any

from .. import config

NODE_CERT = "/opt/hydra-node/certs/fullchain.pem"
NODE_KEY = "/opt/hydra-node/certs/privkey.pem"
XRAY_API = "127.0.0.1:10085"
HY2_STATS = "127.0.0.1:9999"
HY2_SECRET = "hydra-stats"


def email_for(client: dict, inb: dict) -> str:
    return f"{client['uuid']}|{inb['tag']}"


def _xray_clients(inb: dict, clients: list[dict]) -> list[dict]:
    proto = inb["protocol"]
    out = []
    for c in clients:
        item = {"email": email_for(c, inb)}
        if proto == "vless":
            item["id"] = c["uuid"]
            flow = inb.get("flow") or ""
            if flow and (inb.get("network") or "raw") in ("raw", "tcp"):
                item["flow"] = flow
        else:  # trojan
            item["password"] = c["uuid"]
        out.append(item)
    return out


def _stream(inb: dict) -> dict:
    net = (inb.get("network") or "raw").lower()
    if net == "tcp" and not config.XRAY_RAW_COMPAT:
        net = "raw"
    sec = (inb.get("security") or "").lower()
    if not sec:
        sec = "reality" if inb.get("reality_public") else "tls"

    st: dict[str, Any] = {"network": net}
    if sec == "reality":
        st["security"] = "reality"
        st["realitySettings"] = {
            "show": False,
            "dest": inb.get("reality_dest") or "yahoo.com:443",
            "xver": 0,
            "serverNames": [inb.get("sni") or "yahoo.com"],
            "privateKey": inb.get("reality_private") or "",
            "shortIds": [inb.get("reality_short_id") or ""],
        }
    elif sec in ("tls", "xtls"):
        st["security"] = "tls"
        st["tlsSettings"] = {
            "serverName": inb.get("sni") or "",
            "alpn": ["h2", "http/1.1"],
            "certificates": [{"certificateFile": NODE_CERT, "keyFile": NODE_KEY}],
        }
        # маскировка: всё, что не распознали как наш трафик, уходит на заглушку
        st["tlsSettings"]["rejectUnknownSni"] = False
        st["fallbacks"] = [
            {"dest": 8080, "xver": 0},
        ]

    if net == "ws":
        st["wsSettings"] = {
            "path": inb.get("path") or "/",
            "headers": {"Host": inb.get("host") or inb.get("sni") or ""},
        }
    elif net == "grpc":
        st["grpcSettings"] = {
            "serviceName": inb.get("service_name") or "",
            "multiMode": False,
        }
    elif net == "xhttp":
        st["xhttpSettings"] = {
            "path": inb.get("path") or "/",
            "host": inb.get("host") or inb.get("sni") or "",
            "mode": inb.get("xhttp_mode") or "auto",
        }
    return st


def _xray_inbound(inb: dict, clients: list[dict]) -> dict:
    proto = inb["protocol"]
    if proto == "vless":
        settings = {"clients": _xray_clients(inb, clients), "decryption": "none"}
    else:
        settings = {"clients": _xray_clients(inb, clients)}
    return {
        "tag": inb["tag"],
        "listen": inb.get("listen") or "0.0.0.0",
        "port": int(inb["port"]),
        "protocol": proto,
        "settings": settings,
        "streamSettings": _stream(inb),
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": False},
    }


def build_xray(inbounds: list[dict], clients_by_inbound: dict[int, list[dict]]) -> dict | None:
    items = [i for i in inbounds if i["protocol"] in ("vless", "trojan") and i.get("enabled")]
    if not items:
        return None
    cfg: dict[str, Any] = {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "services": ["HandlerService", "StatsService"]},
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {"statsInboundUplink": False, "statsInboundDownlink": False},
        },
        "inbounds": [
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": 10085,
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
            }
        ]
        + [_xray_inbound(i, clients_by_inbound.get(i["id"], [])) for i in items],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom", "settings": {}},
            {"tag": "block", "protocol": "blackhole", "settings": {}},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "block"},
            ],
        },
    }
    return cfg


def build_hysteria2(inbounds: list[dict], clients_by_inbound: dict[int, list[dict]]) -> str | None:
    items = [i for i in inbounds if i["protocol"] in ("hysteria2", "hy2") and i.get("enabled")]
    if not items:
        return None
    # один процесс hysteria2 может слушать несколько портов через несколько inbound-секций
    lines = ["listen: :0"]
    first = items[0]
    port = int(first["port"])
    lines = [f"listen: :{port}"]
    lines.append("sniGuard: strict" if first.get("sni") else "sniGuard: disable")
    lines.append("tls:")
    lines.append(f"  cert: {NODE_CERT}")
    lines.append(f"  key: {NODE_KEY}")
    lines.append("auth:")
    lines.append("  type: userpass")
    lines.append("  userpass:")
    for inb in items:
        for c in clients_by_inbound.get(inb["id"], []):
            lines.append(f"    {c['uuid']}: {c['uuid']}")
    obfs = first.get("obfs_type") or ""
    if obfs == "salamander":
        lines.append("obfs:")
        lines.append("  type: salamander")
        lines.append(f"  salamander:")
        lines.append(f"    password: {first.get('obfs_password') or 'hydra'}")
    lines.append("trafficStats:")
    lines.append(f"  listen: {HY2_STATS}")
    lines.append(f"  secret: {HY2_SECRET}")
    lines.append("masquerade:")
    lines.append("  type: proxy")
    lines.append("  proxy:")
    lines.append("    url: https://www.bing.com")
    lines.append("    rewriteHost: true")
    return "\n".join(lines) + "\n"


def build_singbox(inbounds: list[dict], clients_by_inbound: dict[int, list[dict]]) -> dict | None:
    items = [i for i in inbounds if i.get("enabled")]
    if not items:
        return None
    ins: list[dict] = []
    for inb in items:
        proto = inb["protocol"]
        clients = clients_by_inbound.get(inb["id"], [])
        if proto in ("hysteria2", "hy2"):
            item = {
                "type": "hysteria2",
                "tag": inb["tag"],
                "listen": "::",
                "listen_port": int(inb["port"]),
                "users": [{"name": c["name"], "password": c["uuid"]} for c in clients],
                "tls": {
                    "enabled": True,
                    "certificate_path": NODE_CERT,
                    "key_path": NODE_KEY,
                },
            }
            if (inb.get("obfs_type") or "") == "salamander":
                item["obfs"] = {"type": "salamander", "password": inb.get("obfs_password") or ""}
            ins.append(item)
        elif proto == "vless":
            item = {
                "type": "vless",
                "tag": inb["tag"],
                "listen": "::",
                "listen_port": int(inb["port"]),
                "users": [
                    {
                        "name": c["name"],
                        "uuid": c["uuid"],
                        **({"flow": inb["flow"]} if inb.get("flow") else {}),
                    }
                    for c in clients
                ],
                "tls": {
                    "enabled": True,
                    "server_name": inb.get("sni") or "",
                    "certificate_path": NODE_CERT,
                    "key_path": NODE_KEY,
                },
            }
            net = (inb.get("network") or "raw").lower()
            if net == "ws":
                item["transport"] = {"type": "ws", "path": inb.get("path") or "/"}
            elif net == "grpc":
                item["transport"] = {"type": "grpc", "service_name": inb.get("service_name") or ""}
            ins.append(item)
        elif proto == "trojan":
            ins.append({
                "type": "trojan",
                "tag": inb["tag"],
                "listen": "::",
                "listen_port": int(inb["port"]),
                "users": [{"name": c["name"], "password": c["uuid"]} for c in clients],
                "tls": {
                    "enabled": True,
                    "server_name": inb.get("sni") or "",
                    "certificate_path": NODE_CERT,
                    "key_path": NODE_KEY,
                },
            })
    return {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": ins,
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "experimental": {
            "clash_api": {"external_controller": "127.0.0.1:9090", "default_mode": "direct"},
            "cache_file": {"enabled": True, "path": "/opt/hydra-node/singbox-cache.db"},
        },
    }


def build_payload(node: dict, inbounds: list[dict], clients_by_inbound: dict[int, list[dict]],
                  tls: dict | None) -> dict:
    xray = build_xray(inbounds, clients_by_inbound)
    hy2 = build_hysteria2(inbounds, clients_by_inbound)
    sbox = None
    if (node.get("engine") or "xray") == "singbox" and xray is None:
        sbox = build_singbox(inbounds, clients_by_inbound)
    return {
        "node_id": node["id"],
        "engine": node.get("engine") or "xray",
        "xray": xray,
        "hysteria2": hy2,
        "singbox": sbox,
        "tls": tls or {},
        "fallback_port": 8080,
    }

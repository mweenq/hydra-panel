"""Генерация share-ссылок и конфигов клиентов из инбаундов."""
from __future__ import annotations

import urllib.parse as up
from typing import Any

FP = "chrome"


def _addr(node: dict) -> str:
    """Адрес, который попадёт в ссылку: публичный, если задан."""
    return (node.get("public_host") or node.get("address") or "").strip()


def _name(inb: dict, node: dict) -> str:
    remark = inb.get("remark") or inb.get("tag") or "Hydra"
    node_name = node.get("name") or ""
    return f"{remark} · {node_name}" if node_name and node_name not in remark else remark


def _host_header(inb: dict, node: dict) -> str:
    return inb.get("host") or inb.get("sni") or _addr(node)


def _path(inb: dict) -> str:
    return inb.get("path") or "/"


def _net(inb: dict) -> str:
    net = (inb.get("network") or "raw").lower()
    return net


def _security(inb: dict) -> str:
    sec = (inb.get("security") or "").lower()
    if sec:
        return sec
    return "reality" if inb.get("reality_public") else "tls"


def _tls_params(inb: dict, node: dict) -> dict[str, str]:
    sec = _security(inb)
    sni = inb.get("sni") or _addr(node)
    out: dict[str, str] = {"sni": sni}
    if sec == "reality":
        out["security"] = "reality"
        out["pbk"] = inb.get("reality_public") or ""
        out["sid"] = inb.get("reality_short_id") or ""
        out["fp"] = FP
        out["spx"] = "/"
    elif sec in ("tls", "xtls"):
        out["security"] = "tls"
        out["fp"] = FP
        out["alpn"] = "h2,http/1.1"
        out["allowInsecure"] = "0"
    else:
        out["security"] = "none"
    return out


def share_link(inb: dict, node: dict, client: dict) -> str:
    """Универсальная ссылка (vless://, trojan://, hysteria2://)."""
    proto = (inb.get("protocol") or "vless").lower()
    host = _addr(node)
    port = int(inb.get("port") or 443)
    net = _net(inb)
    name = _name(inb, node)

    if proto == "vless":
        p: dict[str, str] = {
            "type": "raw" if net == "tcp" else net,
            "encryption": "none",
            "headerType": "none",
        }
        p.update(_tls_params(inb, node))
        flow = inb.get("flow") or ""
        if flow and net in ("raw", "tcp"):
            p["flow"] = flow
        if net == "grpc":
            p["serviceName"] = inb.get("service_name") or ""
            p["mode"] = "gun"
        elif net == "ws":
            p["path"] = _path(inb)
            p["host"] = _host_header(inb, node)
        elif net == "xhttp":
            p["path"] = _path(inb)
            p["host"] = _host_header(inb, node)
            p["mode"] = inb.get("xhttp_mode") or "auto"
        query = up.urlencode({k: v for k, v in p.items() if v not in ("", None)})
        return f"vless://{client['uuid']}@{host}:{port}?{query}#{up.quote(name)}"

    if proto == "trojan":
        p = {"type": net if net != "raw" else "tcp", "security": "tls"}
        sec = _security(inb)
        sni = inb.get("sni") or _addr(node)
        p["sni"] = sni
        p["fp"] = FP
        if sec == "reality":
            p["security"] = "reality"
            p["pbk"] = inb.get("reality_public") or ""
            p["sid"] = inb.get("reality_short_id") or ""
        if net == "grpc":
            p["serviceName"] = inb.get("service_name") or ""
            p["mode"] = "gun"
        elif net == "xhttp":
            p["path"] = _path(inb)
            p["host"] = _host_header(inb, node)
            p["mode"] = "auto"
        elif net == "ws":
            p["path"] = _path(inb)
            p["host"] = _host_header(inb, node)
        query = up.urlencode({k: v for k, v in p.items() if v not in ("", None)})
        return f"trojan://{client['uuid']}@{host}:{port}?{query}#{up.quote(name)}"

    if proto in ("hysteria2", "hy2"):
        p = {"sni": inb.get("sni") or _addr(node), "insecure": "0"}
        if (inb.get("obfs_type") or "") == "salamander":
            p["obfs"] = "salamander"
            p["obfs-password"] = inb.get("obfs_password") or ""
        query = up.urlencode({k: v for k, v in p.items() if v not in ("", None)})
        return f"hysteria2://{client['uuid']}@{host}:{port}?{query}#{up.quote(name)}"

    return ""


# ------------------------------------------------------------------ clash
def clash_proxy(inb: dict, node: dict, client: dict) -> dict[str, Any]:
    proto = (inb.get("protocol") or "vless").lower()
    net = _net(inb)
    sec = _security(inb)
    proxy: dict[str, Any] = {
        "name": _name(inb, node),
        "type": "vless" if proto == "vless" else ("trojan" if proto == "trojan" else "hysteria2"),
        "server": _addr(node),
        "port": int(inb.get("port") or 443),
        "udp": True,
    }
    if proto == "vless":
        proxy["uuid"] = client["uuid"]
        proxy["flow"] = inb.get("flow") or ""
        proxy["client-fingerprint"] = FP
        proxy["tls"] = sec in ("tls", "reality", "xtls")
        if sec == "reality":
            proxy["reality-opts"] = {
                "public-key": inb.get("reality_public") or "",
                "short-id": inb.get("reality_short_id") or "",
            }
        proxy["servername"] = inb.get("sni") or _addr(node)
        proxy["network"] = net if net != "raw" else "tcp"
        if net == "grpc":
            proxy["grpc-opts"] = {"grpc-service-name": inb.get("service_name") or ""}
        elif net == "ws":
            proxy["ws-opts"] = {
                "path": _path(inb),
                "headers": {"Host": _host_header(inb, node)},
            }
        elif net == "xhttp":
            proxy["xhttp-opts"] = {
                "path": _path(inb),
                "host": _host_header(inb, node),
                "mode": inb.get("xhttp_mode") or "auto",
            }
        if not proxy["flow"]:
            proxy.pop("flow")
        if not proxy["tls"]:
            proxy.pop("tls")
            proxy.pop("servername", None)
    elif proto == "trojan":
        proxy["password"] = client["uuid"]
        proxy["sni"] = inb.get("sni") or _addr(node)
        proxy["skip-cert-verify"] = False
        proxy["client-fingerprint"] = FP
        if sec == "reality":
            proxy["reality-opts"] = {
                "public-key": inb.get("reality_public") or "",
                "short-id": inb.get("reality_short_id") or "",
            }
        proxy["network"] = net if net != "raw" else "tcp"
        if net == "grpc":
            proxy["grpc-opts"] = {"grpc-service-name": inb.get("service_name") or ""}
        elif net == "xhttp":
            proxy["xhttp-opts"] = {"path": _path(inb), "host": _host_header(inb, node)}
    else:
        proxy["password"] = client["uuid"]
        proxy["sni"] = inb.get("sni") or _addr(node)
        if (inb.get("obfs_type") or "") == "salamander":
            proxy["obfs"] = "salamander"
            proxy["obfs-password"] = inb.get("obfs_password") or ""
    return proxy


# ------------------------------------------------------------------ sing-box
def singbox_outbound(inb: dict, node: dict, client: dict) -> dict[str, Any]:
    proto = (inb.get("protocol") or "vless").lower()
    net = _net(inb)
    sec = _security(inb)
    sni = inb.get("sni") or _addr(node)
    out: dict[str, Any] = {
        "type": "vless" if proto == "vless" else ("trojan" if proto == "trojan" else "hysteria2"),
        "tag": _name(inb, node),
        "server": _addr(node),
        "server_port": int(inb.get("port") or 443),
    }
    if proto in ("vless", "trojan"):
        out["uuid" if proto == "vless" else "password"] = client["uuid"]
        if proto == "vless" and inb.get("flow") and net in ("raw", "tcp"):
            out["flow"] = inb["flow"]
        tls: dict[str, Any] = {"enabled": True, "server_name": sni, "utls": {"enabled": True, "fingerprint": FP}}
        if sec == "reality":
            tls["reality"] = {
                "enabled": True,
                "public_key": inb.get("reality_public") or "",
                "short_id": inb.get("reality_short_id") or "",
            }
        out["tls"] = tls
        if net == "grpc":
            out["transport"] = {"type": "grpc", "service_name": inb.get("service_name") or ""}
        elif net == "ws":
            out["transport"] = {
                "type": "ws",
                "path": _path(inb),
                "headers": {"Host": _host_header(inb, node)},
            }
        elif net == "xhttp":
            out["transport"] = {
                "type": "http",
                "host": [_host_header(inb, node)],
                "path": _path(inb),
            }
    else:
        out["password"] = client["uuid"]
        out["tls"] = {"enabled": True, "server_name": sni, "insecure": False}
        if (inb.get("obfs_type") or "") == "salamander":
            out["obfs"] = {"type": "salamander", "password": inb.get("obfs_password") or ""}
    return out


# ------------------------------------------------------------------ xray (клиент)
def xray_outbound(inb: dict, node: dict, client: dict) -> dict[str, Any] | None:
    """Outbound для клиентского конфига Xray: VLESS и Trojan."""
    proto = (inb.get("protocol") or "vless").lower()
    if proto not in ("vless", "trojan"):
        return None
    net = _net(inb)
    sec = _security(inb)
    sni = inb.get("sni") or _addr(node)
    host = _addr(node)
    port = int(inb.get("port") or 443)

    stream: dict[str, Any] = {"network": "tcp" if net in ("raw", "tcp") else net}
    if sec == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": sni,
            "fingerprint": FP,
            "publicKey": inb.get("reality_public") or "",
            "shortId": inb.get("reality_short_id") or "",
            "spiderX": "/",
        }
    elif sec in ("tls", "xtls"):
        stream["security"] = "tls"
        stream["tlsSettings"] = {
            "serverName": sni,
            "fingerprint": FP,
            "alpn": ["h2", "http/1.1"],
        }
    else:
        stream["security"] = "none"

    if net == "ws":
        stream["wsSettings"] = {"path": _path(inb), "headers": {"Host": _host_header(inb, node)}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": inb.get("service_name") or "", "multiMode": False}
    elif net == "xhttp":
        stream["xhttpSettings"] = {
            "path": _path(inb),
            "host": _host_header(inb, node),
            "mode": inb.get("xhttp_mode") or "auto",
        }

    if proto == "vless":
        user: dict[str, Any] = {"id": client["uuid"], "encryption": "none"}
        flow = inb.get("flow") or ""
        if flow and net in ("raw", "tcp"):
            user["flow"] = flow
        settings = {"vnext": [{"address": host, "port": port, "users": [user]}]}
    else:
        settings = {"servers": [{"address": host, "port": port, "password": client["uuid"]}]}

    return {
        "tag": _name(inb, node),
        "protocol": proto,
        "settings": settings,
        "streamSettings": stream,
    }


def happ_outbound(inb: dict, node: dict, client: dict) -> dict[str, Any] | None:
    """Outbound в диалекте Happ: то же ядро Xray + свой транспорт `hysteria`.

    Hysteria2 в апстрим-Xray нет, но в форке Happ она есть как `protocol:
    hysteria` с `hysteriaSettings`. Поля — из infra/conf/transport_internet.go
    форка Happ-proxy/Xray-core.
    """
    proto = (inb.get("protocol") or "vless").lower()
    if proto not in ("hysteria2", "hy2"):
        return xray_outbound(inb, node, client)
    return {
        "tag": _name(inb, node),
        "protocol": "hysteria",
        "settings": {
            "version": 2,
            "address": _addr(node),
            "port": int(inb.get("port") or 443),
        },
        "streamSettings": {
            "network": "hysteria",
            "security": "tls",
            "tlsSettings": {
                "serverName": inb.get("sni") or _addr(node),
                "alpn": ["h3"],
            },
            "hysteriaSettings": {
                "version": 2,
                "auth": client["uuid"],
                "udpIdleTimeout": 60,
            },
        },
    }

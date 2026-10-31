"""Публичные страницы: корень, страница подписки, сама подписка."""
from __future__ import annotations

import base64
import io

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from .. import config, db
from ..services import clients as clients_svc, devices, subs
from .templating import TEMPLATES

router = APIRouter()


def _qr_data_uri(text: str) -> str:
    try:
        import qrcode

        q = qrcode.QRCode(box_size=8, border=2)
        q.add_data(text)
        q.make(fit=True)
        img = q.make_image(fill_color="#111827", back_color="#ffffff")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return ""


@router.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    s = db.all_settings()
    mode = s.get("root_mode") or "dashboard"
    if mode == "redirect_sub":
        target = s.get("root_redirect") or ""
        if target:
            return RedirectResponse(target, status_code=302)
    if mode == "custom" and (s.get("root_html") or "").strip():
        return HTMLResponse(s["root_html"])
    # dashboard (по умолчанию): кидаем всех на панель/логин
    return RedirectResponse("/dashboard", status_code=302)


def _accent(color: str, theme: str) -> str:
    """Цвет полосы не должен сливаться с фоном темы."""
    try:
        c = (color or "").lstrip("#")
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, TypeError):
        return "#e7e9ec" if theme == "dark" else "#111827"
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    if theme == "dark" and lum < 0.45:
        return "#e7e9ec"
    if theme != "dark" and lum > 0.75:
        return "#111827"
    return color


GUIDES = [
    {"name": "Happ", "os": ["ios", "android", "macos"], "fmt": "v2ray",
     "steps": ["Откройте приложение и нажмите «+».",
               "Выберите «Добавить из буфера обмена» или отсканируйте QR.",
               "Подключитесь и разрешите VPN."]},
    {"name": "v2RayTun", "os": ["ios", "android", "macos"], "fmt": "v2ray", "deep": "v2raytun",
     "steps": ["Нажмите «+» в правом верхнем углу.",
               "«Импорт из буфера обмена» — вставьте ссылку.",
               "Выберите сервер и подключитесь."]},
    {"name": "Streisand", "os": ["ios", "macos"], "fmt": "v2ray",
     "steps": ["«Добавить» → «Импорт подписки».",
               "Вставьте ссылку и сохраните.",
               "Выберите сервер в списке."]},
    {"name": "Shadowrocket", "os": ["ios"], "fmt": "v2ray",
     "steps": ["«+» → тип «Subscribe».",
               "Вставьте ссылку → Save.",
               "Серверы подтянутся автоматически."]},
    {"name": "Clash / mihomo / FlClash", "os": ["android", "windows", "macos", "linux"], "fmt": "clash",
     "steps": ["Скопируйте ссылку с окончанием /clash.",
               "Профили → Добавить → Из URL.",
               "Обновите профиль и включите VPN."]},
    {"name": "sing-box / Hiddify / Karing", "os": ["ios", "android", "windows", "macos", "linux"], "fmt": "singbox",
     "steps": ["Скопируйте ссылку с окончанием /singbox.",
               "Добавьте её как профиль из URL.",
               "Включите туннель."]},
    {"name": "v2rayNG / NekoBox", "os": ["android", "windows"], "fmt": "v2ray",
     "steps": ["«+» → «Импорт подписки».",
               "Вставьте ссылку.",
               "Обновите список серверов."]},
]


@router.get("/decoy", response_class=HTMLResponse)
def decoy(request: Request):
    """Заглушка для fallback: nginx на 127.0.0.1:8080 проксирует сюда."""
    return TEMPLATES.TemplateResponse(
        request, "decoy.html", {"request": request, "s": db.all_settings()}
    )


ROOT_DIR = config.TEMPLATE_DIR.parent.parent


@router.get("/install-node.sh", response_class=PlainTextResponse)
def install_node_script():
    p = config.STATIC_DIR / "install-node.sh"
    return PlainTextResponse(p.read_text(encoding="utf-8"), media_type="text/plain")


@router.get("/node-agent.py", response_class=PlainTextResponse)
def node_agent_script():
    p = ROOT_DIR / "node" / "hydra-node.py"
    if not p.exists():
        return PlainTextResponse("# agent not found", status_code=404)
    return PlainTextResponse(p.read_text(encoding="utf-8"), media_type="text/plain")


def _platform(ua: str) -> str:
    u = (ua or "").lower()
    if "iphone" in u or "ipad" in u or "ios" in u:
        return "ios"
    if "android" in u:
        return "android"
    if "windows" in u:
        return "windows"
    if "macintosh" in u or "mac os" in u:
        return "macos"
    if "linux" in u:
        return "linux"
    return ""


def _is_client(ua: str) -> bool:
    u = (ua or "").lower()
    if not u:
        return False
    keys = (
        "clash", "mihomo", "stash", "flclash", "sing-box", "sfa", "sfi", "sft",
        "shadowrocket", "v2raytun", "happ", "streisand", "v2rayng", "nekobox",
        "hiddify", "karing", "incy", "foxray", "sagernet", "clashx", "verge",
    )
    return any(k in u for k in keys)


def _sub_response(client: dict, fmt: str, ua: str = "") -> Response:
    body, ctype = subs.render(client, fmt)
    headers = subs.sub_headers(client, fmt, ua)
    return Response(content=body, media_type=ctype.split(";")[0], headers=headers)


# ------------------------------------------------------------------ HWID
HWID_REQUIRED_TEXT = "Нужен клиент, передающий X-HWID (например, Happ)."
HWID_LIMIT_TEXT = "Лимит устройств исчерпан. Отвяжите устройство в боте или у администратора."
HWID_BLOCKED_TEXT = "Устройство заблокировано."


def _hwid_state(request: Request, client: dict) -> dict:
    return devices.touch(
        client,
        request.headers.get(devices.HEADER, ""),
        platform=request.headers.get("x-device-os", ""),
        model=request.headers.get("x-device-model", ""),
        ver_os=request.headers.get("x-ver-os", ""),
        ua=request.headers.get("user-agent", ""),
        ip=request.client.host if request.client else "",
    )


def _hwid_denied(text: str, headers: dict[str, str]) -> Response:
    base = {"x-hwid-active": "true"}
    base.update(headers)
    return PlainTextResponse(text, status_code=403, headers=base)


def _serve(request: Request, client: dict, fmt: str) -> Response:
    """Отдаёт профиль, попутно считая устройства и проверяя лимит."""
    ua = request.headers.get("user-agent", "")
    if not devices.enabled():
        return _sub_response(client, fmt, ua)

    state = _hwid_state(request, client)
    if state["status"] == "blocked":
        return _hwid_denied(HWID_BLOCKED_TEXT, {"x-hwid-max-devices-reached": "true"})
    if state["status"] == "limit":
        return _hwid_denied(HWID_LIMIT_TEXT, {
            "x-hwid-max-devices-reached": "true", "x-hwid-limit": "true",
        })
    if state["status"] == "none" and devices.limit_for(client) > 0:
        return _hwid_denied(HWID_REQUIRED_TEXT, {"x-hwid-not-supported": "true"})

    resp = _sub_response(client, fmt, ua)
    resp.headers["x-hwid-active"] = "true"
    resp.headers["subscription-always-hwid-enable"] = "true"
    return resp


@router.get("/sub/{token}")
def sub_entry(request: Request, token: str, format: str = ""):
    c = clients_svc.get_by_sub(token)
    if not c:
        return PlainTextResponse("subscription not found", status_code=404)
    ua = request.headers.get("user-agent", "")
    if not c["enabled"]:
        return PlainTextResponse("subscription disabled", status_code=403)
    if clients_svc.client_status(c) in ("expired", "limit"):
        return PlainTextResponse("subscription expired", status_code=403)
    if format or _is_client(ua):
        return _serve(request, c, subs.detect_format(ua, format))

    accept = request.headers.get("accept", "")
    if "text/html" not in accept and "application/xhtml" not in accept:
        return _serve(request, c, "v2ray")

    s = db.all_settings()
    platform = _platform(ua)
    url = clients_svc.sub_url(token, s.get("panel_url") or str(request.base_url).rstrip("/"))
    guides = []
    for g in sorted(GUIDES, key=lambda x: 0 if platform in x["os"] else 1):
        item = dict(g)
        item["link"] = url + ("/clash" if g["fmt"] == "clash" else "/singbox" if g["fmt"] == "singbox" else "")
        # схемы, которые подтверждены в документации клиента
        if g.get("deep") == "v2raytun":
            item["deep_link"] = f"v2raytun://import/{url}"
        guides.append(item)
    return TEMPLATES.TemplateResponse(
        request, "sub.html",
        {
            "request": request, "s": s, "c": c, "url": url,
            "qr": _qr_data_uri(url) if s.get("sub_show_qr") else "",
            "used_pct": int(min(100, (c["up_bytes"] + c["down_bytes"]) * 100 / c["limit_bytes"]))
            if c.get("limit_bytes") else 0,
            "used_h": clients_svc.human_bytes((c["up_bytes"] or 0) + (c["down_bytes"] or 0)),
            "limit_h": clients_svc.human_bytes(c["limit_bytes"]) if c.get("limit_bytes") else "без лимита",
            "expire_h": (c.get("expire_at") or "")[:10] or "бессрочно",
            "status": clients_svc.client_status(c),
            "dev": devices.summary(c),
            "platform": platform,
            "guides": guides,
            "accent": _accent(s.get("sub_theme") or "", s.get("theme") or "dark"),
        },
    )


@router.get("/sub/{token}/{fmt}")
def sub_format(request: Request, token: str, fmt: str):
    c = clients_svc.get_by_sub(token)
    if not c:
        return PlainTextResponse("subscription not found", status_code=404)
    if not c["enabled"] or clients_svc.client_status(c) in ("expired", "limit"):
        return PlainTextResponse("subscription inactive", status_code=403)
    return _serve(request, c, subs.detect_format("", fmt))

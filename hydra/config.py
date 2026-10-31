"""Hydra Panel — конфигурация и пути."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

VERSION = "1.0.2"
APP_NAME = "Hydra Panel"


def _home() -> Path:
    env = os.environ.get("HYDRA_HOME")
    if env:
        return Path(env).expanduser().resolve()
    # dev-режим: <project>/data
    return (Path(__file__).resolve().parent.parent / "data")


HOME: Path = _home()
DATA_DIR: Path = HOME / "data"
CERT_DIR: Path = HOME / "certs"
RUN_DIR: Path = HOME / "run"
STATIC_DIR: Path = Path(__file__).resolve().parent / "static"
TEMPLATE_DIR: Path = Path(__file__).resolve().parent / "templates"

DB_PATH: Path = Path(os.environ.get("HYDRA_DB", DATA_DIR / "hydra.db"))
NODE_API_PORT = int(os.environ.get("HYDRA_NODE_PORT", "8443"))
WEB_HOST = os.environ.get("HYDRA_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("HYDRA_PORT", "8080"))

# Публичный адрес панели (для ссылок на подписку). Переопределяется настройкой panel_url.
PANEL_URL = os.environ.get("HYDRA_PANEL_URL", "").rstrip("/")

# Секрет для подписи сессий/токенов. Генерируется один раз в data/secret.key
def _secret() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = DATA_DIR / "secret.key"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    val = secrets.token_urlsafe(48)
    p.write_text(val, encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return val


def ensure_dirs() -> None:
    for d in (HOME, DATA_DIR, CERT_DIR, RUN_DIR):
        d.mkdir(parents=True, exist_ok=True)


ensure_dirs()
SECRET = _secret()

# Xray использует "raw" начиная с 25.6, старые сборки понимают только "tcp".
XRAY_RAW_COMPAT = os.environ.get("HYDRA_XRAY_TCP_COMPAT", "0") == "1"

DEFAULT_SETTINGS: dict[str, object] = {
    "panel_url": PANEL_URL,
    "panel_name": "Hydra",
    "theme": "dark",
    "sub_title": "Hydra VPN",
    "sub_support_url": "",
    "sub_announce": "",
    "sub_theme": "#111827",
    "sub_show_qr": 1,
    "root_mode": "dashboard",  # dashboard | redirect_sub | custom
    "root_html": "",
    "root_redirect": "",
    "fallback_dest": "yahoo.com:443",
    "decoy_title": "",
    "default_engine": "xray",
    "traffic_check_interval": 60,
    "sub_update_hours": 12,
    "backup_keep": 14,
    "backup_tg_chat": "",
    "admin_tg_ids": "",
    "bot_token": "",
    "user_bot_token": "",
    "user_bot_enabled": 1,
    # оплата в боте подписок
    "pay_enabled": 1,
    "pay_currency": "RUB",          # валюта для оплаты через провайдера
    "pay_provider_token": "",       # токен платёжного провайдера Telegram
    "pay_manual_enabled": 0,        # перевод вручную с подтверждением админом
    "pay_manual_details": "",       # реквизиты: карта, СБП, USDT…
    "pay_support_url": "",          # куда идти, если что-то пошло не так
    "pay_title": "Продление подписки",
    "bot_texts": {},                # переопределения сообщений бота
    # привязка устройств
    "hwid_enabled": 1,
    "hwid_limit": 0,                # 0 — не ограничивать, но устройства считаются
    "hwid_reset_on_pay": 0,         # оплата снимает все привязки
    # напоминания об окончании подписки
    "notify_enabled": 1,
    "notify_days": 3,
    "acme_email": "",
    "tls_mode": "acme",  # acme | selfsigned
    "api_key": "",
    "wildcard_domain": "",  # example.com — сертификат раздаётся нодам
}

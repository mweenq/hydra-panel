"""Напоминания об окончании подписки — сообщением в боте подписок.

Раз в несколько часов проверяем, у кого срок кончается в ближайшие N дней,
и пишем в Telegram. Одному клиенту — не чаще раза в сутки (метка в настройках).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from .. import db
from . import bottexts, clients as clients_svc

log = logging.getLogger("hydra.notify")

SENT_KEY = "notify_sent"
CHECK_EVERY = 6 * 3600


def enabled() -> bool:
    return bool(int(db.get_setting("notify_enabled", 1) or 0))


def days() -> int:
    try:
        return max(1, int(db.get_setting("notify_days", 3) or 3))
    except (TypeError, ValueError):
        return 3


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def due_clients(days_ahead: int | None = None) -> list[dict]:
    """Кому пора напомнить: срок истекает в ближайшие N дней, есть Telegram."""
    horizon = days_ahead if days_ahead is not None else days()
    now = datetime.now(timezone.utc)
    limit = now + timedelta(days=horizon)
    rows = db.dicts(db.q(
        """SELECT * FROM clients
           WHERE tg_id<>'' AND enabled=1 AND expire_at<>'' ORDER BY expire_at"""
    ))
    out = []
    for r in rows:
        exp = _parse(r["expire_at"])
        if exp and now <= exp <= limit:
            r["days_left"] = max(0, (exp - now).days)
            out.append(r)
    return out


def _send(token: str, chat_id: str, text: str) -> bool:
    import httpx

    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        return r.status_code == 200 and bool(r.json().get("ok"))
    except Exception as e:  # noqa: BLE001
        log.warning("notify %s: %s", chat_id, e)
        return False


def send_reminders(dry_run: bool = False) -> int:
    """Отправляет напоминания. Возвращает число получателей."""
    if not enabled():
        return 0
    token = str(db.get_setting("user_bot_token", "") or "").strip()
    if not token and not dry_run:
        return 0

    sent = db.get_setting(SENT_KEY, {}) or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = (db.get_setting("panel_url") or "").rstrip("/")
    count = 0

    for c in due_clients():
        key = str(c["id"])
        if sent.get(key) == today:
            continue
        text = bottexts.render(
            "notify_expiring",
            name=c["name"],
            days_left=c["days_left"],
            expire=(c["expire_at"] or "")[:10],
            url=clients_svc.sub_url(c["sub_token"], base),
            support=str(db.get_setting("pay_support_url", "") or ""),
        )
        if dry_run:
            count += 1
            continue
        if _send(token, c["tg_id"], text):
            sent[key] = today
            count += 1

    if count and not dry_run:
        db.set_setting(SENT_KEY, sent)
        db.audit("notify.expiring", meta={"count": count})
    return count


def _loop() -> None:
    time.sleep(60)  # не мешаем старту панели
    while True:
        try:
            n = send_reminders()
            if n:
                log.info("напоминаний отправлено: %s", n)
        except Exception as e:  # noqa: BLE001
            log.warning("notify loop: %s", e)
        time.sleep(CHECK_EVERY)


def start_scheduler() -> None:
    if not enabled():
        return
    threading.Thread(target=_loop, name="hydra-notify", daemon=True).start()

"""Запуск ботов в фоне."""
from __future__ import annotations

import logging
import threading

from .. import db

log = logging.getLogger("hydra.bots")


def _run(bot, name: str) -> None:
    try:
        log.info("bot %s polling", name)
        bot.infinity_polling(timeout=30, long_polling_timeout=25, skip_pending=True)
    except Exception as e:  # noqa: BLE001
        log.warning("bot %s stopped: %s", name, e)


def start_bots() -> None:
    from . import admin_bot, user_bot

    token = str(db.get_setting("bot_token", "") or "").strip()
    if token:
        try:
            bot = admin_bot.build(token)
            threading.Thread(target=_run, args=(bot, "admin"), daemon=True).start()
        except Exception as e:  # noqa: BLE001
            log.warning("admin bot init failed: %s", e)

    utoken = str(db.get_setting("user_bot_token", "") or "").strip()
    if utoken and db.get_setting("user_bot_enabled", 1):
        try:
            bot = user_bot.build(utoken)
            threading.Thread(target=_run, args=(bot, "user"), daemon=True).start()
        except Exception as e:  # noqa: BLE001
            log.warning("user bot init failed: %s", e)

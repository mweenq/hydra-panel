"""Админ-бот: статистика, ноды, клиенты, бэкапы."""
from __future__ import annotations

import logging

import telebot
from telebot import types

from .. import db
from ..services import backup, clients as clients_svc, nodes as nodes_svc, traffic

log = logging.getLogger("hydra.bot")


def _admin_ids() -> set[str]:
    ids: set[str] = set()
    raw = str(db.get_setting("admin_tg_ids", "") or "")
    for part in raw.replace(";", ",").split(","):
        if part.strip():
            ids.add(part.strip())
    for row in db.q("SELECT tg_id FROM admins WHERE tg_id != ''"):
        ids.add(str(row["tg_id"]))
    return ids


def _kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("Статистика", callback_data="stats"),
        types.InlineKeyboardButton("Ноды", callback_data="nodes"),
        types.InlineKeyboardButton("Клиенты", callback_data="clients"),
        types.InlineKeyboardButton("Бэкап", callback_data="backup"),
    )
    return kb


def _stats_text() -> str:
    s = traffic.summary()
    nodes = nodes_svc.list_nodes()
    online = sum(1 for n in nodes if n["status"] == "online")
    lines = [
        "<b>Панель</b>",
        f"Клиентов: {s['active']} активных из {s['clients']}",
        f"Трафик: {s['total_h']} (↑ {clients_svc.human_bytes(s['up'])} / ↓ {clients_svc.human_bytes(s['down'])})",
        f"Ноды: {online} онлайн из {len(nodes)}",
        "",
        "<b>Топ по трафику</b>",
    ]
    for c in traffic.top_clients(5):
        lines.append(f"· {c['name']} — {c['used_h']}")
    return "\n".join(lines)


def _nodes_text() -> str:
    nodes = nodes_svc.list_nodes()
    if not nodes:
        return "Нод нет. Добавьте через веб-панель."
    out = ["<b>Ноды</b>"]
    for n in nodes:
        mark = {"online": "●", "pending": "○", "error": "×", "offline": "×"}.get(n["status"], "○")
        out.append(f"{mark} {n['name']} — {n['address']} · {n['engine']} · {n['status']}")
    return "\n".join(out)


def _clients_text() -> str:
    rows = clients_svc.list_clients()[:15]
    if not rows:
        return "Клиентов нет."
    out = ["<b>Клиенты</b>"]
    for c in rows:
        out.append(f"{c['id']}. {c['name']} — {c['used_h']}/{c['limit_h']} · {c['status']}")
    return "\n".join(out)


def build(token: str) -> telebot.TeleBot:
    bot = telebot.TeleBot(token, parse_mode="HTML", threaded=False)

    def guard(m) -> bool:
        if str(m.from_user.id) not in _admin_ids():
            bot.reply_to(m, "Доступ закрыт. Добавьте свой Telegram ID в настройках панели.")
            return False
        return True

    @bot.message_handler(commands=["start", "menu"])
    def start(m):
        if not guard(m):
            return
        bot.send_message(
            m.chat.id,
            "<b>Hydra</b>\nУправление панелью. Выберите раздел.",
            reply_markup=_kb(),
        )

    @bot.message_handler(commands=["stats"])
    def stats(m):
        if guard(m):
            bot.send_message(m.chat.id, _stats_text(), reply_markup=_kb())

    @bot.message_handler(commands=["nodes"])
    def nodes(m):
        if guard(m):
            bot.send_message(m.chat.id, _nodes_text(), reply_markup=_kb())

    @bot.message_handler(commands=["clients"])
    def clients(m):
        if guard(m):
            bot.send_message(m.chat.id, _clients_text(), reply_markup=_kb())

    @bot.message_handler(commands=["backup"])
    def make_backup(m):
        if not guard(m):
            return
        bot.send_message(m.chat.id, "Собираю архив…")
        try:
            path = backup.create()
        except Exception as e:  # noqa: BLE001
            bot.send_message(m.chat.id, f"Ошибка: {e}")
            return
        with open(path, "rb") as fh:
            bot.send_document(m.chat.id, fh, caption=f"Бэкап {path.name}")
        db.audit("backup.telegram", actor=str(m.from_user.id), target=path.name)

    @bot.message_handler(commands=["push"])
    def push(m):
        if not guard(m):
            return
        parts = (m.text or "").split()
        if len(parts) < 2:
            bot.send_message(m.chat.id, "Использование: /push &lt;id ноды&gt;")
            return
        res = nodes_svc.push(int(parts[1]))
        bot.send_message(m.chat.id, "Применено." if res.get("ok") else f"Ошибка: {res.get('error')}")

    @bot.message_handler(commands=["ban", "unban"])
    def toggle(m):
        if not guard(m):
            return
        parts = (m.text or "").split()
        if len(parts) < 2:
            bot.send_message(m.chat.id, "Использование: /ban &lt;id клиента&gt;")
            return
        cid = int(parts[1])
        c = clients_svc.get_client(cid)
        if not c:
            bot.send_message(m.chat.id, "Клиент не найден.")
            return
        enable = 1 if parts[0].startswith("/unban") else 0
        clients_svc.update_client(cid, enabled=enable)
        nodes_svc.push_async()
        bot.send_message(m.chat.id, f"{c['name']}: {'включён' if enable else 'выключен'}")

    @bot.message_handler(commands=["help"])
    def help_cmd(m):
        if not guard(m):
            return
        bot.send_message(
            m.chat.id,
            "/stats — сводка\n/nodes — список нод\n/clients — клиенты\n"
            "/push &lt;id&gt; — применить конфиг\n/ban &lt;id&gt;, /unban &lt;id&gt;\n/backup — архив в чат",
        )

    @bot.callback_query_handler(func=lambda c: True)
    def cb(call):
        if str(call.from_user.id) not in _admin_ids():
            bot.answer_callback_query(call.id, "Нет доступа")
            return
        bot.answer_callback_query(call.id)
        try:
            if call.data == "stats":
                bot.edit_message_text(_stats_text(), call.message.chat.id, call.message.message_id,
                                      reply_markup=_kb())
            elif call.data == "nodes":
                bot.edit_message_text(_nodes_text(), call.message.chat.id, call.message.message_id,
                                      reply_markup=_kb())
            elif call.data == "clients":
                bot.edit_message_text(_clients_text(), call.message.chat.id, call.message.message_id,
                                      reply_markup=_kb())
            elif call.data == "backup":
                bot.send_message(call.message.chat.id, "Собираю архив…")
                path = backup.create()
                with open(path, "rb") as fh:
                    bot.send_document(call.message.chat.id, fh, caption=f"Бэкап {path.name}")
        except Exception as e:  # noqa: BLE001
            log.warning("callback failed: %s", e)

    return bot

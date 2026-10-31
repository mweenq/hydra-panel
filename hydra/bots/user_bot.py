"""Бот выдачи подписок: ссылка, QR, инструкции, статус, оплата."""
from __future__ import annotations

import io
import logging

import telebot
from telebot import types

from .. import db
from ..services import bottexts, clients as clients_svc, devices, payments as pay_svc

log = logging.getLogger("hydra.userbot")


def t(key: str, **kw) -> str:
    return bottexts.render(key, **kw)


def _admins() -> list[str]:
    raw = str(db.get_setting("admin_tg_ids", "") or "")
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


def _is_admin(tg_id) -> bool:
    return str(tg_id) in _admins()


def _find(tg_id: int) -> dict | None:
    row = db.q1("SELECT * FROM clients WHERE tg_id=?", (str(tg_id),))
    return dict(row) if row else None


def _expire_h(c: dict) -> str:
    return (c.get("expire_at") or "")[:10] or "бессрочно"


def _status_text(c: dict) -> str:
    used = (c["up_bytes"] or 0) + (c["down_bytes"] or 0)
    limit = c["limit_bytes"] or 0
    return t(
        "status",
        name=c["name"],
        status=clients_svc.client_status(c),
        traffic=clients_svc.human_bytes(used),
        limit=f" из {clients_svc.human_bytes(limit)}" if limit else " (без лимита)",
        expire=_expire_h(c),
        days_left=clients_svc.days_left(c) or "",
    )


def _kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton(t("btn_sub"), callback_data="sub"),
        types.InlineKeyboardButton(t("btn_qr"), callback_data="qr"),
    )
    kb.add(
        types.InlineKeyboardButton(t("btn_status"), callback_data="status"),
        types.InlineKeyboardButton(t("btn_howto"), callback_data="howto"),
    )
    if devices.enabled():
        kb.add(types.InlineKeyboardButton(t("btn_devices"), callback_data="devices"))
    if pay_svc.active():
        kb.add(types.InlineKeyboardButton(t("btn_pay"), callback_data="pay"))
    return kb


def _howto_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("Happ", callback_data="how:happ"),
        types.InlineKeyboardButton("v2RayTun", callback_data="how:v2raytun"),
    )
    kb.add(
        types.InlineKeyboardButton("Clash", callback_data="how:clash"),
        types.InlineKeyboardButton("sing-box", callback_data="how:singbox"),
    )
    kb.add(types.InlineKeyboardButton("Windows / Linux", callback_data="how:windows"))
    return kb


def build(token: str) -> telebot.TeleBot:
    bot = telebot.TeleBot(token, parse_mode="HTML", threaded=False)

    def notify_admins(payment: dict, extra: str = "") -> None:
        c = clients_svc.get_client(payment["client_id"]) or {}
        text = t(
            "pay_admin_new",
            id=payment["id"],
            name=c.get("name", "—"),
            plan=payment["plan_name"],
            price=pay_svc.price_h(payment["amount"], payment["currency"]),
            method=pay_svc.METHODS.get(payment["method"], payment["method"]),
        )
        if extra:
            text += "\n" + extra
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton(t("btn_approve"), callback_data=f"payok:{payment['id']}"),
            types.InlineKeyboardButton(t("btn_reject"), callback_data=f"payno:{payment['id']}"),
        )
        for admin in _admins():
            try:
                bot.send_message(int(admin), text, reply_markup=kb)
            except Exception as e:  # noqa: BLE001
                log.warning("payment notify %s: %s", admin, e)

    def tell_client(tg_id: str, text: str) -> None:
        if not tg_id:
            return
        try:
            bot.send_message(int(tg_id), text, reply_markup=_kb())
        except Exception as e:  # noqa: BLE001
            log.warning("payment notify client %s: %s", tg_id, e)

    # -------------------------------------------------------------- команды
    @bot.message_handler(commands=["start"])
    def start(m):
        c = _find(m.from_user.id)
        if not c:
            bot.send_message(
                m.chat.id,
                t("start_no_sub", support=pay_svc.support_url()),
            )
            return
        bot.send_message(m.chat.id, _status_text(c), reply_markup=_kb())

    @bot.message_handler(commands=["sub"])
    def sub_cmd(m):
        c = _find(m.from_user.id)
        if not c:
            bot.send_message(m.chat.id, t("not_linked"))
            return
        url = clients_svc.sub_url(c["sub_token"])
        bot.send_message(m.chat.id, t("sub_link", url=url, name=c["name"]), reply_markup=_kb())

    @bot.message_handler(commands=["help"])
    def help_cmd(m):
        bot.send_message(m.chat.id, t("help"), reply_markup=_kb())

    # -------------------------------------------------------------- привязка
    @bot.message_handler(content_types=["text"])
    def bind(m):
        text = (m.text or "").strip()
        token = text.rstrip("/").split("/")[-1]
        c = clients_svc.get_by_sub(token)
        if not c:
            bot.send_message(m.chat.id, t("bind_bad_token"))
            return
        if c.get("tg_id") and str(c["tg_id"]) != str(m.from_user.id):
            bot.send_message(m.chat.id, t("bind_taken"))
            return
        db.ex("UPDATE clients SET tg_id=? WHERE id=?", (str(m.from_user.id), c["id"]))
        db.audit("client.bind_tg", target=str(c["id"]), meta={"tg": m.from_user.id})
        c["tg_id"] = str(m.from_user.id)
        bot.send_message(
            m.chat.id, t("bind_ok", name=c["name"], status=_status_text(c)), reply_markup=_kb()
        )

    # -------------------------------------------------------------- оплата
    def show_plans(chat_id: int, c: dict) -> None:
        plans = pay_svc.list_plans(only_enabled=True)
        if not plans:
            bot.send_message(chat_id, t("pay_none", support=pay_svc.support_url()))
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for p in plans:
            kb.add(types.InlineKeyboardButton(
                f"{p['name']} · {p['price_h']}", callback_data=f"plan:{p['id']}"
            ))
        bot.send_message(chat_id, t("pay_title", name=c["name"]), reply_markup=kb)

    def show_plan(chat_id: int, pid: int) -> None:
        p = pay_svc.get_plan(pid)
        if not p:
            return
        methods = pay_svc.methods_for(p)
        kb = types.InlineKeyboardMarkup(row_width=1)
        if "stars" in methods:
            kb.add(types.InlineKeyboardButton(t("btn_pay_stars"), callback_data=f"pay:stars:{pid}"))
        if "provider" in methods:
            kb.add(types.InlineKeyboardButton(t("btn_pay_card"), callback_data=f"pay:provider:{pid}"))
        if "manual" in methods:
            kb.add(types.InlineKeyboardButton(t("btn_pay_manual"), callback_data=f"pay:manual:{pid}"))
        kb.add(types.InlineKeyboardButton(t("btn_back"), callback_data="pay"))
        bot.send_message(
            chat_id,
            t("pay_plan", plan=p["name"], days=p["days_h"], traffic=p["traffic_h"],
              price=p["price_h"], note=p.get("note") or ""),
            reply_markup=kb,
        )

    def show_devices(chat_id: int, c: dict) -> None:
        items = devices.list_devices(c["id"])
        limit = devices.limit_for(c)
        if not items:
            bot.send_message(chat_id, t("devices_empty"))
            return
        lines = [t("devices_title", count=len(items),
                   limit=f" из {limit}" if limit else "")]
        kb = types.InlineKeyboardMarkup(row_width=1)
        for i, d in enumerate(items, start=1):
            when = (d["last_seen"] or "")[:16].replace("T", " ")
            lines.append(f"{i}. {d['title']} — {when}")
            label = f"{i}. {d['title']}"[:28] + " ✕"
            kb.add(types.InlineKeyboardButton(label, callback_data=f"dev:{d['id']}"))
        kb.add(types.InlineKeyboardButton(t("btn_unlink_all"), callback_data="devall"))
        bot.send_message(chat_id, "\n".join(lines), reply_markup=kb)

    def start_payment(chat_id: int, c: dict, tg_id: str, method: str, pid: int) -> None:
        p = pay_svc.get_plan(pid)
        if not p:
            return
        cur = (p["currency"] or "XTR").upper()
        payment_id = pay_svc.create_payment(c, p, method, tg_id)
        price = pay_svc.price_h(p["price"], cur)

        if method == "manual":
            payment = pay_svc.get_payment(payment_id)
            bot.send_message(
                chat_id,
                t("pay_manual", plan=p["name"], price=price,
                  details=pay_svc.manual_details(), support=pay_svc.support_url()),
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton(t("btn_paid"), callback_data=f"paid:{payment_id}")
                ),
            )
            notify_admins(payment)
            return

        provider = "" if method == "stars" else pay_svc.provider_token()
        if method == "provider" and not provider:
            bot.send_message(chat_id, t("pay_bad"))
            return
        title = t("pay_invoice_title", plan=p["name"],
                  title=str(db.get_setting("pay_title", "Подписка") or "Подписка"))[:32]
        desc = t("pay_invoice_desc", plan=p["name"], days=p["days_h"],
                 traffic=p["traffic_h"])[:255]
        try:
            bot.send_invoice(
                chat_id=chat_id,
                title=title,
                description=desc,
                invoice_payload=f"pay:{payment_id}",
                provider_token=provider,
                currency="XTR" if method == "stars" else cur,
                prices=[types.LabeledPrice(
                    label=p["name"][:32],
                    amount=pay_svc.amount_minor(p["price"], "XTR" if method == "stars" else cur),
                )],
                protect_content=False,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("invoice failed: %s", e)
            bot.send_message(chat_id, t("pay_bad"))

    @bot.pre_checkout_query_handler(func=lambda q: True)
    def checkout(q):
        payload = q.invoice_payload or ""
        ok = False
        if payload.startswith("pay:"):
            try:
                p = pay_svc.get_payment(int(payload.split(":", 1)[1]))
                ok = bool(p) and p["status"] != "canceled"
            except ValueError:
                ok = False
        bot.answer_pre_checkout_query(q.id, ok=ok, error_message=None if ok else "Счёт устарел")

    @bot.message_handler(content_types=["successful_payment"])
    def payment_done(m):
        sp = m.successful_payment
        payload = sp.invoice_payload or ""
        if not payload.startswith("pay:"):
            return
        try:
            pid = int(payload.split(":", 1)[1])
        except ValueError:
            return
        p = pay_svc.mark_paid(pid, charge_id=sp.telegram_payment_charge_id or "")
        if not p:
            return
        c = clients_svc.get_client(p["client_id"]) or {}
        bot.send_message(
            m.chat.id,
            t("pay_paid", plan=p["plan_name"], expire=_expire_h(c),
              days_left=clients_svc.days_left(c) or ""),
            reply_markup=_kb(),
        )
        notify_admins(p, extra="Оплачено автоматически.")

    # -------------------------------------------------------------- кнопки
    @bot.callback_query_handler(func=lambda c: True)
    def cb(call):
        data = call.data or ""

        if data.startswith(("payok:", "payno:")):
            if not _is_admin(call.from_user.id):
                bot.answer_callback_query(call.id, "Только для администратора")
                return
            action, _, raw_id = data.partition(":")
            try:
                pid = int(raw_id)
            except ValueError:
                bot.answer_callback_query(call.id)
                return
            payment = pay_svc.get_payment(pid)
            if not payment:
                bot.answer_callback_query(call.id, "Заявка не найдена")
                return
            if action == "payok":
                payment = pay_svc.mark_paid(pid, note=f"подтвердил {call.from_user.id}")
                c = clients_svc.get_client(payment["client_id"]) or {}
                bot.answer_callback_query(call.id, "Оплата зачтена")
                tell_client(payment["tg_id"] or c.get("tg_id", ""),
                            t("pay_approved", plan=payment["plan_name"], expire=_expire_h(c)))
            else:
                payment = pay_svc.mark_canceled(pid, note=f"отклонил {call.from_user.id}")
                bot.answer_callback_query(call.id, "Отклонено")
                tell_client(payment["tg_id"], t("pay_rejected", support=pay_svc.support_url()))
            try:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                              reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
            return

        c = _find(call.from_user.id)
        if not c:
            bot.answer_callback_query(call.id, t("not_linked"))
            return
        bot.answer_callback_query(call.id)
        try:
            if data == "sub":
                url = clients_svc.sub_url(c["sub_token"])
                bot.send_message(call.message.chat.id, t("sub_link", url=url, name=c["name"]))
            elif data == "qr":
                import qrcode

                q = qrcode.QRCode(box_size=8, border=2)
                q.add_data(clients_svc.sub_url(c["sub_token"]))
                q.make(fit=True)
                buf = io.BytesIO()
                q.make_image(fill_color="#111827", back_color="#ffffff").save(buf, format="PNG")
                buf.seek(0)
                bot.send_photo(call.message.chat.id, buf)
            elif data == "status":
                bot.send_message(call.message.chat.id, _status_text(c), reply_markup=_kb())
            elif data == "howto":
                bot.send_message(call.message.chat.id, t("howto_title"), reply_markup=_howto_kb())
            elif data.startswith("how:"):
                key = data.split(":", 1)[1]
                text = bottexts.get(f"howto_{key}")
                bot.send_message(call.message.chat.id,
                                 text if text else t("howto_missing"))
            elif data == "devices":
                show_devices(call.message.chat.id, c)
            elif data.startswith("dev:"):
                did = int(data.split(":", 1)[1])
                row = devices.get(did)
                if row and row["client_id"] == c["id"]:
                    devices.unlink(did)
                    bot.send_message(call.message.chat.id, t("devices_unlinked"))
                show_devices(call.message.chat.id, c)
            elif data == "devall":
                n = devices.unlink_all(c["id"])
                bot.send_message(call.message.chat.id, t("devices_unlinked_all", count=n))
            elif data == "pay":
                show_plans(call.message.chat.id, c)
            elif data.startswith("plan:"):
                show_plan(call.message.chat.id, int(data.split(":", 1)[1]))
            elif data.startswith("pay:"):
                _, method, raw_id = data.split(":", 2)
                start_payment(call.message.chat.id, c, str(call.from_user.id), method, int(raw_id))
            elif data.startswith("paid:"):
                pid = int(data.split(":", 1)[1])
                payment = pay_svc.get_payment(pid)
                if payment and payment["status"] == "paid":
                    bot.send_message(call.message.chat.id, t("pay_already"))
                elif payment:
                    bot.send_message(call.message.chat.id, t("pay_pending",
                                                              plan=payment["plan_name"],
                                                              price=pay_svc.price_h(
                                                                  payment["amount"],
                                                                  payment["currency"])))
                    notify_admins(payment, extra="Пользователь нажал «Я оплатил».")
        except Exception as e:  # noqa: BLE001
            log.warning("userbot callback: %s", e)

    return bot

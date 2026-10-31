"""Оплата подписки: тарифы, счета Telegram и ручные заявки.

Три способа получить деньги за продление:

- **stars** — Telegram Stars (валюта XTR). Работает без платёжного провайдера,
  включён всегда, когда у тарифа цена в звёздах.
- **provider** — обычный платёжный провайдер Telegram (карта, СБП). Нужен
  `pay_provider_token` из @BotFather и валюта из настроек.
- **manual** — перевод по реквизитам, заявку подтверждает админ кнопкой в чате.

Тариф хранит снимок в момент оплаты, поэтому изменение цены не ломает
уже выставленные счета.
"""
from __future__ import annotations

from .. import db
from . import clients as clients_svc

CURRENCIES = {
    "XTR": "звёзды",
    "RUB": "рубли",
    "USD": "доллары",
    "EUR": "евро",
}

SYMBOL = {"XTR": "⭐", "RUB": "₽", "USD": "$", "EUR": "€"}

METHODS = {
    "stars": "Telegram Stars",
    "provider": "картой через Telegram",
    "manual": "перевод вручную",
}


# ---------------------------------------------------------------- настройки
def enabled() -> bool:
    return bool(int(db.get_setting("pay_enabled", 1) or 0))


def provider_token() -> str:
    return str(db.get_setting("pay_provider_token", "") or "").strip()


def currency() -> str:
    cur = str(db.get_setting("pay_currency", "RUB") or "RUB").upper()
    return cur if cur in CURRENCIES else "RUB"


def manual_enabled() -> bool:
    return bool(int(db.get_setting("pay_manual_enabled", 0) or 0))


def manual_details() -> str:
    return str(db.get_setting("pay_manual_details", "") or "").strip()


def support_url() -> str:
    return str(
        db.get_setting("pay_support_url", "") or db.get_setting("sub_support_url", "") or ""
    ).strip()


def active() -> bool:
    """Есть ли что показывать клиенту: включено и есть хотя бы один тариф."""
    return enabled() and bool(list_plans(only_enabled=True))


# ---------------------------------------------------------------- формат
def price_h(price: float, cur: str) -> str:
    cur = (cur or "XTR").upper()
    val = float(price or 0)
    if cur == "XTR":
        return f"{int(round(val))} {SYMBOL['XTR']}"
    txt = f"{val:.2f}".rstrip("0").rstrip(".")
    return f"{txt} {SYMBOL.get(cur, cur)}"


def traffic_h(gb: float) -> str:
    return f"+{float(gb):g} ГБ" if gb else "без изменений"


def amount_minor(price: float, cur: str) -> int:
    """Сумма в минимальных единицах валюты — так её ждёт Telegram."""
    if (cur or "").upper() == "XTR":
        return int(round(float(price or 0)))
    return int(round(float(price or 0) * 100))


# ---------------------------------------------------------------- тарифы
def list_plans(only_enabled: bool = False) -> list[dict]:
    sql = "SELECT * FROM plans"
    if only_enabled:
        sql += " WHERE enabled=1"
    sql += " ORDER BY position, id"
    rows = db.dicts(db.q(sql))
    for r in rows:
        r["price_h"] = price_h(r["price"], r["currency"])
        r["traffic_h"] = traffic_h(r["gb"])
        r["days_h"] = f"{int(r['days'])} дн." if r["days"] else "без срока"
        r["currency_h"] = CURRENCIES.get((r["currency"] or "").upper(), r["currency"])
    return rows


def get_plan(pid: int) -> dict | None:
    row = db.q1("SELECT * FROM plans WHERE id=?", (pid,))
    if not row:
        return None
    p = dict(row)
    p["price_h"] = price_h(p["price"], p["currency"])
    p["traffic_h"] = traffic_h(p["gb"])
    return p


def create_plan(name: str, days: int, gb: float, price: float, cur: str,
                note: str = "") -> int:
    cur = (cur or "XTR").upper()
    if cur not in CURRENCIES:
        raise ValueError("unknown currency")
    pos = db.q1("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM plans")["p"]
    pid = db.ex(
        """INSERT INTO plans(name, days, gb, price, currency, enabled, position, note)
           VALUES(?,?,?,?,?,1,?,?)""",
        (name.strip(), int(days), float(gb or 0), float(price or 0), cur, pos, note.strip()),
    )
    db.audit("plan.create", target=str(pid), meta={"name": name, "price": price})
    return pid


def update_plan(pid: int, name: str, days: int, gb: float, price: float, cur: str,
                enabled: int) -> None:
    cur = (cur or "XTR").upper()
    if cur not in CURRENCIES:
        raise ValueError("unknown currency")
    db.ex(
        """UPDATE plans SET name=?, days=?, gb=?, price=?, currency=?, enabled=? WHERE id=?""",
        (name.strip(), int(days), float(gb or 0), float(price or 0), cur, int(enabled), pid),
    )


def delete_plan(pid: int) -> None:
    db.ex("DELETE FROM plans WHERE id=?", (pid,))
    db.audit("plan.delete", target=str(pid))


# ---------------------------------------------------------------- заявки
def methods_for(plan: dict) -> list[str]:
    """Какими способами можно оплатить этот тариф."""
    cur = (plan.get("currency") or "XTR").upper()
    out: list[str] = []
    if cur == "XTR":
        out.append("stars")
    elif provider_token():
        out.append("provider")
    if manual_enabled() and manual_details():
        out.append("manual")
    return out


def create_payment(client: dict, plan: dict, method: str, tg_id: str = "") -> int:
    pid = db.ex(
        """INSERT INTO payments(client_id, plan_id, plan_name, days, gb, amount, currency,
                                method, status, tg_id, created_at)
           VALUES(?,?,?,?,?,?,?,?,'pending',?,?)""",
        (client["id"], plan["id"], plan["name"], int(plan["days"] or 0),
         float(plan["gb"] or 0), float(plan["price"] or 0),
         (plan.get("currency") or "XTR").upper(), method, str(tg_id), db.now()),
    )
    db.audit("payment.create", target=str(pid),
             meta={"client": client["id"], "plan": plan["name"], "method": method})
    return pid


def get_payment(pid: int) -> dict | None:
    row = db.q1("SELECT * FROM payments WHERE id=?", (pid,))
    return dict(row) if row else None


def list_payments(limit: int = 40, status: str = "") -> list[dict]:
    sql = ("SELECT p.*, c.name AS client_name FROM payments p "
           "LEFT JOIN clients c ON c.id=p.client_id")
    args: list = []
    if status:
        sql += " WHERE p.status=?"
        args.append(status)
    sql += " ORDER BY p.id DESC LIMIT ?"
    args.append(int(limit))
    rows = db.dicts(db.q(sql, args))
    for r in rows:
        r["amount_h"] = price_h(r["amount"], r["currency"])
        r["method_h"] = METHODS.get(r["method"], r["method"])
        r["plan_h"] = f"{r['days']} дн." + (f" · +{r['gb']:g} ГБ" if r["gb"] else "")
    return rows


def payments_stats() -> dict:
    paid = db.q1("SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS s FROM payments WHERE status='paid'")
    pending = db.q1("SELECT COUNT(*) AS n FROM payments WHERE status='pending'")
    return {"paid": paid["n"], "pending": pending["n"], "sum": paid["s"]}


def apply_plan(client_id: int, days: int, gb: float) -> None:
    """Начисляет оплаченный тариф: продлевает срок и добавляет трафик."""
    if days:
        clients_svc.extend(client_id, int(days))
    if gb:
        db.ex(
            "UPDATE clients SET limit_bytes = limit_bytes + ? WHERE id=?",
            (int(float(gb) * 1024**3), client_id),
        )
    db.ex("UPDATE clients SET enabled=1 WHERE id=?", (client_id,))
    if int(db.get_setting("hwid_reset_on_pay", 0) or 0):
        from . import devices as devices_svc

        devices_svc.unlink_all(client_id)
    from . import nodes as nodes_svc

    nodes_svc.push_async()


def mark_paid(pid: int, charge_id: str = "", note: str = "") -> dict | None:
    p = get_payment(pid)
    if not p:
        return None
    if p["status"] == "paid":
        return p
    apply_plan(p["client_id"], p["days"], p["gb"])
    db.ex(
        "UPDATE payments SET status='paid', paid_at=?, charge_id=?, note=? WHERE id=?",
        (db.now(), charge_id, note or p["note"], pid),
    )
    db.audit("payment.paid", target=str(pid),
             meta={"client": p["client_id"], "plan": p["plan_name"], "amount": p["amount"]})
    return get_payment(pid)


def mark_canceled(pid: int, note: str = "") -> dict | None:
    p = get_payment(pid)
    if not p:
        return None
    if p["status"] != "paid":
        db.ex("UPDATE payments SET status='canceled', note=? WHERE id=?", (note, pid))
        db.audit("payment.cancel", target=str(pid), meta={"note": note})
    return get_payment(pid)

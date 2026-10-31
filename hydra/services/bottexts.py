"""Тексты бота подписок: значения по умолчанию, переменные и переопределения.

Редактор в панели (/bots/texts) правит только отличия от дефолта — так после
обновления панели новые формулировки подхватываются сами, а правки не теряются.
"""
from __future__ import annotations

from .. import db

SETTING = "bot_texts"

GROUPS: list[tuple[str, str]] = [
    ("start", "Начало"),
    ("sub", "Подписка и статус"),
    ("howto", "Инструкции"),
    ("pay", "Оплата"),
    ("notify", "Уведомления"),
    ("buttons", "Кнопки"),
]

TEXTS: dict[str, dict] = {
    # ---------------------------------------------------------------- начало
    "start_no_sub": {
        "group": "start", "label": "Приветствие без подписки", "vars": ["support"],
        "text": (
            "Здравствуйте. Чтобы получить подписку, отправьте её токен "
            "(последняя часть ссылки <code>/sub/&lt;токен&gt;</code>).\n\n"
            "Если токена нет — напишите в поддержку."
        ),
    },
    "bind_ok": {
        "group": "start", "label": "Подписка привязана", "vars": ["name", "status"],
        "text": "Готово. Подписка привязана.\n\n{status}",
    },
    "bind_bad_token": {
        "group": "start", "label": "Токен не найден", "vars": [],
        "text": "Токен не найден. Проверьте ссылку.",
    },
    "bind_taken": {
        "group": "start", "label": "Подписка занята другим аккаунтом", "vars": [],
        "text": "Эта подписка уже привязана к другому аккаунту.",
    },
    "not_linked": {
        "group": "start", "label": "Нет привязки", "vars": [],
        "text": "Подписка не привязана. Отправьте токен из ссылки.",
    },
    "help": {
        "group": "start", "label": "Справка /help", "vars": [],
        "text": "/start — статус подписки\n/sub — ссылка\n/help — эта справка",
    },
    # ---------------------------------------------------------------- подписка
    "status": {
        "group": "sub", "label": "Статус подписки", "vars": ["name", "status", "traffic", "limit", "expire", "days_left"],
        "text": (
            "<b>{name}</b>\n"
            "Статус: {status}\n"
            "Трафик: {traffic}{limit}\n"
            "Действует до: {expire}"
        ),
    },
    "sub_link": {
        "group": "sub", "label": "Ссылка на подписку", "vars": ["url", "name"],
        "text": "<code>{url}</code>",
    },
    "devices_title": {
        "group": "sub", "label": "Список устройств", "vars": ["count", "limit"],
        "text": "<b>Устройства</b>\nПривязано: {count}{limit}",
    },
    "devices_empty": {
        "group": "sub", "label": "Устройств нет", "vars": [],
        "text": "Устройства не привязаны. Зайдите в приложение — оно пришлёт свой HWID.",
    },
    "devices_unlinked": {
        "group": "sub", "label": "Устройство отвязано", "vars": [],
        "text": "Устройство отвязано.",
    },
    "devices_unlinked_all": {
        "group": "sub", "label": "Все устройства отвязаны", "vars": ["count"],
        "text": "Отвязано устройств: {count}. Можно подключаться заново.",
    },
    # ---------------------------------------------------------------- инструкции
    "howto_title": {
        "group": "howto", "label": "Заголовок выбора приложения", "vars": [],
        "text": "Выберите приложение:",
    },
    "howto_happ": {
        "group": "howto", "label": "Happ", "vars": [],
        "text": (
            "<b>Happ</b>\n"
            "1. Откройте приложение и нажмите «+».\n"
            "2. «Добавить из буфера обмена» или отсканируйте QR.\n"
            "3. Подключитесь и разрешите VPN.\n\n"
            "Подписка обновляется сама при запуске."
        ),
    },
    "howto_v2raytun": {
        "group": "howto", "label": "v2RayTun", "vars": [],
        "text": (
            "<b>v2RayTun</b>\n"
            "1. Нажмите «+» в правом верхнем углу.\n"
            "2. «Импорт из буфера обмена» — вставьте ссылку.\n"
            "3. Выберите сервер и подключитесь."
        ),
    },
    "howto_clash": {
        "group": "howto", "label": "Clash / mihomo", "vars": ["url"],
        "text": (
            "<b>Clash / mihomo / FlClash</b>\n"
            "1. Скопируйте ссылку с окончанием <code>/clash</code>.\n"
            "2. Профили → Добавить → Из URL.\n"
            "3. Обновите профиль и включите VPN."
        ),
    },
    "howto_singbox": {
        "group": "howto", "label": "sing-box / Hiddify", "vars": ["url"],
        "text": (
            "<b>sing-box / Hiddify / Karing</b>\n"
            "1. Скопируйте ссылку с окончанием <code>/singbox</code>.\n"
            "2. Добавьте её как профиль из URL.\n"
            "3. Включите туннель."
        ),
    },
    "howto_windows": {
        "group": "howto", "label": "Windows / Linux", "vars": [],
        "text": (
            "<b>Windows / Linux</b>\n"
            "1. Установите Hiddify, NekoRay или v2rayN.\n"
            "2. «Подписка» → «Добавить» → вставьте ссылку.\n"
            "3. Обновите список серверов и подключитесь."
        ),
    },
    "howto_missing": {
        "group": "howto", "label": "Инструкции нет", "vars": [],
        "text": "Инструкция недоступна.",
    },
    # ---------------------------------------------------------------- оплата
    "pay_title": {
        "group": "pay", "label": "Заголовок оплаты", "vars": ["name"],
        "text": "<b>Продление подписки</b>\nВыберите тариф:",
    },
    "pay_none": {
        "group": "pay", "label": "Оплата недоступна", "vars": ["support"],
        "text": "Оплата пока не настроена. Напишите в поддержку.",
    },
    "pay_plan": {
        "group": "pay", "label": "Карточка тарифа", "vars": ["plan", "days", "traffic", "price", "note"],
        "text": "<b>{plan}</b>\nСрок: {days}\nТрафик: {traffic}\nЦена: <b>{price}</b>",
    },
    "pay_invoice_title": {
        "group": "pay", "label": "Заголовок счёта", "vars": ["plan", "title"],
        "text": "{title} · {plan}",
    },
    "pay_invoice_desc": {
        "group": "pay", "label": "Описание счёта", "vars": ["plan", "days", "traffic"],
        "text": "{plan}: {days}, трафик {traffic}",
    },
    "pay_paid": {
        "group": "pay", "label": "Оплата получена", "vars": ["plan", "expire", "days_left"],
        "text": "Оплата получена. {plan}\nПодписка действует до {expire}.",
    },
    "pay_manual": {
        "group": "pay", "label": "Реквизиты для перевода", "vars": ["plan", "price", "details", "support"],
        "text": (
            "<b>{plan}</b>\nЦена: {price}\n\n{details}\n\n"
            "После перевода нажмите «Я оплатил» — заявка уже у администратора."
        ),
    },
    "pay_pending": {
        "group": "pay", "label": "Заявка отправлена админу", "vars": ["plan", "price"],
        "text": "Заявка на {plan} ({price}) у администратора. Проверим и включим подписку.",
    },
    "pay_admin_new": {
        "group": "pay", "label": "Уведомление админу", "vars": ["id", "name", "plan", "price", "method"],
        "text": (
            "Заявка на оплату #{id}\n"
            "Пользователь: {name}\n"
            "Тариф: {plan}\n"
            "Сумма: {price}\n"
            "Способ: {method}"
        ),
    },
    "pay_approved": {
        "group": "pay", "label": "Оплата подтверждена", "vars": ["plan", "expire"],
        "text": "Оплата подтверждена. {plan}\nПодписка действует до {expire}.",
    },
    "pay_rejected": {
        "group": "pay", "label": "Оплата отклонена", "vars": ["support"],
        "text": "Заявка на оплату отклонена. Напишите в поддержку.",
    },
    "pay_bad": {
        "group": "pay", "label": "Счёт не создался", "vars": [],
        "text": "Не получилось выставить счёт. Попробуйте позже или напишите в поддержку.",
    },
    "pay_already": {
        "group": "pay", "label": "Счёт уже оплачен", "vars": [],
        "text": "Этот счёт уже оплачен.",
    },
    # ---------------------------------------------------------------- уведомления
    "notify_expiring": {
        "group": "notify", "label": "Напоминание об окончании", "vars": ["name", "days_left", "expire", "url", "support"],
        "text": (
            "<b>{name}</b>, подписка заканчивается {expire} — осталось {days_left} дн.\n\n"
            "Продлить можно в этом боте, кнопка «Оплатить».\n"
            "Ссылка на подписку: <code>{url}</code>"
        ),
    },
    # ---------------------------------------------------------------- кнопки
    "btn_sub": {"group": "buttons", "label": "Ссылка", "vars": [], "text": "Ссылка"},
    "btn_qr": {"group": "buttons", "label": "QR-код", "vars": [], "text": "QR-код"},
    "btn_status": {"group": "buttons", "label": "Статус", "vars": [], "text": "Статус"},
    "btn_howto": {"group": "buttons", "label": "Инструкции", "vars": [], "text": "Инструкции"},
    "btn_pay": {"group": "buttons", "label": "Оплатить", "vars": [], "text": "Оплатить"},
    "btn_devices": {"group": "buttons", "label": "Устройства", "vars": [], "text": "Устройства"},
    "btn_unlink": {"group": "buttons", "label": "Отвязать устройство", "vars": [], "text": "Отвязать"},
    "btn_unlink_all": {"group": "buttons", "label": "Отвязать все", "vars": [], "text": "Отвязать все"},
    "btn_pay_stars": {"group": "buttons", "label": "Оплатить звёздами", "vars": [], "text": "Оплатить звёздами"},
    "btn_pay_card": {"group": "buttons", "label": "Оплатить картой", "vars": [], "text": "Оплатить картой"},
    "btn_pay_manual": {"group": "buttons", "label": "Перевод вручную", "vars": [], "text": "Перевод вручную"},
    "btn_paid": {"group": "buttons", "label": "Я оплатил", "vars": [], "text": "Я оплатил"},
    "btn_back": {"group": "buttons", "label": "Назад", "vars": [], "text": "Назад"},
    "btn_approve": {"group": "buttons", "label": "Подтвердить", "vars": [], "text": "Подтвердить"},
    "btn_reject": {"group": "buttons", "label": "Отклонить", "vars": [], "text": "Отклонить"},
}

VARS_HINT = {
    "name": "имя пользователя",
    "status": "статус подписки",
    "traffic": "израсходованный трафик",
    "limit": "лимит трафика",
    "expire": "дата окончания",
    "days_left": "сколько дней осталось",
    "url": "ссылка на подписку",
    "plan": "название тарифа",
    "days": "срок тарифа",
    "price": "цена",
    "details": "реквизиты из настроек",
    "support": "ссылка на поддержку",
    "count": "сколько устройств привязано",
    "limit": "лимит устройств",
    "id": "номер заявки",
    "method": "способ оплаты",
    "title": "заголовок оплаты из настроек",
    "note": "заметка к тарифу",
}


class _Keep(dict):
    """Неизвестная переменная остаётся как есть, а не роняет отправку."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def default(key: str) -> str:
    return TEXTS.get(key, {}).get("text", "")


def overrides() -> dict[str, str]:
    saved = db.get_setting(SETTING, {}) or {}
    if not isinstance(saved, dict):
        return {}
    return {k: str(v) for k, v in saved.items() if k in TEXTS}


def get(key: str) -> str:
    return overrides().get(key) or default(key)


def is_custom(key: str) -> bool:
    return key in overrides()


def render(key: str, **kw) -> str:
    text = get(key)
    try:
        return text.format_map(_Keep(kw))
    except (ValueError, IndexError):
        return text


def save(values: dict[str, str]) -> int:
    """Сохраняет только отличия от дефолта. Возвращает число правок."""
    saved = {}
    for key, raw in values.items():
        if key not in TEXTS:
            continue
        text = (raw or "").replace("\r\n", "\n").strip()
        if text and text != default(key).strip():
            saved[key] = text
    db.set_setting(SETTING, saved)
    return len(saved)


def reset(key: str) -> None:
    saved = overrides()
    saved.pop(key, None)
    db.set_setting(SETTING, saved)


def reset_all() -> None:
    db.set_setting(SETTING, {})


def view() -> list[dict]:
    custom = overrides()
    out = []
    for gkey, gname in GROUPS:
        items = []
        for key, meta in TEXTS.items():
            if meta["group"] != gkey:
                continue
            items.append({
                "key": key,
                "label": meta["label"],
                "vars": meta.get("vars") or [],
                "text": custom.get(key) or meta["text"],
                "default": meta["text"],
                "custom": key in custom,
            })
        out.append({"key": gkey, "name": gname, "items": items})
    return out


def sample() -> dict[str, str]:
    """Значения для предпросмотра в редакторе."""
    return {
        "name": "Иван",
        "status": "активна",
        "traffic": "12.4 ГБ",
        "limit": " из 100.0 ГБ",
        "expire": "2026-12-01",
        "days_left": "31",
        "url": "https://panel.example.com/sub/abc123",
        "plan": "Месяц",
        "days": "30 дн.",
        "traffic_gb": "100",
        "price": "199 ₽",
        "details": "СБП: +7 900 000-00-00",
        "support": "https://t.me/support",
        "id": "42",
        "method": "перевод вручную",
        "title": "Продление подписки",
        "note": "",
    }

"""
🛒 Telegram Sales Bot — Бот для замовлень у навчальному закладі
"""

import os
import json
import logging
import asyncio
from datetime import datetime, date as ddate

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Load .env ──────────────────────────────────────────────────────────────────
def load_env(path=".env"):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

load_env()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ADMIN_ID       = int(os.getenv("ADMIN_ID", "0"))

# ── File paths ─────────────────────────────────────────────────────────────────
PRODUCTS_FILE = "products.json"
ORDERS_FILE   = "orders.json"
SHOPPERS_FILE = "shoppers.json"
STATE_FILE    = "state.json"

# ══════════════════════════════════════════════════════════════════════════════
#  DATA HELPERS
# ══════════════════════════════════════════════════════════════════════════════

# ── Products ───────────────────────────────────────────────────────────────────
def load_products() -> list:
    try:
        with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_products(products: list):
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

# ── Orders ─────────────────────────────────────────────────────────────────────
def load_orders() -> list:
    try:
        with open(ORDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_orders(orders: list):
    with open(ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)

def append_order(order: dict):
    orders = load_orders()
    orders.append(order)
    save_orders(orders)

def get_today_orders() -> list:
    today = ddate.today().isoformat()
    return [o for o in load_orders() if o.get("date") == today]

def get_today_stats() -> dict:
    orders = get_today_orders()
    total = sum(o["total"] for o in orders)
    cash  = sum(o["total"] for o in orders if o.get("payment_method") == "cash")
    card  = sum(o["total"] for o in orders if o.get("payment_method") == "card")
    paid  = sum(1 for o in orders if o.get("payment_confirmed"))
    return {"count": len(orders), "total": total, "cash": cash, "card": card, "paid": paid}

# ── Shoppers ───────────────────────────────────────────────────────────────────
# shoppers.json structure:
# {
#   "123456789": {
#     "name": "Ростик",
#     "username": "rostyk",
#     "card": "5375 1234 5678 9012",
#     "card_name": "Ростислав Іванченко",
#     "bank": "Monobank"
#   },
#   "pending_Андрій": {          ← ще не прив'язаний (доданий адміном)
#     "name": "Андрій",
#     "card": "...",
#     ...
#     "pending": true
#   }
# }

def load_shoppers() -> dict:
    try:
        with open(SHOPPERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_shoppers(shoppers: dict):
    with open(SHOPPERS_FILE, "w", encoding="utf-8") as f:
        json.dump(shoppers, f, ensure_ascii=False, indent=2)

def get_linked_shoppers() -> dict:
    """Тільки прив'язані (не pending) покупці."""
    return {k: v for k, v in load_shoppers().items()
            if not v.get("pending") and not k.startswith("pending_")}

def get_shopper_info(user_id: int) -> dict | None:
    return load_shoppers().get(str(user_id))

def is_shopper(user_id: int) -> bool:
    return get_shopper_info(user_id) is not None

def link_shopper_by_name(user_id: int, name: str, username: str = "") -> str:
    """
    Прив'язує pending-запис за ім'ям до telegram-аккаунта.
    Повертає ім'я (з pending) або те що ввів юзер.
    """
    shoppers = load_shoppers()
    pending_key = f"pending_{name}"
    if pending_key in shoppers:
        entry = shoppers.pop(pending_key)
        entry.pop("pending", None)
        entry["username"] = username
        entry["name"] = name
        shoppers[str(user_id)] = entry
        save_shoppers(shoppers)
        return name
    # Немає pending — просто реєструємо
    if str(user_id) not in shoppers:
        shoppers[str(user_id)] = {
            "name": name, "username": username,
            "card": "", "card_name": "", "bank": "",
        }
        save_shoppers(shoppers)
    return name

# ── State (today's shopper) ────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def get_today_shopper_id() -> int | None:
    s = load_state().get("today_shopper")
    return int(s) if s else None

def set_today_shopper(user_id: int | None):
    st = load_state()
    st["today_shopper"] = user_id
    save_state(st)

# ── Misc ───────────────────────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID

def get_cart(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "cart" not in context.user_data:
        context.user_data["cart"] = {}
    return context.user_data["cart"]

def cart_summary_text(cart: dict, products: list) -> str:
    if not cart:
        return "🛒 Кошик порожній"
    pm = {p["id"]: p for p in products}
    lines, total = [], 0.0
    for pid, qty in cart.items():
        if pid in pm:
            p = pm[pid]
            s = p["price"] * qty
            total += s
            lines.append(f"• {p['name']} × {qty} = {s:.0f} грн")
    lines.append(f"\n💰 <b>Разом: {total:.0f} грн</b>")
    return "\n".join(lines)

def format_order_notify(order: dict) -> str:
    pay = "💵 Готівка" if order.get("payment_method") == "cash" else "💳 Картою"
    lines = [
        f"🆕 <b>ЗАМОВЛЕННЯ #{order['id']}</b>  {pay}",
        f"📅 {order['datetime']}",
        f"👤 {order['name']} | 📞 {order['phone']}",
        f"──────────────────",
    ]
    for it in order["items"]:
        lines.append(f"• {it['name']} × {it['qty']} = {it['subtotal']:.0f} грн")
    lines.append(f"──────────────────")
    lines.append(f"💰 <b>Сума: {order['total']:.0f} грн</b>")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════
def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    btns = [
        [InlineKeyboardButton("🛒 Каталог", callback_data="open_catalog")],
        [InlineKeyboardButton("🧺 Кошик",   callback_data="open_cart")],
        [InlineKeyboardButton("ℹ️ Як замовити", callback_data="help")],
    ]
    if not is_shopper(user_id) and not is_admin(user_id):
        btns.append([InlineKeyboardButton("🙋 Я — покупець (реєстрація)", callback_data="register_shopper")])
    if is_shopper(user_id):
        btns.append([InlineKeyboardButton("🎒 Панель покупця", callback_data="shopper_panel")])
    if is_admin(user_id):
        btns.append([InlineKeyboardButton("⚙️ Адмін",          callback_data="admin_panel")])
    return InlineKeyboardMarkup(btns)

def catalog_kb(products: list, cart: dict, category: str = None) -> InlineKeyboardMarkup:
    cats = {}
    for p in products:
        cats.setdefault(p.get("category", "Інше"), []).append(p)
    btns = []
    if category is None:
        for cat in cats:
            btns.append([InlineKeyboardButton(f"📦 {cat}", callback_data=f"cat:{cat}")])
    else:
        for p in cats.get(category, []):
            ic = cart.get(p["id"], 0)
            lbl = f"{p['name']} — {p['price']:.0f} грн" + (f"  ✅{ic}" if ic else "")
            btns.append([InlineKeyboardButton(lbl, callback_data=f"item:{p['id']}")])
        btns.append([InlineKeyboardButton("◀️ Категорії", callback_data="back_catalog")])
    btns.append([InlineKeyboardButton("🧺 Кошик", callback_data="open_cart")])
    btns.append([InlineKeyboardButton("🏠 Меню",  callback_data="main_menu")])
    return InlineKeyboardMarkup(btns)

def item_kb(p: dict, qty: int) -> InlineKeyboardMarkup:
    pid  = p["id"]
    unit = p["unit"]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖", callback_data=f"dec:{pid}"),
            InlineKeyboardButton(f"  {qty} {unit}  " if qty else "  0  ", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"inc:{pid}"),
        ],
        [InlineKeyboardButton("✅ Додати" if qty else "🗑 Видалити", callback_data=f"add_confirm:{pid}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"cat:{p.get('category','Інше')}")],
    ])

def cart_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Оформити замовлення", callback_data="checkout")],
        [InlineKeyboardButton("🗑 Очистити",           callback_data="clear_cart")],
        [InlineKeyboardButton("🛒 Каталог",            callback_data="open_catalog")],
        [InlineKeyboardButton("🏠 Меню",               callback_data="main_menu")],
    ])

def payment_method_kb() -> InlineKeyboardMarkup:
    """Вибір способу оплати."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Переказ на карту",   callback_data="pay_card")],
        [InlineKeyboardButton("💵 Готівкою",           callback_data="pay_cash")],
        [InlineKeyboardButton("❌ Скасувати",           callback_data="cancel_order")],
    ])

def confirm_order_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Підтвердити", callback_data="confirm_order"),
        InlineKeyboardButton("❌ Скасувати",  callback_data="cancel_order"),
    ]])

# Покупець: підтвердити оплату для конкретного замовлення
def shopper_confirm_pay_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Оплату отримано", callback_data=f"shopper_pay_ok:{order_id}"),
    ]])

def shopper_panel_kb() -> InlineKeyboardMarkup:
    today = get_today_orders()
    # "Оплачені" = підтверджена картою АБО готівка
    paid_orders = [o for o in today if o.get("payment_confirmed") or o.get("payment_method") == "cash"]
    unconfirmed = [o for o in today if not o.get("payment_confirmed") and o.get("payment_method") == "card"]
    open_orders = [o for o in today if o.get("status") != "closed"]
    lbl_close   = f"✅ Закрити список ({len(open_orders)})" if open_orders else "✅ Список порожній"
    lbl_unconf  = f"💳 Непідтверджені оплати ({len(unconfirmed)})" if unconfirmed else "💳 Всі оплати підтверджені"
    lbl_shop    = f"🛍️ Список покупок ({len(paid_orders)} замовл.)" if paid_orders else "🛍️ Список покупок (порожній)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl_shop,   callback_data="shopper_shopping_list")],
        [InlineKeyboardButton("📋 Всі замовлення сьогодні",  callback_data="shopper_orders")],
        [InlineKeyboardButton(lbl_unconf,                callback_data="shopper_unconfirmed")],
        [InlineKeyboardButton(lbl_close,                 callback_data="shopper_close_list")],
        [InlineKeyboardButton("🏠 Меню",                 callback_data="main_menu")],
    ])

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👟 Хто іде сьогодні",     callback_data="admin_pick_shopper")],
        [InlineKeyboardButton("📊 Статистика дня",        callback_data="admin_stats")],
        [InlineKeyboardButton("📋 Замовлення сьогодні",   callback_data="admin_today_orders")],
        [InlineKeyboardButton("👥 Покупці та їх карти",   callback_data="admin_shoppers")],
        [InlineKeyboardButton("📦 Товари",                callback_data="admin_products")],
        [InlineKeyboardButton("➕ Додати товар",          callback_data="admin_add_product")],
        [InlineKeyboardButton("🏠 Меню",                  callback_data="main_menu")],
    ])

def pick_shopper_kb() -> InlineKeyboardMarkup:
    shoppers = get_linked_shoppers()
    today_id = get_today_shopper_id()
    btns = []
    for uid, info in shoppers.items():
        card_ok = "💳" if info.get("card") else "❌"
        lbl = f"✅ {info['name']} (сьогодні)" if int(uid) == today_id else f"{card_ok} {info['name']}"
        btns.append([InlineKeyboardButton(lbl, callback_data=f"set_shopper:{uid}")])
    btns.append([InlineKeyboardButton("❌ Ніхто",  callback_data="set_shopper:none")])
    btns.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(btns)

def shoppers_list_kb() -> InlineKeyboardMarkup:
    shoppers = get_linked_shoppers()
    btns = []
    for uid, info in shoppers.items():
        card_ic = "💳" if info.get("card") else "❌ без карти"
        btns.append([InlineKeyboardButton(f"{card_ic}  {info['name']}", callback_data=f"edit_shopper:{uid}")])
    btns.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(btns)

def edit_shopper_kb(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Встановити / змінити карту", callback_data=f"shopper_setcard:{uid}")],
        [InlineKeyboardButton("🗑 Видалити покупця",           callback_data=f"shopper_delete:{uid}")],
        [InlineKeyboardButton("◀️ Назад",                     callback_data="admin_shoppers")],
    ])

# ══════════════════════════════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    user = update.effective_user

    if is_admin(uid):
        today_id = get_today_shopper_id()
        sl       = get_linked_shoppers()
        t_txt    = f"👟 Сьогодні іде: <b>{sl[str(today_id)]['name']}</b>" if today_id and str(today_id) in sl else "👟 Покупець <b>не призначений</b>"
        await update.message.reply_text(f"⚙️ <b>Адмін-панель</b>\n\n{t_txt}", reply_markup=admin_kb(), parse_mode="HTML")
        return

    if is_shopper(uid):
        info  = get_shopper_info(uid)
        stats = get_today_stats()
        await update.message.reply_text(
            f"🎒 <b>{info['name']}</b>\n\n"
            f"📊 Сьогодні: <b>{stats['count']}</b> замовлень | <b>{stats['total']:.0f} грн</b>",
            reply_markup=shopper_panel_kb(), parse_mode="HTML",
        )
        return

    await update.message.reply_text(
        f"👋 <b>Вітаємо, {user.first_name}!</b>\n\n"
        f"🛒 Оберіть товари та оформіть замовлення!",
        reply_markup=main_menu_kb(uid), parse_mode="HTML",
    )

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏠 Меню:", reply_markup=main_menu_kb(update.effective_user.id), parse_mode="HTML")

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("⚙️ <b>Адмін-панель</b>", reply_markup=admin_kb(), parse_mode="HTML")

async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    stats = get_today_stats()
    today = get_today_orders()
    lines = [
        f"📊 <b>Сьогодні ({ddate.today().strftime('%d.%m.%Y')}):</b>",
        f"Замовлень: <b>{stats['count']}</b> | Підтверджено: <b>{stats['paid']}</b>",
        f"💰 Всього: <b>{stats['total']:.0f} грн</b>",
        f"💵 Готівка: <b>{stats['cash']:.0f} грн</b>  💳 Картою: <b>{stats['card']:.0f} грн</b>",
        "",
    ]
    for o in today:
        s = "✅" if o.get("payment_confirmed") else "⏳"
        p = "💵" if o.get("payment_method") == "cash" else "💳"
        lines.append(f"{s}{p} #{o['id']} {o['name']} | {o['total']:.0f} грн")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def cmd_addshopper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addshopper Ростик 5375 1234 5678 9012 Ростислав Іванченко Monobank
    Або без карти:
    /addshopper Ростик
    """
    if not is_admin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Формат:\n<code>/addshopper Ім'я [карта] [Власник] [Банк]</code>\n\n"
            "Приклад:\n<code>/addshopper Ростик 5375 1234 5678 9012 Ростислав Іванченко Monobank</code>",
            parse_mode="HTML",
        )
        return
    name = args[0]
    remaining = args[1:]
    card_parts, rest = [], []
    for i, part in enumerate(remaining):
        if part.replace(" ", "").isdigit() and len(card_parts) < 4:
            card_parts.append(part)
        else:
            rest = remaining[i:]; break
    card      = " ".join(card_parts)
    card_name = " ".join(rest[:-1]) if len(rest) > 1 else ""
    bank      = rest[-1] if rest else ""

    shoppers = load_shoppers()
    shoppers[f"pending_{name}"] = {
        "name": name, "card": card, "card_name": card_name, "bank": bank, "pending": True,
    }
    save_shoppers(shoppers)
    await update.message.reply_text(
        f"✅ <b>«{name}» доданий у список покупців!</b>\n"
        f"💳 Карта: <code>{card or 'не вказана'}</code>\n"
        f"👤 {card_name or '—'} | 🏦 {bank or '—'}\n\n"
        f"Коли {name} зайде в бот і натисне «Я — покупець» та введе ім'я <b>{name}</b> — "
        f"прив'яжеться автоматично.",
        parse_mode="HTML",
    )

# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════════════
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    user    = update.effective_user
    uid     = user.id
    products = load_products()
    cart    = get_cart(context)

    # ── Navigation ─────────────────────────────────────────────────────────────
    if data == "main_menu":
        await query.edit_message_text("🏠 <b>Меню</b>", reply_markup=main_menu_kb(uid), parse_mode="HTML")

    elif data == "help":
        await query.edit_message_text(
            "ℹ️ <b>Як замовити:</b>\n\n"
            "1️⃣ Каталог → вибери товари (➕/➖)\n"
            "2️⃣ Кошик → перевір\n"
            "3️⃣ Введи ім'я і телефон\n"
            "4️⃣ Обери оплату: 💳 картою або 💵 готівкою\n"
            "5️⃣ Підтвердь — отримай реквізити\n\n"
            "При оплаті картою — покупець підтвердить отримання.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]]),
            parse_mode="HTML",
        )

    # ── Registration ───────────────────────────────────────────────────────────
    elif data == "register_shopper":
        if is_shopper(uid):
            await query.answer("Ви вже зареєстровані!", show_alert=True); return
        context.user_data["action"] = "enter_shopper_name"
        await query.edit_message_text(
            "🙋 <b>Реєстрація покупця</b>\n\nВведіть ваше ім'я:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Скасувати", callback_data="main_menu")]]),
            parse_mode="HTML",
        )

    # ── Shopper panel ──────────────────────────────────────────────────────────
    elif data == "shopper_panel":
        if not (is_shopper(uid) or is_admin(uid)):
            await query.answer("⛔", show_alert=True); return
        info  = get_shopper_info(uid) or {"name": "Покупець"}
        stats = get_today_stats()
        await query.edit_message_text(
            f"🎒 <b>Панель покупця — {info['name']}</b>\n\n"
            f"📦 Замовлень сьогодні: <b>{stats['count']}</b>\n"
            f"💰 Загальна сума: <b>{stats['total']:.0f} грн</b>\n"
            f"💵 Готівка: <b>{stats['cash']:.0f} грн</b>  💳 Картою: <b>{stats['card']:.0f} грн</b>\n"
            f"✅ Підтверджено оплат: <b>{stats['paid']}</b>",
            reply_markup=shopper_panel_kb(),
            parse_mode="HTML",
        )

    elif data == "shopper_shopping_list":
        """Зведений список що треба купити (тільки оплачені замовлення)."""
        if not (is_shopper(uid) or is_admin(uid)):
            await query.answer("⛔", show_alert=True); return
        today = get_today_orders()
        # Оплачені = картою підтверджено АБО готівка
        paid = [o for o in today
                if o.get("payment_confirmed") or o.get("payment_method") == "cash"]
        if not paid:
            await query.edit_message_text(
                "🛍️ <b>Список покупок порожній</b>\n\n"
                "Немає оплачених замовлень.\n"
                "<i>(Картові замовлення з'являються після підтвердження оплати,\n"
                "готівкові — одразу після оформлення)</i>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="shopper_panel")]]),
                parse_mode="HTML",
            )
            return
        # Агрегуємо по назві товару
        from collections import defaultdict as _dd
        agg = _dd(lambda: {"qty": 0, "unit": "", "total": 0.0})
        for o in paid:
            for it in o["items"]:
                agg[it["name"]]["qty"]   += it["qty"]
                agg[it["name"]]["unit"]   = it["unit"]
                agg[it["name"]]["total"] += it["subtotal"]
        cash_t = sum(o["total"] for o in paid if o.get("payment_method") == "cash")
        card_t = sum(o["total"] for o in paid if o.get("payment_method") == "card")
        lines = [
            f"🛍️ <b>Список покупок — {len(paid)} замовлень</b>\n",
        ]
        for name, d in sorted(agg.items()):
            lines.append(f"• {name}: <b>{d['qty']} {d['unit']}</b>")
        lines.append(f"\n──────────────────")
        lines.append(f"💰 Разом: <b>{cash_t + card_t:.0f} грн</b>")
        lines.append(f"💵 Готівка: <b>{cash_t:.0f} грн</b>  💳 Картою: <b>{card_t:.0f} грн</b>")
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="shopper_panel")]]),
            parse_mode="HTML",
        )

    elif data == "shopper_orders":
        if not (is_shopper(uid) or is_admin(uid)):
            await query.answer("⛔", show_alert=True); return
        today = get_today_orders()
        if not today:
            text = "📋 <b>Сьогодні замовлень немає.</b>"
        else:
            lines = [f"📋 <b>Замовлення сьогодні ({len(today)}):</b>\n"]
            for o in today:
                s  = "✅" if o.get("payment_confirmed") else ("💳" if o["payment_method"]=="card" else "💵")
                lines.append(f"{s} #{o['id']} {o['name']} | {o['total']:.0f} грн — {o['phone']}")
            text = "\n".join(lines)
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="shopper_panel")]]),
            parse_mode="HTML",
        )

    elif data == "shopper_unconfirmed":
        """Список замовлень де оплату ще не підтверджено (картою)."""
        if not (is_shopper(uid) or is_admin(uid)):
            await query.answer("⛔", show_alert=True); return
        today       = get_today_orders()
        unconfirmed = [o for o in today if not o.get("payment_confirmed") and o.get("payment_method") == "card"]
        if not unconfirmed:
            await query.edit_message_text(
                "✅ <b>Всі картові оплати підтверджені!</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="shopper_panel")]]),
                parse_mode="HTML",
            )
            return
        lines = [f"💳 <b>Очікують підтвердження оплати ({len(unconfirmed)}):</b>\n"]
        btns  = []
        for o in unconfirmed:
            lines.append(f"#{o['id']} {o['name']} | {o['total']:.0f} грн | {o['phone']}")
            btns.append([InlineKeyboardButton(
                f"✅ #{o['id']} — {o['name']} ({o['total']:.0f} грн) — ОПЛАТА ОТРИМАНА",
                callback_data=f"shopper_pay_ok:{o['id']}"
            )])
        btns.append([InlineKeyboardButton("◀️ Назад", callback_data="shopper_panel")])
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(btns),
            parse_mode="HTML",
        )

    elif data.startswith("shopper_pay_ok:"):
        """Покупець підтверджує отримання картової оплати."""
        if not (is_shopper(uid) or is_admin(uid)):
            await query.answer("⛔", show_alert=True); return
        order_id = int(data.split(":")[1])
        orders   = load_orders()
        client_uid = None
        order_name = ""
        for o in orders:
            if o["id"] == order_id and not o.get("payment_confirmed"):
                o["payment_confirmed"] = True
                o["payment_confirmed_by"] = uid
                o["payment_confirmed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                client_uid  = o.get("user_id")
                order_name  = o.get("name", "")
                break
        save_orders(orders)
        await query.answer(f"✅ Оплату #{order_id} підтверджено!", show_alert=True)
        # Notify client
        if client_uid:
            try:
                await context.bot.send_message(
                    chat_id=client_uid,
                    text=(
                        f"✅ <b>Вашу оплату підтверджено!</b>\n\n"
                        f"Замовлення #{order_id} оплачено. Дякуємо! 🎉"
                    ),
                    parse_mode="HTML",
                )
            except Exception: pass
        # Refresh unconfirmed list
        today       = get_today_orders()
        unconfirmed = [o for o in today if not o.get("payment_confirmed") and o.get("payment_method") == "card"]
        if not unconfirmed:
            await query.edit_message_text(
                "✅ <b>Всі картові оплати підтверджені!</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Панель", callback_data="shopper_panel")]]),
                parse_mode="HTML",
            )
        else:
            lines = [f"💳 <b>Очікують підтвердження ({len(unconfirmed)}):</b>\n"]
            btns  = []
            for o in unconfirmed:
                lines.append(f"#{o['id']} {o['name']} | {o['total']:.0f} грн")
                btns.append([InlineKeyboardButton(
                    f"✅ #{o['id']} — {o['name']} ({o['total']:.0f} грн)",
                    callback_data=f"shopper_pay_ok:{o['id']}"
                )])
            btns.append([InlineKeyboardButton("◀️ Назад", callback_data="shopper_panel")])
            await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

    elif data == "shopper_close_list":
        if not (is_shopper(uid) or is_admin(uid)):
            await query.answer("⛔", show_alert=True); return
        today = ddate.today().isoformat()
        orders = load_orders()
        closed_count = cash_t = card_t = 0
        for o in orders:
            if o.get("date") == today and o.get("status") != "closed":
                o["status"] = "closed"
                closed_count += 1
                if o.get("payment_method") == "cash":
                    cash_t += o["total"]
                else:
                    card_t += o["total"]
        save_orders(orders)
        info  = get_shopper_info(uid) or {"name": "Покупець"}
        text  = (
            f"✅ <b>Список закрито!</b>\n\n"
            f"📦 Закрито замовлень: <b>{closed_count}</b>\n"
            f"💵 Готівка: <b>{cash_t:.0f} грн</b>\n"
            f"💳 Картою: <b>{card_t:.0f} грн</b>\n"
            f"💰 Разом: <b>{cash_t + card_t:.0f} грн</b>"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]]),
            parse_mode="HTML",
        )
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"📦 <b>{info['name']} закрив список!</b>\n\n{text}",
                    parse_mode="HTML",
                )
            except Exception: pass

    # ── Catalog ────────────────────────────────────────────────────────────────
    elif data == "open_catalog":
        context.user_data.pop("current_category", None)
        await query.edit_message_text(
            "🛒 <b>Каталог</b>\n\nОберіть категорію:",
            reply_markup=catalog_kb(products, cart), parse_mode="HTML",
        )

    elif data == "back_catalog":
        context.user_data.pop("current_category", None)
        await query.edit_message_text(
            "🛒 <b>Каталог</b>\n\nОберіть категорію:",
            reply_markup=catalog_kb(products, cart), parse_mode="HTML",
        )

    elif data.startswith("cat:"):
        cat = data[4:]
        context.user_data["current_category"] = cat
        await query.edit_message_text(
            f"📦 <b>{cat}</b>\n\nОберіть товар:",
            reply_markup=catalog_kb(products, cart, cat), parse_mode="HTML",
        )

    elif data.startswith("item:"):
        pid = data[5:]
        pm  = {p["id"]: p for p in products}
        p   = pm.get(pid)
        if not p: await query.answer("Товар не знайдено", show_alert=True); return
        key = f"edit_qty_{pid}"
        if key not in context.user_data:
            context.user_data[key] = cart.get(pid, 0) or 1
        qty = context.user_data[key]
        await query.edit_message_text(
            f"<b>{p['name']}</b>\n💰 <b>{p['price']:.0f} грн / {p['unit']}</b>\n\nКількість:",
            reply_markup=item_kb(p, qty), parse_mode="HTML",
        )

    elif data.startswith("inc:"):
        pid = data[4:]
        key = f"edit_qty_{pid}"
        context.user_data[key] = context.user_data.get(key, 0) + 1
        pm  = {p["id"]: p for p in products}
        p   = pm[pid]; qty = context.user_data[key]
        await query.edit_message_text(
            f"<b>{p['name']}</b>\n💰 <b>{p['price']:.0f} грн / {p['unit']}</b>\n\nКількість:",
            reply_markup=item_kb(p, qty), parse_mode="HTML",
        )

    elif data.startswith("dec:"):
        pid = data[4:]
        key = f"edit_qty_{pid}"
        context.user_data[key] = max(0, context.user_data.get(key, 1) - 1)
        pm  = {p["id"]: p for p in products}
        p   = pm[pid]; qty = context.user_data[key]
        await query.edit_message_text(
            f"<b>{p['name']}</b>\n💰 <b>{p['price']:.0f} грн / {p['unit']}</b>\n\nКількість:",
            reply_markup=item_kb(p, qty), parse_mode="HTML",
        )

    elif data.startswith("add_confirm:"):
        pid = data[12:]
        key = f"edit_qty_{pid}"
        qty = context.user_data.get(key, 0)
        pm  = {p["id"]: p for p in products}
        p   = pm[pid]
        if qty > 0:
            cart[pid] = qty
            await query.answer(f"✅ {qty} шт. додано!", show_alert=False)
        else:
            cart.pop(pid, None)
            await query.answer("🗑 Видалено", show_alert=False)
        context.user_data.pop(key, None)
        cat = p.get("category", "Інше")
        await query.edit_message_text(
            f"📦 <b>{cat}</b>\n\nОберіть товар:",
            reply_markup=catalog_kb(products, cart, cat), parse_mode="HTML",
        )

    elif data == "noop":
        pass

    # ── Cart ───────────────────────────────────────────────────────────────────
    elif data == "open_cart":
        if not cart:
            await query.edit_message_text(
                "🛒 <b>Кошик порожній</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 Каталог", callback_data="open_catalog")],
                    [InlineKeyboardButton("🏠 Меню",    callback_data="main_menu")],
                ]),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                f"🧺 <b>Кошик:</b>\n\n{cart_summary_text(cart, products)}",
                reply_markup=cart_kb(), parse_mode="HTML",
            )

    elif data == "clear_cart":
        context.user_data["cart"] = {}
        await query.edit_message_text(
            "🗑 <b>Кошик очищено.</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Каталог", callback_data="open_catalog")],
                [InlineKeyboardButton("🏠 Меню",    callback_data="main_menu")],
            ]),
            parse_mode="HTML",
        )

    # ── Checkout ───────────────────────────────────────────────────────────────
    elif data == "checkout":
        if not cart:
            await query.answer("❌ Кошик порожній!", show_alert=True); return
        context.user_data["checkout_step"] = "name"
        await query.edit_message_text(
            "📝 <b>Оформлення</b>\n\nКрок 1/3: Введіть ваше <b>ім'я</b>:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Скасувати", callback_data="cancel_order")]]),
            parse_mode="HTML",
        )

    elif data == "cancel_order":
        for k in ("checkout_step", "order_name", "order_phone", "order_payment"):
            context.user_data.pop(k, None)
        await query.edit_message_text("❌ Скасовано.", reply_markup=main_menu_kb(uid), parse_mode="HTML")

    elif data in ("pay_card", "pay_cash"):
        context.user_data["order_payment"] = "card" if data == "pay_card" else "cash"
        context.user_data["checkout_step"] = "confirm"
        pay_lbl = "💳 Переказ на карту" if data == "pay_card" else "💵 Готівкою"
        summary = cart_summary_text(cart, products)
        await query.edit_message_text(
            f"📋 <b>Підтвердіть замовлення:</b>\n\n"
            f"👤 {context.user_data.get('order_name', '—')}\n"
            f"📞 {context.user_data.get('order_phone', '—')}\n"
            f"💳 Оплата: <b>{pay_lbl}</b>\n\n"
            f"🛒 <b>Товари:</b>\n{summary}",
            reply_markup=confirm_order_kb(), parse_mode="HTML",
        )

    elif data == "confirm_order":
        await finalize_order(update, context)

    # ── Admin panel ────────────────────────────────────────────────────────────
    elif data == "admin_panel":
        if not is_admin(uid): await query.answer("⛔", show_alert=True); return
        today_id = get_today_shopper_id()
        sl       = get_linked_shoppers()
        t_txt    = f"👟 Сьогодні: <b>{sl[str(today_id)]['name']}</b>" if today_id and str(today_id) in sl else "👟 Покупець <b>не призначений</b>"
        await query.edit_message_text(f"⚙️ <b>Адмін-панель</b>\n\n{t_txt}", reply_markup=admin_kb(), parse_mode="HTML")

    elif data == "admin_pick_shopper":
        if not is_admin(uid): await query.answer("⛔", show_alert=True); return
        if not get_linked_shoppers():
            await query.answer("Немає покупців! Використайте /addshopper", show_alert=True); return
        await query.edit_message_text("👟 <b>Хто іде сьогодні?</b>", reply_markup=pick_shopper_kb(), parse_mode="HTML")

    elif data.startswith("set_shopper:"):
        if not is_admin(uid): await query.answer("⛔", show_alert=True); return
        val = data[12:]
        if val == "none":
            set_today_shopper(None)
            await query.edit_message_text("✅ Покупець знятий.", reply_markup=admin_kb(), parse_mode="HTML")
        else:
            set_today_shopper(int(val))
            sl   = get_linked_shoppers()
            info = sl.get(val, {})
            name = info.get("name", val)
            try:
                card_txt = f"\n\n💳 Ваша карта: <code>{info['card']}</code>" if info.get("card") else ""
                await context.bot.send_message(
                    chat_id=int(val),
                    text=f"🎒 <b>Вас призначено покупцем на сьогодні!</b>{card_txt}\nЗамовлення надходитимуть до вас.",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"Cannot notify shopper {val}: {e}")
            await query.edit_message_text(f"✅ <b>{name}</b> — покупець сьогодні!", reply_markup=admin_kb(), parse_mode="HTML")

    elif data == "admin_stats":
        if not is_admin(uid): await query.answer("⛔", show_alert=True); return
        stats = get_today_stats()
        today = get_today_orders()
        closed = len([o for o in today if o.get("status") == "closed"])
        await query.edit_message_text(
            f"📊 <b>Статистика {ddate.today().strftime('%d.%m.%Y')}:</b>\n\n"
            f"📦 Замовлень: <b>{stats['count']}</b> (закрито: {closed})\n"
            f"✅ Підтверджено оплат: <b>{stats['paid']}</b>\n"
            f"💰 Загальна сума: <b>{stats['total']:.0f} грн</b>\n"
            f"💵 Готівка: <b>{stats['cash']:.0f} грн</b>\n"
            f"💳 Картою: <b>{stats['card']:.0f} грн</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]]),
            parse_mode="HTML",
        )

    elif data == "admin_today_orders":
        if not is_admin(uid): await query.answer("⛔", show_alert=True); return
        today = get_today_orders()
        if not today:
            text = "📋 <b>Сьогодні замовлень немає.</b>"
        else:
            lines = [f"📋 <b>Сьогодні ({len(today)}):</b>\n"]
            for o in today:
                paid = "✅" if o.get("payment_confirmed") else ("⏳")
                pay  = "💵" if o.get("payment_method") == "cash" else "💳"
                lines.append(f"{paid}{pay} #{o['id']} {o['name']} | {o['total']:.0f} грн")
            text = "\n".join(lines)
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]]),
            parse_mode="HTML",
        )

    elif data == "admin_shoppers":
        if not is_admin(uid): await query.answer("⛔", show_alert=True); return
        sl = get_linked_shoppers()
        if not sl:
            await query.edit_message_text(
                "👥 <b>Покупців немає.</b>\n\n"
                "Додайте командою:\n<code>/addshopper Ростик 5375 1234 5678 9012 Ростислав Monobank</code>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")]]),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                "👥 <b>Покупці</b>\n(натисніть для редагування карти):",
                reply_markup=shoppers_list_kb(), parse_mode="HTML",
            )

    elif data.startswith("edit_shopper:"):
        if not is_admin(uid): return
        s_uid  = data[13:]
        shoppers = load_shoppers()
        info   = shoppers.get(s_uid, {})
        card   = info.get("card", "не вказана")
        c_name = info.get("card_name", "—")
        bank   = info.get("bank", "—")
        await query.edit_message_text(
            f"👤 <b>{info.get('name', s_uid)}</b>\n\n"
            f"💳 Карта: <code>{card}</code>\n"
            f"👤 Власник: {c_name}\n"
            f"🏦 Банк: {bank}",
            reply_markup=edit_shopper_kb(s_uid), parse_mode="HTML",
        )

    elif data.startswith("shopper_setcard:"):
        if not is_admin(uid): return
        s_uid = data[16:]
        context.user_data["admin_action"] = f"setcard:{s_uid}"
        shoppers = load_shoppers()
        name   = shoppers.get(s_uid, {}).get("name", s_uid)
        await query.edit_message_text(
            f"💳 <b>Карта для {name}</b>\n\n"
            f"Введіть дані у форматі:\n"
            f"<code>5375 1234 5678 9012 | Власник Карти | Monobank</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Скасувати", callback_data=f"edit_shopper:{s_uid}")]]),
            parse_mode="HTML",
        )

    elif data.startswith("shopper_delete:"):
        if not is_admin(uid): return
        s_uid  = data[15:]
        shoppers = load_shoppers()
        name   = shoppers.pop(s_uid, {}).get("name", s_uid)
        save_shoppers(shoppers)
        await query.edit_message_text(f"🗑 <b>{name} видалений.</b>", reply_markup=admin_kb(), parse_mode="HTML")

    elif data == "admin_products":
        if not is_admin(uid): return
        lines = [f"📦 <b>Товари ({len(products)}):</b>\n"]
        for p in products:
            lines.append(f"• {p['name']} — {p['price']:.0f} грн/{p['unit']}")
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Додати", callback_data="admin_add_product")],
                [InlineKeyboardButton("◀️ Назад", callback_data="admin_panel")],
            ]),
            parse_mode="HTML",
        )

    elif data == "admin_add_product":
        if not is_admin(uid): return
        context.user_data["admin_action"] = "add_product"
        await query.edit_message_text(
            "➕ <b>Додати товар</b>\n\n<code>Назва | Ціна | Одиниця | Категорія</code>\n"
            "<i>Приклад: Фанта 0.5л | 35 | шт | Напої</i>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Скасувати", callback_data="admin_panel")]]),
            parse_mode="HTML",
        )

# ══════════════════════════════════════════════════════════════════════════════
#  TEXT MESSAGE HANDLER
# ══════════════════════════════════════════════════════════════════════════════
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.strip()
    uid   = update.effective_user.id
    user  = update.effective_user
    step  = context.user_data.get("checkout_step")
    act   = context.user_data.get("action")
    a_act = context.user_data.get("admin_action")

    # ── Admin: set card for shopper ────────────────────────────────────────────
    if a_act and a_act.startswith("setcard:") and is_admin(uid):
        s_uid = a_act[8:]
        context.user_data.pop("admin_action", None)
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 1:
            await update.message.reply_text("⚠️ Формат: <code>Номер карти | Власник | Банк</code>", parse_mode="HTML"); return
        card      = parts[0]
        card_name = parts[1] if len(parts) > 1 else ""
        bank      = parts[2] if len(parts) > 2 else ""
        shoppers  = load_shoppers()
        if s_uid in shoppers:
            shoppers[s_uid]["card"]      = card
            shoppers[s_uid]["card_name"] = card_name
            shoppers[s_uid]["bank"]      = bank
            save_shoppers(shoppers)
            name = shoppers[s_uid]["name"]
            await update.message.reply_text(
                f"✅ <b>Карту для {name} оновлено!</b>\n"
                f"💳 <code>{card}</code>\n👤 {card_name}\n🏦 {bank}",
                reply_markup=admin_kb(), parse_mode="HTML",
            )
        return

    # ── Admin: add product ─────────────────────────────────────────────────────
    if a_act == "add_product" and is_admin(uid):
        context.user_data.pop("admin_action", None)
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 4:
            await update.message.reply_text("⚠️ Формат: <code>Назва | Ціна | Одиниця | Категорія</code>", parse_mode="HTML"); return
        name_p, price_str, unit, cat = parts[0], parts[1], parts[2], parts[3]
        try:
            price = float(price_str.replace(",", "."))
        except ValueError:
            await update.message.reply_text("⚠️ Невірна ціна."); return
        products = load_products()
        pid      = name_p.lower().replace(" ", "_")[:20]
        existing = {p["id"] for p in products}
        base, i  = pid, 1
        while pid in existing: pid = f"{base}_{i}"; i += 1
        products.append({"id": pid, "name": name_p, "unit": unit, "price": price, "category": cat})
        save_products(products)
        await update.message.reply_text(f"✅ <b>{name_p}</b> — {price:.0f} грн/{unit} ({cat})", reply_markup=admin_kb(), parse_mode="HTML")
        return

    # ── Registration: enter shopper name ──────────────────────────────────────
    if act == "enter_shopper_name":
        context.user_data.pop("action", None)
        linked_name = link_shopper_by_name(uid, text, user.username or "")
        if ADMIN_ID:
            try:
                shoppers = load_shoppers()
                info     = shoppers.get(str(uid), {})
                card_txt = f"\n💳 {info['card']}" if info.get("card") else ""
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"🙋 <b>Новий покупець:</b> {linked_name}"
                        + (f" (@{user.username})" if user.username else "")
                        + f"\n🆔 <code>{uid}</code>{card_txt}"
                    ),
                    parse_mode="HTML",
                )
            except Exception: pass
        await update.message.reply_text(
            f"✅ <b>Вас зареєстровано як покупця!</b>\n👤 {linked_name}",
            reply_markup=shopper_panel_kb(), parse_mode="HTML",
        )
        return

    # ── Checkout: name ─────────────────────────────────────────────────────────
    if step == "name":
        if len(text) < 2:
            await update.message.reply_text("⚠️ Мінімум 2 символи."); return
        context.user_data["order_name"] = text
        context.user_data["checkout_step"] = "phone"
        await update.message.reply_text(
            "📱 Крок 2/3: Введіть <b>номер телефону</b>:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Скасувати", callback_data="cancel_order")]]),
            parse_mode="HTML",
        )

    # ── Checkout: phone ────────────────────────────────────────────────────────
    elif step == "phone":
        digits = "".join(c for c in text if c.isdigit())
        if len(digits) < 10:
            await update.message.reply_text("⚠️ Мінімум 10 цифр."); return
        context.user_data["order_phone"] = text
        context.user_data["checkout_step"] = "payment"
        await update.message.reply_text(
            "💳 Крок 3/3: Оберіть <b>спосіб оплати</b>:",
            reply_markup=payment_method_kb(), parse_mode="HTML",
        )

    else:
        await update.message.reply_text(
            "👋 Скористайтесь кнопками або /start",
            reply_markup=main_menu_kb(uid), parse_mode="HTML",
        )

# ══════════════════════════════════════════════════════════════════════════════
#  FINALIZE ORDER
# ══════════════════════════════════════════════════════════════════════════════
async def finalize_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    user     = update.effective_user
    products = load_products()
    cart     = get_cart(context)

    if not cart:
        await query.answer("❌ Кошик порожній!", show_alert=True); return

    pm    = {p["id"]: p for p in products}
    items, total = [], 0.0
    for pid, qty in cart.items():
        if pid in pm:
            p   = pm[pid]
            sub = p["price"] * qty
            total += sub
            items.append({"id": pid, "name": p["name"], "qty": qty, "unit": p["unit"],
                          "price": p["price"], "subtotal": sub})

    payment_method   = context.user_data.get("order_payment", "cash")
    today_shopper_id = get_today_shopper_id()
    shoppers         = load_shoppers()
    shopper_info     = shoppers.get(str(today_shopper_id)) if today_shopper_id else None

    all_orders = load_orders()
    order_id   = len(all_orders) + 1
    order = {
        "id":                order_id,
        "datetime":          datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date":              ddate.today().isoformat(),
        "user_id":           user.id,
        "username":          user.username or "",
        "name":              context.user_data.get("order_name", "—"),
        "phone":             context.user_data.get("order_phone", "—"),
        "items":             items,
        "total":             total,
        "payment_method":    payment_method,
        "shopper_id":        today_shopper_id,
        "shopper_name":      shopper_info["name"] if shopper_info else "",
        "status":            "pending",
        "payment_confirmed": False,
    }
    append_order(order)

    # Clear state
    context.user_data["cart"] = {}
    for k in ("checkout_step", "order_name", "order_phone", "order_payment"):
        context.user_data.pop(k, None)

    # Build reply for client
    items_text = "\n".join(f"• {it['name']} × {it['qty']} = {it['subtotal']:.0f} грн" for it in items)

    if payment_method == "card":
        if shopper_info and shopper_info.get("card"):
            pay_block = (
                f"💳 <b>Реквізити для переказу:</b>\n"
                f"🏦 {shopper_info.get('bank', '')}\n"
                f"💳 <code>{shopper_info['card']}</code>\n"
                f"👤 {shopper_info.get('card_name', shopper_info['name'])}\n\n"
                f"⚠️ <i>Після переказу покупець підтвердить отримання оплати.</i>"
            )
        else:
            pay_block = "💳 <i>Реквізити уточніть у покупця.</i>"
    else:
        pay_block = "💵 <b>Оплата готівкою</b> при отриманні."

    client_text = (
        f"✅ <b>Замовлення #{order_id} прийнято!</b>\n\n"
        f"📦 <b>Товари:</b>\n{items_text}\n\n"
        f"💰 <b>Сума: {total:.0f} грн</b>\n\n"
        f"{pay_block}"
    )
    await query.edit_message_text(
        client_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]]),
        parse_mode="HTML",
    )

    # Notify admin
    notify_text = format_order_notify(order)
    if ADMIN_ID:
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=notify_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Admin notify failed: {e}")

    # Notify today's shopper (with confirm button if card payment)
    if today_shopper_id and today_shopper_id != ADMIN_ID:
        try:
            kb = shopper_confirm_pay_kb(order_id) if payment_method == "card" else None
            await context.bot.send_message(
                chat_id=today_shopper_id,
                text=notify_text + ("\n\n💳 Підтвердіть отримання оплати:" if payment_method == "card" else ""),
                reply_markup=kb,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Shopper notify failed: {e}")
    elif ADMIN_ID and payment_method == "card":
        # If no separate shopper, admin confirms payment
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text="💳 Підтвердіть отримання оплати:",
                reply_markup=shopper_confirm_pay_kb(order_id),
            )
        except Exception: pass

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "your_telegram_bot_token_here":
        print("❌ ПОМИЛКА: Встановіть TELEGRAM_TOKEN у .env")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("menu",        cmd_menu))
    app.add_handler(CommandHandler("admin",       cmd_admin))
    app.add_handler(CommandHandler("orders",      cmd_orders))
    app.add_handler(CommandHandler("addshopper",  cmd_addshopper))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Бот запущено! Ctrl+C для зупинки.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()

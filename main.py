import asyncio
import logging
import os
import sqlite3

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto,
)

load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------- DATABASE ----------------
os.makedirs("data", exist_ok=True)
conn = sqlite3.connect("data/oshxona.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS products (
    name TEXT PRIMARY KEY,
    price INTEGER,
    stock INTEGER,
    img TEXT,
    category TEXT
)
""")
for col in ("img", "category"):
    try:
        cursor.execute(f"ALTER TABLE products ADD COLUMN {col} TEXT")
    except sqlite3.OperationalError:
        pass

cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    items TEXT,
    total INTEGER,
    status TEXT
)
""")

# Seed kategoriyalar
_DEFAULT_CATS = ["🍛 Quyuq taom", "🍲 Suyuq taom", "🍰 Shirinliklar", "🥤 Ichimliklar"]
for _c in _DEFAULT_CATS:
    cursor.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (_c,))

# Seed mahsulotlar
_DEFAULT_PRODUCTS = [
    ("Osh",    30000, 10, "🍛 Quyuq taom"),
    ("Manti",   8000, 10, "🍛 Quyuq taom"),
    ("Sho'rva", 35000, 10, "🍲 Suyuq taom"),
    ("Tort",   15000, 10, "🍰 Shirinliklar"),
    ("Cola",    8000, 10, "🥤 Ichimliklar"),
]
for _n, _p, _s, _cat in _DEFAULT_PRODUCTS:
    cursor.execute(
        "INSERT OR IGNORE INTO products (name, price, stock, category) VALUES (?,?,?,?)",
        (_n, _p, _s, _cat),
    )
    # Mavjud mahsulotlarga kategoriya yozish (eski qatorlar uchun)
    cursor.execute(
        "UPDATE products SET category=? WHERE name=? AND category IS NULL",
        (_cat, _n),
    )

conn.commit()

cart = {}
user_quantity = {}   # {(user_id, name): qty}
user_selected = {}   # {user_id: name}
user_cat_msg = {}    # {user_id: (message_id, is_photo)}


# ---------------- DB HELPERS ----------------
def get_categories() -> list[str]:
    cursor.execute("SELECT name FROM categories ORDER BY id")
    return [r[0] for r in cursor.fetchall()]


def db_items(category_name: str) -> list[tuple]:
    cursor.execute(
        "SELECT name, price FROM products WHERE category=?", (category_name,)
    )
    return cursor.fetchall()


def find_cat(item_name: str) -> str | None:
    cursor.execute("SELECT category FROM products WHERE name=?", (item_name,))
    row = cursor.fetchone()
    return row[0] if row else None


def db_img(name: str) -> str | None:
    cursor.execute("SELECT img FROM products WHERE name=?", (name,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


# ---------------- FSM ----------------
class AdminState(StatesGroup):
    # Mahsulot qo'shish
    add_name  = State()
    add_price = State()
    add_stock = State()
    add_cat   = State()
    add_img   = State()
    # Kategoriya qo'shish
    new_cat_name = State()
    # Kategoriya tahrirlash
    edit_cat_select = State()
    edit_cat_name   = State()
    # Kategoriya o'chirish
    del_cat_select  = State()
    # Mahsulot tahrirlash
    edit_select = State()
    edit_field  = State()
    edit_value  = State()
    # Mahsulot o'chirish
    del_select = State()
    # (eski holatlar, saqlab qolindi)
    edit_name  = State()
    edit_price = State()


# ---------------- KEYBOARDS ----------------
def main_kb(is_admin=False):
    cats = get_categories()
    rows = []
    # Kategoriyalarni 2 tadan qator qilib joylash
    for i in range(0, len(cats), 2):
        pair = cats[i:i+2]
        rows.append([KeyboardButton(text=c) for c in pair])
    rows.append([KeyboardButton(text="🛒 Savatcha"), KeyboardButton(text="📦 Buyurtmalarim")])
    if is_admin:
        rows.append([KeyboardButton(text="⚙️ Admin panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Mahsulot qo'shish"),    KeyboardButton(text="✏️ Mahsulot tahrirlash")],
        [KeyboardButton(text="🗑 Mahsulot o'chirish"),   KeyboardButton(text="📦 Ombor")],
        [KeyboardButton(text="📂 Kategoriya qo'shish"),  KeyboardButton(text="✏️ Kategoriya tahrirlash")],
        [KeyboardButton(text="🗑 Kategoriya o'chirish"), KeyboardButton(text="🔙 Orqaga")],
    ], resize_keyboard=True)


def category_kb(items_prices):
    """Kategoriya ro'yxati — barcha mahsulotlar."""
    rows = [[InlineKeyboardButton(
        text=f"🍽 {name} — {price:,} so'm",
        callback_data=f"sel|{name}",
    )] for name, price in items_prices]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def item_kb(user_id, name, cat):
    """Tanlangan mahsulot ko'rinishi — faqat shu mahsulot."""
    qty = user_quantity.get((user_id, name), 1)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➖", callback_data=f"minus|{name}"),
            InlineKeyboardButton(text=f"  {qty} dona  ", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"plus|{name}"),
        ],
        [InlineKeyboardButton(text="🛒 Savatga qo'shish", callback_data=f"add|{name}")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"back|{cat}")],
    ])


# ---------------- START ----------------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🍽 Smart oshxona botiga xush kelibsiz!",
        reply_markup=main_kb(message.from_user.id == ADMIN_ID),
    )


# ---------------- HELPERS ----------------
async def delete_cat_msg(user_id):
    entry = user_cat_msg.pop(user_id, None)
    if entry:
        try:
            await bot.delete_message(user_id, entry[0])
        except Exception:
            pass


# ---------------- CATEGORY (dinamik filter) ----------------
async def _is_category(message: Message) -> bool:
    return message.text in get_categories()


@dp.message(_is_category)
async def show_category(message: Message):
    cat = message.text
    items = db_items(cat)
    if not items:
        await message.answer("Bu kategoriyada mahsulotlar yo'q.")
        return
    uid = message.from_user.id
    user_selected.pop(uid, None)
    await delete_cat_msg(uid)
    text = f"{cat}\n{'─'*28}\nTaomni tanlang 👇"
    sent = await message.answer(text, reply_markup=category_kb(items))
    user_cat_msg[uid] = (sent.message_id, False)


# ---------------- SELECT ----------------
@dp.callback_query(F.data.startswith("sel|"))
async def select_item(callback: CallbackQuery):
    name = callback.data.split("|")[1]
    uid = callback.from_user.id
    user_selected[uid] = name

    cat = find_cat(name)
    kb = item_kb(uid, name, cat)
    img = db_img(name)

    cursor.execute("SELECT price FROM products WHERE name=?", (name,))
    row = cursor.fetchone()
    price_text = f"{row[0]:,} so'm" if row else ""
    caption = f"🍽 {name}\n💰 {price_text}"

    if img:
        await delete_cat_msg(uid)
        sent = await bot.send_photo(
            callback.message.chat.id, img,
            caption=caption, reply_markup=kb,
        )
        user_cat_msg[uid] = (sent.message_id, True)
    else:
        await callback.message.edit_text(caption, reply_markup=kb)
        user_cat_msg[uid] = (callback.message.message_id, False)

    await callback.answer()


# ---------------- BACK TO CATEGORY ----------------
@dp.callback_query(F.data.startswith("back|"))
async def back_to_category(callback: CallbackQuery):
    cat = callback.data.split("|", 1)[1]
    uid = callback.from_user.id
    items = db_items(cat)
    text = f"{cat}\n{'─'*28}\nTaomni tanlang 👇"

    await delete_cat_msg(uid)
    sent = await callback.message.answer(text, reply_markup=category_kb(items))
    user_cat_msg[uid] = (sent.message_id, False)
    await callback.answer()


# ---------------- QUANTITY ----------------
async def update_qty_kb(callback: CallbackQuery, name: str):
    uid = callback.from_user.id
    cat = find_cat(name)
    kb = item_kb(uid, name, cat)
    entry = user_cat_msg.get(uid)
    msg_id = entry[0] if entry else callback.message.message_id
    try:
        await bot.edit_message_reply_markup(
            chat_id=callback.message.chat.id,
            message_id=msg_id,
            reply_markup=kb,
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("plus|"))
async def plus_qty(callback: CallbackQuery):
    name = callback.data.split("|")[1]
    key = (callback.from_user.id, name)
    user_quantity[key] = user_quantity.get(key, 1) + 1
    await update_qty_kb(callback, name)
    await callback.answer()


@dp.callback_query(F.data.startswith("minus|"))
async def minus_qty(callback: CallbackQuery):
    name = callback.data.split("|")[1]
    key = (callback.from_user.id, name)
    user_quantity[key] = max(1, user_quantity.get(key, 1) - 1)
    await update_qty_kb(callback, name)
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()


# ---------------- ADD TO CART ----------------
@dp.callback_query(F.data.startswith("add|"))
async def add_to_cart(callback: CallbackQuery):
    name = callback.data.split("|")[1]
    uid = callback.from_user.id
    qty = user_quantity.get((uid, name), 1)

    cursor.execute("SELECT price, stock FROM products WHERE name=?", (name,))
    row = cursor.fetchone()
    if not row:
        await callback.answer("❌ Mahsulot topilmadi")
        return
    price, stock = row
    if qty > stock:
        await callback.answer("❌ Omborda yetarli emas")
        return

    cart.setdefault(uid, []).append((name, price, qty))
    await callback.answer(f"✅ {name} x{qty} savatga qo'shildi")


# ---------------- CART ----------------
@dp.message(F.text == "🛒 Savatcha")
async def show_cart(message: Message):
    uid = message.from_user.id
    if not cart.get(uid):
        return await message.answer("Savat bo'sh")
    total = 0
    text = "🛒 Savat:\n"
    for name, price, qty in cart[uid]:
        text += f"  • {name} x{qty} = {price*qty:,} so'm\n"
        total += price * qty
    text += f"\n💰 Jami: {total:,} so'm"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Buyurtma berish", callback_data="order")]
    ])
    await message.answer(text, reply_markup=kb)


# ---------------- ORDER ----------------
@dp.callback_query(F.data == "order")
async def make_order(callback: CallbackQuery):
    uid = callback.from_user.id
    if not cart.get(uid):
        return await callback.answer("Savat bo'sh")
    items = str(cart[uid])
    total = sum(p * q for _, p, q in cart[uid])
    cursor.execute(
        "INSERT INTO orders (user_id, items, total, status) VALUES (?,?,?,?)",
        (uid, items, total, "pending"),
    )
    conn.commit()
    await callback.answer()
    await bot.send_message(uid, "💳 Karta: 8600 1234 5678 0000\n📤 Chek rasmini yuboring")


# ---------------- PAYMENT CHECK ----------------
@dp.message(F.photo, StateFilter(None))
async def payment_check(message: Message):
    uid = message.from_user.id
    cursor.execute("SELECT id FROM orders WHERE user_id=? AND status='pending'", (uid,))
    if not cursor.fetchone():
        await message.answer("❌ Sizda faol buyurtma yo'q.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Qabul", callback_data=f"accept|{uid}")],
        [InlineKeyboardButton(text="🍽 Tayyor", callback_data=f"ready|{uid}")],
    ])
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id,
                         caption=f"User: {uid}", reply_markup=kb)


# ---------------- ACCEPT / READY ----------------
@dp.callback_query(F.data.startswith("accept|"))
async def accept(callback: CallbackQuery):
    uid = int(callback.data.split("|")[1])
    await callback.answer()
    await bot.send_message(uid, "✅ To'lovingiz qabul qilindi")


@dp.callback_query(F.data.startswith("ready|"))
async def ready(callback: CallbackQuery):
    uid = int(callback.data.split("|")[1])
    for name, price, qty in cart.get(uid, []):
        cursor.execute("UPDATE products SET stock=stock-? WHERE name=?", (qty, name))
    cursor.execute("UPDATE orders SET status='done' WHERE user_id=? AND status='pending'", (uid,))
    conn.commit()
    cart[uid] = []
    await callback.answer()
    await bot.send_message(uid, "🍽 Buyurtmangiz tayyor!")


# ---------------- MY ORDERS ----------------
@dp.message(F.text == "📦 Buyurtmalarim")
async def my_orders(message: Message):
    cursor.execute("SELECT items, total, status FROM orders WHERE user_id=?", (message.from_user.id,))
    rows = cursor.fetchall()
    if not rows:
        return await message.answer("Buyurtmalar yo'q")
    text = ""
    for items, total, status in rows:
        text += f"{items}\n💰 {total:,} so'm | {status}\n\n"
    await message.answer(text)


# ---------------- ADMIN PANEL ----------------
@dp.message(F.text == "⚙️ Admin panel")
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("⚙️ Admin panel", reply_markup=admin_kb())


# ---- KATEGORIYA QO'SHISH ----
@dp.message(F.text == "📂 Kategoriya qo'shish")
async def add_cat_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    cats = get_categories()
    cats_text = "\n".join(f"• {c}" for c in cats)
    await message.answer(
        f"Mavjud kategoriyalar:\n{cats_text}\n\n"
        "Yangi kategoriya nomini yozing (masalan: 🥗 Salatlar):"
    )
    await state.set_state(AdminState.new_cat_name)


@dp.message(AdminState.new_cat_name)
async def add_cat_name_h(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("❌ Bo'm-bo'sh nom bo'lmaydi. Qaytadan yozing:")
        return
    cursor.execute("SELECT name FROM categories WHERE name=?", (name,))
    if cursor.fetchone():
        await message.answer(f"❌ '{name}' kategoriyasi allaqachon mavjud. Boshqa nom yozing:")
        return
    cursor.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    conn.commit()
    await message.answer(
        f"✅ '{name}' kategoriyasi qo'shildi!\n\n"
        "Endi foydalanuvchilar asosiy menyuda bu kategoriyani ko'radi.",
        reply_markup=admin_kb(),
    )
    await state.clear()


# ---- KATEGORIYA TAHRIRLASH ----
def cats_manage_kb(prefix: str):
    cats = get_categories()
    btns = [[InlineKeyboardButton(text=c, callback_data=f"{prefix}|{c}")] for c in cats]
    btns.append([InlineKeyboardButton(text="❌ Bekor", callback_data="catcancel")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


@dp.message(F.text == "✏️ Kategoriya tahrirlash")
async def edit_cat_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Tahrirlamoqchi bo'lgan kategoriyani tanlang:", reply_markup=cats_manage_kb("editcat"))
    await state.set_state(AdminState.edit_cat_select)


@dp.callback_query(AdminState.edit_cat_select, F.data.startswith("editcat|"))
async def edit_cat_select_h(callback: CallbackQuery, state: FSMContext):
    cat = callback.data.split("|", 1)[1]
    await state.update_data(old_cat=cat)
    await callback.message.edit_text(f"'{cat}' uchun yangi nomni yozing:")
    await state.set_state(AdminState.edit_cat_name)
    await callback.answer()


@dp.message(AdminState.edit_cat_name)
async def edit_cat_name_h(message: Message, state: FSMContext):
    new_name = message.text.strip()
    if not new_name:
        await message.answer("❌ Bo'm-bo'sh nom bo'lmaydi:")
        return
    data = await state.get_data()
    old_cat = data["old_cat"]
    cursor.execute("SELECT name FROM categories WHERE name=?", (new_name,))
    if cursor.fetchone():
        await message.answer(f"❌ '{new_name}' allaqachon mavjud. Boshqa nom yozing:")
        return
    cursor.execute("UPDATE categories SET name=? WHERE name=?", (new_name, old_cat))
    cursor.execute("UPDATE products SET category=? WHERE category=?", (new_name, old_cat))
    conn.commit()
    await message.answer(f"✅ '{old_cat}' → '{new_name}' ga o'zgartirildi.", reply_markup=admin_kb())
    await state.clear()


@dp.callback_query(F.data == "catcancel")
async def cat_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()


# ---- KATEGORIYA O'CHIRISH ----
def confirm_del_cat_kb(cat: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, o'chir", callback_data=f"delcatconfirm|{cat}")],
        [InlineKeyboardButton(text="❌ Yo'q",       callback_data="catcancel")],
    ])


@dp.message(F.text == "🗑 Kategoriya o'chirish")
async def del_cat_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("O'chirmoqchi bo'lgan kategoriyani tanlang:", reply_markup=cats_manage_kb("delcat"))
    await state.set_state(AdminState.del_cat_select)


@dp.callback_query(AdminState.del_cat_select, F.data.startswith("delcat|"))
async def del_cat_select_h(callback: CallbackQuery, state: FSMContext):
    cat = callback.data.split("|", 1)[1]
    cursor.execute("SELECT COUNT(*) FROM products WHERE category=?", (cat,))
    count = cursor.fetchone()[0]
    warning = f"\n⚠️ Bu kategoriyada {count} ta mahsulot bor, ular kategoriyasiz qoladi." if count else ""
    await callback.message.edit_text(
        f"'{cat}' kategoriyasini o'chirishni tasdiqlaysizmi?{warning}",
        reply_markup=confirm_del_cat_kb(cat),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("delcatconfirm|"))
async def del_cat_confirm_h(callback: CallbackQuery, state: FSMContext):
    cat = callback.data.split("|", 1)[1]
    cursor.execute("UPDATE products SET category=NULL WHERE category=?", (cat,))
    cursor.execute("DELETE FROM categories WHERE name=?", (cat,))
    conn.commit()
    await state.clear()
    await callback.message.edit_text(f"🗑 '{cat}' kategoriyasi o'chirildi.")
    await callback.answer()


# ---- MAHSULOT TAHRIRLASH ----
def products_list_kb():
    cursor.execute("SELECT name FROM products")
    rows = cursor.fetchall()
    btns = [[InlineKeyboardButton(text=r[0], callback_data=f"editprod|{r[0]}")] for r in rows]
    return InlineKeyboardMarkup(inline_keyboard=btns)


def edit_fields_kb(name):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Nom",   callback_data=f"editfield|{name}|nom")],
        [InlineKeyboardButton(text="💰 Narx",  callback_data=f"editfield|{name}|narx")],
        [InlineKeyboardButton(text="📦 Soni",  callback_data=f"editfield|{name}|soni")],
        [InlineKeyboardButton(text="🖼 Rasm",  callback_data=f"editfield|{name}|rasm")],
        [InlineKeyboardButton(text="❌ Bekor", callback_data="editcancel")],
    ])


@dp.message(F.text == "✏️ Mahsulot tahrirlash")
async def edit_prod_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Tahrirlamoqchi bo'lgan mahsulotni tanlang:", reply_markup=products_list_kb())
    await state.set_state(AdminState.edit_select)


@dp.callback_query(AdminState.edit_select, F.data.startswith("editprod|"))
async def edit_prod_select(callback: CallbackQuery, state: FSMContext):
    name = callback.data.split("|")[1]
    cursor.execute("SELECT price, stock FROM products WHERE name=?", (name,))
    row = cursor.fetchone()
    await callback.message.edit_text(
        f"📋 {name}\n💰 {row[0]:,} so'm | 📦 {row[1]} dona\n\nNimani o'zgartirish?",
        reply_markup=edit_fields_kb(name),
    )
    await state.update_data(edit_name=name)
    await state.set_state(AdminState.edit_field)


@dp.callback_query(AdminState.edit_field, F.data.startswith("editfield|"))
async def edit_field_select(callback: CallbackQuery, state: FSMContext):
    _, name, field = callback.data.split("|")
    await state.update_data(field=field)
    prompts = {
        "nom":  "Yangi nomni yozing:",
        "narx": "Yangi narxni yozing (so'm):",
        "soni": "Yangi sonini yozing (dona):",
        "rasm": "Yangi rasmni yuboring:",
    }
    await callback.message.edit_text(prompts[field])
    await state.set_state(AdminState.edit_value)
    await callback.answer()


@dp.callback_query(F.data == "editcancel")
async def edit_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()


@dp.message(AdminState.edit_value, F.photo)
async def edit_value_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("field") != "rasm":
        await message.answer("❌ Matn yozing.")
        return
    file_id = message.photo[-1].file_id
    cursor.execute("UPDATE products SET img=? WHERE name=?", (file_id, data["edit_name"]))
    conn.commit()
    await message.answer(f"✅ {data['edit_name']} rasmi yangilandi.", reply_markup=admin_kb())
    await state.clear()


@dp.message(AdminState.edit_value)
async def edit_value_text(message: Message, state: FSMContext):
    data = await state.get_data()
    name = data["edit_name"]
    field = data["field"]

    if field == "nom":
        cursor.execute("UPDATE products SET name=? WHERE name=?", (message.text, name))
        conn.commit()
        await message.answer(f"✅ Nom: {name} → {message.text}", reply_markup=admin_kb())

    elif field == "narx":
        if not message.text.isdigit():
            await message.answer("❌ Faqat raqam yozing:")
            return
        cursor.execute("UPDATE products SET price=? WHERE name=?", (int(message.text), name))
        conn.commit()
        await message.answer(f"✅ {name} narxi → {int(message.text):,} so'm", reply_markup=admin_kb())

    elif field == "soni":
        if not message.text.isdigit():
            await message.answer("❌ Faqat raqam yozing:")
            return
        cursor.execute("UPDATE products SET stock=? WHERE name=?", (int(message.text), name))
        conn.commit()
        await message.answer(f"✅ {name} soni → {message.text} dona", reply_markup=admin_kb())

    elif field == "rasm":
        await message.answer("❌ Rasm yuboring (fayl emas).")
        return

    await state.clear()


# ---- MAHSULOT O'CHIRISH ----
def delete_list_kb():
    cursor.execute("SELECT name FROM products")
    rows = cursor.fetchall()
    btns = [[InlineKeyboardButton(text=r[0], callback_data=f"delprod|{r[0]}")] for r in rows]
    btns.append([InlineKeyboardButton(text="❌ Bekor", callback_data="delcancel")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


def confirm_delete_kb(name):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, o'chir", callback_data=f"delconfirm|{name}")],
        [InlineKeyboardButton(text="❌ Yo'q",       callback_data="delcancel")],
    ])


@dp.message(F.text == "🗑 Mahsulot o'chirish")
async def del_prod_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("O'chirmoqchi bo'lgan mahsulotni tanlang:", reply_markup=delete_list_kb())
    await state.set_state(AdminState.del_select)


@dp.callback_query(AdminState.del_select, F.data.startswith("delprod|"))
async def del_prod_select(callback: CallbackQuery, state: FSMContext):
    name = callback.data.split("|")[1]
    await callback.message.edit_text(
        f"⚠️ '{name}' ni o'chirishni tasdiqlaysizmi?",
        reply_markup=confirm_delete_kb(name),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("delconfirm|"))
async def del_confirmed(callback: CallbackQuery, state: FSMContext):
    name = callback.data.split("|")[1]
    cursor.execute("DELETE FROM products WHERE name=?", (name,))
    conn.commit()
    await state.clear()
    await callback.message.edit_text(f"🗑 '{name}' o'chirildi.")
    await callback.answer()


@dp.callback_query(F.data == "delcancel")
async def del_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()


# ---- MAHSULOT QO'SHISH ----
def cats_inline_kb():
    cats = get_categories()
    btns = [[InlineKeyboardButton(text=c, callback_data=f"pickcat|{c}")] for c in cats]
    return InlineKeyboardMarkup(inline_keyboard=btns)


@dp.message(F.text == "➕ Mahsulot qo'shish")
async def add_prod_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Mahsulot nomini yozing:")
    await state.set_state(AdminState.add_name)


@dp.message(AdminState.add_name)
async def add_name_h(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Narxi (so'm):")
    await state.set_state(AdminState.add_price)


@dp.message(AdminState.add_price)
async def add_price_h(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Faqat raqam:")
        return
    await state.update_data(price=int(message.text))
    await message.answer("Soni (dona):")
    await state.set_state(AdminState.add_stock)


@dp.message(AdminState.add_stock)
async def add_stock_h(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Faqat raqam:")
        return
    await state.update_data(stock=int(message.text))
    await message.answer("Kategoriyani tanlang:", reply_markup=cats_inline_kb())
    await state.set_state(AdminState.add_cat)


@dp.callback_query(AdminState.add_cat, F.data.startswith("pickcat|"))
async def add_cat_pick_h(callback: CallbackQuery, state: FSMContext):
    cat = callback.data.split("|", 1)[1]
    await state.update_data(cat=cat)
    await callback.message.edit_text(f"✅ Kategoriya: {cat}")
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏭ O'tkazib yuborish")]],
        resize_keyboard=True,
    )
    await callback.message.answer(
        "Mahsulot rasmini yuboring:\n(Rasm bo'lmasa — O'tkazib yuborish)",
        reply_markup=kb,
    )
    await state.set_state(AdminState.add_img)
    await callback.answer()


@dp.message(AdminState.add_img, F.photo)
async def add_img_photo_h(message: Message, state: FSMContext):
    data = await state.get_data()
    file_id = message.photo[-1].file_id
    cursor.execute(
        "INSERT OR IGNORE INTO products (name, price, stock, img, category) VALUES (?,?,?,?,?)",
        (data["name"], data["price"], data["stock"], file_id, data["cat"]),
    )
    conn.commit()
    await message.answer(
        f"✅ {data['name']} → {data['cat']} ga qo'shildi (rasm bilan)",
        reply_markup=admin_kb(),
    )
    await state.clear()


@dp.message(AdminState.add_img, F.text == "⏭ O'tkazib yuborish")
async def add_img_skip_h(message: Message, state: FSMContext):
    data = await state.get_data()
    cursor.execute(
        "INSERT OR IGNORE INTO products (name, price, stock, category) VALUES (?,?,?,?)",
        (data["name"], data["price"], data["stock"], data["cat"]),
    )
    conn.commit()
    await message.answer(
        f"✅ {data['name']} → {data['cat']} ga qo'shildi (rasmsiz)",
        reply_markup=admin_kb(),
    )
    await state.clear()


@dp.message(AdminState.add_img)
async def add_img_wrong_h(message: Message):
    await message.answer("❌ Rasm yuboring yoki O'tkazib yuborish tugmasini bosing.")


# ---- OMBOR ----
@dp.message(F.text == "📦 Ombor")
async def show_stock(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT name, price, stock, img, category FROM products")
    rows = cursor.fetchall()
    lines = [
        f"{'✅' if img else '❌'} {n} | {p:,} so'm | {s} dona | {cat or '—'}"
        for n, p, s, img, cat in rows
    ]
    await message.answer("\n".join(lines) or "Bo'sh")


# ---- ORQAGA ----
@dp.message(F.text == "🔙 Orqaga")
async def go_back(message: Message):
    await cmd_start(message)


# ---------------- RUN ----------------
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

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
CREATE TABLE IF NOT EXISTS products (
    name TEXT PRIMARY KEY,
    price INTEGER,
    stock INTEGER,
    img TEXT
)
""")
try:
    cursor.execute("ALTER TABLE products ADD COLUMN img TEXT")
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
conn.commit()

for name, price, stock in [
    ("Osh", 30000, 10), ("Manti", 8000, 10),
    ("Sho'rva", 35000, 10), ("Tort", 15000, 10), ("Cola", 8000, 10),
]:
    cursor.execute(
        "INSERT OR IGNORE INTO products (name, price, stock) VALUES (?,?,?)",
        (name, price, stock),
    )
conn.commit()

MENU = {
    "🍛 Quyuq taom": ["Osh", "Manti"],
    "🍲 Suyuq taom": ["Sho'rva"],
    "🍰 Shirinliklar": ["Tort"],
    "🥤 Ichimliklar": ["Cola"],
}
CATEGORY_KEYS = list(MENU.keys())

cart = {}
user_quantity = {}   # {(user_id, name): qty}
user_selected = {}   # {user_id: name}
user_cat_msg = {}    # {user_id: (message_id, is_photo)}


# ---------------- FSM ----------------
class AdminState(StatesGroup):
    add_name   = State()
    add_price  = State()
    add_stock  = State()
    add_cat    = State()
    add_img    = State()
    edit_select = State()
    edit_field  = State()
    edit_value  = State()
    del_select  = State()
    edit_name  = State()   # narx o'zgartirish uchun (eski)
    edit_price = State()
    img_name   = State()
    img_photo  = State()


# ---------------- KEYBOARDS ----------------
def main_kb(is_admin=False):
    rows = [
        [KeyboardButton(text="🍛 Quyuq taom"),  KeyboardButton(text="🍲 Suyuq taom")],
        [KeyboardButton(text="🍰 Shirinliklar"), KeyboardButton(text="🥤 Ichimliklar")],
        [KeyboardButton(text="🛒 Savatcha"),     KeyboardButton(text="📦 Buyurtmalarim")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="⚙️ Admin panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Mahsulot qo'shish"), KeyboardButton(text="✏️ Mahsulot tahrirlash")],
        [KeyboardButton(text="🖼 Rasm qo'shish"),     KeyboardButton(text="🗑 Mahsulot o'chirish")],
        [KeyboardButton(text="📦 Ombor"),             KeyboardButton(text="🔙 Orqaga")],
    ], resize_keyboard=True)


def category_kb(user_id, items_prices, selected=None):
    rows = []
    for name, price in items_prices:
        tick = "✅" if name == selected else "🍽"
        rows.append([InlineKeyboardButton(
            text=f"{tick} {name} — {price:,} so'm",
            callback_data=f"sel|{name}",
        )])
    if selected:
        qty = user_quantity.get((user_id, selected), 1)
        rows.append([
            InlineKeyboardButton(text="➖", callback_data=f"minus|{selected}"),
            InlineKeyboardButton(text=f"  {qty} dona  ", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"plus|{selected}"),
        ])
        rows.append([InlineKeyboardButton(
            text=f"🛒 Savatga qo'shish — {selected}",
            callback_data=f"add|{selected}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------- START ----------------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🍽 Smart oshxona botiga xush kelibsiz!",
        reply_markup=main_kb(message.from_user.id == ADMIN_ID),
    )


# ---------------- HELPERS ----------------
def db_items(category_name):
    result = []
    for name in MENU.get(category_name, []):
        cursor.execute("SELECT price FROM products WHERE name=?", (name,))
        row = cursor.fetchone()
        if row:
            result.append((name, row[0]))
    return result


def find_cat(item_name):
    for cat, items in MENU.items():
        if item_name in items:
            return cat
    return None


def db_img(name):
    cursor.execute("SELECT img FROM products WHERE name=?", (name,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


async def delete_cat_msg(user_id):
    entry = user_cat_msg.pop(user_id, None)
    if entry:
        try:
            await bot.delete_message(user_id, entry[0])
        except Exception:
            pass


# ---------------- CATEGORY ----------------
@dp.message(F.text.in_(CATEGORY_KEYS))
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
    sent = await message.answer(text, reply_markup=category_kb(uid, items))
    user_cat_msg[uid] = (sent.message_id, False)


# ---------------- SELECT ----------------
@dp.callback_query(F.data.startswith("sel|"))
async def select_item(callback: CallbackQuery):
    name = callback.data.split("|")[1]
    uid = callback.from_user.id
    user_selected[uid] = name

    cat = find_cat(name)
    items = db_items(cat) if cat else []
    kb = category_kb(uid, items, selected=name)
    img = db_img(name)

    entry = user_cat_msg.get(uid)
    caption = f"{cat}\n{'─'*28}\n🍽 {name} tanlandi 👇"

    if img:
        if entry and entry[1]:
            # Allaqachon foto xabar — mediasini va keyboardini yangilash
            try:
                await bot.edit_message_media(
                    chat_id=callback.message.chat.id,
                    message_id=entry[0],
                    media=InputMediaPhoto(media=img, caption=caption),
                )
                await bot.edit_message_reply_markup(
                    chat_id=callback.message.chat.id,
                    message_id=entry[0],
                    reply_markup=kb,
                )
                await callback.answer()
                return
            except Exception:
                pass
        # Matn xabarni o'chirib, foto xabar yuborish
        await delete_cat_msg(uid)
        sent = await bot.send_photo(
            callback.message.chat.id, img,
            caption=caption, reply_markup=kb,
        )
        user_cat_msg[uid] = (sent.message_id, True)
    else:
        # Rasm yo'q — faqat keyboard yangilash
        await callback.message.edit_reply_markup(reply_markup=kb)

    await callback.answer()


# ---------------- QUANTITY ----------------
async def update_qty_kb(callback: CallbackQuery, name: str):
    uid = callback.from_user.id
    cat = find_cat(name)
    items = db_items(cat) if cat else []
    kb = category_kb(uid, items, selected=name)
    entry = user_cat_msg.get(uid)
    if entry:
        await bot.edit_message_reply_markup(
            chat_id=callback.message.chat.id,
            message_id=entry[0],
            reply_markup=kb,
        )
    else:
        await callback.message.edit_reply_markup(reply_markup=kb)


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


# ---------------- PAYMENT CHECK (foydalanuvchi) ----------------
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


# ---------------- ADMIN: RASM QO'SHISH ----------------
@dp.message(F.text == "🖼 Rasm qo'shish")
async def img_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT name FROM products")
    names = [r[0] for r in cursor.fetchall()]
    await message.answer("Qaysi mahsulotga rasm qo'shmoqchisiz?\n\n" + "\n".join(f"• {n}" for n in names))
    await state.set_state(AdminState.img_name)


@dp.message(AdminState.img_name)
async def img_name_handler(message: Message, state: FSMContext):
    cursor.execute("SELECT name FROM products WHERE name=?", (message.text,))
    if not cursor.fetchone():
        await message.answer("❌ Bunday mahsulot yo'q. Ro'yxatdan tanlang:")
        return
    await state.update_data(img_name=message.text)
    await message.answer(f"✅ '{message.text}' tanlandi.\nEndi rasmni yuboring:")
    await state.set_state(AdminState.img_photo)


@dp.message(AdminState.img_photo, F.photo)
async def img_photo_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    name = data["img_name"]
    file_id = message.photo[-1].file_id
    cursor.execute("UPDATE products SET img=? WHERE name=?", (file_id, name))
    conn.commit()
    await message.answer(f"✅ '{name}' uchun rasm saqlandi!", reply_markup=admin_kb())
    await state.clear()


@dp.message(AdminState.img_photo)
async def img_photo_wrong(message: Message):
    await message.answer("❌ Iltimos rasm yuboring (fayl emas).")


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
        # MENU ni yangilash
        for cat in MENU:
            if name in MENU[cat]:
                MENU[cat][MENU[cat].index(name)] = message.text
                break
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
    for cat in MENU:
        if name in MENU[cat]:
            MENU[cat].remove(name)
            break
    await state.clear()
    await callback.message.edit_text(f"🗑 '{name}' o'chirildi.")
    await callback.answer()


@dp.callback_query(F.data == "delcancel")
async def del_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.answer()


# ---- MAHSULOT QO'SHISH ----
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
    cats = "\n".join(f"{i+1}. {k}" for i, k in enumerate(CATEGORY_KEYS))
    await message.answer(f"Kategoriyani tanlang:\n\n{cats}\n\nRaqamini yozing (1-{len(CATEGORY_KEYS)}):")
    await state.set_state(AdminState.add_cat)


@dp.message(AdminState.add_cat)
async def add_cat_h(message: Message, state: FSMContext):
    if not message.text.isdigit() or not (1 <= int(message.text) <= len(CATEGORY_KEYS)):
        await message.answer(f"❌ 1 dan {len(CATEGORY_KEYS)} gacha raqam yozing:")
        return
    await state.update_data(cat=CATEGORY_KEYS[int(message.text) - 1])
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏭ O'tkazib yuborish")]],
        resize_keyboard=True,
    )
    await message.answer("Mahsulot rasmini yuboring:\n(Rasm bo'lmasa — O'tkazib yuborish)", reply_markup=kb)
    await state.set_state(AdminState.add_img)


@dp.message(AdminState.add_img, F.photo)
async def add_img_photo_h(message: Message, state: FSMContext):
    data = await state.get_data()
    file_id = message.photo[-1].file_id
    cursor.execute(
        "INSERT OR IGNORE INTO products (name, price, stock, img) VALUES (?,?,?,?)",
        (data["name"], data["price"], data["stock"], file_id),
    )
    conn.commit()
    MENU[data["cat"]].append(data["name"])
    await message.answer(f"✅ {data['name']} → {data['cat']} ga qo'shildi (rasm bilan)", reply_markup=admin_kb())
    await state.clear()


@dp.message(AdminState.add_img, F.text == "⏭ O'tkazib yuborish")
async def add_img_skip_h(message: Message, state: FSMContext):
    data = await state.get_data()
    cursor.execute(
        "INSERT OR IGNORE INTO products (name, price, stock) VALUES (?,?,?)",
        (data["name"], data["price"], data["stock"]),
    )
    conn.commit()
    MENU[data["cat"]].append(data["name"])
    await message.answer(f"✅ {data['name']} → {data['cat']} ga qo'shildi (rasmsiz)", reply_markup=admin_kb())
    await state.clear()


@dp.message(AdminState.add_img)
async def add_img_wrong_h(message: Message):
    await message.answer("❌ Rasm yuboring yoki O'tkazib yuborish tugmasini bosing.")


# ---- OMBOR ----
@dp.message(F.text == "📦 Ombor")
async def show_stock(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT name, price, stock, img FROM products")
    rows = cursor.fetchall()
    lines = [f"{'✅' if img else '❌'} {n} | {p:,} so'm | {s} dona"
             for n, p, s, img in rows]
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

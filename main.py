import asyncio
import logging
import os
import sqlite3

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
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
    stock INTEGER
)
""")
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

default_products = [
    ("Osh", 30000, 10),
    ("Manti", 8000, 10),
    ("Sho'rva", 35000, 10),
    ("Tort", 15000, 10),
    ("Cola", 8000, 10),
]
for p in default_products:
    cursor.execute("INSERT OR IGNORE INTO products VALUES (?, ?, ?)", p)
conn.commit()

menu = {
    "\U0001f35b Quyuq taom": ["Osh", "Manti"],
    "\U0001f372 Suyuq taom": ["Sho'rva"],
    "\U0001f370 Shirinliklar": ["Tort"],
    "\U0001f964 Ichimliklar": ["Cola"],
}

cart = {}
user_quantity = {}


# ---------------- FSM ----------------
class AdminState(StatesGroup):
    add_name = State()
    add_price = State()
    add_stock = State()
    edit_name = State()
    edit_price = State()


# ---------------- KEYBOARDS ----------------
def main_kb(is_admin=False):
    rows = [
        [KeyboardButton(text="\U0001f35b Quyuq taom"), KeyboardButton(text="\U0001f372 Suyuq taom")],
        [KeyboardButton(text="\U0001f370 Shirinliklar"), KeyboardButton(text="\U0001f964 Ichimliklar")],
        [KeyboardButton(text="\U0001f6d2 Savatcha"), KeyboardButton(text="\U0001f4e6 Mening buyurtmalarim")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="⚙️ Admin panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Mahsulot qo'shish"), KeyboardButton(text="✏️ Narx o'zgartirish")],
        [KeyboardButton(text="\U0001f4e6 Ombor"), KeyboardButton(text="\U0001f519 Orqaga")],
    ], resize_keyboard=True)


def quantity_kb(name, qty):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➖", callback_data=f"minus|{name}"),
            InlineKeyboardButton(text=str(qty), callback_data="ignore"),
            InlineKeyboardButton(text="➕", callback_data=f"plus|{name}"),
        ],
        [InlineKeyboardButton(text="\U0001f6d2 Savatga qo'shish", callback_data=f"add|{name}")],
    ])


# ---------------- START ----------------
@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "\U0001f37d Smart oshxona botiga xush kelibsiz!",
        reply_markup=main_kb(message.from_user.id == ADMIN_ID),
    )


# ---------------- CATEGORY ----------------
@dp.message(F.text.in_(menu.keys()))
async def category(message: Message):
    for name in menu[message.text]:
        cursor.execute("SELECT price FROM products WHERE name=?", (name,))
        row = cursor.fetchone()
        if not row:
            continue
        price = row[0]
        qty = user_quantity.get((message.from_user.id, name), 1)
        await message.answer(f"{name}\n\U0001f4b0 {price} so'm", reply_markup=quantity_kb(name, qty))


# ---------------- QUANTITY ----------------
@dp.callback_query(F.data.startswith("plus"))
async def plus(callback: CallbackQuery):
    name = callback.data.split("|")[1]
    key = (callback.from_user.id, name)
    user_quantity[key] = user_quantity.get(key, 1) + 1
    await callback.message.edit_reply_markup(reply_markup=quantity_kb(name, user_quantity[key]))
    await callback.answer()


@dp.callback_query(F.data.startswith("minus"))
async def minus(callback: CallbackQuery):
    name = callback.data.split("|")[1]
    key = (callback.from_user.id, name)
    user_quantity[key] = max(1, user_quantity.get(key, 1) - 1)
    await callback.message.edit_reply_markup(reply_markup=quantity_kb(name, user_quantity[key]))
    await callback.answer()


@dp.callback_query(F.data == "ignore")
async def ignore(callback: CallbackQuery):
    await callback.answer()


# ---------------- ADD TO CART ----------------
@dp.callback_query(F.data.startswith("add"))
async def add_to_cart(callback: CallbackQuery):
    name = callback.data.split("|")[1]
    user_id = callback.from_user.id
    qty = user_quantity.get((user_id, name), 1)

    cursor.execute("SELECT price, stock FROM products WHERE name=?", (name,))
    price, stock = cursor.fetchone()

    if qty > stock:
        await callback.answer("❌ Yetarli emas")
        return

    cart.setdefault(user_id, []).append((name, price, qty))
    await callback.answer(f"{name} x{qty} qo'shildi")


# ---------------- CART ----------------
@dp.message(F.text == "\U0001f6d2 Savatcha")
async def show_cart(message: Message):
    user_id = message.from_user.id
    if not cart.get(user_id):
        return await message.answer("Savat bo'sh")

    total = 0
    text = "\U0001f6d2 Savat:\n"
    for name, price, qty in cart[user_id]:
        text += f"{name} x{qty} = {price * qty} so'm\n"
        total += price * qty
    text += f"\n\U0001f4b0 Jami: {total} so'm"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001f4b3 Buyurtma berish", callback_data="order")]
    ])
    await message.answer(text, reply_markup=kb)


# ---------------- ORDER ----------------
@dp.callback_query(F.data == "order")
async def order(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not cart.get(user_id):
        await callback.answer("Savat bo'sh")
        return

    items = str(cart[user_id])
    total = sum(p * q for _, p, q in cart[user_id])

    cursor.execute(
        "INSERT INTO orders (user_id, items, total, status) VALUES (?, ?, ?, ?)",
        (user_id, items, total, "pending"),
    )
    conn.commit()
    await callback.answer()
    await bot.send_message(user_id, "\U0001f4b3 Karta: 8600 1234 5678 0000\n\U0001f4e4 Chek yuboring")


# ---------------- CHECK (photo) ----------------
@dp.message(F.photo)
async def check(message: Message):
    user_id = message.from_user.id
    cursor.execute("SELECT id FROM orders WHERE user_id=? AND status='pending'", (user_id,))
    if not cursor.fetchone():
        await message.answer("❌ Sizda faol buyurtma yo'q.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Qabul", callback_data=f"accept|{user_id}")],
        [InlineKeyboardButton(text="\U0001f37d Tayyor", callback_data=f"ready|{user_id}")],
    ])
    await bot.send_photo(
        ADMIN_ID, message.photo[-1].file_id,
        caption=f"User: {user_id}",
        reply_markup=kb,
    )


# ---------------- ACCEPT ----------------
@dp.callback_query(F.data.startswith("accept"))
async def accept(callback: CallbackQuery):
    user_id = int(callback.data.split("|")[1])
    await callback.answer()
    await bot.send_message(user_id, "✅ To'lov qabul qilindi")


# ---------------- READY ----------------
@dp.callback_query(F.data.startswith("ready"))
async def ready(callback: CallbackQuery):
    user_id = int(callback.data.split("|")[1])

    for name, price, qty in cart.get(user_id, []):
        cursor.execute("UPDATE products SET stock = stock - ? WHERE name=?", (qty, name))
    cursor.execute("UPDATE orders SET status='done' WHERE user_id=? AND status='pending'", (user_id,))
    conn.commit()

    cart[user_id] = []
    await callback.answer()
    await bot.send_message(user_id, "\U0001f37d Buyurtmangiz tayyor!")


# ---------------- MY ORDERS ----------------
@dp.message(F.text == "\U0001f4e6 Mening buyurtmalarim")
async def my_orders(message: Message):
    cursor.execute("SELECT items, total, status FROM orders WHERE user_id=?", (message.from_user.id,))
    data = cursor.fetchall()

    if not data:
        return await message.answer("Buyurtmalar yo'q")

    text = ""
    for items, total, status in data:
        text += f"{items}\n\U0001f4b0 {total} so'm | {status}\n\n"
    await message.answer(text)


# ---------------- ADMIN PANEL ----------------
@dp.message(F.text == "⚙️ Admin panel")
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Admin panel", reply_markup=admin_kb())


# ---- EDIT PRICE FSM ----
@dp.message(F.text == "✏️ Narx o'zgartirish")
async def edit_price_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Mahsulot nomini yozing:")
    await state.set_state(AdminState.edit_name)


@dp.message(AdminState.edit_name)
async def edit_name_handler(message: Message, state: FSMContext):
    cursor.execute("SELECT name FROM products WHERE name=?", (message.text,))
    if not cursor.fetchone():
        await message.answer("❌ Bunday mahsulot yo'q. Qaytadan yozing:")
        return
    await state.update_data(name=message.text)
    await message.answer("Yangi narxni yozing:")
    await state.set_state(AdminState.edit_price)


@dp.message(AdminState.edit_price)
async def edit_price_handler(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Faqat raqam yozing:")
        return
    data = await state.get_data()
    cursor.execute("UPDATE products SET price=? WHERE name=?", (int(message.text), data["name"]))
    conn.commit()
    await message.answer(f"✅ {data['name']} narxi {message.text} so'm ga o'zgartirildi")
    await state.clear()


# ---- ADD PRODUCT FSM ----
@dp.message(F.text == "➕ Mahsulot qo'shish")
async def add_product(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Nomini yozing:")
    await state.set_state(AdminState.add_name)


@dp.message(AdminState.add_name)
async def add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Narx (so'm):")
    await state.set_state(AdminState.add_price)


@dp.message(AdminState.add_price)
async def add_price(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Faqat raqam yozing:")
        return
    await state.update_data(price=int(message.text))
    await message.answer("Soni (dona):")
    await state.set_state(AdminState.add_stock)


@dp.message(AdminState.add_stock)
async def add_stock(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Faqat raqam yozing:")
        return
    data = await state.get_data()
    cursor.execute(
        "INSERT OR IGNORE INTO products VALUES (?, ?, ?)",
        (data["name"], data["price"], int(message.text)),
    )
    conn.commit()
    menu["\U0001f35b Quyuq taom"].append(data["name"])
    await message.answer(f"✅ {data['name']} qo'shildi")
    await state.clear()


# ---- STOCK ----
@dp.message(F.text == "\U0001f4e6 Ombor")
async def stock(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT * FROM products")
    data = cursor.fetchall()
    text = "\n".join(f"{n} | {p} so'm | {s} dona" for n, p, s in data)
    await message.answer(text or "Bo'sh")


# ---- BACK ----
@dp.message(F.text == "\U0001f519 Orqaga")
async def back(message: Message):
    await start(message)


# ---------------- RUN ----------------
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

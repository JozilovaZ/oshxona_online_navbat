import sqlite3
from aiogram import types


async def show_menu(message: types.Message, menu: dict):
    category = message.text
    if category not in menu:
        return

    conn = sqlite3.connect('oshxona.db')
    cursor = conn.cursor()

    for name in menu[category]:
        cursor.execute("SELECT price FROM products WHERE name=?", (name,))
        row = cursor.fetchone()
        if not row:
            continue
        price = row[0]

        kb = types.InlineKeyboardMarkup(row_width=3)
        kb.add(
            types.InlineKeyboardButton("➖", callback_data=f"minus|{name}"),
            types.InlineKeyboardButton("1", callback_data="ignore"),
            types.InlineKeyboardButton("➕", callback_data=f"plus|{name}")
        )
        kb.add(types.InlineKeyboardButton("🛒 Savatga qo'shish", callback_data=f"add|{name}"))
        await message.answer(f"{name}\n💰 {price} so'm", reply_markup=kb)

    conn.close()

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def get_main_kb(is_admin=False):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🍛 Quyuq taom"), KeyboardButton("🍲 Suyuq taom"))
    kb.add(KeyboardButton("🍰 Shirinliklar"), KeyboardButton("🥤 Ichimliklar"))
    kb.add(KeyboardButton("🛒 Savatcha"), KeyboardButton("📦 Mening buyurtmalarim"))
    if is_admin:
        kb.add(KeyboardButton("⚙️ Admin panel"))
    return kb


def get_admin_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Mahsulot qo'shish"), KeyboardButton("✏️ Narx o'zgartirish"))
    kb.add(KeyboardButton("📦 Ombor"), KeyboardButton("🔙 Orqaga"))
    return kb

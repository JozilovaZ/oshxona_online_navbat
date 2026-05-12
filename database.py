import sqlite3


def init_db():
    conn = sqlite3.connect('oshxona.db')
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

    default_products = [
        ("Osh", 30000, 10),
        ("Manti", 8000, 10),
        ("Sho'rva", 35000, 10),
        ("Tort", 15000, 10),
        ("Cola", 8000, 10),
    ]
    cursor.executemany("INSERT OR IGNORE INTO products VALUES (?, ?, ?)", default_products)

    conn.commit()
    conn.close()

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = set()

for value in os.getenv("ADMIN_IDS", "").split(","):
    value = value.strip()

    if value.isdigit():
        ADMIN_IDS.add(int(value))


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN belum diisi di file .env"
    )


# ============================================================
# DATABASE
# ============================================================

DB_PATH = Path("bot.db")


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_database():
    connection = db()
    cursor = connection.cursor()

    # USER
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            name TEXT,
            username TEXT,
            balance INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    # SUBSCRIPTION
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            package_code TEXT,
            package_name TEXT,
            price INTEGER DEFAULT 0,
            start_date TEXT,
            expiry_date TEXT,
            status TEXT DEFAULT 'inactive'
        )
    """)

    # TRANSACTION
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            amount INTEGER DEFAULT 0,
            payment_method TEXT,
            package_code TEXT,
            package_name TEXT,
            proof_file_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            processed_at TEXT
        )
    """)

    # FORMAT SETTING
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS format_settings (
            telegram_id INTEGER PRIMARY KEY,
            template TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    # FORMAT RESULTS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS format_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            input_text TEXT,
            result_text TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # FORMAT HISTORY
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS format_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            input_text TEXT,
            result_text TEXT,
            created_at TEXT NOT NULL,
            deleted_at TEXT NOT NULL
        )
    """)

    # JMO SOLUTIONS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jmo_solutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            keywords TEXT,
            error_code TEXT,
            problem TEXT,
            solution TEXT,
            source TEXT,
            created_at TEXT NOT NULL
        )
    """)

    connection.commit()
    connection.close()


def register_user(user):
    connection = db()

    connection.execute("""
        INSERT INTO users (
            telegram_id,
            name,
            username,
            balance,
            created_at
        )
        VALUES (?, ?, ?, 0, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            name = excluded.name,
            username = excluded.username
    """, (
        user.id,
        user.full_name,
        user.username,
        datetime.now().isoformat(),
    ))

    connection.commit()
    connection.close()


def get_user(telegram_id):
    connection = db()

    result = connection.execute("""
        SELECT *
        FROM users
        WHERE telegram_id = ?
    """, (telegram_id,)).fetchone()

    connection.close()

    return result


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 PROFIL",
                    callback_data="profile"
                ),
                InlineKeyboardButton(
                    text="💳 TOP UP",
                    callback_data="topup"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 STATUS",
                    callback_data="status"
                ),
                InlineKeyboardButton(
                    text="🛠️ SOLUSI JMO",
                    callback_data="solusi_jmo"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📝 AUTO FORMAT",
                    callback_data="auto_format"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📞 HUBUNGI ADMIN",
                    callback_data="contact_admin"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔐 PANEL ADMIN",
                    callback_data="admin_panel"
                ),
            ],
        ]
    )


def back_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ KEMBALI",
                    callback_data="back_main"
                )
            ]
        ]
    )


# ============================================================
# BOT
# ============================================================

dp = Dispatcher()


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):

    register_user(message.from_user)

    text = (
        "🤖 <b>JMO LINTAS TERKINI</b>\n\n"
        f"👋 Selamat datang, "
        f"<b>{message.from_user.full_name}</b>!\n\n"
        "Silakan pilih menu di bawah:"
    )

    await message.answer(
        text,
        reply_markup=main_menu()
    )


# ============================================================
# BACK TO MAIN MENU
# ============================================================

@dp.callback_query(F.data == "back_main")
async def back_main_handler(callback: CallbackQuery):

    await callback.message.edit_text(
        "🤖 <b>JMO LINTAS TERKINI</b>\n\n"
        "Silakan pilih menu:",
        reply_markup=main_menu()
    )

    await callback.answer()


# ============================================================
# PROFIL
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile_handler(callback: CallbackQuery):

    user = get_user(callback.from_user.id)

    if not user:
        register_user(callback.from_user)
        user = get_user(callback.from_user.id)

    username = (
        f"@{user['username']}"
        if user["username"]
        else "-"
    )

    balance = user["balance"]

    text = (
        "👤 <b>PROFIL USER</b>\n\n"
        f"🆔 Telegram ID : <code>{user['telegram_id']}</code>\n"
        f"👤 Nama : {user['name']}\n"
        f"📱 Username : {username}\n"
        f"💰 Saldo : <b>Rp {balance:,}</b>\n\n"
        "📦 Paket : Belum ada\n"
        "📅 Masa aktif : -\n"
        "📊 Status : Tidak aktif"
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# TOP UP
# ============================================================

@dp.callback_query(F.data == "topup")
async def topup_handler(callback: CallbackQuery):

    text = (
        "💳 <b>TOP UP</b>\n\n"
        "Silakan lakukan pembayaran melalui:\n\n"
        "🏦 <b>SEABANK</b>\n"
        "901040978290\n"
        "A/N HAMBALI\n\n"
        "💰 <b>DANA</b>\n"
        "083824101264\n"
        "A/N HAMBALI\n\n"
        "Setelah melakukan pembayaran, "
        "silakan upload bukti pembayaran."
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# STATUS
# ============================================================

@dp.callback_query(F.data == "status")
async def status_handler(callback: CallbackQuery):

    text = (
        "📊 <b>STATUS USER</b>\n\n"
        "📦 Paket : Belum berlangganan\n"
        "🟡 Status : BELUM AKTIF\n\n"
        "Silakan TOP UP untuk mendapatkan "
        "akses layanan."
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# SOLUSI JMO
# ============================================================

@dp.callback_query(F.data == "solusi_jmo")
async def solusi_jmo_handler(callback: CallbackQuery):

    text = (
        "🛠️ <b>SOLUSI JMO</b>\n\n"
        "Silakan ketik masalah JMO yang kamu alami "
        "atau upload foto/screenshot masalahnya.\n\n"
        "Contoh:\n"
        "• Cara atasi 025\n"
        "• JMO tidak bisa login\n"
        "• Gagal verifikasi wajah\n"
        "• Saldo JHT tidak muncul\n"
        "• KPJ tidak ditemukan\n"
        "• Kode error JMO\n\n"
        "📸 Kamu juga bisa upload screenshot."
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# AUTO FORMAT
# ============================================================

@dp.callback_query(F.data == "auto_format")
async def auto_format_handler(callback: CallbackQuery):

    text = (
        "🔒 <b>AUTO FORMAT TERKUNCI</b>\n\n"
        "Untuk menggunakan AUTO FORMAT, "
        "silakan membeli paket akses.\n\n"
        "💰 <b>PILIH PAKET</b>\n\n"
        "🟢 6 BULAN — Rp50.000\n"
        "🔵 1 TAHUN — Rp80.000\n"
        "🟣 UNLIMITED — Rp200.000\n\n"
        "Setelah pembayaran dikonfirmasi Admin, "
        "AUTO FORMAT akan otomatis terbuka."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟢 6 BULAN - Rp50K",
                    callback_data="package_6m"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔵 1 TAHUN - Rp80K",
                    callback_data="package_1y"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟣 UNLIMITED - Rp200K",
                    callback_data="package_unlimited"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ KEMBALI",
                    callback_data="back_main"
                )
            ],
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# CONTACT ADMIN
# ============================================================

@dp.callback_query(F.data == "contact_admin")
async def contact_admin_handler(callback: CallbackQuery):

    text = (
        "📞 <b>HUBUNGI ADMIN</b>\n\n"
        "Jika membutuhkan bantuan, silakan "
        "hubungi Admin:\n\n"
        "👤 <b>Telegram</b>\n"
        "@Hambali1995\n\n"
        "📱 <b>WhatsApp</b>\n"
        "083160776091"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 CHAT TELEGRAM",
                    url="https://t.me/Hambali1995"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 CHAT WHATSAPP",
                    url="https://wa.me/6283160776091"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ KEMBALI",
                    callback_data="back_main"
                )
            ],
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# ADMIN PANEL
# ============================================================

@dp.callback_query(F.data == "admin_panel")
async def admin_panel_handler(callback: CallbackQuery):

    if callback.from_user.id not in ADMIN_IDS:

        await callback.answer(
            "❌ Kamu bukan Admin.",
            show_alert=True
        )

        return

    text = (
        "🔐 <b>PANEL ADMIN</b>\n\n"
        "Silakan pilih menu Admin:"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👥 CEK USER AKTIF",
                    callback_data="admin_active"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 TRANSAKSI PENDING",
                    callback_data="admin_pending"
                )
            ],
            [
                InlineKeyboardButton(
                    text="➕ TAMBAH SALDO",
                    callback_data="admin_add_balance"
                ),
                InlineKeyboardButton(
                    text="➖ KURANGI SALDO",
                    callback_data="admin_sub_balance"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑️ HAPUS USER",
                    callback_data="admin_delete_user"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 BROADCAST",
                    callback_data="admin_broadcast"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ KEMBALI",
                    callback_data="back_main"
                )
            ],
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# PACKAGE BUTTONS - SEMENTARA
# ============================================================

@dp.callback_query(
    F.data.in_({
        "package_6m",
        "package_1y",
        "package_unlimited"
    })
)
async def package_handler(callback: CallbackQuery):

    packages = {
        "package_6m": (
            "6 BULAN",
            50000
        ),
        "package_1y": (
            "1 TAHUN",
            80000
        ),
        "package_unlimited": (
            "UNLIMITED",
            200000
        ),
    }

    package_name, price = packages[callback.data]

    text = (
        "💳 <b>PEMBELIAN AUTO FORMAT</b>\n\n"
        f"📦 Paket : <b>{package_name}</b>\n"
        f"💰 Harga : <b>Rp {price:,}</b>\n\n"
        "Silakan lakukan pembayaran melalui:\n\n"
        "🏦 SEABANK\n"
        "901040978290\n"
        "A/N HAMBALI\n\n"
        "💰 DANA\n"
        "083824101264\n"
        "A/N HAMBALI\n\n"
        "Setelah pembayaran, kirim foto "
        "bukti pembayaran kepada bot."
    )

    await callback.message.edit_text(
        text,
        reply_markup=back_menu()
    )

    await callback.answer()


# ============================================================
# MAIN
# ============================================================

async def main():

    init_database()

    logging.info(
        "🤖 JMO LINTAS TERKINI sedang berjalan..."
    )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    try:
        await dp.start_polling(bot)

    finally:
        await bot.session.close()


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO
    )

    asyncio.run(main())

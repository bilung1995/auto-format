import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = set()
for value in os.getenv("ADMIN_IDS", "").split(","):
    value = value.strip()
    if value.isdigit():
        ADMIN_IDS.add(int(value))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN belum diisi di Railway Variables")


DB_PATH = Path("bot.db")


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_database():
    connection = db()
    cursor = connection.cursor()

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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS format_settings (
            telegram_id INTEGER PRIMARY KEY,
            template TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS format_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            input_text TEXT,
            result_text TEXT,
            created_at TEXT NOT NULL
        )
    """)

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
            telegram_id, name, username, balance, created_at
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
        SELECT * FROM users WHERE telegram_id = ?
    """, (telegram_id,)).fetchone()
    connection.close()
    return result


def rupiah(amount: int) -> str:
    return f"Rp {amount:,}".replace(",", ".")


def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 PROFIL", callback_data="profile"),
                InlineKeyboardButton(text="💳 TOP UP", callback_data="topup"),
            ],
            [
                InlineKeyboardButton(text="📊 STATUS", callback_data="status"),
                InlineKeyboardButton(text="🛠️ SOLUSI JMO", callback_data="solusi_jmo"),
            ],
            [
                InlineKeyboardButton(text="📝 AUTO FORMAT", callback_data="auto_format"),
            ],
            [
                InlineKeyboardButton(text="📞 HUBUNGI ADMIN", callback_data="contact_admin"),
            ],
            [
                InlineKeyboardButton(text="🔐 PANEL ADMIN", callback_data="admin_panel"),
            ],
        ]
    )


def back_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")]
        ]
    )


class PaymentState(StatesGroup):
    waiting_proof = State()


dp = Dispatcher()


@dp.message(CommandStart())
async def start_handler(message: Message):
    register_user(message.from_user)

    await message.answer(
        "🤖 <b>JMO LINTAS TERKINI</b>\n\n"
        f"👋 Selamat datang, <b>{message.from_user.full_name}</b>!\n\n"
        "Silakan pilih menu di bawah:",
        reply_markup=main_menu()
    )


@dp.callback_query(F.data == "back_main")
async def back_main_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🤖 <b>JMO LINTAS TERKINI</b>\n\nSilakan pilih menu:",
        reply_markup=main_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "profile")
async def profile_handler(callback: CallbackQuery):
    register_user(callback.from_user)
    user = get_user(callback.from_user.id)

    username = f"@{user['username']}" if user["username"] else "-"

    await callback.message.edit_text(
        "👤 <b>PROFIL USER</b>\n\n"
        f"🆔 Telegram ID : <code>{user['telegram_id']}</code>\n"
        f"👤 Nama : {user['name']}\n"
        f"📱 Username : {username}\n"
        f"💰 Saldo : <b>{rupiah(user['balance'])}</b>\n\n"
        "📦 Paket : Belum ada\n"
        "📅 Masa aktif : -\n"
        "📊 Status : Tidak aktif",
        reply_markup=back_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "topup")
async def topup_handler(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟢 6 BULAN — Rp50.000", callback_data="package_6m")],
            [InlineKeyboardButton(text="🔵 1 TAHUN — Rp80.000", callback_data="package_1y")],
            [InlineKeyboardButton(text="🟣 UNLIMITED — Rp200.000", callback_data="package_unlimited")],
            [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")],
        ]
    )

    await callback.message.edit_text(
        "💳 <b>TOP UP</b>\n\nSilakan pilih paket yang ingin dibeli:",
        reply_markup=keyboard
    )
    await callback.answer()


PACKAGES = {
    "package_6m": {"code": "6M", "name": "AUTO FORMAT 6 BULAN", "price": 50000},
    "package_1y": {"code": "1Y", "name": "AUTO FORMAT 1 TAHUN", "price": 80000},
    "package_unlimited": {"code": "UNLIMITED", "name": "AUTO FORMAT UNLIMITED", "price": 200000},
}


@dp.callback_query(F.data.in_(set(PACKAGES.keys())))
async def package_handler(callback: CallbackQuery, state: FSMContext):
    package = PACKAGES[callback.data]

    await state.update_data(
        package_code=package["code"],
        package_name=package["name"],
        amount=package["price"]
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ SUDAH BAYAR", callback_data="payment_done")],
            [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="topup")],
        ]
    )

    await callback.message.edit_text(
        "💳 <b>PEMBAYARAN AUTO FORMAT</b>\n\n"
        f"📦 Paket : <b>{package['name']}</b>\n"
        f"💰 Harga : <b>{rupiah(package['price'])}</b>\n\n"
        "Silakan transfer ke:\n\n"
        "🏦 <b>SEABANK</b>\n"
        "901040978290\n"
        "A/N HAMBALI\n\n"
        "💰 <b>DANA</b>\n"
        "083824101264\n"
        "A/N HAMBALI\n\n"
        "Setelah membayar, tekan <b>SUDAH BAYAR</b>.",
        reply_markup=keyboard
    )
    await callback.answer()


@dp.callback_query(F.data == "payment_done")
async def payment_done_handler(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    if not data.get("package_code"):
        await callback.answer("❌ Silakan pilih paket terlebih dahulu.", show_alert=True)
        return

    await state.set_state(PaymentState.waiting_proof)

    await callback.message.edit_text(
        "📸 <b>UPLOAD BUKTI PEMBAYARAN</b>\n\n"
        f"📦 Paket : {data['package_name']}\n"
        f"💰 Nominal : {rupiah(data['amount'])}\n\n"
        "Silakan kirim <b>FOTO</b> bukti transfer di chat ini."
    )
    await callback.answer()


@dp.message(PaymentState.waiting_proof, F.photo)
async def payment_proof_handler(message: Message, state: FSMContext):
    data = await state.get_data()

    if not data.get("package_code"):
        await state.clear()
        await message.answer("❌ Data pembayaran tidak ditemukan.")
        return

    photo = message.photo[-1]

    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO transactions (
            telegram_id, amount, payment_method, package_code,
            package_name, proof_file_id, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
    """, (
        message.from_user.id,
        data["amount"],
        "SEABANK/DANA",
        data["package_code"],
        data["package_name"],
        photo.file_id,
        datetime.now().isoformat()
    ))

    transaction_id = cursor.lastrowid
    connection.commit()
    connection.close()

    await state.clear()

    await message.answer(
        "✅ <b>BUKTI PEMBAYARAN DITERIMA</b>\n\n"
        f"📦 Paket : {data['package_name']}\n"
        f"💰 Nominal : {rupiah(data['amount'])}\n"
        f"🧾 Transaksi : #{transaction_id}\n\n"
        "⏳ Menunggu konfirmasi Admin."
    )

    admin_text = (
        "💰 <b>PEMBAYARAN BARU</b>\n\n"
        f"🧾 Transaksi : #{transaction_id}\n"
        f"👤 Nama : {message.from_user.full_name}\n"
        f"🆔 Telegram ID : <code>{message.from_user.id}</code>\n"
        f"📱 Username : @{message.from_user.username or '-'}\n\n"
        f"📦 Paket : {data['package_name']}\n"
        f"💰 Nominal : {rupiah(data['amount'])}\n"
        "💳 Metode : SEABANK/DANA\n"
        "🟡 Status : PENDING"
    )

    admin_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ SETUJU", callback_data=f"approve_{transaction_id}"),
                InlineKeyboardButton(text="❌ TOLAK", callback_data=f"reject_{transaction_id}")
            ]
        ]
    )

    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_photo(
                chat_id=admin_id,
                photo=photo.file_id,
                caption=admin_text,
                reply_markup=admin_keyboard
            )
        except Exception as error:
            logging.error(f"Gagal kirim transaksi ke admin {admin_id}: {error}")


@dp.message(PaymentState.waiting_proof)
async def payment_wrong_input(message: Message):
    await message.answer("📸 Silakan kirim <b>FOTO bukti pembayaran</b>.")


@dp.callback_query(F.data.startswith("approve_"))
async def approve_payment_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Kamu bukan Admin.", show_alert=True)
        return

    transaction_id = int(callback.data.split("_")[1])

    connection = db()
    transaction = connection.execute(
        "SELECT * FROM transactions WHERE id = ?",
        (transaction_id,)
    ).fetchone()

    if not transaction:
        connection.close()
        await callback.answer("❌ Transaksi tidak ditemukan.", show_alert=True)
        return

    if transaction["status"] != "pending":
        connection.close()
        await callback.answer("⚠️ Transaksi sudah diproses.", show_alert=True)
        return

    connection.execute("""
        UPDATE transactions
        SET status = 'approved', processed_at = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), transaction_id))
    connection.commit()
    connection.close()

    package_code = transaction["package_code"]

    if package_code == "6M":
        now = datetime.now()
        month = now.month + 6
        year = now.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        try:
            expiry = now.replace(year=year, month=month)
        except ValueError:
            expiry = now.replace(year=year, month=month, day=28)
        status = "active"

    elif package_code == "1Y":
        try:
            expiry = datetime.now().replace(year=datetime.now().year + 1)
        except ValueError:
            expiry = datetime.now().replace(year=datetime.now().year + 1, day=28)
        status = "active"

    else:
        expiry = None
        status = "unlimited"

    connection = db()
    connection.execute("""
        INSERT INTO subscriptions (
            telegram_id, package_code, package_name, price,
            start_date, expiry_date, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id)
        DO UPDATE SET
            package_code = excluded.package_code,
            package_name = excluded.package_name,
            price = excluded.price,
            start_date = excluded.start_date,
            expiry_date = excluded.expiry_date,
            status = excluded.status
    """, (
        transaction["telegram_id"],
        package_code,
        transaction["package_name"],
        transaction["amount"],
        datetime.now().isoformat(),
        expiry.isoformat() if expiry else None,
        status
    ))
    connection.commit()
    connection.close()

    expiry_text = expiry.strftime("%d-%m-%Y") if expiry else "SELAMANYA"

    try:
        await callback.bot.send_message(
            chat_id=transaction["telegram_id"],
            text=(
                "✅ <b>PEMBAYARAN DISETUJUI</b>\n\n"
                f"📦 Paket : {transaction['package_name']}\n"
                f"💰 Nominal : {rupiah(transaction['amount'])}\n"
                f"📅 Berlaku sampai : {expiry_text}\n\n"
                "🔓 <b>AUTO FORMAT SEKARANG SUDAH TERBUKA.</b>"
            )
        )
    except Exception as error:
        logging.error(f"Gagal kirim notifikasi user: {error}")

    await callback.message.edit_caption(
        caption=(callback.message.caption or "") + "\n\n✅ <b>PEMBAYARAN DISETUJUI</b>",
        reply_markup=None
    )
    await callback.answer("✅ Pembayaran disetujui.")


@dp.callback_query(F.data.startswith("reject_"))
async def reject_payment_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Kamu bukan Admin.", show_alert=True)
        return

    transaction_id = int(callback.data.split("_")[1])

    connection = db()
    transaction = connection.execute(
        "SELECT * FROM transactions WHERE id = ?",
        (transaction_id,)
    ).fetchone()

    if not transaction:
        connection.close()
        await callback.answer("❌ Transaksi tidak ditemukan.", show_alert=True)
        return

    if transaction["status"] != "pending":
        connection.close()
        await callback.answer("⚠️ Transaksi sudah diproses.", show_alert=True)
        return

    connection.execute("""
        UPDATE transactions
        SET status = 'rejected', processed_at = ?
        WHERE id = ?
    """, (datetime.now().isoformat(), transaction_id))
    connection.commit()
    connection.close()

    try:
        await callback.bot.send_message(
            chat_id=transaction["telegram_id"],
            text=(
                "❌ <b>PEMBAYARAN DITOLAK</b>\n\n"
                f"📦 Paket : {transaction['package_name']}\n"
                f"💰 Nominal : {rupiah(transaction['amount'])}\n\n"
                "Silakan periksa kembali bukti pembayaran "
                "atau hubungi Admin."
            )
        )
    except Exception as error:
        logging.error(f"Gagal mengirim penolakan: {error}")

    await callback.message.edit_caption(
        caption=(callback.message.caption or "") + "\n\n❌ <b>PEMBAYARAN DITOLAK</b>",
        reply_markup=None
    )
    await callback.answer("❌ Pembayaran ditolak.")


@dp.callback_query(F.data == "status")
async def status_handler(callback: CallbackQuery):
    connection = db()

    subscription = connection.execute(
        "SELECT * FROM subscriptions WHERE telegram_id = ?",
        (callback.from_user.id,)
    ).fetchone()

    user = connection.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (callback.from_user.id,)
    ).fetchone()

    connection.close()

    balance = user["balance"] if user else 0

    if not subscription:
        text = (
            "📊 <b>STATUS AKUN</b>\n\n"
            f"💰 Saldo : {rupiah(balance)}\n\n"
            "📦 Paket : Belum berlangganan\n"
            "🟡 Status : BELUM AKTIF\n\n"
            "Silakan TOP UP untuk mengaktifkan AUTO FORMAT."
        )
    else:
        if subscription["status"] == "unlimited":
            expiry_text = "SELAMANYA"
            status_text = "🟢 AKTIF"
        elif subscription["expiry_date"]:
            expiry = datetime.fromisoformat(subscription["expiry_date"])
            status_text = "🟢 AKTIF" if datetime.now() < expiry else "🔴 EXPIRED"
            expiry_text = expiry.strftime("%d-%m-%Y")
        else:
            expiry_text = "-"
            status_text = "🟡"

        text = (
            "📊 <b>STATUS AKUN</b>\n\n"
            f"💰 Saldo : {rupiah(balance)}\n\n"
            f"📦 Paket : {subscription['package_name']}\n"
            f"💵 Harga : {rupiah(subscription['price'])}\n"
            f"📅 Berakhir : {expiry_text}\n"
            f"📊 Status : {status_text}"
        )

    await callback.message.edit_text(text, reply_markup=back_menu())
    await callback.answer()


@dp.callback_query(F.data == "solusi_jmo")
async def solusi_jmo_handler(callback: CallbackQuery):
    await callback.message.edit_text(
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
        "📸 Kamu juga bisa upload screenshot.",
        reply_markup=back_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "auto_format")
async def auto_format_handler(callback: CallbackQuery):
    connection = db()
    subscription = connection.execute(
        "SELECT * FROM subscriptions WHERE telegram_id = ?",
        (callback.from_user.id,)
    ).fetchone()
    connection.close()

    active = False

    if subscription:
        if subscription["status"] == "unlimited":
            active = True
        elif subscription["status"] == "active" and subscription["expiry_date"]:
            active = datetime.now() < datetime.fromisoformat(subscription["expiry_date"])

    if not active:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🟢 6 BULAN - Rp50K", callback_data="package_6m")],
                [InlineKeyboardButton(text="🔵 1 TAHUN - Rp80K", callback_data="package_1y")],
                [InlineKeyboardButton(text="🟣 UNLIMITED - Rp200K", callback_data="package_unlimited")],
                [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")],
            ]
        )
        text = (
            "🔒 <b>AUTO FORMAT TERKUNCI</b>\n\n"
            "Untuk menggunakan AUTO FORMAT, silakan membeli paket akses.\n\n"
            "🟢 6 BULAN — Rp50.000\n"
            "🔵 1 TAHUN — Rp80.000\n"
            "🟣 UNLIMITED — Rp200.000"
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📝 BUAT FORMAT", callback_data="format_create")],
                [InlineKeyboardButton(text="📄 HASIL FORMAT", callback_data="format_results")],
                [InlineKeyboardButton(text="⚙️ SETTING FORMAT", callback_data="format_setting")],
                [InlineKeyboardButton(text="🕘 HISTORY", callback_data="format_history")],
                [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")],
            ]
        )
        text = "📝 <b>AUTO FORMAT</b>\n\n🔓 Akses kamu aktif.\n\nSilakan pilih menu:"

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "contact_admin")
async def contact_admin_handler(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 CHAT TELEGRAM", url="https://t.me/Hambali1995")],
            [InlineKeyboardButton(text="📱 CHAT WHATSAPP", url="https://wa.me/6283160776091")],
            [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")],
        ]
    )

    await callback.message.edit_text(
        "📞 <b>HUBUNGI ADMIN</b>\n\n"
        "Jika membutuhkan bantuan, silakan hubungi Admin:\n\n"
        "👤 <b>Telegram</b>\n"
        "@Hambali1995\n\n"
        "📱 <b>WhatsApp</b>\n"
        "083160776091",
        reply_markup=keyboard
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_handler(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Kamu bukan Admin.", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 CEK USER AKTIF", callback_data="admin_active")],
            [InlineKeyboardButton(text="💰 TRANSAKSI PENDING", callback_data="admin_pending")],
            [
                InlineKeyboardButton(text="➕ TAMBAH SALDO", callback_data="admin_add_balance"),
                InlineKeyboardButton(text="➖ KURANGI SALDO", callback_data="admin_sub_balance"),
            ],
            [InlineKeyboardButton(text="🗑️ HAPUS USER", callback_data="admin_delete_user")],
            [InlineKeyboardButton(text="📢 BROADCAST", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")],
        ]
    )

    await callback.message.edit_text(
        "🔐 <b>PANEL ADMIN</b>\n\nSilakan pilih menu Admin:",
        reply_markup=keyboard
    )
    await callback.answer()


async def main():
    init_database()

    logging.info("🤖 JMO LINTAS TERKINI sedang berjalan...")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

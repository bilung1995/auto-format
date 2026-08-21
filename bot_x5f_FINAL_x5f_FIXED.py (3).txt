import asyncio
import logging
import html
import os
import sqlite3
import re
import aiohttp
from aiohttp import web
from datetime import datetime
from pathlib import Path
from calendar import monthrange

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN belum diisi di Railway Variables")

# === BOT BARU KHUSUS FONNTE - SATSET DI BAWAH 100RB - TANPA GREEN API / WABLAS ===
# Bot lama pakai GREEN API - biarin jalan di bot lama
# Bot baru ini 100% FONNTE 35RB/BULAN - SATSET 0.5 DETIK!
# Daftar: https://fonnte.com

WA_PROVIDER = "fonnte"
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN", "").strip()  # token dari fonnte.com - WAJIB ISI
FONNTE_DEVICE = os.getenv("FONNTE_DEVICE", "").strip()  # optional

# Legacy vars (biar gak error kalau masih ada di Railway, tapi gak dipakai)
WABLAS_TOKEN = os.getenv("WABLAS_TOKEN", "").strip()
WABLAS_DOMAIN = os.getenv("WABLAS_DOMAIN", "").strip()
WABLAS_DEVICE = os.getenv("WABLAS_DEVICE", "").strip()
STARSENDER_TOKEN = os.getenv("STARSENDER_TOKEN", "").strip()
STARSENDER_DEVICE = os.getenv("STARSENDER_DEVICE", "").strip()

async def send_wa_notif_kota(phone_number: str, kota_nama: str, user_name: str = ""):
    """Kirim notif WA via FONNTE 35RB - SATSET 0.5 DETIK - KHUSUS BOT BARU"""
    msg = f"""✅ *KOTA DIPILIH*

Halo {user_name},
Kamu memilih kota: *{kota_nama}*

Bot akan filter data hanya untuk kota {kota_nama} saja.

📍 SAHABAT JHT BOT BARU V2 - FONNTE SATSET"""

    if not FONNTE_TOKEN:
        print("⚠️ FONNTE_TOKEN belum diset di Railway Variables")
        return False

    # Normalisasi nomor 08xxx -> 628xxx
    phone = phone_number.strip()
    phone = re.sub(r'[^0-9]', '', phone)
    if phone.startswith('0'):
        phone = '62' + phone[1:]
    elif phone.startswith('8'):
        phone = '62' + phone
    if phone.startswith('6262'):
        phone = phone[2:]

    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.fonnte.com/send"
            headers = {"Authorization": FONNTE_TOKEN}
            data = {
                "target": phone,
                "message": msg,
                "countryCode": "62",
                "delay": "0"  # 0 detik biar satset, gak pakai antrian
            }
            if FONNTE_DEVICE:
                data["device"] = FONNTE_DEVICE
            
            async with session.post(url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                txt = await resp.text()
                print(f"FONNTE {resp.status}: {txt[:400]}")
                # Fonnte sukses kalau status 200 dan ada "status":true atau "sent"
                if resp.status == 200:
                    if '"status":true' in txt or '"status": true' in txt or 'success' in txt.lower() or 'sent' in txt.lower() or 'detail' not in txt.lower():
                        print(f"✅ FONNTE SATSET ke {phone} - kota {kota_nama}")
                        return True
                    # Kadang Fonnte return reason tapi tetap terkirim
                    if 'whatsapp' in txt.lower() or 'device' in txt.lower():
                        print(f"⚠️ FONNTE warning tapi mungkin terkirim: {txt[:300]}")
                        return True
                else:
                    print(f"❌ FONNTE gagal {resp.status}: {txt[:500]}")
                    return False
    except Exception as e:
        print(f"FONNTE error: {e}")
        return False

    print("⚠️ FONNTE gagal - cek token")
    return False


FOOTER_PERINGATAN = """--------------------------
PERHATIAN :
Tetap hati-hati dan waspada dalam bertransaksi untuk lebih aman gunakan jasa rekber. Terimakasih
Sumber: https://t.me/seduluranjht_bot"""

# ========== FILTER 2 KUNCI WAJIB: KOTA + KECAMATAN - FORMAT ZOLDYCK - NO ADMIN NOTIF + FOOTER ==========
def parse_kota_kec_from_text(text: str):
    if not text:
        return None, None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    kab = None
    kec = None
    for i, line in enumerate(lines):
        if re.match(r'^\d{4}$', line):
            if i+1 < len(lines):
                maybe_kab = lines[i+1].strip().upper()
                if maybe_kab and ':' not in maybe_kab and 'RP' not in maybe_kab and len(maybe_kab) < 30:
                    kab = maybe_kab
            if i+2 < len(lines):
                maybe_kec = lines[i+2].strip().upper()
                if maybe_kec and ':' not in maybe_kec and 'RP' not in maybe_kec and len(maybe_kec) < 40:
                    kec = maybe_kec
            break
    if not kab or not kec:
        low = text.lower()
        try:
            conn = db()
            rows = conn.execute("SELECT DISTINCT kab, kec FROM user_kota").fetchall()
            conn.close()
            for r in rows:
                db_kab = (r['kab'] or '').strip()
                db_kec = (r['kec'] or '').strip()
                if not db_kab or not db_kec:
                    continue
                if db_kab.lower() in low and db_kec.lower() in low:
                    return db_kab, db_kec
        except:
            pass
        if kab and kec:
            return kab, kec
        return None, None
    return kab, kec

def find_users_by_kota_kec(kab: str, kec: str):
    if not kab or not kec:
        return []
    try:
        conn = db()
        rows = conn.execute("SELECT DISTINCT telegram_id FROM user_kota WHERE UPPER(kab)=UPPER(?) AND UPPER(kec)=UPPER(?)", (kab.strip(), kec.strip())).fetchall()
        if not rows:
            rows = conn.execute("SELECT DISTINCT telegram_id FROM user_kota WHERE kab LIKE ? AND kec LIKE ?", (f"%{kab}%", f"%{kec}%")).fetchall()
        conn.close()
        return [r['telegram_id'] for r in rows]
    except:
        return []

async def forward_wa_to_telegram(bot, wa_text: str, wa_group: str, wa_sender: str, parsed_kab: str, parsed_kec: str):
    user_ids = find_users_by_kota_kec(parsed_kab, parsed_kec)
    if not user_ids:
        print(f"⏭️ No match {parsed_kab}+{parsed_kec}")
        return 0
    count = 0
    # Format FINAL sesuai request: INFO MASUK KEYWORD + isi + footer rekber
    clean_text = wa_text.strip()
    # Hilangkan header lama jika ada
    clean_text = re.sub(r'^(📩|📢).*?(MASUK|KEYWORD).*?\n+', '', clean_text, flags=re.IGNORECASE|re.MULTILINE).strip()
    # Pastikan footer ada
    if "PERHATIAN" not in clean_text:
        clean_text = f"{clean_text}\n\n{FOOTER_PERINGATAN}"
    # Format final persis permintaan user
    final_text = f"📢 INFO MASUK KEYWORD \n\n{clean_text}"
    final_text = final_text.replace("https//", "https://")

    for tid in user_ids:
        try:
            await bot.send_message(tid, final_text[:4000])
            count += 1
        except Exception as e:
            print(f"Gagal kirim ke {tid}: {e}")
    return count

async def fonnte_webhook(request):
    try:
        data = await request.json()
    except:
        try:
            data = await request.post()
        except:
            data = {}
    wa_text = data.get('message') or data.get('text') or data.get('msg') or ''
    wa_group = data.get('group') or data.get('group_name') or data.get('name') or 'ZOLDYCK STORE'
    wa_sender = data.get('sender') or data.get('from') or data.get('pushname') or data.get('author') or ''
    if not wa_text and isinstance(data.get('data'), dict):
        wa_text = data['data'].get('message','')
    if not wa_text:
        wa_text = str(data)[:2000]
    kab, kec = parse_kota_kec_from_text(wa_text)
    try:
        conn = db()
        conn.execute("INSERT INTO wa_inbox_log(wa_group,wa_sender,message,parsed_kab,parsed_kec,created_at) VALUES(?,?,?,?,?,?)",
                     (wa_group, wa_sender, wa_text, kab or '', kec or '', datetime.now().isoformat()))
        db_commit_and_sync(conn)
        conn.close()
    except Exception as e:
        print(f"log fail {e}")
    if not kab or not kec:
        return web.json_response({"status": True, "forwarded": 0, "reason": "need kota+kec", "parsed_kab": kab, "parsed_kec": kec})
    bot = request.app['bot']
    forwarded = await forward_wa_to_telegram(bot, wa_text, wa_group, wa_sender, kab, kec)
    return web.json_response({"status": True, "forwarded": forwarded, "kab": kab, "kec": kec, "admin_notif": False, "footer_added": True})



def send_wa_notif_background(phone_number: str, kota_nama: str, user_name: str = ""):
    """Kirim WA di background biar Telegram satset, gak nunggu Fonnte"""
    try:
        import threading
        def run():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(send_wa_notif_kota(phone_number, kota_nama, user_name))
                loop.close()
            except Exception as e:
                print(f"Background FONNTE error: {e}")
        threading.Thread(target=run, daemon=True).start()
        print(f"🚀 FONNTE background task started untuk {phone_number} - {kota_nama}")
        return True
    except Exception as e:
        print(f"Background task gagal: {e}")
        return False

# === BOT BARU V2 - 100% TERPISAH DARI BOT LAMA ===
# Bot lama: bot.db (jangan diutak-atik, biarin jalan)
# Bot baru: bot_baru_v2.db (file terpisah, gak benturan sama sekali!)
BOT_ID = "BOT_BARU_V2_2025"  # ID unik bot baru
DB_PATH = Path(os.getenv("DB_PATH", f"/data/bot_baru_v2.db" if os.path.exists("/data") else "bot_baru_v2.db"))

# UNTUK SIMPAN USER BIAR PERMANEN (pilih 1):
# OPSI 1: Pakai Railway Volume (jika ada) -> DB_PATH=/data/bot_baru_v2.db (permanen)
# OPSI 2: Pakai PASADATA BARU (buat PASADATA baru, jangan pakai URL PASADATA lama!)
#         Set di Railway: PASADATA_URL_BARU=https://api.jsonbin.io/v3/b/xxxxx (BIN BARU!)
# OPSI 3: Tanpa PASADATA, pakai file lokal (akan hilang kalau Railway restart, tapi gak benturan)

PASADATA_URL_BARU = os.getenv("PASADATA_URL_BARU", "").strip()  # Opsional; isi hanya jika memang memakai PASADATA
PASADATA_KEY_BARU = os.getenv("PASADATA_KEY_BARU", "").strip()

def sync_from_pasadata_baru():
    """Download DB dari PASADATA BARU (bukan yang lama!)"""
    if not PASADATA_URL_BARU:
        return False
    try:
        import requests
        headers = {"X-Master-Key": PASADATA_KEY_BARU} if PASADATA_KEY_BARU else {}
        resp = requests.get(PASADATA_URL_BARU + "/latest", headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            # jsonbin format
            if 'record' in data and 'file_data' in data['record']:
                import base64
                file_bytes = base64.b64decode(data['record']['file_data'])
                if file_bytes[:6] == b'SQLite':
                    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                    with open(DB_PATH, 'wb') as f:
                        f.write(file_bytes)
                    print(f"✅ DB baru restored dari PASADATA BARU")
                    return True
    except Exception as e:
        print(f"PASADATA BARU download gagal: {e}")
    return False

def sync_to_pasadata_baru():
    """Upload DB ke PASADATA BARU (bukan yang lama!)"""
    if not PASADATA_URL_BARU or not DB_PATH.exists():
        return False
    try:
        import requests, base64
        headers = {"X-Master-Key": PASADATA_KEY_BARU, "Content-Type": "application/json"} if PASADATA_KEY_BARU else {"Content-Type": "application/json"}
        with open(DB_PATH, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        payload = {"file_data": b64, "bot_id": BOT_ID}
        resp = requests.put(PASADATA_URL_BARU, json=payload, headers=headers, timeout=30)
        if resp.status_code in [200, 201]:
            print(f"✅ DB baru backup ke PASADATA BARU")
            return True
    except Exception as e:
        print(f"PASADATA BARU upload gagal: {e}")
    return False

_pasadata_synced = False

DEFAULT_TEMPLATE = """📍KAB : {KAB}
📍KEC : {KEC}
📍KEL : {KEL}

💰 SALDO : {SALDO}

🆔 KELAMIN : {KELAMIN}
💳 KPJ : {KPJ}
🔰 SENSOR: {SENSOR}
📆 IT : {IT}
🏛️ PT : {PT}

🏆 DPT JMO LASIK ✅
"""


def db():
    global _pasadata_synced, DB_PATH

    # Pastikan folder database ada. Railway Volume biasanya /data;
    # kalau /data tidak bisa ditulis, otomatis fallback ke folder aplikasi.
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"⚠️ Folder DB tidak bisa dibuat ({DB_PATH.parent}): {e}. Fallback ke database lokal.")
        DB_PATH = Path("bot_baru_v2.db")
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Restore PASADATA hanya sekali dan hanya jika URL memang diset.
    if not _pasadata_synced and PASADATA_URL_BARU:
        try:
            if not DB_PATH.exists() or DB_PATH.stat().st_size < 100:
                sync_from_pasadata_baru()
        except Exception as e:
            print(f"⚠️ Restore PASADATA dilewati: {e}")
        _pasadata_synced = True

    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        # Fallback terakhir agar bot tetap bisa startup jika Railway Volume
        # bermasalah/readonly.
        fallback = Path("bot_baru_v2.db")
        print(f"⚠️ Gagal membuka DB {DB_PATH}: {e}. Pakai {fallback}.")
        fallback.parent.mkdir(parents=True, exist_ok=True)
        DB_PATH = fallback
        conn = sqlite3.connect(DB_PATH, timeout=30)

    conn.row_factory = sqlite3.Row
    return conn

def db_commit_and_sync(conn):
    """Commit + auto backup ke PASADATA BARU biar permanen"""
    conn.commit()
    if PASADATA_URL_BARU:
        try:
            import threading
            threading.Thread(target=sync_to_pasadata_baru, daemon=True).start()
        except:
            pass


def init_database():
    """Buat/migrasikan database tanpa membuat startup bot gagal karena backup eksternal."""
    global _pasadata_synced

    # Backup eksternal bersifat OPSIONAL. Jika gagal, bot tetap lanjut memakai SQLite lokal.
    if PASADATA_URL_BARU and not _pasadata_synced:
        try:
            if not DB_PATH.exists() or DB_PATH.stat().st_size < 100:
                sync_from_pasadata_baru()
        except Exception as e:
            print(f"⚠️ PASADATA tidak tersedia saat startup: {e}")
        _pasadata_synced = True

    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        name TEXT,
        username TEXT,
        wa_number TEXT,
        balance INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_wa_numbers (
        telegram_id INTEGER PRIMARY KEY,
        wa_number TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS subscriptions (
        telegram_id INTEGER PRIMARY KEY,
        package_code TEXT,
        package_name TEXT,
        price INTEGER DEFAULT 0,
        start_date TEXT,
        expiry_date TEXT,
        status TEXT DEFAULT 'inactive'
    );
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        payment_method TEXT DEFAULT 'SEABANK/DANA',
        package_code TEXT,
        package_name TEXT,
        proof_file_id TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        processed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS format_settings (
        telegram_id INTEGER PRIMARY KEY,
        template TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS format_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        input_text TEXT,
        result_text TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS format_codes (
        telegram_id INTEGER PRIMARY KEY,
        prefix TEXT NOT NULL,
        suffix TEXT DEFAULT '',
        current_number INTEGER NOT NULL,
        padding INTEGER NOT NULL,
        enabled INTEGER DEFAULT 1,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS format_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        email TEXT,
        password TEXT,
        raw_text TEXT,
        result_id INTEGER,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS kota_quota (
        telegram_id INTEGER PRIMARY KEY,
        quota INTEGER DEFAULT 0,
        total_used INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS user_kota (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        provinsi TEXT DEFAULT '',
        provinsi_id TEXT DEFAULT '',
        kab TEXT DEFAULT '',
        kab_id TEXT DEFAULT '',
        kec TEXT DEFAULT '',
        kec_id TEXT DEFAULT '',
        kel TEXT DEFAULT '',
        catatan TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS format_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        input_text TEXT,
        result_text TEXT,
        created_at TEXT NOT NULL,
        deleted_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS wa_inbox_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wa_group TEXT DEFAULT '',
        wa_sender TEXT DEFAULT '',
        message TEXT NOT NULL,
        parsed_kab TEXT DEFAULT '',
        parsed_kec TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS blacklist_numbers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT UNIQUE NOT NULL,
        added_by INTEGER,
        note TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    """)
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
    CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(telegram_id);
    CREATE INDEX IF NOT EXISTS idx_format_results_user ON format_results(telegram_id);
    CREATE INDEX IF NOT EXISTS idx_blacklist_number ON blacklist_numbers(number);
    """)
    # Migration: tambah wa_number kalau belum ada (untuk DB lama)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN wa_number TEXT")
        print("✅ Migrasi: tambah kolom wa_number di users")
    except:
        pass
    try:
        conn.execute("ALTER TABLE user_kota ADD COLUMN wa_notif_sent INTEGER DEFAULT 0")
    except:
        pass
    db_commit_and_sync(conn)
    conn.close()


def register_user(user):
    conn = db()
    conn.execute("""
        INSERT INTO users(telegram_id,name,username,balance,created_at)
        VALUES(?,?,?,0,?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            name=excluded.name, username=excluded.username
    """, (user.id, user.full_name, user.username, datetime.now().isoformat()))
    db_commit_and_sync(conn)
    conn.close()


def rupiah(value):
    return f"Rp {int(value):,}".replace(",", ".")



def normalize_number(num: str) -> str:
    cleaned = re.sub(r'\D', '', num.strip())
    if not cleaned:
        return ""
    if len(cleaned) < 8 or len(cleaned) > 15:
        return ""
    return cleaned

def get_blacklist_count():
    conn = db()
    row = conn.execute("SELECT COUNT(*) as c FROM blacklist_numbers").fetchone()
    conn.close()
    return row["c"] if row else 0


def is_admin(user_id):
    if user_id in ADMIN_IDS:
        return True
    try:
        conn = db()
        row = conn.execute("SELECT telegram_id FROM admins WHERE telegram_id=?", (user_id,)).fetchone()
        conn.close()
        return bool(row)
    except:
        return user_id in ADMIN_IDS


def get_subscription(user_id):
    conn = db()
    row = conn.execute("SELECT * FROM subscriptions WHERE telegram_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def has_auto_format_access(user_id):
    if user_id in ADMIN_IDS or is_admin(user_id):
        return True
    sub = get_subscription(user_id)
    if not sub:
        return False
    kode = (sub.get("package_code") or "").upper()
    if sub["status"] == "unlimited" or kode == "UNLIMITED" or "UNLIMITED" in (sub.get("package_name") or "").upper():
        return True
    if sub["status"] == "active" and sub["expiry_date"]:
        try:
            return datetime.now() < datetime.fromisoformat(sub["expiry_date"])
        except ValueError:
            return False
    return False


def add_months(dt, months):
    idx = dt.month - 1 + months
    year = dt.year + idx // 12
    month = idx % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)



def main_menu(user_id=None):
    if user_id is None:
        user_id = 0
    # 2 KOLOM FULL LAYAR + BLACKLIST (fitur sudah ada tapi tombol hilang kemarin)
    rows = [
        [InlineKeyboardButton(text="👤 PROFIL", callback_data="profile"), InlineKeyboardButton(text="📊 STATUS", callback_data="status")],
        [InlineKeyboardButton(text="💳 TOP UP", callback_data="topup"), InlineKeyboardButton(text="📝 AUTO FORMAT", callback_data="auto_format")],
        [InlineKeyboardButton(text="🏙️ KOTA SAYA", callback_data="kota_saya"), InlineKeyboardButton(text="➕ TAMBAH KOTA", callback_data="kota_add")],
        [InlineKeyboardButton(text="🚫 NO BLACKLIST", callback_data="blacklist_view"), InlineKeyboardButton(text="💡 SOLUSI JMO", callback_data="solusi_jmo")],
        [InlineKeyboardButton(text="🔎 CARI LAINNYA", callback_data="kota_search_lain")],
        [InlineKeyboardButton(text="📞 ADMIN", callback_data="contact_admin"), InlineKeyboardButton(text="🆘 BANTUAN", callback_data="bantuan")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton(text="🔐 PANEL ADMIN", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu_with_colors_note():
    return main_menu()

def back_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
    ])

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ AKTIF", callback_data="admin_active"), InlineKeyboardButton(text="⏳ PENDING", callback_data="admin_pending")],
        [InlineKeyboardButton(text="👥 CEK USER AKTIF", callback_data="admin_user_aktif"), InlineKeyboardButton(text="📊 STATS USER", callback_data="admin_stats")],
        [InlineKeyboardButton(text="➕ TAMBAH SALDO", callback_data="admin_add_balance"), InlineKeyboardButton(text="➖ KURANGI SALDO", callback_data="admin_sub_balance")],
        [InlineKeyboardButton(text="🚫 TAMBAH BLACKLIST", callback_data="admin_blacklist_add"), InlineKeyboardButton(text="🗑️ HAPUS BLACKLIST", callback_data="admin_blacklist_del")],
        [InlineKeyboardButton(text="👑 TAMBAH ADMIN", callback_data="admin_add_admin"), InlineKeyboardButton(text="❌ HAPUS ADMIN", callback_data="admin_del_admin")],
        [InlineKeyboardButton(text="🗑️ HAPUS USER", callback_data="admin_delete_user"), InlineKeyboardButton(text="📢 BROADCAST", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📋 LIST BLACKLIST", callback_data="admin_blacklist_view")],
        [InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]
    ])

def auto_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ BUAT FORMAT BARU", callback_data="format_create"), InlineKeyboardButton(text="📝 MANUAL", callback_data="format_manual")],
        [InlineKeyboardButton(text="⚙️ SET TEMPLATE", callback_data="format_setting"), InlineKeyboardButton(text="🔢 SET KODE", callback_data="set_kode_format")],
        [InlineKeyboardButton(text="📊 HASIL", callback_data="format_results"), InlineKeyboardButton(text="👤 AKUN", callback_data="hasil_akun")],
        [InlineKeyboardButton(text="🌆 KOTA", callback_data="kota_list"), InlineKeyboardButton(text="📜 HISTORY", callback_data="format_history")],
        [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")]
    ])

# Dispatcher dan FSM harus dibuat sebelum decorator handler digunakan.
dp = Dispatcher()

class PaymentState(StatesGroup):
    waiting_topup_amount = State()
    waiting_proof = State()

class JmoState(StatesGroup):
    waiting_question = State()

class KotaState(StatesGroup):
    waiting_kota_catatan = State()
    waiting_cari_lainnya = State()

class FormatState(StatesGroup):
    waiting_kode = State()
    waiting_manual = State()
    waiting_excel = State()
    waiting_setting = State()
    waiting_search = State()
    waiting_search_akun = State()
    waiting_edit = State()
    waiting_edit_akun = State()

class AdminState(StatesGroup):
    waiting_blacklist_add = State()
    waiting_blacklist_del = State()
    waiting_user_amount = State()
    waiting_delete_user = State()
    waiting_broadcast = State()


# Dispatcher sudah dibuat di atas sebelum handler pertama.

# ==================== DATABASE SOLUSI JMO SUPER LENGKAP ====================

JMO_SOLUTIONS = {
    # ERROR KODE
    "025": {
        "keywords": ["025", "error 025", "kode 025", "pesan 025", "025 jmo"],
        "solution": """🔎 <b>MASALAH KODE 025 - JMO</b>

❌ <b>Penyebab:</b>
• Data identitas tidak sesuai dengan database BPJS Ketenagakerjaan
• NIK, nama, atau tanggal lahir tidak match
• Kepesertaan belum terdaftar atau tidak aktif
• Sistem JMO sedang maintenance

✅ <b>Solusi:</b>
1️⃣ Periksa kembali NIK, Nama, dan Tanggal Lahir
2️⃣ Pastikan kepesertaan BPJS Ketenagakerjaan aktif
3️⃣ Update aplikasi JMO ke versi terbaru
4️⃣ Coba login ulang setelah 1x24 jam
5️⃣ Hubungi kantor cabang BPJS terdekat jika masih gagal

📌 <b>Tips:</b> Pastikan data yang diinput SAMA PERSIS dengan kartu kepesertaan."""
    },
    
    "026": {
        "keywords": ["026", "error 026", "kode 026"],
        "solution": """🔎 <b>MASALAH KODE 026 - JMO</b>

❌ <b>Penyebab:</b>
• Nomor KPJ tidak ditemukan atau tidak valid
• Kepesertaan sudah berakhir (resign/pensiun)
• Data kepesertaan belum sinkron

✅ <b>Solusi:</b>
1️⃣ Cek nomor KPJ di kartu kepesertaan fisik
2️⃣ Pastikan status kepesertaan masih aktif
3️⃣ Jika baru daftar, tunggu 1-2 hari kerja
4️⃣ Hubungi HRD/Perusahaan untuk cek kepesertaan
5️⃣ Kunjungi kantor BPJS terdekat untuk update data

📌 <b>Tips:</b> Simpan screenshot error untuk dibawa ke kantor BPJS."""
    },
    
    "027": {
        "keywords": ["027", "error 027", "kode 027"],
        "solution": """🔎 <b>MASALAH KODE 027 - JMO</b>

❌ <b>Penyebab:</b>
• Email tidak terdaftar atau tidak aktif
• Email sudah digunakan oleh akun lain
• Format email tidak valid

✅ <b>Solusi:</b>
1️⃣ Gunakan email aktif (Gmail, Yahoo, dll)
2️⃣ Cek folder SPAM untuk email verifikasi
3️⃣ Gunakan email yang belum terdaftar
4️⃣ Pastikan format email benar (contoh: nama@gmail.com)

📌 <b>Tips:</b> Gunakan email yang selalu diakses untuk memudahkan pemulihan akun."""
    },
    
    "028": {
        "keywords": ["028", "error 028", "kode 028"],
        "solution": """🔎 <b>MASALAH KODE 028 - JMO</b>

❌ <b>Penyebab:</b>
• Nomor HP tidak aktif atau tidak terdaftar
• Format nomor HP salah
• Nomor HP sudah digunakan oleh akun lain

✅ <b>Solusi:</b>
1️⃣ Pastikan nomor HP aktif dan dapat SMS
2️⃣ Gunakan format 08xxxxxxxx (tanpa +62)
3️⃣ Cek sinyal dan jaringan HP
4️⃣ Tunggu beberapa saat lalu coba lagi

📌 <b>Tips:</b> Gunakan nomor HP yang terdaftar di bank untuk memudahkan verifikasi."""
    },
    
    "029": {
        "keywords": ["029", "error 029", "kode 029"],
        "solution": """🔎 <b>MASALAH KODE 029 - JMO</b>

❌ <b>Penyebab:</b>
• Verifikasi wajah (face recognition) gagal
• Pencahayaan kurang atau terlalu terang
• Wajah tidak terlihat jelas
• Menggunakan foto, bukan wajah asli

✅ <b>Solusi:</b>
1️⃣ Cari tempat dengan pencahayaan cukup
2️⃣ Hapus kacamata/aksesoris yang menutupi wajah
3️⃣ Posisikan wajah di tengah frame
4️⃣ Jangan gunakan foto atau video
5️⃣ Pastikan izin kamera aktif di pengaturan HP

📌 <b>Tips:</b> 
• Lakukan di ruangan terang tapi tidak silau
• Wajah harus menghadap kamera
• Ikuti instruksi gerakan (blink, tersenyum)"""
    },
    
    "030": {
        "keywords": ["030", "error 030", "kode 030"],
        "solution": """🔎 <b>MASALAH KODE 030 - JMO</b>

❌ <b>Penyebab:</b>
• Waktu sesi login habis (timeout)
• Koneksi internet tidak stabil
• Server JMO sedang sibuk

✅ <b>Solusi:</b>
1️⃣ Periksa koneksi internet (WiFi/Data)
2️⃣ Login ulang ke aplikasi
3️⃣ Tunggu 5-10 menit lalu coba lagi
4️⃣ Gunakan jaringan yang stabil
5️⃣ Clear cache aplikasi JMO

📌 <b>Tips:</b> Biasanya terjadi saat jam sibuk (08:00-10:00)."""
    },
    
    "031": {
        "keywords": ["031", "error 031", "kode 031"],
        "solution": """🔎 <b>MASALAH KODE 031 - JMO</b>

❌ <b>Penyebab:</b>
• Aplikasi JMO versi lama
• Ada bug/error pada aplikasi
• Konflik dengan aplikasi lain

✅ <b>Solusi:</b>
1️⃣ Update aplikasi JMO ke versi terbaru
2️⃣ Restart HP dan buka ulang aplikasi
3️⃣ Clear cache dan data aplikasi (hati-hati)
4️⃣ Reinstall aplikasi JMO
5️⃣ Cek kompatibilitas dengan sistem HP

📌 <b>Tips:</b> Backup data penting sebelum reinstall."""
    },
    
    "032": {
        "keywords": ["032", "error 032", "kode 032"],
        "solution": """🔎 <b>MASALAH KODE 032 - JMO</b>

❌ <b>Penyebab:</b>
• Data peserta tidak ditemukan
• Status kepesertaan non-aktif
• Perubahan data belum sinkron

✅ <b>Solusi:</b>
1️⃣ Cek status kepesertaan di aplikasi
2️⃣ Hubungi HRD untuk verifikasi kepesertaan
3️⃣ Kunjungi kantor BPJS terdekat
4️⃣ Tunggu proses sinkronisasi data

📌 <b>Tips:</b> Bawa KTP dan kartu kepesertaan saat ke kantor BPJS."""
    },
    
    "033": {
        "keywords": ["033", "error 033", "kode 033"],
        "solution": """🔎 <b>MASALAH KODE 033 - JMO</b>

❌ <b>Penyebab:</b>
• Password yang dimasukkan salah
• Password telah kadaluarsa
• Akun terkunci karena terlalu banyak percobaan

✅ <b>Solusi:</b>
1️⃣ Gunakan fitur "Lupa Password"
2️⃣ Reset password melalui email/HP terdaftar
3️⃣ Tunggu 15 menit jika akun terkunci
4️⃣ Gunakan password dengan kombinasi huruf/angka
5️⃣ Pastikan CapsLock tidak aktif

📌 <b>Tips:</b> Gunakan password yang mudah diingat tetapi aman."""
    },
    
    # MASALAH UMUM
    "login": {
        "keywords": ["login", "tidak bisa login", "gagal login", "masuk", "sign in"],
        "solution": """🔎 <b>MASALAH LOGIN JMO</b>

❌ <b>Penyebab:</b>
• Email/HP dan password tidak cocok
• Akun belum terdaftar
• Server JMO bermasalah
• Koneksi internet tidak stabil

✅ <b>Solusi:</b>
1️⃣ Periksa kembali email/HP dan password
2️⃣ Gunakan fitur "Lupa Kata Sandi"
3️⃣ Cek koneksi internet
4️⃣ Update aplikasi ke versi terbaru
5️⃣ Coba login di waktu lain (off-peak)

📌 <b>Tips:</b> 
• Simpan password di tempat aman
• Gunakan login dengan Face ID/Fingerprint jika tersedia"""
    },
    
    "lupa password": {
        "keywords": ["password", "kata sandi", "lupa sandi", "lupa password", "reset password"],
        "solution": """🔎 <b>LUPA PASSWORD JMO</b>

❌ <b>Penyebab:</b>
• Lupa password yang digunakan
• Password kadaluarsa
• Akun dibajak

✅ <b>Solusi:</b>
1️⃣ Klik "Lupa Kata Sandi" di halaman login
2️⃣ Masukkan email atau HP terdaftar
3️⃣ Ikuti instruksi reset via email/SMS
4️⃣ Buat password baru yang kuat
5️⃣ Simpan password di tempat aman

📌 <b>Tips:</b> 
• Gunakan kombinasi huruf besar, kecil, angka & simbol
• Jangan gunakan password yang sama dengan akun lain
• Aktifkan 2FA jika tersedia"""
    },
    
    "verifikasi wajah": {
        "keywords": ["verifikasi wajah", "face", "wajah", "face recognition", "selfie"],
        "solution": """🔎 <b>MASALAH VERIFIKASI WAJAH JMO</b>

❌ <b>Penyebab:</b>
• Pencahayaan kurang atau berlebihan
• Wajah tidak terlihat jelas
• Menggunakan foto, bukan selfie langsung
• Kacamata/penutup wajah
• Ekspresi wajah tidak sesuai instruksi

✅ <b>Solusi:</b>
1️⃣ Cari ruangan dengan pencahayaan cukup
2️⃣ Hapus kacamata, topi, masker
3️⃣ Posisi wajah di tengah frame
4️⃣ Jangan gunakan filter atau efek
5️⃣ Ikuti instruksi (blink, senyum, angkat alis)
6️⃣ Pastikan izin kamera aktif

📌 <b>Tips:</b> 
• Lakukan di tempat tenang
• Gunakan kamera belakang (kualitas lebih baik)
• Pastikan tidak ada bayangan di wajah"""
    },
    
    "kpj tidak ditemukan": {
        "keywords": ["kpj", "nomor kpj", "kpj tidak ditemukan", "kpj tidak valid"],
        "solution": """🔎 <b>MASALAH NOMOR KPJ JMO</b>

❌ <b>Penyebab:</b>
• Nomor KPJ yang dimasukkan salah
• Kepesertaan tidak aktif
• Data kepesertaan belum sinkron
• Peserta baru (belum terdaftar sistem)

✅ <b>Solusi:</b>
1️⃣ Periksa kembali nomor KPJ di kartu
2️⃣ Cek status kepesertaan di aplikasi
3️⃣ Hubungi HRD untuk verifikasi
4️⃣ Tunggu 1-2 hari kerja jika baru daftar
5️⃣ Kunjungi kantor BPJS untuk update data

📌 <b>Tips:</b> 
• Foto kartu kepesertaan untuk cadangan
• Simpan nomor KPJ di catatan HP"""
    },
    
    "saldo jht tidak muncul": {
        "keywords": ["saldo", "jht tidak muncul", "saldo jht", "cek saldo", "jht"],
        "solution": """🔎 <b>SALDO JHT TIDAK MUNCUL</b>

❌ <b>Penyebab:</b>
• Data tidak sinkron dengan server
• Kepesertaan tidak aktif
• Aplikasi versi lama
• Proses refresh gagal

✅ <b>Solusi:</b>
1️⃣ Tarik layar ke bawah untuk refresh
2️⃣ Login ulang ke aplikasi
3️⃣ Update aplikasi ke versi terbaru
4️⃣ Cek di website BPJS (bukan hanya aplikasi)
5️⃣ Tunggu 1-2 jam dan coba lagi

📌 <b>Tips:</b> 
• Saldo JHT biasanya update setiap bulan
• Cek secara berkala (tanggal 1-5 setiap bulan)
• Simpan bukti potongan gaji untuk verifikasi"""
    },
    
    "otp": {
        "keywords": ["otp", "kode otp", "verifikasi otp", "sms otp"],
        "solution": """🔎 <b>MASALAH OTP JMO</b>

❌ <b>Penyebab:</b>
• Nomor HP tidak aktif
• Sinyal buruk atau tidak ada
• SMS terblokir
• Provider mengalami gangguan
• Request OTP terlalu sering

✅ <b>Solusi:</b>
1️⃣ Pastikan sinyal HP bagus
2️⃣ Cek folder spam/blocked SMS
3️⃣ Tunggu 1-2 menit, jangan spam request
4️⃣ Restart HP dan coba lagi
5️⃣ Gunakan nomor HP lain jika masih gagal

📌 <b>Tips:</b> 
• Minta OTP di waktu sinyal stabil
• Jangan berbagi kode OTP dengan siapapun
• OTP berlaku 3 menit"""
    },
    
    "email": {
        "keywords": ["email", "ubah email", "ganti email", "verifikasi email"],
        "solution": """🔎 <b>MASALAH EMAIL JMO</b>

❌ <b>Penyebab:</b>
• Email tidak aktif
• Folder spam penuh
• Email sudah digunakan
• Link verifikasi kadaluarsa

✅ <b>Solusi:</b>
1️⃣ Cek folder SPAM/Trash
2️⃣ Pastikan email aktif dan bisa diakses
3️⃣ Gunakan email yang selalu dipakai
4️⃣ Minta kirim ulang email verifikasi
5️⃣ Hubungi admin jika email tidak terkirim

📌 <b>Tips:</b> 
• Gunakan Gmail untuk hasil terbaik
• Tandai email JMO sebagai "Important"
• Jangan gunakan email kantor"""
    },
    
    "aktivasi": {
        "keywords": ["aktivasi", "belum terdaftar", "registrasi", "daftar", "register"],
        "solution": """🔎 <b>MASALAH AKTIVASI/REGISTRASI JMO</b>

❌ <b>Penyebab:</b>
• Data kepesertaan belum terdaftar
• Status kepesertaan non-aktif
• Sistem maintenance
• Proses registrasi belum lengkap

✅ <b>Solusi:</b>
1️⃣ Pastikan data kepesertaan sudah terdaftar
2️⃣ Cek status kepesertaan di BPJS
3️⃣ Tunggu 1-2 hari kerja setelah pendaftaran
4️⃣ Lengkapi semua data yang diminta
5️⃣ Hubungi HRD jika masih terkendala

📌 <b>Tips:</b> 
• Siapkan KTP dan KK saat registrasi
• Data harus SAMA PERSIS dengan dokumen"""
    },
    
    "kartu digital": {
        "keywords": ["kartu digital", "kartu kepesertaan", "e-card", "virtual card"],
        "solution": """🔎 <b>MASALAH KARTU DIGITAL JMO</b>

❌ <b>Penyebab:</b>
• Data tidak sinkron
• Aplikasi versi lama
• Tidak ada akses internet
• Server BPJS bermasalah

✅ <b>Solusi:</b>
1️⃣ Refresh/update aplikasi
2️⃣ Login ulang ke aplikasi
3️⃣ Update ke versi terbaru
4️⃣ Coba di jam non-sibuk
5️⃣ Screenshot kartu digital untuk cadangan

📌 <b>Tips:</b> 
• Download kartu digital saat jaringan stabil
• Simpan di galeri HP untuk akses cepat
• Bawa kartu fisik sebagai cadangan"""
    },
    
    "klaim": {
        "keywords": ["klaim", "pengajuan klaim", "klaim jht", "cairkan jht"],
        "solution": """🔎 <b>MASALAH PENGAJUAN KLAIM JHT</b>

❌ <b>Penyebab:</b>
• Data tidak lengkap
• Dokumen tidak sesuai
• Kepesertaan tidak aktif
• Belum memenuhi syarat

✅ <b>Solusi:</b>
1️⃣ Periksa syarat dan ketentuan klaim
2️⃣ Lengkapi semua dokumen yang diminta
3️⃣ Pastikan kepesertaan aktif
4️⃣ Periksa masa kerja minimal (5 tahun)
5️⃣ Baca pesan error dengan teliti

📌 <b>Tips:</b> 
• Siapkan dokumen: KTP, KK, buku rekening
• Klaim bisa dilakukan online/offline
• Proses klaim 7-14 hari kerja"""
    },
    
    "bpu": {
        "keywords": ["bpu", "bukan penerima upah", "bpumandiri"],
        "solution": """🔎 <b>MASALAH KEPESERTAAN BPU</b>

❌ <b>Penyebab:</b>
• Data kepesertaan tidak sesuai
• Status non-aktif
• Pembayaran iuran tertunda
• Data sinkronisasi gagal

✅ <b>Solusi:</b>
1️⃣ Cek status kepesertaan di aplikasi
2️⃣ Pastikan pembayaran iuran lancar
3️⃣ Update data jika ada perubahan
4️⃣ Hubungi BPJS untuk verifikasi
5️⃣ Bawa KTP dan bukti pembayaran

📌 <b>Tips:</b> 
• BPU = Bukan Penerima Upah (mandiri)
• Bayar iuran tepat waktu
• Update data jika pindah alamat"""
    },
    
    "data tidak sesuai": {
        "keywords": ["data tidak sesuai", "identitas tidak sesuai", "nik", "nama", "ttl"],
        "solution": """🔎 <b>MASALAH DATA IDENTITAS TIDAK SESUAI</b>

❌ <b>Penyebab:</b>
• Data di JMO berbeda dengan KTP/KK
• NIK tidak terdaftar di Dukcapil
• Perubahan data belum sinkron
• Kesalahan input saat registrasi

✅ <b>Solusi:</b>
1️⃣ Periksa data di KTP dan KK
2️⃣ Input data SAMA PERSIS dengan dokumen
3️⃣ Hubungi Dukcapil jika NIK bermasalah
4️⃣ Update data ke BPJS terdekat
5️⃣ Tunggu proses sinkronisasi (1-2 hari)

📌 <b>Tips:</b> 
• Bawa fotokopi KTP/KK ke kantor BPJS
• Pastikan e-KTP sudah aktif
• Data harus match dengan database kependudukan"""
    },
    
    "kamera": {
        "keywords": ["kamera", "izin kamera", "akses kamera", "permission camera"],
        "solution": """🔎 <b>MASALAH KAMERA JMO</b>

❌ <b>Penyebab:</b>
• Izin kamera tidak aktif
• Aplikasi tidak memiliki akses kamera
• Kamera HP rusak/tertutup
• HP kotor atau lemot

✅ <b>Solusi:</b>
1️⃣ Aktifkan izin kamera di pengaturan HP
2️⃣ Restart aplikasi JMO
3️⃣ Bersihkan lensa kamera
4️⃣ Coba gunakan kamera depan
5️⃣ Update aplikasi JMO

📌 <b>Tips:</b> 
• Cek di Settings > Apps > JMO > Permissions
• Izinkan akses kamera secara permanen
• Gunakan HP dengan kamera yang bagus"""
    },
    
    "server": {
        "keywords": ["server", "gangguan", "maintenance", "tidak dapat terhubung", "connection"],
        "solution": """🔎 <b>MASALAH KONEKSI/SERVER JMO</b>

❌ <b>Penyebab:</b>
• Server BPJS sedang maintenance
• Gangguan jaringan internet
• Aplikasi tidak update
• Traffic pengguna tinggi

✅ <b>Solusi:</b>
1️⃣ Cek koneksi internet
2️⃣ Tunggu 15-30 menit
3️⃣ Coba di jam non-sibuk
4️⃣ Update aplikasi ke versi terbaru
5️⃣ Coba via website jika aplikasi error

📌 <b>Tips:</b> 
• Biasanya maintenance jam 00:00-04:00
• Cek akun media sosial JMO untuk info maintenance
• Gunakan WiFi stabil untuk hasil terbaik"""
    },
    
    "lemot": {
        "keywords": ["lemot", "lambat", "loading", "error loading", "blank"],
        "solution": """🔎 <b>MASALAH APLIKASI LEMOT/BLANK</b>

❌ <b>Penyebab:</b>
• Cache aplikasi penuh
• RAM HP penuh
• Aplikasi versi lama
• Koneksi internet lemot

✅ <b>Solusi:</b>
1️⃣ Clear cache aplikasi JMO
2️⃣ Restart HP
3️⃣ Tutup aplikasi lain yang berjalan
4️⃣ Update aplikasi ke versi terbaru
5️⃣ Gunakan WiFi 4G/5G

📌 <b>Tips:</b> 
• Hapus aplikasi yang jarang dipakai
• Aktifkan mode hemat data jika perlu
• Reinstall jika masih lemot"""
    },
    
    "notifikasi": {
        "keywords": ["notifikasi", "push notif", "pemberitahuan", "alert"],
        "solution": """🔎 <b>MASALAH NOTIFIKASI JMO</b>

❌ <b>Penyebab:</b>
• Izin notifikasi tidak aktif
• Aplikasi di mode silent
• HP mode Do Not Disturb
• Notifikasi diblock di pengaturan

✅ <b>Solusi:</b>
1️⃣ Aktifkan notifikasi di pengaturan HP
2️⃣ Cek setting notifikasi di aplikasi
3️⃣ Matikan mode Do Not Disturb
4️⃣ Allow notifikasi di Settings > Apps > JMO

📌 <b>Tips:</b> 
• Aktifkan notifikasi untuk info penting
• Cek secara manual jika notifikasi tidak muncul
• Allow semua izin notifikasi"""
    },
    
    "lupa email": {
        "keywords": ["lupa email", "email lupa", "tidak ingat email"],
        "solution": """🔎 <b>LUPA EMAIL JMO</b>

❌ <b>Penyebab:</b>
• Lupa email yang digunakan
• Email tidak aktif
• Email kantor sudah tidak digunakan

✅ <b>Solusi:</b>
1️⃣ Coba semua email yang mungkin
2️⃣ Gunakan fitur "Lupa Email" jika ada
3️⃣ Hubungi admin JMO
4️⃣ Gunakan email personal (bukan kantor)
5️⃣ Buat akun baru dengan email aktif

📌 <b>Tips:</b> 
• Gunakan email yang selalu diakses
• Catat email di tempat aman
• Gunakan 1 email khusus untuk JMO"""
    },
    
    "lupa nomor hp": {
        "keywords": ["lupa nomor hp", "nomor hp lupa", "ganti nomor"],
        "solution": """🔎 <b>LUPA/GANTI NOMOR HP JMO</b>

❌ <b>Penyebab:</b>
• Ganti nomor HP tanpa update
• Nomor HP tidak aktif
• Lupa nomor yang digunakan

✅ <b>Solusi:</b>
1️⃣ Update nomor HP di pengaturan akun
2️⃣ Hubungi admin untuk verifikasi
3️⃣ Bawa KTP ke kantor BPJS
4️⃣ Gunakan email untuk reset jika lupa
5️⃣ Pastikan nomor baru aktif

📌 <b>Tips:</b> 
• Update nomor segera jika ganti HP
• Gunakan nomor yang selalu aktif
• Simpan nomor cadangan jika perlu"""
    },
    
    "cara daftar": {
        "keywords": ["cara daftar", "pendaftaran", "registrasi", "daftar jmo"],
        "solution": """🔎 <b>CARA DAFTAR JMO</b>

📝 <b>Langkah-langkah:</b>

1️⃣ Download aplikasi JMO di Play Store/App Store
2️⃣ Buka aplikasi dan pilih "Daftar"
3️⃣ Input data:
   • NIK (KTP)
   • Nama lengkap
   • Tanggal lahir
   • Nomor HP aktif
   • Email aktif
4️⃣ Verifikasi OTP via SMS
5️⃣ Verifikasi email
6️⃣ Buat password
7️⃣ Login dan lengkapi data

📌 <b>Persyaratan:</b>
• WNI dengan e-KTP
• Memiliki kepesertaan BPJS Ketenagakerjaan
• Nomor HP aktif
• Email aktif

💡 <b>Tips:</b> 
• Siapkan KTP/KK sebelum daftar
• Gunakan jaringan stabil
• Data harus SAMA PERSIS dengan KTP"""
    },
    
    "jht cair": {
        "keywords": ["jht cair", "cairkan jht", "pencairan jht", "jht 2025"],
        "solution": """🔎 <b>PENCAIRAN JHT 2025</b>

📌 <b>Syarat Pencairan JHT:</b>
1️⃣ Peserta sudah berhenti bekerja (resign/di-PHK)
2️⃣ Masa kepesertaan minimal 5 tahun (156 bulan)
3️⃣ Status kepesertaan non-aktif
4️⃣ Mengisi form pengajuan klaim JHT

📝 <b>Dokumen yang dibutuhkan:</b>
• KTP asli dan fotokopi
• KK asli dan fotokopi
• Kartu kepesertaan BPJS
• Surat PHK/resign dari perusahaan
• Buku rekening bank aktif

💡 <b>Proses:</b>
• Online: melalui aplikasi JMO atau website BPJS
• Offline: datang ke kantor BPJS terdekat
• Waktu proses: 7-14 hari kerja

📌 <b>Tips:</b> 
• Pastikan semua dokumen lengkap
• Cek saldo JHT terlebih dahulu di aplikasi
• Ajukan klaim segera setelah resign
• Simpan bukti pengajuan klaim"""
    }
,

    "OTP_LENGKAP": {"keywords": ["otp","kode otp","otp tidak masuk"], "solution": """🔎 <b>MASALAH OTP JMO VERSI LENGKAP</b>

✅ Cek pulsa, sinyal, no HP aktif, tunggu 60 detik, cek Spam, restart HP, ganti jaringan"""},
    "AMALGAMASI_LENGKAP": {"keywords": ["amalgamasi","gabung kpj","kpj ganda"], "solution": """🔎 <b>AMALGAMASI - GABUNG KPJ GANDA VERSI LENGKAP</b>

1. Pengkinian Data dulu 2. Menu Penggabungan Saldo 3. Masukkan KPJ lama 4. Syarat Nama & NIK sama persis 5. Proses 3x24 jam kerja"""},
    "REKENING_LENGKAP": {"keywords": ["rekening","buku tabungan"], "solution": """🔎 <b>REKENING GAGAL - LENGKAP</b>

✅ Rekening atas nama sendiri, bukan e-wallet, foto buku tabungan halaman 1 jelas"""},
    "KARTU_LENGKAP": {"keywords": ["kartu","kpj hilang"], "solution": """🔎 <b>KPJ HILANG / LUPA - LENGKAP</b>

✅ Tanya HRD, cek di JMO, atau datang ke cabang bawa KTP"""},

}



def get_jmo_solution(text: str) -> str:
    """Mencari solusi JMO berdasarkan keyword yang cocok"""
    text_lower = text.lower()
    
    # Cari kecocokan di database
    for key, data in JMO_SOLUTIONS.items():
        for keyword in data["keywords"]:
            if keyword in text_lower:
                return data["solution"]
    
    # Jika tidak ada yang cocok, berikan respon default dengan saran
    return """🛠️ <b>BELUM DITEMUKAN SOLUSI KHUSUS</b>

Maaf, saya belum menemukan solusi yang tepat untuk masalah Anda.

📌 <b>Untuk mendapatkan solusi yang lebih akurat, silakan:</b>

1️⃣ Tulis ulang masalah dengan lebih DETAIL
2️⃣ Sertakan KODE ERROR yang muncul (contoh: 025, 026, dst)
3️⃣ Jelaskan TAHAPAN yang gagal (login, verifikasi, dll)
4️⃣ Sebutkan PESAN ERROR lengkapnya
5️⃣ Foto/screenshot ERROR bisa diupload

💡 <b>Contoh pertanyaan yang baik:</b>
• "Kode 025 saat login JMO, data sudah sesuai"
• "JMO error 026, nomor KPJ tidak ditemukan"
• "Verifikasi wajah gagal terus, sudah dicoba berkali-kali"

📞 <b>Atau hubungi langsung:</b>
• Admin: @Hambali1995
• WhatsApp: 083160776091
• Kantor BPJS terdekat

⚠️ Jangan lupa screenshot error untuk dibawa ke kantor BPJS!"""


# ==================== COMMAND START ====================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    register_user(message.from_user)
    # FIX: Hapus ReplyKeyboard kotak abu-abu yang lama
    try:
        await message.answer("Menghapus menu lama...", reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass
    await message.answer(
        "🤖 <b>SAHABAT JHT 🤖</b>\n\n"
        f"👋 Selamat datang, <b>{message.from_user.full_name}</b>!\n"
        "Gimana kabarnya nih, saya berharap kabar baik-baik saja yah, "
        "tetap semangat dan jangan lupa bersyukur.\n"
        "Silahkan pilih menu di bawah ini : 👇",
        reply_markup=main_menu(message.from_user.id)
    )



@dp.message(Command("profil"))
async def cmd_profil(message: Message, state: FSMContext):
    await state.clear()
    register_user(message.from_user)
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (message.from_user.id,)).fetchone()
    sub = conn.execute("SELECT * FROM subscriptions WHERE telegram_id=?", (message.from_user.id,)).fetchone()
    conn.close()
    username = f"@{user['username']}" if user["username"] else "-"
    package = sub["package_name"] if sub else "Belum ada"
    expiry = "-"
    status = "🔴 Tidak aktif"
    if sub:
        if sub["status"] == "unlimited":
            expiry, status = "SELAMANYA", "🟢 AKTIF"
        elif sub["expiry_date"]:
            from datetime import datetime as dt
            exp = dt.fromisoformat(sub["expiry_date"])
            expiry = exp.strftime("%d-%m-%Y")
            status = "🟢 AKTIF" if dt.now() < exp else "🔴 EXPIRED"
    def rupiah2(v): return f"Rp {int(v):,}".replace(",", ".")
    await message.answer(
        "👤 <b>PROFIL USER</b>\n\n"
        f"🆔 Telegram ID : <code>{user['telegram_id']}</code>\n"
        f"👤 Nama : {user['name']}\n"
        f"📱 Username : {username}\n"
        f"💰 Saldo : <b>{rupiah2(user['balance'])}</b>\n\n"
        f"📦 Langganan : {package}\n"
        f"📅 Berakhir : {expiry}\n"
        f"📊 Status : {status}",
        reply_markup=main_menu(message.from_user.id)
    )

@dp.message(Command("status"))
async def cmd_status(message: Message, state: FSMContext):
    await state.clear()
    sub = get_subscription(message.from_user.id)
    if not sub:
        txt = "📊 <b>CEK STATUS LAYANAN</b>\n\n🔴 Belum ada langganan aktif.\nSilakan beli paket di menu AUTO FORMAT."
    else:
        expiry = sub["expiry_date"] if sub["expiry_date"] else "SELAMANYA"
        txt = f"📊 <b>CEK STATUS LAYANAN</b>\n\n📦 Paket : {sub['package_name']}\n📅 Berakhir : {expiry}\n📊 Status : {sub['status'].upper()}"
    await message.answer(txt, reply_markup=main_menu(message.from_user.id))

@dp.message(Command("autoformat"))
@dp.message(Command("auto_format"))
async def cmd_autoformat(message: Message, state: FSMContext):
    await state.clear()
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    if has_auto_format_access(message.from_user.id):
        await message.answer(
            "📝 <b>AUTO FORMAT</b>\n\n🔓 Akses kamu aktif.\nSilakan pilih menu:",
            reply_markup=auto_menu()
        )
    else:
        await message.answer(
            "🔒 <b>AUTO FORMAT TERKUNCI</b>\n\nUntuk membuka AUTO FORMAT, silakan pilih paket:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🟢 PAKET 6 BULAN — Rp50.000", callback_data="af_6m")],
                [InlineKeyboardButton(text="🔵 PAKET 1 TAHUN — Rp80.000", callback_data="af_1y")],
                [InlineKeyboardButton(text="🟣 PAKET UNLIMITED — Rp200.000", callback_data="af_unlimited")],
                [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
            ])
        )

@dp.message(Command("topup"))
async def cmd_topup(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(PaymentState.waiting_topup_amount)
    await message.answer(
        "💳 <b>TOP UP SALDO</b>\n\n"
        "Silakan transfer ke:\n\n"
        "🏦 <b>SEABANK</b>\n901040978290\nA/N HAMBALI\n\n"
        "💰 <b>DANA</b>\n083824101264\nA/N HAMBALI\n\n"
        "Ketik nominal. Contoh : <code>50000</code>",
        reply_markup=back_main()
    )

@dp.message(Command("solusijmo"))
@dp.message(Command("jmo"))
async def cmd_jmo(message: Message):
    await message.answer(
        "🛠️ <b>SOLUSI MASALAH JMO</b>\n\nKetik masalah kamu, contoh:\n<code>Kode 025 saat login</code>\n<code>JMO error 026</code>\n<code>Verifikasi wajah gagal</code>",
        reply_markup=back_main()
    )

@dp.message(Command("bantuan"))
@dp.message(Command("admin"))
async def cmd_bantuan(message: Message):
    await message.answer(
        "📞 <b>HUBUNGI ADMIN</b>\n\n👤 Admin : @Hambali1995\n📱 WhatsApp : 083160776091",
        reply_markup=back_main()
    )

@dp.message(Command("info"))
async def cmd_info(message: Message):
    await message.answer(
        "ℹ️ <b>INFO BOT SABABAT JHT</b>\n\n🤖 Bot bantuan JHT, Auto Format, Top Up, dan Solusi JMO.",
        reply_markup=back_main()
    )


@dp.callback_query(F.data == "back_main")
async def back_main_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except:
        pass
    await callback.message.answer(
        "🤖 <b>SAHABAT JHT 🤖</b>\n\n"
        f"👋 Selamat datang, <b>{callback.from_user.full_name}</b>!\n"
        "Gimana kabarnya nih, saya berharap kabar baik-baik saja yah, "
        "tetap semangat dan jangan lupa bersyukur.\n"
        "Silahkan pilih menu di bawah ini : 👇",
        reply_markup=main_menu(callback.from_user.id)
    )
    await callback.answer()

@dp.message(Command("start"))
@dp.message(CommandStart())
async def start_fixed(message: Message, state: FSMContext):
    await state.clear()
    register_user(message.from_user)
    try:
        await message.answer("Menghapus menu lama...", reply_markup=ReplyKeyboardRemove())
    except:
        pass
    await message.answer(
        "🤖 <b>SAHABAT JHT 🤖</b>\n\n"
        f"👋 Selamat datang, <b>{message.from_user.full_name}</b>!\n"
        "Gimana kabarnya nih, saya berharap kabar baik-baik saja yah, "
        "tetap semangat dan jangan lupa bersyukur.\n"
        "Silahkan pilih menu di bawah ini : 👇",
        reply_markup=main_menu(message.from_user.id)
    )


@dp.callback_query(F.data == "bantuan")
async def bantuan_handler(callback: CallbackQuery):
    await callback.message.edit_text(
        "🆘 <b>BANTUAN - SAHABAT JHT BOT</b>\n\n"
        "🤖 <b>Tentang Bot:</b>\n"
        "Bot ini membantu kamu untuk Auto Format JHT, cek saldo KPJ, manajemen kota, cek blacklist, dan solusi error JMO.\n\n"
        "📚 <b>Panduan Menu:</b>\n"
        "• 👤 <b>PROFIL</b> - Lihat info akun, saldo, paket aktif & expired\n"
        "• 📊 <b>STATUS</b> - Cek status langganan dan sisa kuota\n"
        "• 💳 <b>TOP UP</b> - Isi saldo via Seabank/Dana, kirim bukti foto\n"
        "• 📝 <b>AUTO FORMAT</b> - Buat format otomatis sesuai template (butuh langganan)\n"
        "• 🏙️ <b>KOTA SAYA</b> - Lihat daftar kota & kecamatan yang sudah dipilih, lengkap tgl aktif & expired\n"
        "• ➕ <b>TAMBAH KOTA</b> - Pilih Provinsi (1 kolom), Kab/Kota (1 kolom), Kecamatan (1 kolom, bisa pilih banyak >3 lalu SIMPAN)\n"
        "• 🚫 <b>NO BLACKLIST</b> - Lihat semua nomor blacklist + fitur CARI nomor\n"
        "• 🔎 <b>CARI LAINNYA</b> - Cari history WA berdasarkan nama kota\n"
        "• 💡 <b>SOLUSI JMO</b> - Solusi error JMO kode 025-033, OTP, verifikasi wajah, dll\n"
        "• 📞 <b>ADMIN</b> - Hubungi admin Telegram & WhatsApp\n\n"
        "💡 <b>Cara Pakai Auto Format:</b>\n"
        "1. Top Up dulu (minimal 10k dapat 2 quota kota)\n"
        "2. Beli paket 6 bulan/1 tahun/unlimited\n"
        "3. Masuk AUTO FORMAT > BUAT FORMAT BARU\n"
        "4. Input data sesuai template\n"
        "5. Bot akan auto format dengan kode JPG - 001 dll\n\n"
        "🎟️ <b>Quota Kota:</b> Setiap Tambah Kota pakai 1 quota. Top Up 1x dapat 2 quota. Admin unlimited.\n\n"
        "🚫 <b>Blacklist:</b> Nomor di blacklist tidak bisa dipakai. Cek di menu NO BLACKLIST > CARI NOMOR.\n\n"
        "📞 <b>Butuh Bantuan Lebih?</b>\n"
        "Chat admin: @Hambali1995 atau WA 083160776091",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📞 HUBUNGI ADMIN", callback_data="contact_admin")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    register_user(callback.from_user)
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (callback.from_user.id,)).fetchone()
    sub = conn.execute("SELECT * FROM subscriptions WHERE telegram_id=?", (callback.from_user.id,)).fetchone()
    conn.close()

    username = f"@{user['username']}" if user["username"] else "-"
    package = sub["package_name"] if sub else "Belum ada"
    expiry = "-"
    status = "🔴 Tidak aktif"

    if sub:
        if sub["status"] == "unlimited":
            expiry, status = "SELAMANYA", "🟢 AKTIF"
        elif sub["expiry_date"]:
            exp = datetime.fromisoformat(sub["expiry_date"])
            expiry = exp.strftime("%d-%m-%Y")
            status = "🟢 AKTIF" if datetime.now() < exp else "🔴 EXPIRED"

    await callback.message.edit_text(
        "👤 <b>PROFIL USER</b>\n\n"
        f"🆔 Telegram ID : <code>{user['telegram_id']}</code>\n"
        f"👤 Nama : {user['name']}\n"
        f"📱 Username : {username}\n"
        f"💰 Saldo : <b>{rupiah(user['balance'])}</b>\n\n"
        f"📦 Langganan : {package}\n"
        f"📅 Berakhir : {expiry}\n"
        f"📊 Status : {status}",
        reply_markup=back_main()
    )
    await callback.answer()


@dp.callback_query(F.data == "topup")
async def topup(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(PaymentState.waiting_topup_amount)
    await callback.message.edit_text(
        "💳 <b>TOP UP SALDO</b>\n\n"
        "Silakan transfer ke:\n\n"
        "🏦 <b>SEABANK</b>\n"
        "901040978290\n"
        "A/N HAMBALI\n\n"
        "💰 <b>DANA</b>\n"
        "083824101264\n"
        "A/N HAMBALI\n\n"
        "Atau bisa ketik nominal.\n"
        "Contoh : <code>50000</code>\n\n"
        "Setelah membayar, ketik nominal yang dibayar lalu tekan <b>SUDAH BAYAR</b>.",
        reply_markup=back_main()
    )
    await callback.answer()


@dp.message(PaymentState.waiting_topup_amount, F.text)
async def topup_amount(message: Message, state: FSMContext):
    raw = message.text.strip().lower().replace("rp", "").replace(".", "").replace(",", "").replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("❌ Nominal tidak valid. Contoh: <code>50000</code>")
        return

    amount = int(raw)
    await state.update_data(amount=amount, package_code=None, package_name=None)
    await state.set_state(PaymentState.waiting_proof)
    await message.answer(
        "✅ <b>NOMINAL TERCATAT</b>\n\n"
        f"💰 Nominal : <b>{rupiah(amount)}</b>\n\n"
        "Setelah transfer, tekan tombol:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ SUDAH BAYAR", callback_data="payment_done")],
            [InlineKeyboardButton(text="❌ BATAL", callback_data="back_main")]
        ])
    )


AUTO_PACKAGES = {
    "af_3m": ("3M", "AUTO FORMAT 3 BULAN", 20000),
    "af_6m": ("6M", "AUTO FORMAT 6 BULAN", 50000),
    "af_1y": ("1Y", "AUTO FORMAT 1 TAHUN", 80000),
    "af_unlimited": ("UNLIMITED", "AUTO FORMAT UNLIMITED", 200000)
}


@dp.callback_query(F.data == "auto_format")
async def auto_format(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    nl = chr(10)
    if has_auto_format_access(callback.from_user.id):
        await callback.message.edit_text(
            "📝 <b>AUTO FORMAT</b>" + nl + nl + "🔓 Akses kamu aktif." + nl + nl + "Silakan pilih menu:",
            reply_markup=auto_menu()
        )
    else:
        await callback.message.edit_text(
            "🔒 <b>AUTO FORMAT TERKUNCI</b>" + nl + nl + "Untuk membuka AUTO FORMAT, silakan pilih paket dulu:" + nl + nl + "🟡 3 Bulan — Rp20.000" + nl + "🟢 6 Bulan — Rp50.000" + nl + "🔵 1 Tahun — Rp80.000" + nl + "🟣 Unlimited — Rp200.000" + nl + nl + "Pilih paket di bawah untuk lanjut Top Up:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🟡 PAKET 3 BULAN — Rp20.000", callback_data="af_3m")],
                [InlineKeyboardButton(text="🟢 PAKET 6 BULAN — Rp50.000", callback_data="af_6m")],
                [InlineKeyboardButton(text="🔵 PAKET 1 TAHUN — Rp80.000", callback_data="af_1y")],
                [InlineKeyboardButton(text="🟣 PAKET UNLIMITED — Rp200.000", callback_data="af_unlimited")],
                [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
            ])
        )
    await callback.answer()


@dp.callback_query(F.data.in_(set(AUTO_PACKAGES.keys())))
async def auto_package(callback: CallbackQuery, state: FSMContext):
    code, name, price = AUTO_PACKAGES[callback.data]
    await state.update_data(amount=price, package_code=code, package_name=name)
    await state.set_state(PaymentState.waiting_proof)

    await callback.message.edit_text(
        "💳 <b>PEMBAYARAN AUTO FORMAT</b>\n\n"
        f"📦 Paket : <b>{name}</b>\n"
        f"💰 Harga : <b>{rupiah(price)}</b>\n\n"
        "Silakan transfer ke:\n\n"
        "🏦 <b>SEABANK</b>\n901040978290\nA/N HAMBALI\n\n"
        "💰 <b>DANA</b>\n083824101264\nA/N HAMBALI\n\n"
        "Setelah membayar, tekan <b>SUDAH BAYAR</b> lalu upload bukti.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ SUDAH BAYAR", callback_data="payment_done")],
            [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="auto_format")]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "payment_done")
async def payment_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("amount"):
        await callback.answer("❌ Nominal belum ditentukan.", show_alert=True)
        return
    await state.set_state(PaymentState.waiting_proof)
    await callback.message.edit_text(
        "📸 <b>UPLOAD BUKTI PEMBAYARAN</b>\n\n"
        f"💰 Nominal : <b>{rupiah(data['amount'])}</b>\n"
        f"📦 Paket : {data.get('package_name') or 'TOP UP SALDO'}\n\n"
        "Silakan kirim FOTO bukti transfer di chat ini."
    )
    await callback.answer()


@dp.message(PaymentState.waiting_proof, F.photo)
async def payment_proof(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = int(data.get("amount", 0))
    if amount <= 0:
        await state.clear()
        await message.answer("❌ Nominal pembayaran tidak ditemukan.")
        return

    photo = message.photo[-1]
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO transactions(
            telegram_id,amount,payment_method,package_code,package_name,
            proof_file_id,status,created_at
        ) VALUES(?,?,'SEABANK/DANA',?,?,?,'pending',?)
    """, (
        message.from_user.id, amount, data.get("package_code"),
        data.get("package_name"), photo.file_id, datetime.now().isoformat()
    ))
    tx_id = cur.lastrowid
    conn.commit()
    conn.close()
    await state.clear()

    await message.answer(
        "✅ <b>BUKTI PEMBAYARAN DITERIMA</b>\n\n"
        f"🧾 Transaksi : #{tx_id}\n"
        f"💰 Nominal : {rupiah(amount)}\n"
        f"📦 Paket : {data.get('package_name') or 'TOP UP SALDO'}\n\n"
        "⏳ Menunggu konfirmasi Admin."
    )

    caption = (
        "💰 <b>PEMBAYARAN BARU</b>\n\n"
        f"🧾 Transaksi : #{tx_id}\n"
        f"👤 Nama : {message.from_user.full_name}\n"
        f"🆔 ID : <code>{message.from_user.id}</code>\n"
        f"📱 Username : @{message.from_user.username or '-'}\n"
        f"💰 Nominal : <b>{rupiah(amount)}</b>\n"
        f"📦 Paket : {data.get('package_name') or 'TOP UP SALDO'}\n"
        "🟡 Status : PENDING"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ SETUJU", callback_data=f"approve_{tx_id}"),
        InlineKeyboardButton(text="❌ TOLAK", callback_data=f"reject_{tx_id}")
    ]])

    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_photo(admin_id, photo.file_id, caption=caption, reply_markup=keyboard)
        except Exception:
            logging.exception("Gagal mengirim transaksi ke admin")


@dp.message(PaymentState.waiting_proof)
async def payment_wrong_proof(message: Message):
    await message.answer("📸 Silakan kirim FOTO bukti pembayaran.")


async def process_payment(callback: CallbackQuery, tx_id: int, approve: bool):
    if not is_admin(callback.from_user.id):
        await callback.answer("Kamu bukan Admin.", show_alert=True)
        return
    conn = db()
    tx = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
    if not tx or tx["status"] != "pending":
        conn.close()
        await callback.answer("Transaksi sudah diproses/tidak ditemukan.", show_alert=True)
        return
    now = datetime.now().isoformat()
    nl = chr(10)
    if approve:
        conn.execute("UPDATE transactions SET status='approved', processed_at=? WHERE id=?", (now, tx_id))
        expiry = None
        pkg = tx["package_code"] or ""
        if pkg.startswith("kota_"):
            from datetime import timedelta
            if pkg == "kota_1w":
                expiry = datetime.now() + timedelta(days=7)
            elif pkg == "kota_1m":
                expiry = add_months(datetime.now(), 1)
            elif pkg == "kota_2m":
                expiry = add_months(datetime.now(), 2)
            elif pkg == "kota_6m":
                expiry = add_months(datetime.now(), 6)
            else:
                expiry = None
            status = "active" if pkg != "kota_unlimited" else "unlimited"
            conn.execute("INSERT INTO subscriptions(telegram_id,package_code,package_name,price,start_date,expiry_date,status) VALUES(?,?,?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET package_code=excluded.package_code, package_name=excluded.package_name, price=excluded.price, start_date=excluded.start_date, expiry_date=excluded.expiry_date, status=excluded.status", (tx["telegram_id"], tx["package_code"], tx["package_name"], tx["amount"], now, expiry.isoformat() if expiry else None, status))
            quota = 9999 if pkg == "kota_unlimited" else 3
            conn.execute("INSERT INTO kota_quota(telegram_id,quota,total_used) VALUES(?,?,0) ON CONFLICT(telegram_id) DO UPDATE SET quota=quota+?", (tx["telegram_id"], quota, quota))
        elif pkg:
            if pkg == "3M":
                expiry = add_months(datetime.now(), 3)
                status = "active"
            elif pkg == "6M":
                expiry = add_months(datetime.now(), 6)
                status = "active"
            elif pkg == "1Y":
                expiry = add_months(datetime.now(), 12)
                status = "active"
            else:
                expiry = None
                status = "unlimited"
            conn.execute("INSERT INTO subscriptions(telegram_id,package_code,package_name,price,start_date,expiry_date,status) VALUES(?,?,?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET package_code=excluded.package_code, package_name=excluded.package_name, price=excluded.price, start_date=excluded.start_date, expiry_date=excluded.expiry_date, status=excluded.status", (tx["telegram_id"], tx["package_code"], tx["package_name"], tx["amount"], now, expiry.isoformat() if expiry else None, status))
        else:
            conn.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (tx["amount"], tx["telegram_id"]))
        conn.commit()
        conn.close()
        exp_txt = ""
        if pkg:
            if expiry:
                exp_txt = " Berakhir: " + expiry.strftime("%d-%m-%Y")
            else:
                if pkg in ("kota_unlimited", "UNLIMITED"):
                    exp_txt = " Berakhir: SELAMANYA"
        user_txt = "PEMBAYARAN DISETUJUI" + nl + nl + "Transaksi: #" + str(tx_id) + nl + "Nominal: " + rupiah(tx["amount"]) + nl + "Paket: " + (tx["package_name"] or "TOP UP SALDO") + exp_txt + nl + nl + "Akses sudah dibuka."
    else:
        conn.execute("UPDATE transactions SET status='rejected', processed_at=? WHERE id=?", (now, tx_id))
        conn.commit()
        conn.close()
        user_txt = "PEMBAYARAN DITOLAK" + nl + nl + "Transaksi: #" + str(tx_id) + nl + "Nominal: " + rupiah(tx["amount"])
    try:
        await callback.bot.send_message(tx["telegram_id"], user_txt)
    except:
        pass
    try:
        if callback.message.photo:
            cap = callback.message.caption or ""
            lab = "DISETUJUI" if approve else "DITOLAK"
            await callback.message.edit_caption(caption=cap + nl + nl + lab, reply_markup=None)
        else:
            old = callback.message.text or ""
            lab = "DISETUJUI" if approve else "DITOLAK"
            await callback.message.edit_text(old + nl + nl + lab, reply_markup=None)
    except:
        pass
    await callback.answer("Disetujui." if approve else "Ditolak.")


@dp.callback_query(F.data.startswith("approve_"))
async def approve_payment(callback: CallbackQuery):
    try:
        tx_id = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ ID transaksi tidak valid.", show_alert=True)
        return
    await process_payment(callback, tx_id, True)


@dp.callback_query(F.data.startswith("reject_"))
async def reject_payment(callback: CallbackQuery):
    try:
        tx_id = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ ID transaksi tidak valid.", show_alert=True)
        return
    await process_payment(callback, tx_id, False)


@dp.callback_query(F.data == "status")
async def status(callback: CallbackQuery):
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (callback.from_user.id,)).fetchone()
    sub = conn.execute("SELECT * FROM subscriptions WHERE telegram_id=?", (callback.from_user.id,)).fetchone()
    conn.close()

    balance = user["balance"] if user else 0
    if not sub:
        text = f"📊 <b>STATUS USER</b>\n\n💰 Saldo : <b>{rupiah(balance)}</b>\n📦 Langganan : Belum ada\n📊 Status : 🔴 Tidak aktif"
    elif sub["status"] == "unlimited":
        text = f"📊 <b>STATUS USER</b>\n\n💰 Saldo : <b>{rupiah(balance)}</b>\n📦 Langganan : {sub['package_name']}\n📅 Berakhir : SELAMANYA\n📊 Status : 🟢 AKTIF"
    else:
        exp = datetime.fromisoformat(sub["expiry_date"])
        active = datetime.now() < exp
        text = (
            f"📊 <b>STATUS USER</b>\n\n💰 Saldo : <b>{rupiah(balance)}</b>\n"
            f"📦 Langganan : {sub['package_name']}\n"
            f"📅 Berakhir : {exp.strftime('%d-%m-%Y')}\n"
            f"📊 Status : {'🟢 AKTIF' if active else '🔴 EXPIRED'}"
        )

    await callback.message.edit_text(text, reply_markup=back_main())
    await callback.answer()


# ==================== SOLUSI JMO ====================

@dp.callback_query(F.data == "solusi_jmo")
async def solusi_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(JmoState.waiting_question)
    await callback.message.edit_text(
        "🛠️ <b>SOLUSI JMO</b>\n\n"
        "Silakan masukkan masalah Anda di sini.\n\n"
        "📌 <b>Contoh:</b>\n"
        "• <code>cara atasi solusi 026</code>\n"
        "• <code>JMO error 025</code>\n"
        "• <code>Verifikasi wajah gagal</code>\n"
        "• <code>Login tidak bisa</code>\n\n"
        "💡 <b>Tips:</b> Sebutkan kode error jika ada!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❓ SOLUSI ERROR 025", callback_data="jmo_025")],
            [InlineKeyboardButton(text="❓ SOLUSI ERROR 026", callback_data="jmo_026")],
            [InlineKeyboardButton(text="❓ SOLUSI ERROR 029 (WAJAH)", callback_data="jmo_029")],
            [InlineKeyboardButton(text="❓ CARA CAIRKAN JHT", callback_data="jmo_jht cair")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
    )
    await callback.answer()


# Quick access untuk error umum
@dp.callback_query(F.data.startswith("jmo_"))
async def jmo_quick(callback: CallbackQuery, state: FSMContext):
    error_code = callback.data.split("_", 1)[1]
    # Cari solusi untuk error code
    for key, data in JMO_SOLUTIONS.items():
        if key == error_code:
            await callback.message.edit_text(
                data["solution"],
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 KEMBALI KE MENU SOLUSI", callback_data="solusi_jmo")],
                    [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
                ])
            )
            await callback.answer()
            return
    
    # Jika tidak ditemukan
    await callback.answer("Solusi tidak ditemukan", show_alert=True)


@dp.message(JmoState.waiting_question, F.text)
async def solusi_text(message: Message, state: FSMContext):
    # Cari solusi di database
    solution = get_jmo_solution(message.text)
    
    # Kirim solusi dengan menu kembali
    await message.answer(
        solution,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 TANYA LAGI", callback_data="solusi_jmo")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
    )


@dp.message(JmoState.waiting_question, F.photo)
async def solusi_photo(message: Message, state: FSMContext):
    caption = message.caption or ""
    if caption:
        solution = get_jmo_solution(caption)
        await message.answer(
            solution,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 TANYA LAGI", callback_data="solusi_jmo")],
                [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
            ])
        )
    else:
        await message.answer(
            "📸 <b>FOTO DITERIMA</b>\n\n"
            "Silakan tuliskan kode/error yang terlihat pada foto, misalnya:\n"
            "• <code>025</code>\n"
            "• <code>026</code>\n"
            "• <code>verifikasi wajah</code>\n\n"
            "Saya akan mencari solusi yang tepat!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 KETIK MASALAH", callback_data="solusi_jmo")],
                [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
            ])
        )


# ==================== AUTO FORMAT (OTOMATIS + KAPITAL) ====================

@dp.callback_query(F.data == "format_create")
async def format_create(callback: CallbackQuery):
    if not has_auto_format_access(callback.from_user.id):
        await callback.answer("🔒 AUTO FORMAT masih terkunci.", show_alert=True)
        return
    await callback.message.edit_text(
        "📝 <b>BUAT FORMAT</b>\n\nPilih cara membuat format:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⌨️ KETIK DATA MANUAL", callback_data="format_manual")],
            [InlineKeyboardButton(text="📊 UPLOAD FILE EXCEL (.xlsx)", callback_data="format_excel")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU AUTO FORMAT", callback_data="auto_format")]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "format_manual")
async def format_manual_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FormatState.waiting_manual)
    await callback.message.edit_text(
        "⌨️ <b>BUAT FORMAT</b>\n\n"
        "Ketik data <b>tanpa perlu menulis KAB/KEC/KEL</b>. Bot otomatis membaca urutannya:\n\n"
        "1️⃣ KAB\n2️⃣ KEC\n3️⃣ KEL\n4️⃣ SALDO\n5️⃣ KELAMIN\n6️⃣ KPJ\n7️⃣ SENSOR\n8️⃣ IT\n9️⃣ PT\n\n"
        "Contoh input:\n<code>DEPOK\nCILODONG\nKALIBARU\n10.000.000\nPEREMPUAN 1992\n2019\n23****\n01-07-2022\nINDONESIA MERDEKA</code>\n\n"
        "Hasilnya otomatis menjadi format JMO.",
        reply_markup=back_main()
    )
    await callback.answer()



def make_template_from_example(example_text: str) -> str:
    import re
    lines = example_text.splitlines()
    out=[]
    for line in lines:
        l=line
        # Handle emoji + KAB/KEC/KEL/SALDO etc
        if re.search(r'KAB\s*:', l, re.I):
            l=re.sub(r'(KAB\s*:).*', r'\1 {KAB}', l, flags=re.I)
        elif re.search(r'KEC\s*:', l, re.I):
            l=re.sub(r'(KEC\s*:).*', r'\1 {KEC}', l, flags=re.I)
        elif re.search(r'KEL\s*:', l, re.I):
            l=re.sub(r'(KEL\s*:).*', r'\1 {KEL}', l, flags=re.I)
        elif re.search(r'(TOTAL\s*JHT|SALDO)', l, re.I) and ':' in l:
            l=re.sub(r'(:).*', r'\1 {SALDO}', l)
        elif re.search(r'KELAMIN\s*:', l, re.I):
            l=re.sub(r'(KELAMIN\s*:).*', r'\1 {KELAMIN}', l, flags=re.I)
        elif re.search(r'KPJ\s+SENSOR', l, re.I) or (re.search(r'SENSOR\s*:', l, re.I) and 'KPJ' in l.upper()):
            l=re.sub(r'(:).*', r'\1 {SENSOR}', l)
        elif re.search(r'^[^:]*SENSOR\s*:', l, re.I):
            l=re.sub(r'(:).*', r'\1 {SENSOR}', l)
        elif re.search(r'(?<!SENSOR\s)KPJ\s*:', l, re.I):
            l=re.sub(r'(KPJ\s*:).*', r'\1 {KPJ}', l, flags=re.I)
        elif re.search(r'IURAN\s*T|\bIT\s*:', l, re.I) and ':' in l:
            l=re.sub(r'(:).*', r'\1 {IT}', l)
        elif re.search(r'\bPT\b', l, re.I):
            if ':' in l:
                l=re.sub(r'(:).*', r'\1 {PT}', l)
            else:
                m=re.search(r'(PT\s*\*?\s*)(.*)', l, re.I)
                if m:
                    l=m.group(1)+'{PT}'
        out.append(l)
    # If no placeholder found, return original example (user custom template without placeholders)
    result = "\n".join(out).strip()
    if "{" not in result:
        # If user template already is final format (like emoji template without {KAB}), keep it but ensure placeholders exist
        # Convert emoji template to placeholder version
        result = result.replace("📍KAB :", "📍KAB : {KAB}").replace("📍KEC :", "📍KEC : {KEC}").replace("📍KEL :", "📍KEL : {KEL}")
        result = result.replace("💰 SALDO :", "💰 SALDO : {SALDO}").replace("SALDO :", "SALDO : {SALDO}")
        result = result.replace("🆔 KELAMIN :", "🆔 KELAMIN : {KELAMIN}").replace("KELAMIN :", "KELAMIN : {KELAMIN}")
        result = result.replace("💳 KPJ :", "💳 KPJ : {KPJ}").replace("KPJ :", "KPJ : {KPJ}")
        result = result.replace("🔰 SENSOR:", "🔰 SENSOR: {SENSOR}").replace("SENSOR:", "SENSOR: {SENSOR}").replace("SENSOR :", "SENSOR : {SENSOR}")
        result = result.replace("📆 IT :", "📆 IT : {IT}").replace("IT :", "IT : {IT}")
        result = result.replace("🏛️ PT :", "🏛️ PT : {PT}").replace("PT :", "PT : {PT}")
    return result.strip()

def parse_data_with_akun(raw_text: str) -> dict:
    import re
    data={}
    def get(pat):
        m=re.search(pat, raw_text, re.I | re.M)
        return m.group(1).strip() if m else ""
    data["KAB"]=get(r'KAB\s*:\s*([^\n]+)')
    data["KEC"]=get(r'KEC\s*:\s*([^\n]+)')
    data["KEL"]=get(r'KEL\s*:\s*([^\n]+)')
    m=re.search(r'(?:TOTAL\s*JHT|SALDO)[^:]*:\s*([0-9\.\,]+)', raw_text, re.I)
    data["SALDO"]=m.group(1).strip() if m else get(r'SALDO\s*:\s*([^\n]+)')
    data["KELAMIN"]=get(r'KELAMIN\s*:\s*([^\n]+)')
    kpjs=re.findall(r'KPJ\s*:\s*([^\n]+)', raw_text, re.I)
    data["KPJ"]=""
    for k in kpjs:
        if 'SENSOR' not in k.upper():
            if re.search(r'\d{4}', k):
                data["KPJ"]=k.strip()
                break
    if not data["KPJ"]:
        for k in kpjs:
            if 'SENSOR' not in k.upper():
                data["KPJ"]=k.strip()
                break
    data["SENSOR"]=get(r'(?:KPJ\s*SENSOR|SENSOR)\s*:\s*([^\n]+)')
    data["IT"]=get(r'(?:IURAN\s*T|IT)\s*:\s*([^\n]+)')
    m=re.search(r'PT\s*:?\s*\*?\s*([^\n]+)', raw_text, re.I)
    data["PT"]=m.group(1).strip() if m else ""
    email=""; password=""
    m=re.search(r'AKUN\s*:\s*\n+([^\n@\s]+@[^\n\s]+)\s*\n+([^\n]+)', raw_text, re.I)
    if m:
        email=m.group(1).strip(); password=m.group(2).strip()
    else:
        em=re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', raw_text)
        if em:
            email=em[0]
            after=raw_text.split(email)[-1].splitlines()
            for line in after:
                line=line.strip()
                if line and '@' not in line and len(line)>=3 and 'SAHABAT' not in line.upper():
                    if not line.startswith('🎼') and not line.startswith('🇯🇵'):
                        password=line; break
    data["EMAIL"]=email; data["PASSWORD"]=password
    return data


def apply_template(template, raw_text, kode_header=None):
    akun_data = parse_data_with_akun(raw_text)
    lines = [l.strip() for l in str(raw_text).strip().splitlines() if l.strip()!=""]
    jht_lines=[]
    for l in lines:
        if '@' in l and '.' in l and not __import__('re').search(r'KAB|KEC|KEL|PT|KPJ|SALDO', l, __import__('re').I):
            continue
        if __import__('re').search(r'AKUN\s*:', l, __import__('re').I):
            break
        jht_lines.append(l)
    cleaned=jht_lines
    while len(cleaned)<9:
        cleaned.append("-")
    if len(cleaned)>9:
        cleaned=cleaned[:8]+[" ".join(cleaned[8:])]
    def up(x):
        x=x.strip()
        if x=="-" or x=="": return "-"
        return x.upper()
    data = {
        "KAB": up(akun_data.get("KAB") or (cleaned[0] if len(cleaned)>0 else "-")),
        "KEC": up(akun_data.get("KEC") or (cleaned[1] if len(cleaned)>1 else "-")),
        "KEL": up(akun_data.get("KEL") or (cleaned[2] if len(cleaned)>2 else "-")),
        "SALDO": up(akun_data.get("SALDO") or (cleaned[3] if len(cleaned)>3 else "-")),
        "KELAMIN": up(akun_data.get("KELAMIN") or (cleaned[4] if len(cleaned)>4 else "-")),
        "KPJ": up(akun_data.get("KPJ") or (cleaned[5] if len(cleaned)>5 else "-")),
        "SENSOR": up(akun_data.get("SENSOR") or (cleaned[6] if len(cleaned)>6 else "-")),
        "IT": up(akun_data.get("IT") or (cleaned[7] if len(cleaned)>7 else "-")),
        "PT": up(akun_data.get("PT") or (cleaned[8] if len(cleaned)>8 else "-")),
    }
    if "{" in template and "}" in template:
        result=template
        for k,v in data.items():
            result=result.replace("{"+k+"}", v)
            result=result.replace("{"+k.lower()+"}", v)
            result=result.replace("{"+k.title()+"}", v)
        import re
        result=re.sub(r'\{[A-Z_]+\}', '', result)
        if kode_header:
            centered=kode_header.center(27)
            result=f"{centered}\n━━━━━━━━━━━━━━━━━━━\n{result}"
        return result
    templ_converted=make_template_from_example(template)
    if "{" in templ_converted:
        result=templ_converted
        for k,v in data.items():
            result=result.replace("{"+k+"}", v)
        import re
        result=re.sub(r'\{[A-Z_]+\}', '', result)
        if kode_header:
            centered=kode_header.center(27)
            result=f"{centered}\n━━━━━━━━━━━━━━━━━━━\n{result}"
        return result
    out_lines=[]; pin_count=0
    import re
    for line in template.splitlines():
        upper=line.upper()
        new_line=line
        if "📍" in line:
            pin_count+=1
            if pin_count==1: new_line=re.sub(r"📍\s*.*", f"📍 {data['KAB']}", line)
            elif pin_count==2: new_line=re.sub(r"📍\s*.*", f"📍 {data['KEC']}", line)
            elif pin_count==3: new_line=re.sub(r"📍\s*.*", f"📍 {data['KEL']}", line)
            out_lines.append(new_line); continue
        if "KELAMIN" in upper and ":" in line:
            new_line=re.sub(r":\s*.*", f": {data['KELAMIN']}", line)
        elif "SALDO" in upper and ":" in line:
            if "RP" in upper: new_line=re.sub(r":\s*.*", f": Rp. {data['SALDO']}", line)
            else: new_line=re.sub(r":\s*.*", f": {data['SALDO']}", line)
        elif "KPJ" in upper and "SENSOR" not in upper and ":" in line:
            new_line=re.sub(r":\s*.*", f": {data['KPJ']}", line)
        elif "SENSOR" in upper and ":" in line:
            new_line=re.sub(r":\s*.*", f": {data['SENSOR']}", line)
        elif ("IT" in upper or "IURAN" in upper) and ":" in line:
            new_line=re.sub(r":\s*.*", f": {data['IT']}", line)
        elif "PT" in upper and ":" in line:
            new_line=re.sub(r":\s*.*", f": {data['PT']}", line)
        out_lines.append(new_line)
    result="\n".join(out_lines)
    if kode_header:
        centered=kode_header.center(27)
        result=f"{centered}\n━━━━━━━━━━━━━━━━━━━\n{result}"
    return result



@dp.message(FormatState.waiting_manual, F.text)
async def format_manual_receive(message: Message, state: FSMContext):
    data = await state.get_data()
    edit_id = data.get("edit_result_id")

    conn = db()
    
    # Ambil template dari Database (SETTING FORMAT user)
    row = conn.execute(
        "SELECT template FROM format_settings WHERE telegram_id=?",
        (message.from_user.id,)
    ).fetchone()
    template = row["template"] if row else DEFAULT_TEMPLATE

    kode = generate_next_code(message.from_user.id)
    result = apply_template(template, message.text, kode_header=kode)

    if edit_id:
        conn.execute(
            "UPDATE format_results SET result_text=? WHERE id=? AND telegram_id=?",
            (result, edit_id, message.from_user.id)
        )
        result_id = edit_id
    else:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO format_results(telegram_id,input_text,result_text,created_at)
            VALUES(?,?,?,?)
        """, (message.from_user.id, message.text, result, datetime.now().isoformat()))
        result_id = cur.lastrowid
        # FIX: Jika ada kata AKUN di bawah format, auto masuk ke HASIL FORMAT+AKUN
        akun = parse_data_with_akun(message.text)
        has_akun_keyword = 'AKUN' in message.text.upper()
        if akun.get('EMAIL') or has_akun_keyword:
            email_save = akun.get('EMAIL') or 'AKUN'
            pass_save = akun.get('PASSWORD') or message.text.split('AKUN')[-1].strip()[:500]
            # Jika tidak ada email tapi ada AKUN:, simpan raw AKUN nya
            if not akun.get('EMAIL') and has_akun_keyword:
                email_save = 'AKUN'
                pass_save = message.text.upper().split('AKUN')[-1].strip()[:500]
                if ':' in pass_save:
                    pass_save = pass_save.split(':',1)[-1].strip()
            cur.execute("""INSERT INTO format_accounts(telegram_id,email,password,raw_text,result_id,created_at) VALUES(?,?,?,?,?,?)""", (message.from_user.id, email_save, pass_save, f"{email_save}\n{pass_save}", result_id, datetime.now().isoformat()))

    conn.commit()
    conn.close()
    await state.clear()

    await message.answer(
        "FORMAT OTOMATIS JADI:" + chr(10) + chr(10) + result[:3800],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="SALIN", callback_data=f"copy_result_{result_id}"), InlineKeyboardButton(text="EDIT", callback_data=f"edit_result_{result_id}")],
            [InlineKeyboardButton(text="SIMPAN", callback_data=f"save_result_{result_id}"), InlineKeyboardButton(text="HAPUS", callback_data=f"delete_result_{result_id}")],
            [InlineKeyboardButton(text="BUAT LAGI", callback_data="format_create")],
            [InlineKeyboardButton(text="MENU AUTO FORMAT", callback_data="auto_format")]
        ])
    )
    await callback.answer()



@dp.message(FormatState.waiting_setting, F.text)
async def format_setting_receive(message: Message, state: FSMContext):
    raw = message.text.strip()
    if '{' in raw and '}' in raw:
        template_to_save = raw
    else:
        template_to_save = make_template_from_example(raw)
    conn = db()
    conn.execute("""
        INSERT INTO format_settings(telegram_id,template,updated_at)
        VALUES(?,?,?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            template=excluded.template, updated_at=excluded.updated_at
    """, (message.from_user.id, template_to_save, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    await state.clear()
    # Test preview dengan data dummy
    dummy_input = "JAKARTA\nPENJARINGAN\nBEBAS\n14000\nPEREMPUAN 1992\n2021\n22* 23*\n02-03-2025\nSAKTI MULYA"
    preview = apply_template(template_to_save, dummy_input)
    await message.answer(
        "✅ <b>SETTING FORMAT TERSIMPAN & AKTIF</b>\n\n"
        "Bot sekarang akan <b>ngikutin 100%</b> template ini:\n\n"
        f"<pre>{html.escape(preview[:3800])}</pre>\n\n"
        "Coba BUAT FORMAT baru, hasilnya pasti ngikutin template ini!",
        reply_markup=auto_menu()
    )




@dp.callback_query(F.data == "set_kode_format")
async def set_kode_format(callback: CallbackQuery, state: FSMContext):
    if not has_auto_format_access(callback.from_user.id):
        await callback.answer("🔒 Terkunci", show_alert=True)
        return
    await state.set_state(FormatState.waiting_kode)
    await callback.message.edit_text(
        "🔢 <b>SET KODE FORMAT</b>\n\n"
        "Ketik kode awal yang kamu mau. Contoh:\n"
        "<code>JPG - 001</code>\n"
        "<code>ABC - 001</code>\n\n"
        "Bot akan otomatis urut: 001, 002, 003... tanpa duplikat dan muncul di atas format.\n\n"
        "Ketik kode sekarang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ BATAL", callback_data="format_setting")]
        ])
    )
    await callback.answer()


@dp.message(FormatState.waiting_kode, F.text)
async def receive_kode_format(message: Message, state: FSMContext):
    parsed = parse_kode_input(message.text)
    if not parsed:
        await message.answer("❌ Format kode salah. Contoh: <code>JPG - 001</code> atau <code>🤖 JPG - 001 🤖</code>")
        return
    if len(parsed) == 4:
        prefix, num, padding, suffix = parsed
    else:
        prefix, num, padding = parsed
        suffix = ""
    
    conn = db()
    conn.execute("""
        INSERT INTO format_codes(telegram_id,prefix,suffix,current_number,padding,enabled,updated_at)
        VALUES(?,?,?,?,?,1,?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            prefix=excluded.prefix,
            suffix=excluded.suffix,
            current_number=excluded.current_number,
            padding=excluded.padding,
            enabled=1,
            updated_at=excluded.updated_at
    """, (message.from_user.id, prefix, suffix, num, padding, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    await state.clear()
    demo_code_full = f"{prefix} {str(num).zfill(padding)} {suffix}".strip()
    centered_demo = demo_code_full.center(27)
    await message.answer(
        f"✅ <b>KODE DISIMPAN</b>\n\n"
        f"Kode aktif: <b>{demo_code_full}</b>\n\n"
        f"Format selanjutnya akan otomatis jadi:\n"
        f"<pre>{centered_demo}\n━━━━━━━━━━━━━━━━━━━\n[FORMAT KAMU]</pre>\n\n"
        f"Dan berikutnya otomatis berurutan tanpa duplikat. Posisi kode selalu di TENGAH.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ LIHAT SETTING", callback_data="format_setting")],
            [InlineKeyboardButton(text="📝 BUAT FORMAT", callback_data="format_create")]
        ])
    )



@dp.callback_query(F.data == "delete_kode_format")
async def delete_kode_format(callback: CallbackQuery):
    conn = db()
    conn.execute("DELETE FROM format_codes WHERE telegram_id=?", (callback.from_user.id,))
    conn.commit()
    conn.close()
    await callback.message.edit_text(
        "🗑️ <b>KODE DIHAPUS</b>\n\nKode format dinonaktifkan. Format tidak akan pakai kode lagi.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ KEMBALI KE SETTING", callback_data="format_setting")]
        ])
    )
    await callback.answer()



@dp.callback_query(F.data == "format_excel")
async def format_excel_start(callback: CallbackQuery, state: FSMContext):
    if not has_auto_format_access(callback.from_user.id):
        await callback.answer("🔒 AUTO FORMAT masih terkunci.", show_alert=True)
        return
    await state.set_state(FormatState.waiting_excel)
    await callback.message.edit_text(
        "📊 <b>FORMAT FILE EXCEL</b>\n\nSilakan upload file Excel <b>.xlsx</b>.",
        reply_markup=back_main()
    )
    await callback.answer()


@dp.message(FormatState.waiting_excel, F.document)
async def format_excel_receive(message: Message, state: FSMContext):
    if not (message.document.file_name or "").lower().endswith(".xlsx"):
        await message.answer("❌ File harus berformat .xlsx")
        return
    try:
        from openpyxl import load_workbook
    except ImportError:
        await message.answer("❌ Tambahkan <code>openpyxl</code> ke requirements.txt lalu deploy ulang.")
        return

    file = await message.bot.get_file(message.document.file_id)
    local = Path("/tmp") / f"{message.from_user.id}_{message.document.file_name}"
    await message.bot.download_file(file.file_path, local)

    conn = db()
    row = conn.execute(
        "SELECT template FROM format_settings WHERE telegram_id=?",
        (message.from_user.id,)
    ).fetchone()
    template = row["template"] if row else DEFAULT_TEMPLATE

    wb = load_workbook(local, read_only=True, data_only=True)
    ws = wb.active
    results = []

    for values in ws.iter_rows(values_only=True):
        raw = " ".join(str(v) for v in values if v is not None).strip()
        if raw:
            results.append(apply_template(template, raw))

    if not results:
        conn.close()
        await state.clear()
        await message.answer("❌ File Excel tidak memiliki data.")
        return

    result = "\n\n".join(results)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO format_results(telegram_id,input_text,result_text,created_at)
        VALUES(?,?,?,?)
    """, (message.from_user.id, message.document.file_name, result, datetime.now().isoformat()))
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    await state.clear()

    await message.answer(
        "✅ <b>HASIL FORMAT EXCEL</b>\n\n" + html.escape(result[:3900]),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 SALIN SEMUA HASIL", callback_data=f"copy_all_{rid}")],
            [InlineKeyboardButton(text="💾 SIMPAN KE HISTORY", callback_data=f"save_result_{rid}")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU AUTO FORMAT", callback_data="auto_format")]
        ])
    )


@dp.message(FormatState.waiting_excel)
async def format_excel_wrong(message: Message):
    await message.answer("📊 Silakan upload file Excel .xlsx")




@dp.callback_query(F.data == "format_results")
async def format_results(callback: CallbackQuery):
    if not has_auto_format_access(callback.from_user.id):
        await callback.answer("🔒 AUTO FORMAT masih terkunci.", show_alert=True)
        return
    conn = db()
    rows = conn.execute(
        "SELECT id, result_text, created_at FROM format_results WHERE telegram_id=? ORDER BY id DESC LIMIT 5",
        (callback.from_user.id,)
    ).fetchall()
    conn.close()
    if not rows:
        await callback.message.edit_text(
            "📄 <b>HASIL FORMAT - KOSONG</b>\n\nBelum ada hasil.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📝 BUAT FORMAT BARU", callback_data="format_create")],
                [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="auto_format")]
            ])
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"📄 <b>HASIL FORMAT TERKINI - {len(rows)} TERBARU</b>\n\nMenampilkan {len(rows)} format di bawah, tiap format ada tombolnya sendiri:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU", callback_data="auto_format")]
        ])
    )
    await callback.answer()

    for i, row in enumerate(rows, 1):
        rid = row["id"]
        full_text = row["result_text"].strip()
        if len(full_text) > 3800:
            full_text = full_text[:3800] + "..."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 SALIN", callback_data=f"copy_result_{rid}"),
                InlineKeyboardButton(text="✏️ EDIT", callback_data=f"edit_result_{rid}"),
                InlineKeyboardButton(text="🗑️ HAPUS", callback_data=f"delete_result_{rid}")
            ]
        ])
        try:
            await callback.message.bot.send_message(
                callback.from_user.id,
                f"<b>{i}. ID {rid}</b>\n<pre>{html.escape(full_text)}</pre>",
                reply_markup=kb
            )
            await asyncio.sleep(0.2)
        except Exception:
            pass

    await callback.message.bot.send_message(
        callback.from_user.id,
        "🔍 <b>MENU LANJUTAN</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 CARI HASIL FORMAT", callback_data="format_search")],
            [InlineKeyboardButton(text="📧 HASIL FORMAT+AKUN", callback_data="hasil_akun")],
            [InlineKeyboardButton(text="🗑️ HAPUS SEMUA HASIL", callback_data="clear_results")],
            [InlineKeyboardButton(text="⬅️ MENU AUTO FORMAT", callback_data="auto_format")]
        ])
    )

@dp.callback_query(F.data == "clear_results")
async def clear_results(callback: CallbackQuery):
    conn = db()
    conn.execute("DELETE FROM format_results WHERE telegram_id=?", (callback.from_user.id,))
    conn.commit(); conn.close()
    await callback.message.edit_text("🗑️ Semua hasil format dihapus.", reply_markup=auto_menu())
    await callback.answer()



@dp.callback_query(F.data == "format_search")
async def format_search(callback: CallbackQuery, state: FSMContext):
    if not has_auto_format_access(callback.from_user.id):
        await callback.answer("🔒 AUTO FORMAT masih terkunci.", show_alert=True)
        return
    await state.set_state(FormatState.waiting_search)
    await callback.message.edit_text(
        "🔎 <b>CARI HASIL FORMAT</b>\n\n"
        "Ketik kata yang ingin dicari, misalnya:\n"
        "<code>SERANG</code> atau <code>CIPOCOK</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU HASIL FORMAT", callback_data="format_results")]
        ])
    )
    await callback.answer()




@dp.message(FormatState.waiting_search, F.text)
async def format_search_receive(message: Message, state: FSMContext):
    query = message.text.strip()
    if not query:
        await message.answer("❌ Kata pencarian tidak boleh kosong.")
        return

    conn = db()
    rows = conn.execute("""
        SELECT id, result_text, created_at
        FROM format_results
        WHERE telegram_id=? AND result_text LIKE ?
        ORDER BY id DESC LIMIT 20
    """, (message.from_user.id, f"%{query}%")).fetchall()
    conn.close()
    await state.clear()

    if not rows:
        await message.answer(
            f"Tidak ada format dengan kode atau KAB/KOTA: <code>{query}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 CARI LAGI", callback_data="format_search")],
                [InlineKeyboardButton(text="📄 LIHAT SEMUA", callback_data="format_results")]
            ])
        )
        return

    # Langsung kirim format saja, tanpa kata HASIL CARI
    for row in rows:
        rid = row["id"]
        txt = row["result_text"]
        await message.bot.send_message(
            message.from_user.id,
            f"<pre>{html.escape(txt[:3800])}</pre>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="📋 SALIN", callback_data=f"copy_result_{rid}"),
                    InlineKeyboardButton(text="🗑️ HAPUS", callback_data=f"delete_result_{rid}"),
                    InlineKeyboardButton(text="✏️ EDIT", callback_data=f"edit_result_{rid}")
                ]
            ])
        )
        await asyncio.sleep(0.1)




@dp.callback_query(F.data == "hasil_akun")
async def hasil_akun(callback: CallbackQuery):
    if not has_auto_format_access(callback.from_user.id):
        await callback.answer("🔒 Terkunci.", show_alert=True)
        return
    conn = db()
    rows = conn.execute("SELECT id,email,password,created_at, result_id FROM format_accounts WHERE telegram_id=? ORDER BY id DESC LIMIT 10", (callback.from_user.id,)).fetchall()
    conn.close()
    if not rows:
        await callback.message.edit_text(
            "📧 <b>HASIL FORMAT+AKUN - KOSONG</b>\n\nBelum ada akun. Buat format dengan tambahan:\n<code>AKUN:\nemail@gmail.com\npassword123</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📝 BUAT FORMAT BARU", callback_data="format_create")],
                [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="auto_format")]
            ])
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"📧 <b>HASIL FORMAT+AKUN - {len(rows)} TERBARU</b>\n\nMenampilkan {len(rows)} akun di bawah:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="auto_format")]
        ])
    )
    await callback.answer()

    for i, row in enumerate(rows, 1):
        rid = row["id"]
        email = html.escape(row['email'] or '-')
        pwd = html.escape(row['password'] or '-')
        result_id = row['result_id']
        conn2 = db()
        fmt = conn2.execute("SELECT result_text FROM format_results WHERE id=? AND telegram_id=?", (result_id, callback.from_user.id)).fetchone()
        conn2.close()
        fmt_text = fmt["result_text"] if fmt else "-"
        if "━━━━━━━━" not in fmt_text:
            fmt_text = f"{fmt_text}\n━━━━━━━━━━━━━━━━━━━"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 SALIN", callback_data=f"copy_akun_{rid}"),
                InlineKeyboardButton(text="✏️ EDIT", callback_data=f"edit_akun_{rid}"),
                InlineKeyboardButton(text="🗑️ HAPUS", callback_data=f"del_akun_{rid}")
            ]
        ])
        await callback.message.bot.send_message(
            callback.from_user.id,
            f"<b>{i}. ID {rid} (Format ID: {result_id})</b>\n<pre>{html.escape(fmt_text[:3500])}\n\nAKUN:\n{email}\n{pwd}</pre>",
            reply_markup=kb
        )
        await asyncio.sleep(0.15)

    await callback.message.bot.send_message(
        callback.from_user.id,
        "🔍 <b>MENU AKUN LAINNYA</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 CARI AKUN", callback_data="search_akun")],
            [InlineKeyboardButton(text="📄 HASIL FORMAT TERKINI", callback_data="format_results")],
            [InlineKeyboardButton(text="🗑️ HAPUS SEMUA AKUN", callback_data="hapus_akun_all")],
            [InlineKeyboardButton(text="⬅️ MENU AUTO FORMAT", callback_data="auto_format")]
        ])
    )

@dp.callback_query(F.data.startswith("copy_akun_"))
async def copy_akun(callback: CallbackQuery):
    aid = int(callback.data.replace("copy_akun_",""))
    conn = db(); row = conn.execute("SELECT email,password FROM format_accounts WHERE id=? AND telegram_id=?", (aid, callback.from_user.id)).fetchone(); conn.close()
    if row:
        await callback.message.bot.send_message(callback.from_user.id, f"📋 <b>SALIN AKUN:</b>\n<code>{html.escape(row['email'])}\n{html.escape(row['password'])}</code>")
    await callback.answer("Disalin!")

@dp.callback_query(F.data.startswith("del_akun_"))
async def del_akun(callback: CallbackQuery):
    aid = int(callback.data.replace("del_akun_",""))
    conn = db(); conn.execute("DELETE FROM format_accounts WHERE id=? AND telegram_id=?", (aid, callback.from_user.id)); conn.commit(); conn.close()
    await callback.answer("Dihapus!")
    await hasil_akun(callback)

@dp.callback_query(F.data.startswith("edit_akun_"))
async def edit_akun_start(callback: CallbackQuery, state: FSMContext):
    aid = int(callback.data.replace("edit_akun_",""))
    await state.set_state(FormatState.waiting_edit_akun)
    await state.update_data(edit_id=aid)
    conn = db(); row = conn.execute("SELECT email,password FROM format_accounts WHERE id=? AND telegram_id=?", (aid, callback.from_user.id)).fetchone(); conn.close()
    if row:
        await callback.message.bot.send_message(callback.from_user.id, f"✏️ <b>EDIT AKUN ID {aid}</b>\n\nLama:\n<code>{row['email']}\n{row['password']}</code>\n\nKirim baru:\nemail\npassword")
    await callback.answer()

@dp.message(FormatState.waiting_edit_akun, F.text)
async def edit_akun_save(message: Message, state: FSMContext):
    data = await state.get_data()
    aid = data.get("edit_id")
    lines = [x.strip() for x in message.text.splitlines() if x.strip()]
    email = lines[0] if len(lines)>=1 else ""
    password = lines[1] if len(lines)>=2 else ""
    if "@" not in email:
        await message.answer("❌ Email tidak valid. Kirim lagi: email\npassword")
        return
    conn = db(); conn.execute("UPDATE format_accounts SET email=?, password=?, raw_text=? WHERE id=? AND telegram_id=?", (email, password, f"{email}\n{password}", aid, message.from_user.id)); conn.commit(); conn.close()
    await state.clear()
    await message.answer(f"✅ Akun ID {aid} diedit!", reply_markup=auto_menu())

@dp.callback_query(F.data == "search_akun")
async def search_akun_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FormatState.waiting_search_akun)
    await callback.message.bot.send_message(callback.from_user.id, "🔍 Kirim kata kunci untuk cari di HASIL AKUN+EMAIL:")
    await callback.answer()

@dp.message(FormatState.waiting_search_akun, F.text)
async def search_akun_do(message: Message, state: FSMContext):
    keyword = message.text.strip()
    conn = db()
    rows = conn.execute("SELECT id,email,password FROM format_accounts WHERE telegram_id=? AND (email LIKE ? OR password LIKE ?) ORDER BY id DESC LIMIT 20", (message.from_user.id, f"%{keyword}%", f"%{keyword}%")).fetchall()
    conn.close()
    await state.clear()
    if not rows:
        await message.answer(f"🔍 Tidak ada akun untuk '{keyword}'", reply_markup=auto_menu())
        return
    await message.answer(f"🔍 Hasil '{keyword}' - {len(rows)} akun:")
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📋 Salin", callback_data=f"copy_akun_{r['id']}"), InlineKeyboardButton(text="🗑️ Hapus", callback_data=f"del_akun_{r['id']}")]])
        await message.bot.send_message(message.from_user.id, f"<code>{html.escape(r['email'])}\n{html.escape(r['password'])}</code>", reply_markup=kb)

@dp.callback_query(F.data == "hapus_akun_all")
async def hapus_akun_all(callback: CallbackQuery):
    conn = db(); conn.execute("DELETE FROM format_accounts WHERE telegram_id=?", (callback.from_user.id,)); conn.commit(); conn.close()
    await callback.message.edit_text("🗑️ Semua akun dihapus.", reply_markup=auto_menu())
    await callback.answer()






@dp.callback_query(F.data == "menu_warna")
async def menu_warna(callback: CallbackQuery):
    await callback.message.answer(
        "🎨 <b>MENU PROFESIONAL</b>\n\n"
        "⚠️ Warna background tidak bisa diubah di Telegram (limit API).\n"
        "Semua tombol ikut tema HP (hijau di screenshot).\n\n"
        "🟡 TOP UP = Emas Muda (pakai emoji)\n"
        "🔵 Lainnya = Biru",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟡 TOP UP", callback_data="topup")],
            [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")]
        ])
    )
    await callback.answer()



@dp.callback_query(F.data == "format_history")
async def format_history(callback: CallbackQuery):
    if not has_auto_format_access(callback.from_user.id):
        await callback.answer("🔒 AUTO FORMAT masih terkunci.", show_alert=True)
        return
    conn = db()
    rows = conn.execute("""
        SELECT id,result_text,deleted_at FROM format_history
        WHERE telegram_id=? ORDER BY id DESC LIMIT 20
    """, (callback.from_user.id,)).fetchall()
    conn.close()

    try:
        await callback.message.delete()
    except:
        pass

    if not rows:
        await callback.message.bot.send_message(
            callback.from_user.id,
            "🕘 <b>RIWAYAT HASIL FORMAT - KOSONG</b>\n\nBelum ada format yang dihapus.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📄 LIHAT HASIL FORMAT", callback_data="format_results")],
                [InlineKeyboardButton(text="⬅️ MENU AUTO FORMAT", callback_data="auto_format")]
            ])
        )
        await callback.answer()
        return

    await callback.message.bot.send_message(
        callback.from_user.id,
        f"🕘 <b>RIWAYAT HASIL FORMAT - {len(rows)} DATA</b>\n\nFormat yang dihapus ada di sini. Bisa dihapus permanen atau dipulihkan ke HASIL FORMAT.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ HAPUS SEMUA RIWAYAT", callback_data="clear_history")],
            [InlineKeyboardButton(text="📄 LIHAT HASIL FORMAT", callback_data="format_results")]
        ])
    )

    for r in rows:
        rid = r["id"]
        txt = r["result_text"]
        try:
            dt = r["deleted_at"][:16].replace("T"," ")
        except:
            dt = ""
        await callback.message.bot.send_message(
            callback.from_user.id,
            f"<pre>{html.escape(txt[:3800])}</pre>\n<i>#{rid} • Dihapus: {dt}</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="♻️ PULIHKAN", callback_data=f"restore_history_{rid}"),
                    InlineKeyboardButton(text="🗑️ HAPUS PERMANEN", callback_data=f"delete_history_{rid}")
                ]
            ])
        )
        await asyncio.sleep(0.1)
    await callback.answer(f"📂 {len(rows)} riwayat")

@dp.callback_query(F.data.startswith("delete_result_"))
async def delete_result(callback: CallbackQuery):
    rid = int(callback.data.split("_")[-1])
    conn = db()
    row = conn.execute("SELECT result_text FROM format_results WHERE id=? AND telegram_id=?", (rid, callback.from_user.id)).fetchone()
    if row:
        conn.execute("""
            INSERT INTO format_history(telegram_id,input_text,result_text,created_at,deleted_at)
            VALUES(?,?,?,?,?)
        """, (callback.from_user.id, "", row["result_text"], datetime.now().isoformat(), datetime.now().isoformat()))
        conn.execute("DELETE FROM format_results WHERE id=? AND telegram_id=?", (rid, callback.from_user.id))
        conn.commit()
    conn.close()
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer("🗑️ Dipindahkan ke RIWAYAT")

@dp.callback_query(F.data.startswith("restore_history_"))
async def restore_history(callback: CallbackQuery):
    hid = int(callback.data.split("_")[-1])
    conn = db()
    row = conn.execute("SELECT * FROM format_history WHERE id=? AND telegram_id=?", (hid, callback.from_user.id)).fetchone()
    if not row:
        conn.close()
        await callback.answer("Riwayat tidak ditemukan", show_alert=True)
        return
    conn.execute("""
        INSERT INTO format_results(telegram_id,input_text,result_text,created_at)
        VALUES(?,?,?,?)
    """, (callback.from_user.id, row["input_text"], row["result_text"], datetime.now().isoformat()))
    conn.execute("DELETE FROM format_history WHERE id=? AND telegram_id=?", (hid, callback.from_user.id))
    conn.commit()
    conn.close()
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer("♻️ Dipulihkan ke HASIL FORMAT")
    await callback.message.bot.send_message(
        callback.from_user.id,
        f"<pre>{html.escape(row['result_text'][:3800])}</pre>\n✅ <b>Berhasil dipulihkan ke HASIL FORMAT</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📄 LIHAT HASIL FORMAT", callback_data="format_results")],
            [InlineKeyboardButton(text="🕘 LIHAT RIWAYAT", callback_data="format_history")]
        ])
    )

@dp.callback_query(F.data.startswith("delete_history_"))
async def delete_history(callback: CallbackQuery):
    hid = int(callback.data.split("_")[-1])
    conn = db()
    conn.execute("DELETE FROM format_history WHERE id=? AND telegram_id=?", (hid, callback.from_user.id))
    conn.commit()
    conn.close()
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer("🗑️ Dihapus permanen dari riwayat")





# ==================== API WILAYAH INDONESIA LENGKAP - 38 PROVINSI ====================
WILAYAH_API_BASE = "https://www.emsifa.com/api-wilayah-indonesia/api"

async def fetch_wilayah(endpoint):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{WILAYAH_API_BASE}/{endpoint}", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        logging.error(f"Wilayah API error {endpoint}: {e}")
    return []

def get_user_balance(telegram_id):
    conn = db()
    row = conn.execute("SELECT balance FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    conn.close()
    return row["balance"] if row else 0

def get_kota_quota(telegram_id):
    conn = db()
    row = conn.execute("SELECT quota FROM kota_quota WHERE telegram_id=?", (telegram_id,)).fetchone()
    conn.close()
    return row["quota"] if row else 0

def add_kota_quota(telegram_id, amount=3):
    conn = db()
    conn.execute("INSERT INTO kota_quota(telegram_id,quota,total_used) VALUES(?,?,0) ON CONFLICT(telegram_id) DO UPDATE SET quota=quota+?", (telegram_id, amount, amount))
    conn.commit()
    conn.close()

def use_kota_quota(telegram_id):
    conn = db()
    conn.execute("UPDATE kota_quota SET quota=CASE WHEN quota>0 THEN quota-1 ELSE 0 END, total_used=total_used+1 WHERE telegram_id=?", (telegram_id,))
    conn.commit()
    conn.close()

KOTA_PACKAGES = {
    "kota_1w": ("1 Minggu", 40000),
    "kota_1m": ("1 Bulan", 120000),
    "kota_2m": ("2 Bulan", 220000),
    "kota_6m": ("6 Bulan", 600000),
    "kota_unlimited": ("Unlimited", 2000000),
}

@dp.callback_query(F.data == "kota_add")
async def kota_add_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id in ADMIN_IDS or is_admin(callback.from_user.id):
        await state.clear()
        await callback.message.edit_text(
            "🏙️ <b>TAMBAH KOTA - PILIH PAKET (ADMIN)</b>\n\n"
            "Kamu admin (Unlimited), bisa langsung pilih provinsi atau beli paket lagi.\n\n"
            "💎 <b>Paket tersedia:</b>\n"
            "⏰ 1 Minggu - Rp 40.000 (3x)\n"
            "📅 1 Bulan - Rp 120.000 (3x)\n"
            "📅 2 Bulan - Rp 220.000 (3x)\n"
            "📅 6 Bulan - Rp 600.000 (3x)\n"
            "♾️ Unlimited - Rp 2.000.000 (3x)\n\n"
            "Klik paket untuk Top Up, atau langsung pilih provinsi (Admin Unlimited):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➡️ LANGSUNG PILIH PROVINSI (ADMIN)", callback_data="kota_add_provinsi")],
                [InlineKeyboardButton(text="⏰ 1 Minggu - 40k", callback_data="paket_kota_1w")],
                [InlineKeyboardButton(text="📅 1 Bulan - 120k", callback_data="paket_kota_1m")],
                [InlineKeyboardButton(text="📅 2 Bulan - 220k", callback_data="paket_kota_2m")],
                [InlineKeyboardButton(text="📅 6 Bulan - 600k", callback_data="paket_kota_6m")],
                [InlineKeyboardButton(text="♾️ Unlimited - 2jt", callback_data="paket_kota_unlimited")],
                [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
            ])
        )
        await callback.answer()
        return

    quota = get_kota_quota(callback.from_user.id)
    balance = get_user_balance(callback.from_user.id)
    
    await state.clear()
    if quota > 0:
        await callback.message.edit_text(
            "💎 <b>PILIH PAKET TAMBAH KOTA</b>\n\n"
            f"🎟️ Sisa Kuota kamu: <b>{quota}x</b> lagi\n"
            f"💰 Saldo: {rupiah(balance)}\n\n"
            "Kamu masih punya kuota, bisa langsung lanjut pilih provinsi.\n"
            "Atau mau beli paket baru dulu?\n\n"
            "📦 <b>DAFTAR PAKET:</b>\n"
            "⏰ <b>1 Minggu</b> — Rp 40.000 (dapat 3x kuota)\n"
            "📅 <b>1 Bulan</b> — Rp 120.000 (3x)\n"
            "📅 <b>2 Bulan</b> — Rp 220.000 (3x)\n"
            "📅 <b>6 Bulan</b> — Rp 600.000 (3x)\n"
            "♾️ <b>Unlimited</b> — Rp 2.000.000 (3x per topup)\n\n"
            "👇 Sisa kuota kamu {quota}x, tapi untuk tambah kota baru silakan pilih paket dulu (akan menambah kuota):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏰ 1 Minggu - Rp 40.000", callback_data="paket_kota_1w")],
                [InlineKeyboardButton(text="📅 1 Bulan - Rp 120.000", callback_data="paket_kota_1m")],
                [InlineKeyboardButton(text="📅 2 Bulan - Rp 220.000", callback_data="paket_kota_2m")],
                [InlineKeyboardButton(text="📅 6 Bulan - Rp 600.000", callback_data="paket_kota_6m")],
                [InlineKeyboardButton(text="♾️ Unlimited - Rp 2.000.000", callback_data="paket_kota_unlimited")],
                [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
            ])
        )
    else:
        await callback.message.edit_text(
            "🔒 <b>TAMBAH KOTA - PILIH PAKET DULU</b>\n\n"
            f"💰 Saldo: <b>{rupiah(balance)}</b>\n"
            f"🎟️ Sisa Kuota: <b>{quota}x</b> (habis)\n\n"
            "Untuk bisa TAMBAH KOTA, kamu harus pilih paket dulu.\n"
            "1x Top Up = <b>3x kuota TAMBAH KOTA</b>\n"
            "Setelah 3x pakai, klik ke-4 wajib Top Up lagi!\n\n"
            "📦 <b>DAFTAR PAKET:</b>\n"
            "⏰ <b>1 Minggu</b> — Rp 40.000 (3x kuota)\n"
            "📅 <b>1 Bulan</b> — Rp 120.000 (3x)\n"
            "📅 <b>2 Bulan</b> — Rp 220.000 (3x)\n"
            "📅 <b>6 Bulan</b> — Rp 600.000 (3x)\n"
            "♾️ <b>Unlimited</b> — Rp 2.000.000 (3x)\n\n"
            "Klik paket di bawah untuk lanjut Top Up:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏰ 1 Minggu - Rp 40.000", callback_data="paket_kota_1w")],
                [InlineKeyboardButton(text="📅 1 Bulan - Rp 120.000", callback_data="paket_kota_1m")],
                [InlineKeyboardButton(text="📅 2 Bulan - Rp 220.000", callback_data="paket_kota_2m")],
                [InlineKeyboardButton(text="📅 6 Bulan - Rp 600.000", callback_data="paket_kota_6m")],
                [InlineKeyboardButton(text="♾️ Unlimited - Rp 2.000.000", callback_data="paket_kota_unlimited")],
                [InlineKeyboardButton(text="📞 HUBUNGI ADMIN", callback_data="contact_admin")],
                [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
            ])
        )
    await callback.answer()

@dp.callback_query(F.data == "kota_add_provinsi")
async def kota_add_provinsi(callback: CallbackQuery, state: FSMContext):
    quota = get_kota_quota(callback.from_user.id)
    if callback.from_user.id not in ADMIN_IDS and not is_admin(callback.from_user.id) and quota <= 0:
        await callback.answer("❌ Kuota habis! Pilih paket dulu.", show_alert=True)
        await kota_add_start(callback, state)
        return
    await state.clear()
    await callback.message.edit_text(
        f"🏙️ <b>TAMBAH KOTA - PILIH PROVINSI</b>\n\n🎟️ Sisa Kuota: <b>{quota}x</b>\n⏳ Memuat 38 provinsi...",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="kota_add")]])
    )
    provinces = await fetch_wilayah("provinces.json")
    if not provinces:
        await callback.message.edit_text("❌ Gagal memuat provinsi.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 COBA LAGI", callback_data="kota_add_provinsi")]]))
        await callback.answer()
        return
    buttons = [[InlineKeyboardButton(text=prov["name"], callback_data=f"prov_{prov['id']}_{prov['name']}")] for prov in provinces]
    buttons.append([InlineKeyboardButton(text=f"🎟️ Sisa Kuota: {quota}x", callback_data="kota_saya")])
    buttons.append([InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")])
    await callback.message.edit_text(f"🏙️ <b>PILIH PROVINSI ({len(provinces)})</b>\n🎟️ Kuota: {quota}x", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()

@dp.callback_query(F.data == "topup_paket_kota")
async def topup_paket_kota_list(callback: CallbackQuery, state: FSMContext):
    await kota_add_start(callback, state)

@dp.callback_query(F.data.startswith("paket_kota_"))
async def paket_kota_selected(callback: CallbackQuery, state: FSMContext):
    paket_map = {
        "paket_kota_1w": ("1 Minggu", 40000, "kota_1w"),
        "paket_kota_1m": ("1 Bulan", 120000, "kota_1m"),
        "paket_kota_2m": ("2 Bulan", 220000, "kota_2m"),
        "paket_kota_6m": ("6 Bulan", 600000, "kota_6m"),
        "paket_kota_unlimited": ("Unlimited", 2000000, "kota_unlimited"),
    }
    data = paket_map.get(callback.data)
    if not data:
        await callback.answer("Paket tidak ditemukan", show_alert=True)
        return
    nama_paket, harga, kode = data
    await state.clear()
    await state.set_state(PaymentState.waiting_proof)
    await state.update_data(amount=harga, package_code=kode, package_name=f"TAMBAH KOTA {nama_paket}")
    await callback.message.edit_text(
        f"💳 <b>TOP UP - PAKET {nama_paket.upper()}</b>\n\n"
        f"📦 Paket: <b>TAMBAH KOTA {nama_paket}</b>\n"
        f"💰 Harga: <b>{rupiah(harga)}</b>\n"
        f"🎟️ Dapat: <b>3x kuota TAMBAH KOTA</b>\n\n"
        "Silakan transfer sesuai nominal ke:\n\n"
        "🏦 <b>SEABANK</b>\n901040978290\nA/N HAMBALI\n\n"
        "💰 <b>DANA</b>\n083824101264\nA/N HAMBALI\n\n"
        f"Setelah transfer <b>{rupiah(harga)}</b>, kirim FOTO bukti transfer di sini.\n"
        f"Admin akan dapat notifikasi + tombol SETUJU/TOLAK.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ SUDAH BAYAR - KIRIM BUKTI", callback_data="payment_done")],
            [InlineKeyboardButton(text="📞 HUBUNGI ADMIN", callback_data="contact_admin")],
            [InlineKeyboardButton(text="⬅️ GANTI PAKET", callback_data="topup_paket_kota")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
    )
    await callback.answer(f"Paket {nama_paket} dipilih!")


@dp.callback_query(F.data.startswith("prov_"))
async def provinsi_selected(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 2)
    prov_id = parts[1]
    prov_name = parts[2]
    await state.update_data(provinsi_id=prov_id, provinsi_name=prov_name)
    await callback.message.edit_text(
        f"⏳ Memuat kab/kota di <b>{prov_name}</b>...",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ KEMBALI KE PROVINSI", callback_data="kota_add")]])
    )
    regencies = await fetch_wilayah(f"regencies/{prov_id}.json")
    if not regencies:
        await callback.message.edit_text("❌ Gagal memuat.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="kota_add")]]))
        return
    buttons = []
    for reg in regencies:
        buttons.append([InlineKeyboardButton(text=reg["name"], callback_data=f"kab_{reg['id']}_{reg['name']}")])  # 1 KOLOM
    buttons.append([InlineKeyboardButton(text="⬅️ KEMBALI KE PROVINSI", callback_data="kota_add")])
    await callback.message.edit_text(
        f"🏛️ <b>{prov_name} - PILIH KAB/KOTA ({len(regencies)})</b>\n\nKlik kab/kota untuk lihat kecamatan:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("kab_"))
async def kabupaten_selected(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 2)
    kab_id = parts[1]
    kab_name = parts[2]
    data = await state.get_data()
    prov_name = data.get("provinsi_name", "")
    prov_id = data.get("provinsi_id", "")
    await state.update_data(kab_id=kab_id, kab_name=kab_name)
    await callback.message.edit_text(
        f"⏳ Memuat kecamatan di <b>{kab_name}</b>...",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ KEMBALI", callback_data=f"prov_{prov_id}_{prov_name}")]])
    )
    districts = await fetch_wilayah(f"districts/{kab_id}.json")
    if not districts:
        await callback.message.edit_text("❌ Gagal memuat kecamatan.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="kota_add")]]))
        return
    await state.update_data(kab_id=kab_id, kab_name=kab_name, selected_kec=[], districts_cache=districts)
    buttons = []
    for dist in districts[:60]:
        buttons.append([InlineKeyboardButton(text=f"⬜ {dist['name']}", callback_data=f"kec_toggle_{dist['id']}_{dist['name']}")])  # 1 KOLOM
    buttons.append([InlineKeyboardButton(text="💾 SIMPAN 0 KECAMATAN", callback_data="kec_save")])
    buttons.append([InlineKeyboardButton(text=f"⬅️ KEMBALI KE {prov_name}", callback_data=f"prov_{prov_id}_{prov_name}")])
    buttons.append([InlineKeyboardButton(text="❌ BATAL", callback_data="kota_list")])
    await callback.message.edit_text(
        f"🏘️ <b>{kab_name}, {prov_name} - PILIH KECAMATAN ({len(districts)})</b>\n\n✅ Bisa pilih lebih dari 3 kecamatan. Klik untuk pilih/hapus, lalu SIMPAN:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("kec_toggle_"))
async def kecamatan_toggle(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 3)
    if len(parts) < 4:
        await callback.answer("Data tidak valid")
        return
    kec_id = parts[2]
    kec_name = parts[3]
    data = await state.get_data()
    selected = data.get("selected_kec", [])
    prov_name = data.get("provinsi_name", "")
    prov_id = data.get("provinsi_id", "")
    kab_name = data.get("kab_name", "")
    districts = data.get("districts_cache", [])
    existing_idx = next((i for i, x in enumerate(selected) if x['id'] == kec_id), None)
    if existing_idx is not None:
        selected.pop(existing_idx)
    else:
        selected.append({"id": kec_id, "name": kec_name})
    await state.update_data(selected_kec=selected)
    buttons = []
    selected_ids = {x['id'] for x in selected}
    for dist in districts[:60]:
        prefix = "✅" if dist['id'] in selected_ids else "⬜"
        buttons.append([InlineKeyboardButton(text=f"{prefix} {dist['name']}", callback_data=f"kec_toggle_{dist['id']}_{dist['name']}")])
    count = len(selected)
    save_text = f"💾 SIMPAN {count} KECAMATAN" if count>0 else "💾 SIMPAN 0 KECAMATAN"
    daftar = ", ".join([x['name'] for x in selected[:5]]) if selected else "Belum ada"
    if count > 5:
        daftar += f" +{count-5} lainnya"
    buttons.append([InlineKeyboardButton(text=save_text, callback_data="kec_save")])
    buttons.append([InlineKeyboardButton(text=f"⬅️ KEMBALI KE {prov_name[:20]}", callback_data=f"prov_{prov_id}_{prov_name}")])
    buttons.append([InlineKeyboardButton(text="❌ BATAL", callback_data="kota_list")])
    await callback.message.edit_text(
        f"🏘️ <b>{kab_name}, {prov_name} - PILIH KECAMATAN</b>\n\n📍 Terpilih ({count}): {daftar}\n\nKlik untuk pilih/hapus, lalu SIMPAN:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer(f"{'Ditambahkan' if kec_id in selected_ids else 'Dihapus'}: {kec_name}")


@dp.callback_query(F.data == "kec_save")
async def kecamatan_save(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_kec", [])
    if not selected:
        await callback.answer("❌ Belum ada yang dipilih! Pilih minimal 1.", show_alert=True)
        return
    prov_name = data.get("provinsi_name", "")
    prov_id = data.get("provinsi_id", "")
    kab_name = data.get("kab_name", "")
    kab_id = data.get("kab_id", "")
    conn = db()
    now = datetime.now().isoformat()
    inserted = 0
    for kec in selected:
        conn.execute("INSERT INTO user_kota(telegram_id,provinsi,provinsi_id,kab,kab_id,kec,kec_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (callback.from_user.id, prov_name, prov_id, kab_name, kab_id, kec['name'], kec['id'], now))
        inserted += 1
    conn.commit()
    conn.close()
    if callback.from_user.id not in ADMIN_IDS and not is_admin(callback.from_user.id):
        use_kota_quota(callback.from_user.id)
    quota_sisa = get_kota_quota(callback.from_user.id)
    daftar_text = "\n".join([f"• {x['name']}" for x in selected])
    await callback.message.edit_text(
        f"✅ <b>{inserted} KECAMATAN BERHASIL DITAMBAHKAN</b>\n\n📍 Provinsi: <b>{prov_name}</b>\n🏛️ Kab/Kota: <b>{kab_name}</b>\n🏘️ Kecamatan:\n{daftar_text}\n\n🎟️ Sisa Quota: <b>{quota_sisa}</b>\n\n💬 <i>Notifikasi WA akan dikirim ke nomor kamu untuk kota {kab_name}!</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🌆 LIHAT KOTA SAYA", callback_data="kota_list")],
            [InlineKeyboardButton(text="🏙️ TAMBAH LAGI", callback_data="kota_add")],
            [InlineKeyboardButton(text="📱 SET NOMOR WA", callback_data="set_wa_number")],
            [InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]
        ])
    )
    await state.update_data(selected_kec=[], districts_cache=[])
    await callback.answer(f"✅ {inserted} kecamatan disimpan!")
    
    # Kirim notif WA otomatis via WABLAS / GREEN API - kota yang dipilih user
    try:
        conn2 = db()
        user_wa = conn2.execute("SELECT wa_number FROM users WHERE telegram_id=?", (callback.from_user.id,)).fetchone()
        conn2.close()
        if user_wa and user_wa['wa_number']:
            kota_full = f"{kab_name} - {', '.join([x['name'] for x in selected[:3]])}"
            send_wa_notif_background(user_wa['wa_number'], kota_full, callback.from_user.full_name)  # background biar satset
    except Exception as e:
        print(f"Notif WA kota gagal: {e}")


@dp.callback_query(F.data.startswith("kec_"))
async def kecamatan_selected(callback: CallbackQuery, state: FSMContext):
    await kecamatan_toggle(callback, state)

@dp.callback_query(F.data == "kota_list")
@dp.callback_query(F.data == "kota_saya")
async def kota_list(callback: CallbackQuery):
    conn = db()
    rows = conn.execute("SELECT * FROM user_kota WHERE telegram_id=? ORDER BY created_at DESC LIMIT 30", (callback.from_user.id,)).fetchall()
    user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (callback.from_user.id,)).fetchone()
    quota_row = conn.execute("SELECT * FROM kota_quota WHERE telegram_id=?", (callback.from_user.id,)).fetchone()
    sub = conn.execute("SELECT * FROM subscriptions WHERE telegram_id=?", (callback.from_user.id,)).fetchone()
    conn.close()
    quota = quota_row["quota"] if quota_row else 0
    total_used = quota_row["total_used"] if quota_row else 0
    balance = user["balance"] if user else 0
    username = user["username"] if user and user["username"] else callback.from_user.full_name
    if sub:
        try:
            start_dt = datetime.fromisoformat(sub["start_date"]) if sub["start_date"] else None
            exp_dt = datetime.fromisoformat(sub["expiry_date"]) if sub["expiry_date"] else None
        except:
            start_dt = None
            exp_dt = None
        if sub["status"] == "unlimited":
            aktif_str = start_dt.strftime("%d-%m-%Y") if start_dt else "-"
            expired_str = "SELAMANYA ♾️"
            status_langganan = "🟢 UNLIMITED AKTIF"
        elif exp_dt:
            aktif_str = start_dt.strftime("%d-%m-%Y") if start_dt else "-"
            expired_str = exp_dt.strftime("%d-%m-%Y")
            is_active = datetime.now() < exp_dt
            status_langganan = "🟢 AKTIF" if is_active else "🔴 EXPIRED"
        else:
            aktif_str = "-"
            expired_str = "-"
            status_langganan = "🔴 TIDAK AKTIF"
        paket = sub["package_name"] or "-"
    else:
        aktif_str = "-"
        expired_str = "-"
        status_langganan = "🔴 BELUM LANGGANAN"
        paket = "-"
    if not rows:
        await callback.message.edit_text(
            f"🌆 <b>KOTA SAYA - {html.escape(username)}</b>\n\n"
            f"👤 User: <b>{html.escape(username)}</b>\n"
            f"🆔 ID: <code>{callback.from_user.id}</code>\n"
            f"💰 Saldo: <b>{rupiah(balance)}</b>\n"
            f"📦 Paket: <b>{paket}</b>\n"
            f"📅 Aktif: <b>{aktif_str}</b>\n"
            f"⏰ Expired: <b>{expired_str}</b>\n"
            f"📊 Status: {status_langganan}\n"
            f"🎟️ Sisa Kuota: <b>{quota} kali</b>\n"
            f"📊 Total Dipilih: <b>{total_used} kota</b>\n\n"
            "❌ Belum ada kota yang dipilih.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏙️ TAMBAH KOTA", callback_data="kota_add")],
                [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")]
            ])
        )
        await callback.answer()
        return
    text = f"🌆 <b>KOTA SAYA - {html.escape(username)}</b>\n\n"
    text += f"👤 User: <b>{html.escape(username)}</b>\n🆔 ID: <code>{callback.from_user.id}</code>\n💰 Saldo: <b>{rupiah(balance)}</b>\n📦 Paket: <b>{paket}</b>\n📅 Aktif: <b>{aktif_str}</b>\n⏰ Expired: <b>{expired_str}</b>\n📊 Status: {status_langganan}\n🎟️ Sisa Kuota: <b>{quota} kali</b> | Dipakai: {total_used}x\n📍 Total Kota: <b>{len(rows)} kota</b>\n\n📋 <b>DAFTAR KOTA & KECAMATAN:</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    buttons = []
    for i, r in enumerate(rows, 1):
        provinsi = r['provinsi'] or "-"
        kab = r['kab'] or "-"
        kec = r['kec'] or "-"
        tgl_pilih = r['created_at'][:10] if r['created_at'] else "-"
        text += f"{i}. <b>{provinsi}</b>\n   🏛️ {kab} > 🏘️ {kec}\n   📅 Pilih: {tgl_pilih} | Exp: {expired_str}\n\n"
        buttons.append([InlineKeyboardButton(text=f"🗑️ HAPUS {i}. {kec} - {kab[:15]}", callback_data=f"kota_del_{r['id']}")])
    buttons.append([InlineKeyboardButton(text="🏙️ TAMBAH KOTA BARU", callback_data="kota_add")])
    buttons.append([InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")])
    if len(text) > 3800:
        text = text[:3800] + "\n... dipotong"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()

@dp.callback_query(F.data.startswith("kota_del_"))
async def kota_delete(callback: CallbackQuery):
    kota_id = int(callback.data.split("_")[-1])
    conn = db()
    conn.execute("DELETE FROM user_kota WHERE id=? AND telegram_id=?", (kota_id, callback.from_user.id))
    conn.commit()
    conn.close()
    await callback.answer("🗑️ Dihapus")
    await kota_list(callback)

@dp.callback_query(F.data == "kota_search_lain")
async def kota_search_lain_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(KotaState.waiting_cari_lainnya)
    await callback.message.edit_text(
        "🔎 <b>CARI DATA LAINNYA</b>\n\n"
        "📍 <b>MASUKAN NAMA KOTA</b>\n"
        "💡 Contoh: <code>BANDUNG</code>\n\n"
        "✍️ Ketik kota yang mau dicari\n"
        "Bot akan cari di history WA yang dishare pengirim!\n\n"
        "❌ Ketik /batal untuk batal.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ BATAL", callback_data="back_main")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "contact_admin")
async def contact_admin(callback: CallbackQuery):
    await callback.message.edit_text(
        "📞 <b>HUBUNGI ADMIN</b>\n\n"
        "Jika membutuhkan bantuan, silakan hubungi Admin:\n\n"
        "👤 <b>Telegram</b>\n"
        "@Hambali1995\n\n"
        "📱 <b>WhatsApp</b>\n"
        "083160776091",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 CHAT TELEGRAM", url="https://t.me/Hambali1995")],
            [InlineKeyboardButton(text="📱 CHAT WHATSAPP", url="https://wa.me/6283160776091")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
    )
    await callback.answer()


# ==================== ADMIN PANEL ====================

@dp.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Kamu bukan Admin.")
        return
    await state.clear()
    await message.answer("🔐 <b>PANEL ADMIN</b>\n\nPilih menu:", reply_markup=admin_menu())


@dp.callback_query(F.data == "admin_cancel")
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🔐 <b>PANEL ADMIN</b>\n\nPilih menu:",
        reply_markup=admin_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Kamu bukan Admin.", show_alert=True)
        return
    await callback.message.edit_text("🔐 <b>PANEL ADMIN</b>\n\nPilih menu:", reply_markup=admin_menu())
    await callback.answer()


@dp.callback_query(F.data == "admin_active")
async def admin_active(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin.", show_alert=True)
        return

    conn = db()
    rows = conn.execute("""
        SELECT u.telegram_id,u.name,u.username,s.package_name,s.expiry_date,s.status
        FROM users u JOIN subscriptions s ON s.telegram_id=u.telegram_id
        WHERE s.status='unlimited' OR (s.status='active' AND s.expiry_date>?)
        ORDER BY u.telegram_id
    """, (datetime.now().isoformat(),)).fetchall()
    conn.close()

    if not rows:
        text = "👥 <b>USER AKTIF</b>\n\nTidak ada user aktif."
    else:
        text = "👥 <b>USER AKTIF</b>\n\n" + "\n\n".join(
            f"👤 {r['name']}\n🆔 <code>{r['telegram_id']}</code>\n"
            f"📦 {r['package_name']}\n"
            f"📅 {'SELAMANYA' if r['status']=='unlimited' else r['expiry_date'][:10]}"
            for r in rows
        )
        text += f"\n\n📊 Total user aktif: <b>{len(rows)}</b>"

    await callback.message.edit_text(text[:3900], reply_markup=admin_menu())
    await callback.answer()


@dp.callback_query(F.data == "admin_pending")
async def admin_pending(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin.", show_alert=True)
        return

    conn = db()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE status='pending' ORDER BY id DESC"
    ).fetchall()
    conn.close()

    if not rows:
        await callback.message.edit_text(
            "💰 <b>TRANSAKSI PENDING</b>\n\nTidak ada transaksi pending.",
            reply_markup=admin_menu()
        )
        await callback.answer()
        return

    for row in rows:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ SETUJU", callback_data=f"approve_{row['id']}"),
            InlineKeyboardButton(text="❌ TOLAK", callback_data=f"reject_{row['id']}")
        ]])
        caption = (
            "💰 <b>TRANSAKSI PENDING</b>\n\n"
            f"🧾 #{row['id']}\n🆔 <code>{row['telegram_id']}</code>\n"
            f"💰 {rupiah(row['amount'])}\n📦 {row['package_name'] or 'TOP UP SALDO'}"
        )
        if row["proof_file_id"]:
            await callback.bot.send_photo(callback.from_user.id, row["proof_file_id"],
                                          caption=caption, reply_markup=keyboard)
        else:
            await callback.bot.send_message(callback.from_user.id, caption, reply_markup=keyboard)

    await callback.message.edit_text(
        f"💰 <b>TRANSAKSI PENDING</b>\n\nDitemukan <b>{len(rows)}</b> transaksi.",
        reply_markup=admin_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_add_balance")
async def admin_add_balance_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminState.waiting_user_amount)
    await state.update_data(action="add")
    await callback.message.edit_text(
        "➕ <b>TAMBAH SALDO</b>\n\nKirim:\n<code>ID_USER NOMINAL</code>\n\nContoh: <code>123456789 50000</code>",
        reply_markup=admin_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_sub_balance")
async def admin_sub_balance_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminState.waiting_user_amount)
    await state.update_data(action="sub")
    await callback.message.edit_text(
        "➖ <b>KURANGI SALDO</b>\n\nKirim:\n<code>ID_USER NOMINAL</code>\n\nContoh: <code>123456789 50000</code>",
        reply_markup=admin_menu()
    )
    await callback.answer()


@dp.message(AdminState.waiting_user_amount, F.text)
async def admin_balance_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    data = await state.get_data()
    parts = message.text.strip().split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer("❌ Format salah. Contoh: <code>123456789 50000</code>")
        return

    user_id, amount = int(parts[0]), int(parts[1])
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        await message.answer("❌ User tidak ditemukan.")
        return

    if data.get("action") == "add":
        conn.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (amount, user_id))
        # TAMBAH QUOTA KOTA 2x SETIAP TOPUP
        try:
            conn.execute("INSERT INTO kota_quota(telegram_id,quota,total_used) VALUES(?,?,0) ON CONFLICT(telegram_id) DO UPDATE SET quota=quota+2", (user_id, 2))
        except Exception as e:
            logging.error(f"Quota add error: {e}")
        action_text = f"➕ Ditambah {rupiah(amount)} + 🎟️ Quota Kota 2x"
    else:
        conn.execute("UPDATE users SET balance=MAX(balance-?,0) WHERE telegram_id=?", (amount, user_id))
        action_text = f"➖ Dikurangi {rupiah(amount)}"

    new_balance = conn.execute(
        "SELECT balance FROM users WHERE telegram_id=?", (user_id,)
    ).fetchone()["balance"]
    conn.commit()
    conn.close()
    await state.clear()

    await message.answer(
        "✅ <b>BERHASIL</b>\n\n"
        f"👤 User : <code>{user_id}</code>\n{action_text}\n"
        f"💰 Saldo sekarang : <b>{rupiah(new_balance)}</b>",
        reply_markup=admin_menu()
    )
    try:
        await message.bot.send_message(
            user_id,
            "💰 <b>PERUBAHAN SALDO</b>\n\n"
            f"{action_text}\nSaldo sekarang : <b>{rupiah(new_balance)}</b>"
        )
    except Exception:
        pass


@dp.callback_query(F.data == "admin_delete_user")
async def admin_delete_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminState.waiting_delete_user)
    await callback.message.edit_text(
        "🗑️ <b>HAPUS USER</b>\n\nKirim Telegram ID user.\nContoh: <code>123456789</code>",
        reply_markup=admin_menu()
    )
    await callback.answer()


@dp.message(AdminState.waiting_delete_user, F.text)
async def admin_delete_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text.strip().isdigit():
        await message.answer("❌ Telegram ID harus angka.")
        return

    user_id = int(message.text.strip())
    if user_id in ADMIN_IDS:
        await message.answer("❌ User Admin tidak boleh dihapus.")
        return

    conn = db()
    user = conn.execute("SELECT telegram_id FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        await message.answer("❌ User tidak ditemukan.")
        return

    for table in ["users", "subscriptions", "transactions", "format_settings", "format_results", "format_history"]:
        conn.execute(f"DELETE FROM {table} WHERE telegram_id=?", (user_id,))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(
        f"🗑️ User <code>{user_id}</code> berhasil dihapus.",
        reply_markup=admin_menu()
    )


@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminState.waiting_broadcast)
    await callback.message.edit_text(
        "📢 <b>BROADCAST</b>\n\nKirim pesan/foto/dokumen yang ingin dikirim ke semua user.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ BATAL", callback_data="admin_cancel")]
        ])
    )
    await callback.answer()


@dp.message(AdminState.waiting_broadcast)
async def admin_broadcast_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    conn = db()
    users = conn.execute("SELECT telegram_id FROM users").fetchall()
    conn.close()

    success = failed = 0
    for row in users:
        try:
            if message.text:
                await message.bot.send_message(row["telegram_id"], message.text)
            elif message.photo:
                await message.bot.send_photo(row["telegram_id"], message.photo[-1].file_id, caption=message.caption or "")
            elif message.document:
                await message.bot.send_document(row["telegram_id"], message.document.file_id, caption=message.caption or "")
            else:
                failed += 1
                continue
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await state.clear()
    await message.answer(
        "📢 <b>BROADCAST SELESAI</b>\n\n"
        f"✅ Berhasil : {success}\n❌ Gagal : {failed}",
        reply_markup=admin_menu()
    )



# ==================== BLACKLIST FEATURE ====================
@dp.callback_query(F.data == "blacklist_view")
async def blacklist_view_user(callback: CallbackQuery):
    conn = db()
    rows = conn.execute("SELECT * FROM blacklist_numbers ORDER BY created_at DESC LIMIT 100").fetchall()
    conn.close()
    if not rows:
        text = "🚫 <b>NO BLACKLIST</b>\n\nBelum ada nomor blacklist.\nSemua nomor aman!"
    else:
        text = f"🚫 <b>NO BLACKLIST - {len(rows)} Nomor</b>\n\n"
        text += "Daftar nomor blacklist (tidak bisa dipakai):\n━━━━━━━━━━━━\n"
        for i, r in enumerate(rows, 1):
            text += f"{i}. <code>{r['number']}</code>\n"
        if len(rows) >= 100:
            text += "\n... (hanya 100 terbaru)"
    await callback.message.edit_text(
        text[:3800],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 CARI NOMOR", callback_data="blacklist_search")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "blacklist_search")
async def blacklist_search_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AdminState.waiting_blacklist_add)  # reuse state but for search we use different logic - create new state quickly
    # Use simple state via waiting_cari_lainnya? We'll use custom
    await callback.message.edit_text(
        "🔍 <b>CARI BLACKLIST</b>\n\n"
        "Kirim nomor yang mau dicek aman atau blacklist.\n\n"
        "📌 Contoh: <code>081345678877</code>\n\n"
        "Bot akan cek apakah nomor itu ada di blacklist atau aman dipakai.\n\n"
        "Ketik nomor sekarang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="blacklist_view")]
        ])
    )
    await callback.answer()
    # Set to waiting state for search
    await state.set_state(KotaState.waiting_cari_lainnya)  # reuse, but we will handle in separate handler below - better create new state
    # Actually we create new handler for blacklist check via message handler below, so set flag
    await state.update_data(is_blacklist_search=True)

@dp.message(KotaState.waiting_cari_lainnya, F.text)
async def blacklist_search_check(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("is_blacklist_search"):
        # bukan pencarian blacklist, biar handler lama yang handle - we skip by returning and letting other handler? 
        # But we have duplicate decorator - need to handle both in one function, so we check
        # If is_blacklist_search False, process as kota search (original logic) - we will reimplement original kota search logic here
        # Untuk simplify, jika bukan blacklist search, lanjutkan ke logic kota search lama
        query = message.text.strip()
        if query.lower() in ["/batal", "batal", "/cancel"]:
            await state.clear()
            await message.answer("❌ Pencarian dibatalkan.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]]))
            return
        if len(query) < 2:
            await message.answer("❌ Minimal 2 huruf. Contoh: <code>BANDUNG</code>\nKetik /batal untuk batal.")
            return
        like = f"%{query}%"
        conn = db()
        format_rows = conn.execute("SELECT * FROM format_results WHERE telegram_id=? AND result_text LIKE ? ORDER BY created_at DESC LIMIT 20", (message.from_user.id, like)).fetchall()
        history_rows = conn.execute("SELECT * FROM format_history WHERE telegram_id=? AND result_text LIKE ? ORDER BY created_at DESC LIMIT 20", (message.from_user.id, like)).fetchall()
        kota_rows = conn.execute("SELECT * FROM user_kota WHERE telegram_id=? AND (provinsi LIKE ? OR kab LIKE ? OR kec LIKE ?) ORDER BY created_at DESC LIMIT 10", (message.from_user.id, like, like, like)).fetchall()
        conn.close()
        await state.clear()
        total = len(format_rows) + len(history_rows) + len(kota_rows)
        if total == 0:
            await message.answer(f"🔎 <b>HASIL CARI: {query.upper()}</b>\n\n❌ Tidak ada riwayat dengan kota <b>{query.upper()}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔍 CARI LAGI", callback_data="kota_search_lain")],[InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]]))
            return
        text = f"🔎 <b>HASIL CARI: {query.upper()} - {total} DATA</b>\n\n"
        if kota_rows:
            text += f"🌆 <b>KOTA SAYA ({len(kota_rows)}):</b>\n"
            for r in kota_rows:
                text += f"• {r['provinsi']} > {r['kab']} > {r['kec']}\n"
            text += "\n"
        if format_rows:
            text += f"📄 <b>HASIL TERKINI ({len(format_rows)}):</b>\n"
            for i, r in enumerate(format_rows[:5], 1):
                snippet = r['result_text'][:80].replace('\n', ' ')
                text += f"{i}. {snippet}...\n"
        if len(text) > 3500:
            text = text[:3500] + "\n... dipotong"
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔍 CARI LAGI", callback_data="kota_search_lain")],[InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]]))
        return

    # Ini adalah pencarian blacklist
    nomor = normalize_number(message.text.strip())
    if not nomor:
        await message.answer("❌ Nomor tidak valid. Kirim angka minimal 8 digit. Contoh: 081345678877")
        return
    conn = db()
    row = conn.execute("SELECT * FROM blacklist_numbers WHERE number=?", (nomor,)).fetchone()
    conn.close()
    await state.clear()
    if row:
        await message.answer(
            f"🚫 <b>NOMOR BLACKLIST!</b>\n\n"
            f"📞 Nomor: <code>{nomor}</code>\n"
            f"📅 Ditambahkan: {row['created_at'][:16]}\n"
            f"⚠️ Status: <b>BLACKLIST - TIDAK AMAN</b>\n\n"
            "❌ Nomor ini tidak bisa dipakai untuk format. Silakan pakai nomor lain.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 CARI LAGI", callback_data="blacklist_search")],
                [InlineKeyboardButton(text="📋 LIHAT BLACKLIST", callback_data="blacklist_view")],
                [InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]
            ])
        )
    else:
        await message.answer(
            f"✅ <b>NOMOR AMAN!</b>\n\n"
            f"📞 Nomor: <code>{nomor}</code>\n"
            f"✅ Status: <b>AMAN - TIDAK DI BLACKLIST</b>\n\n"
            "✅ Nomor ini aman dipakai untuk format.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 CARI LAGI", callback_data="blacklist_search")],
                [InlineKeyboardButton(text="📋 LIHAT BLACKLIST", callback_data="blacklist_view")],
                [InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]
            ])
        )



@dp.callback_query(F.data == "admin_blacklist_view")
async def admin_blacklist_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin", show_alert=True)
        return
    conn = db()
    rows = conn.execute("SELECT * FROM blacklist_numbers ORDER BY created_at DESC LIMIT 200").fetchall()
    conn.close()
    if not rows:
        text = "📋 <b>LIST BLACKLIST ADMIN</b>\n\nKosong."
    else:
        text = f"📋 <b>LIST BLACKLIST - {len(rows)} Nomor</b>\n\n"
        for i, r in enumerate(rows[:50], 1):
            text += f"{i}. <code>{r['number']}</code>\n"
        if len(rows) > 50:
            text += f"\n... dan {len(rows)-50} lainnya"
    await callback.message.edit_text(text[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ TAMBAH", callback_data="admin_blacklist_add"), InlineKeyboardButton(text="🗑️ HAPUS", callback_data="admin_blacklist_del")],[InlineKeyboardButton(text="⬅️ ADMIN", callback_data="admin_panel")]]))
    await callback.answer()

@dp.callback_query(F.data == "admin_blacklist_add")
async def admin_blacklist_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminState.waiting_blacklist_add)
    await callback.message.edit_text("🚫 <b>TAMBAH BLACKLIST</b>\n\nKirim banyak nomor sekaligus, per baris:\n<code>081345678877\n014488584878</code>\n\nAtau pakai format:\n<code>/Adds\n081345678877\n0812...</code>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ BATAL", callback_data="admin_cancel")]]))
    await callback.answer()

@dp.message(AdminState.waiting_blacklist_add, F.text)
async def admin_blacklist_add_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    text = re.sub(r'^/Adds\s*', '', message.text.strip(), flags=re.IGNORECASE)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    numbers = []
    for line in lines:
        parts = re.split(r'[\s,]+', line)
        for p in parts:
            norm = normalize_number(p)
            if norm:
                numbers.append(norm)
    if not numbers:
        await message.answer("❌ Tidak ada nomor valid.")
        return
    conn = db()
    added = 0
    dup = 0
    for num in numbers:
        try:
            conn.execute("INSERT INTO blacklist_numbers(number,added_by,created_at) VALUES(?,?,?)", (num, message.from_user.id, datetime.now().isoformat()))
            added += 1
        except sqlite3.IntegrityError:
            dup += 1
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(f"✅ Ditambahkan {added}, duplikat {dup}. Total: {get_blacklist_count()}", reply_markup=admin_menu())

@dp.message(Command("Adds"))
async def cmd_adds(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Hanya admin")
        return
    after = message.text[5:].strip()
    if not after:
        await message.answer("Format:\n/Adds\n0813...\n0812...")
        return
    numbers = []
    for line in after.splitlines():
        for p in re.split(r'[\s,]+', line.strip()):
            norm = normalize_number(p)
            if norm:
                numbers.append(norm)
    conn = db()
    added = 0
    dup = 0
    for num in numbers:
        try:
            conn.execute("INSERT INTO blacklist_numbers(number,added_by,created_at) VALUES(?,?,?)", (num, message.from_user.id, datetime.now().isoformat()))
            added += 1
        except sqlite3.IntegrityError:
            dup += 1
    conn.commit()
    conn.close()
    await message.answer(f"✅ /Adds: {added} ditambah, {dup} duplikat. Total: {get_blacklist_count()}")

@dp.callback_query(F.data == "admin_blacklist_del")
async def admin_blacklist_del_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminState.waiting_blacklist_del)
    conn = db()
    rows = conn.execute("SELECT * FROM blacklist_numbers ORDER BY created_at DESC LIMIT 20").fetchall()
    conn.close()
    txt = "🗑️ <b>HAPUS BLACKLIST</b>\n\n"
    if rows:
        for r in rows:
            txt += f"• <code>{r['number']}</code>\n"
        txt += "\nKirim nomor yang mau dihapus (bisa banyak baris) atau /all untuk hapus semua"
    else:
        txt += "Belum ada blacklist"
    await callback.message.edit_text(txt[:3500], reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ BATAL", callback_data="admin_cancel")]]))
    await callback.answer()

@dp.message(AdminState.waiting_blacklist_del, F.text)
async def admin_blacklist_del_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    txt_msg = message.text.strip()
    if txt_msg.lower() == "/all":
        conn = db()
        conn.execute("DELETE FROM blacklist_numbers")
        conn.commit()
        conn.close()
        await state.clear()
        await message.answer("🗑️ Semua blacklist dihapus!", reply_markup=admin_menu())
        return
    numbers = []
    for line in txt_msg.splitlines():
        for p in re.split(r'[\s,]+', line.strip()):
            norm = normalize_number(p)
            if norm:
                numbers.append(norm)
    conn = db()
    deleted = 0
    for num in numbers:
        cur = conn.execute("DELETE FROM blacklist_numbers WHERE number=?", (num,))
        if cur.rowcount>0:
            deleted+=1
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(f"🗑️ Dihapus {deleted}. Sisa: {get_blacklist_count()}", reply_markup=admin_menu())

# ==================== END BLACKLIST ====================

# ==================== SET WA NUMBER & NOTIF KOTA ====================
class WaState(StatesGroup):
    waiting_wa_number = State()

@dp.callback_query(F.data == "set_wa_number")
async def set_wa_number_start(callback: CallbackQuery, state: FSMContext):
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (callback.from_user.id,)).fetchone()
    conn.close()
    current_wa = user['wa_number'] if user and user['wa_number'] else "Belum diset"
    await state.clear()
    await state.set_state(WaState.waiting_wa_number)
    await callback.message.edit_text(
        f"📱 <b>SET NOMOR WHATSAPP</b>\n\n"
        f"Nomor WA sekarang: <code>{current_wa}</code>\n\n"
        f"Kirim nomor WA kamu untuk notifikasi kota yang dipilih:\n"
        f"Contoh: <code>081234567890</code>\n\n"
        f"Bot baru ini khusus FONNTE 35RB - SATSET 0.5 detik! (tanpa Green API/WABLAS)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ BATAL", callback_data="kota_list")]
        ])
    )
    await callback.answer()

@dp.message(WaState.waiting_wa_number, F.text)
async def set_wa_number_process(message: Message, state: FSMContext):
    raw = message.text.strip()
    # Normalisasi nomor
    num = re.sub(r'[^0-9]', '', raw)
    if num.startswith('0'):
        num = '62' + num[1:]
    elif num.startswith('62'):
        pass
    else:
        num = '62' + num
    
    if len(num) < 10 or len(num) > 15:
        await message.answer("❌ Nomor tidak valid. Contoh: 081234567890")
        return
    
    conn = db()
    conn.execute("UPDATE users SET wa_number=? WHERE telegram_id=?", (num, message.from_user.id))
    # Juga simpan di tabel user_wa_numbers
    conn.execute("INSERT INTO user_wa_numbers(telegram_id,wa_number,updated_at) VALUES(?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET wa_number=excluded.wa_number, updated_at=excluded.updated_at",
                 (message.from_user.id, num, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    await state.clear()
    
    # Test kirim notif via FONNTE khusus bot baru SATSET
    test_kota = "TEST - Notif WA berhasil!"
    try:
        await send_wa_notif_kota(num, test_kota, message.from_user.full_name)  # test pakai await biar keliatan hasilnya
        await message.answer(
            f"✅ <b>Nomor WA berhasil disimpan!</b>\n\n📱 Nomor: <code>{num}</code>\n\n✅ Test notifikasi WA sudah dikirim via FONNTE SATSET!\nCek WA kamu untuk kota yang dipilih nanti akan otomatis dapat notif.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌆 KOTA SAYA", callback_data="kota_saya")],
                [InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]
            ])
        )
    except Exception as e:
        await message.answer(
            f"✅ Nomor WA disimpan: <code>{num}</code>\n\n⚠️ Test notif gagal: {e}\nPastikan FONNTE_TOKEN sudah diset di Railway Variables!",
            reply_markup=main_menu()
        )


@dp.message(F.text)
async def fallback(message: Message):
    # Masalah JMO tetap dijawab walaupun user mengetik tanpa membuka menu
    words = ["jmo", "error", "kode", "025", "026", "027", "028", "029", "030", "031", "032", "033", 
             "login", "verifikasi", "kpj", "jht", "otp", "wajah", "password", "email", "aktivasi", 
             "klaim", "bpu", "kamera", "server", "lemot", "notifikasi", "daftar", "cair"]
    if any(w in message.text.lower() for w in words):
        await message.answer(get_jmo_solution(message.text))
    else:
        await message.answer("Silakan gunakan menu utama dengan /start", reply_markup=main_menu())


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    try:
        init_database()
        logging.info(f"✅ Database siap: {DB_PATH}")
    except Exception:
        logging.exception("❌ Gagal inisialisasi database")
        raise
    logging.info("🤖 JMO BOT V2 - FILTER 2 KUNCI + FOOTER REKBER - NO ADMIN NOTIF")
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    app = web.Application()
    app['bot'] = bot
    app.router.add_post('/webhook/fonnte', fonnte_webhook)
    app.router.add_post('/webhook', fonnte_webhook)
    app.router.add_get('/', lambda r: web.json_response({"status": "ok", "filter": "KOTA+KEC WAJIB 2 KUNCI - FOOTER REKBER - NO ADMIN NOTIF"}))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8000"))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"🌐 Webhook jalan di port {port}")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())

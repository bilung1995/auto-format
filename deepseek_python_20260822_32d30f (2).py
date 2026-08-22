import asyncio
import logging
import html
import os
import sqlite3
import re
import time
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
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
# Admin IDs: dukung koma/spasi/baris baru seperti bot lama.
_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = {int(x) for x in re.findall(r"\d+", _raw_admin_ids) if int(x) > 0}
# Chat tambahan untuk menerima notifikasi bukti (opsional).
_raw_admin_notify = os.getenv("ADMIN_NOTIFY_IDS", os.getenv("ADMIN_CHAT_ID", ""))
ADMIN_NOTIFY_IDS = {int(x) for x in re.findall(r"-?\d+", _raw_admin_notify) if x not in ("0", "-0")}
print(f"🔧 ADMIN_IDS RAW: '{_raw_admin_ids}' -> PARSED: {ADMIN_IDS}")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN belum diisi di Railway Variables")

# === WHATSAPP: GREEN-API ONLY ===
GREEN_API_ID = os.getenv("GREEN_API_ID", "710722716543").strip()

FOOTER_PERINGATAN = """━━━━━━━━━━━━━━━━━━━━
⚠️ PERHATIAN :
Tetap hati-hati dan waspada untuk lebih aman gunakan jasa rekber.Terimakasih🙏
Sumber: https//t.me/seduluranjht_bot"""

# ========== CACHE UNTUK USER KOTA ==========
_user_kota_cache = []
_cache_time = 0
_CACHE_TTL = 60  # 60 detik

def get_user_kota_cache():
    """Ambil data user_kota dengan cache untuk mempercepat"""
    global _user_kota_cache, _cache_time
    now = time.time()
    if now - _cache_time < _CACHE_TTL:
        return _user_kota_cache
    
    try:
        conn = db()
        rows = conn.execute("""
            SELECT DISTINCT telegram_id, kab, kec, provinsi 
            FROM user_kota 
            WHERE kab IS NOT NULL AND kab != '' 
            AND kec IS NOT NULL AND kec != ''
        """).fetchall()
        conn.close()
        
        _user_kota_cache = [dict(r) for r in rows]
        _cache_time = now
        return _user_kota_cache
    except Exception as e:
        print(f"Cache error: {e}")
        return []

# ========== FILTER 2 KUNCI WAJIB: KOTA + KECAMATAN ==========
def _norm_region_name(value: str):
    value = re.sub(r"[^A-Z0-9 ]+", " ", str(value or "").upper())
    value = re.sub(r"\b(KOTA|KABUPATEN|KAB|KECAMATAN|KEC)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def parse_kota_kec_from_text(text: str):
    if not text:
        return None, None
    lines = [l.strip() for l in str(text).splitlines() if l.strip()]
    kab = kec = None
    # Format eksplisit: KAB/KOTA dan KEC/KECAMATAN.
    m_kab = re.search(r"(?:KABUPATEN|KAB|KOTA)\s*[:\-]?\s*([^\n\r,|]+)", text, re.IGNORECASE)
    m_kec = re.search(r"(?:KECAMATAN|KEC)\s*[:\-]?\s*([^\n\r,|]+)", text, re.IGNORECASE)
    if m_kab:
        kab = m_kab.group(1).strip().upper()
    if m_kec:
        kec = m_kec.group(1).strip().upper()

    # Format lama: kode pos -> kab/kota -> kecamatan.
    if not kab or not kec:
        for i, line in enumerate(lines):
            if re.match(r'^\d{4}$', line):
                if not kab and i + 1 < len(lines):
                    kab = lines[i + 1].strip().upper()
                if not kec and i + 2 < len(lines):
                    kec = lines[i + 2].strip().upper()
                break

    # Paling penting untuk bot baru: cocokkan langsung dengan semua lokasi user.
    try:
        conn = db()
        rows = conn.execute("SELECT DISTINCT kab,kec FROM user_kota").fetchall()
        conn.close()
        low = str(text).upper()
        for r in rows:
            db_kab = (r['kab'] or '').strip()
            db_kec = (r['kec'] or '').strip()
            if not db_kab or not db_kec:
                continue
            nkab = _norm_region_name(db_kab)
            nkec = _norm_region_name(db_kec)
            if nkab and nkec and re.search(r'(?<![A-Z0-9])' + re.escape(nkab) + r'(?![A-Z0-9])', low) and re.search(r'(?<![A-Z0-9])' + re.escape(nkec) + r'(?![A-Z0-9])', low):
                return db_kab, db_kec
    except Exception as e:
        print(f"parse DB check error: {e}")

    if kab and kec:
        kab = re.sub(r'[^A-Z0-9 ]', '', kab).strip()
        kec = re.sub(r'[^A-Z0-9 ]', '', kec).strip()
        return kab, kec
    return kab, kec

def find_users_by_kota_kec(kab: str, kec: str):
    """Cari user berdasarkan pasangan kab/kota + kecamatan yang tersimpan."""
    if not kab or not kec:
        return []
    try:
        conn = db()
        rows = conn.execute("SELECT DISTINCT telegram_id,kab,kec FROM user_kota").fetchall()
        conn.close()
        target_kab = _norm_region_name(kab)
        target_kec = _norm_region_name(kec)
        result = []
        for r in rows:
            rk = _norm_region_name(r['kab'])
            rc = _norm_region_name(r['kec'])
            if rk == target_kab and rc == target_kec:
                result.append(r['telegram_id'])
        return list(dict.fromkeys(result))
    except Exception as e:
        print(f"find_users_by_kota_kec error: {e}")
        return []

# ========== PERBAIKAN FUNGSI PENCARIAN USER ==========
def find_users_from_wa_message(wa_text: str):
    """Cocokkan langsung isi pesan WA dengan semua pasangan lokasi user.
    Perbaikan: lebih akurat dan cepat dengan cache."""
    text = str(wa_text or "").upper()
    if not text:
        return []
    
    rows = get_user_kota_cache()
    if not rows:
        return []
    
    matches = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    
    for r in rows:
        kab = (r.get('kab') or '').strip().upper()
        kec = (r.get('kec') or '').strip().upper()
        if not kab or not kec:
            continue
        
        # Cek apakah kab dan kec ada di teks
        kab_found = kab in text
        kec_found = kec in text
        
        # Cek juga apakah ada di baris terpisah
        if not kab_found or not kec_found:
            for ln in lines:
                if kab in ln:
                    kab_found = True
                if kec in ln:
                    kec_found = True
        
        if kab_found and kec_found:
            matches.append((r['telegram_id'], kab, kec))
    
    # Hapus duplikat
    return list(dict.fromkeys(matches))

# ========== FUNGSI FORMAT PESAN WA DENGAN TOMBOL ==========
def format_wa_message_for_display(wa_row, index):
    """Format pesan WA menjadi kotak dengan tombol Salin & Chat Pengirim"""
    wa_group = html.escape(str(wa_row.get('wa_group', '-'))[:50])
    wa_sender = html.escape(str(wa_row.get('wa_sender', '-'))[:50])
    wa_number = extract_number_from_sender(wa_row.get('wa_sender', ''))
    message = html.escape(str(wa_row.get('message', ''))[:500])
    created_at = wa_row.get('created_at', '')
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at)
            created_at = dt.strftime("%d/%m/%Y %H:%M")
        except:
            created_at = created_at[:16]
    
    # Format kotak pesan
    text = (
        f"📨 <b>PESAN #{index}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Grup: {wa_group}\n"
        f"👤 Pengirim: {wa_sender}\n"
        f"🕐 Waktu: {created_at}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{message}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    # Tombol inline
    buttons = []
    
    # Tombol Salin
    if message:
        buttons.append(InlineKeyboardButton(
            text="📋 SALIN PESAN", 
            callback_data=f"copy_wa_msg_{wa_row.get('id', 0)}"
        ))
    
    # Tombol Chat Pengirim (jika ada nomor)
    if wa_number and len(wa_number) >= 10:
        buttons.append(InlineKeyboardButton(
            text="💬 CHAT PENGIRIM", 
            url=f"https://wa.me/{wa_number}"
        ))
    
    # Tombol Salin Semua
    buttons.append(InlineKeyboardButton(
        text="📋 SALIN SEMUA", 
        callback_data=f"copy_wa_all_{wa_row.get('id', 0)}"
    ))
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
    
    return text, keyboard

def extract_number_from_sender(sender: str) -> str:
    """Ekstrak nomor WA dari sender string"""
    if not sender:
        return ""
    # Format: "6281234567890@c.us" atau "6281234567890"
    num = re.sub(r'[^0-9]', '', sender)
    if num.startswith('62'):
        num = '0' + num[2:]
    if len(num) >= 10:
        return num
    return ""

def group_wa_messages_by_sender(wa_rows):
    """Kelompokkan pesan WA berdasarkan pengirim"""
    grouped = {}
    for row in wa_rows:
        sender = row.get('wa_sender', '-')
        if sender not in grouped:
            grouped[sender] = []
        grouped[sender].append(row)
    return grouped

async def forward_wa_to_telegram(bot, wa_text: str, wa_group: str, wa_sender: str, 
                                  wa_number: str = "", parsed_kab: str = "", parsed_kec: str = ""):
    """Format notifikasi WA seperti bot lama dan kirim ke user yang lokasi cocok."""
    wa_text = str(wa_text or "").strip()
    wa_group = str(wa_group or "ZOLDYCK STORE").strip()
    wa_sender = str(wa_sender or "-").strip()
    wa_number = str(wa_number or "-").strip()

    # PENTING: Cari user berdasarkan pesan WA
    matched = find_users_from_wa_message(wa_text)
    
    # Jika tidak ketemu, coba pakai parsed kab/kec
    if not matched and parsed_kab and parsed_kec:
        matched = [(tid, parsed_kab, parsed_kec) for tid in find_users_by_kota_kec(parsed_kab, parsed_kec)]

    if not matched:
        print(f"⏭️ Tidak ada user cocok dari pesan WA: {wa_text[:100]}")
        return 0

    # Tambahkan footer jika belum ada
    clean_text = wa_text
    if "PERHATIAN" not in clean_text.upper():
        clean_text = f"{clean_text}\n\n{FOOTER_PERINGATAN}"
    clean_text = clean_text.replace("https//", "https://")

    # Kirim ke semua user yang cocok
    count = 0
    for tid, kab, kec in matched:
        safe_group = html.escape(wa_group[:120])
        safe_sender = html.escape(wa_sender[:120])
        safe_number = html.escape(wa_number[:80])
        safe_kab = html.escape(str(kab))
        safe_kec = html.escape(str(kec))
        safe_message = html.escape(clean_text[:3300])

        # FORMAT NOTIFIKASI YANG LEBIH JELAS
        final_text = (
            f"📩 <b>PESAN WHATSAPP MASUK</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 Grup: {safe_group}\n"
            f"👤 Pengirim: {safe_sender}\n"
            f"📍 Lokasi: {safe_kab} / {safe_kec}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{safe_message}"
        )
        try:
            await bot.send_message(tid, final_text[:4090])
            count += 1
            print(f"✅ WA -> Telegram {tid} MATCH {kab}/{kec}")
        except Exception as e:
            print(f"⚠️ Gagal kirim WA -> Telegram {tid}: {e}")
    
    return count


async def notify_custom_keywords(bot, wa_text: str, wa_group: str, wa_sender: str):
    """Kirim notifikasi Telegram instan untuk keyword custom yang cocok dengan pesan WA."""
    if not wa_text:
        return 0
    try:
        conn = db()
        rows = conn.execute("SELECT telegram_id, keyword FROM user_keywords ORDER BY id ASC").fetchall()
        conn.close()
    except Exception as e:
        print(f"keyword notify DB error: {e}")
        return 0
    text_upper = wa_text.upper()
    sent_users = set()
    count = 0
    for row in rows:
        tid = int(row["telegram_id"])
        keyword = (row["keyword"] or "").strip()
        if not keyword or tid in sent_users:
            continue
        if keyword.upper() not in text_upper:
            continue
        try:
            await bot.send_message(
                tid,
                "📢 <b>NOTIFIKASI KEYWORD</b>\n\n"
                f"🔑 Keyword: <code>{html.escape(keyword)}</code>\n"
                f"👥 Grup: {html.escape(str(wa_group or '-'))}\n"
                f"👤 Pengirim: {html.escape(str(wa_sender or '-'))}\n\n"
                f"💬 <b>Pesan WhatsApp:</b>\n{html.escape(wa_text[:3500])}"
            )
            sent_users.add(tid)
            count += 1
        except Exception as e:
            print(f"keyword notif gagal ke {tid}: {e}")
    if count:
        print(f"📢 Keyword notif terkirim ke {count} user")
    return count


def _extract_wa_payload(data):
    """Parse Green-API incomingMessageReceived payload reliably."""
    if not isinstance(data, dict):
        return '', '', '', '', 'green_api'

    # Green-API sends these objects at the root of the webhook payload.
    sender_data = data.get('senderData') or {}
    message_data = data.get('messageData') or {}

    # IMPORTANT: typeMessage is inside messageData on Green-API.
    ttype = str(message_data.get('typeMessage') or data.get('typeMessage') or '').strip()

    # Some deployments/proxies wrap the original payload in body/data.
    if not sender_data and isinstance(data.get('body'), dict):
        data = data['body']
        sender_data = data.get('senderData') or {}
        message_data = data.get('messageData') or {}
        ttype = str(message_data.get('typeMessage') or data.get('typeMessage') or '').strip()
    elif not sender_data and isinstance(data.get('data'), dict):
        data = data['data']
        sender_data = data.get('senderData') or {}
        message_data = data.get('messageData') or {}
        ttype = str(message_data.get('typeMessage') or data.get('typeMessage') or '').strip()

    sender = (
        sender_data.get('sender')
        or sender_data.get('senderChatId')
        or sender_data.get('chatId')
        or message_data.get('sender')
        or ''
    )
    group = (
        sender_data.get('chatName')
        or sender_data.get('chatId')
        or 'Grup WA'
    )
    sender_name = (
        sender_data.get('senderName')
        or sender_data.get('senderContactName')
        or sender_data.get('senderContactName')
        or 'Pengirim WA'
    )

    text = ''
    if ttype == 'textMessage':
        block = message_data.get('textMessageData') or {}
        text = block.get('textMessage', '') if isinstance(block, dict) else ''
    elif ttype in ('extendedTextMessage', 'quotedMessage'):
        block = message_data.get('extendedTextMessageData') or {}
        if isinstance(block, dict):
            text = block.get('text') or block.get('textMessage') or ''
    elif ttype == 'imageMessage':
        block = message_data.get('imageMessageData') or {}
        text = block.get('caption', '') if isinstance(block, dict) else ''
    elif ttype == 'documentMessage':
        block = message_data.get('documentMessageData') or {}
        text = block.get('caption', '') if isinstance(block, dict) else ''
    elif ttype == 'audioMessage':
        text = '🎵 Pesan Suara'
    elif ttype == 'videoMessage':
        text = '🎬 Pesan Video'
    else:
        # Fallback so a minor Green-API payload variation does not silently
        # produce HTTP 200 with zero forwarding.
        block = message_data.get('textMessageData')
        if isinstance(block, dict):
            text = block.get('textMessage', '')
        if not text:
            block = message_data.get('extendedTextMessageData')
            if isinstance(block, dict):
                text = block.get('text') or block.get('textMessage', '')
        if not text and isinstance(message_data.get('caption'), str):
            text = message_data.get('caption', '')

    number = str(sender or '')
    if '@' in number:
        number = number.split('@')[0]
    number = ''.join(filter(str.isdigit, number))
    if number.startswith('62'):
        number = '0' + number[2:]

    print(f"📩 GREEN-API PARSER: type={ttype or '-'} group={group} sender={sender_name} text={str(text)[:180]!r}")
    return str(text or ''), str(group), str(sender_name), str(number or '-'), 'green_api'

# ========== WEBHOOK DENGAN PROSES BACKGROUND ==========
async def whatsapp_webhook(request):
    """Green-API webhook dengan response cepat"""
    try:
        data = await request.json()
        
        # Proses di background agar response cepat
        asyncio.create_task(process_wa_message(data, request.app['bot']))
        
        # Response langsung agar webhook tidak timeout
        return web.json_response({'status': True, 'queued': True})
        
    except Exception as e:
        logging.exception(f"❌ Webhook error: {e}")
        return web.json_response({'status': True, 'error': str(e)[:100]})

async def process_wa_message(data, bot):
    """Proses pesan WA di background"""
    try:
        wa_text, wa_group, wa_sender, wa_number, provider = _extract_wa_payload(data)
        if not wa_text:
            return
        
        clean_text = re.sub(r'<[^>]+>', '', str(wa_text)).strip()
        if not clean_text:
            return
        
        kab, kec = parse_kota_kec_from_text(clean_text)
        
        # Simpan log
        try:
            conn = db()
            conn.execute(
                "INSERT INTO wa_inbox_log(wa_group,wa_sender,message,parsed_kab,parsed_kec,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (wa_group, wa_sender, clean_text, kab or '', kec or '', datetime.now().isoformat())
            )
            db_commit_and_sync(conn)
            conn.close()
        except Exception as e:
            print(f"⚠️ WA log gagal: {e}")
        
        # Kirim notifikasi
        await notify_custom_keywords(bot, clean_text, wa_group, wa_sender)
        await forward_wa_to_telegram(bot, clean_text, wa_group, wa_sender, wa_number, kab or '', kec or '')
        
    except Exception as e:
        logging.exception(f"❌ Proses WA error: {e}")


# === BOT BARU V2 - 100% TERPISAH DARI BOT LAMA ===
BOT_ID = "BOT_BARU_V2_2025"
DB_PATH = Path(os.getenv("DB_PATH", f"/data/bot_baru_v2.db" if os.path.exists("/data") else "bot_baru_v2.db"))

PASADATA_URL_BARU = os.getenv("PASADATA_URL_BARU", "").strip()
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

    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"⚠️ Folder DB tidak bisa dibuat ({DB_PATH.parent}): {e}. Fallback ke database lokal.")
        DB_PATH = Path("bot_baru_v2.db")
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

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
    CREATE TABLE IF NOT EXISTS user_keywords (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        keyword TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(telegram_id, keyword)
    );
    CREATE TABLE IF NOT EXISTS blacklist_numbers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number TEXT UNIQUE NOT NULL,
        added_by INTEGER,
        note TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS admins (
        telegram_id INTEGER PRIMARY KEY,
        added_by INTEGER,
        created_at TEXT NOT NULL
    );
    """)
    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
    CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(telegram_id);
    CREATE INDEX IF NOT EXISTS idx_format_results_user ON format_results(telegram_id);
    CREATE INDEX IF NOT EXISTS idx_blacklist_number ON blacklist_numbers(number);
    CREATE INDEX IF NOT EXISTS idx_wa_inbox_message ON wa_inbox_log(message);
    CREATE INDEX IF NOT EXISTS idx_wa_inbox_parsed_kab ON wa_inbox_log(parsed_kab);
    CREATE INDEX IF NOT EXISTS idx_wa_inbox_parsed_kec ON wa_inbox_log(parsed_kec);
    CREATE INDEX IF NOT EXISTS idx_user_kota_kab_kec ON user_kota(kab, kec);
    """)
    # Migration: tambah wa_number kalau belum ada
    try:
        conn.execute("ALTER TABLE users ADD COLUMN wa_number TEXT")
        print("✅ Migrasi: tambah kolom wa_number di users")
    except:
        pass
    try:
        conn.execute("ALTER TABLE user_kota ADD COLUMN wa_notif_sent INTEGER DEFAULT 0")
    except:
        pass
    # Sinkronkan ADMIN_IDS dari Railway/env ke tabel admins.
    for admin_id in ADMIN_IDS:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO admins(telegram_id, added_by, created_at) VALUES(?,?,?)",
                (admin_id, admin_id, datetime.now().isoformat())
            )
        except Exception as e:
            logging.warning(f"Gagal sinkron admin {admin_id}: {e}")

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


def get_all_admin_ids():
    """Gabungkan admin dari Railway Variables dan tabel admins."""
    ids = set(ADMIN_IDS)
    try:
        conn = db()
        rows = conn.execute("SELECT telegram_id FROM admins").fetchall()
        conn.close()
        for row in rows:
            try:
                admin_id = int(row["telegram_id"])
                if admin_id > 0:
                    ids.add(admin_id)
            except Exception:
                pass
    except Exception as e:
        logging.warning(f"Gagal membaca daftar admin dari DB: {e}")
    return sorted(ids)


def get_all_admin_notify_ids():
    """Semua tujuan yang menerima bukti pembayaran."""
    return sorted(set(get_all_admin_ids()) | set(ADMIN_NOTIFY_IDS))


def is_admin(user_id):
    return int(user_id) in set(get_all_admin_ids())


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
    kode = (sub["package_code"] or "").upper()
    if sub["status"] == "unlimited" or kode == "UNLIMITED" or "UNLIMITED" in (sub["package_name"] or "").upper():
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


# ========== MENU UTAMA ==========
def main_menu(user_id=None):
    if user_id is None:
        user_id = 0
    rows = [
        [InlineKeyboardButton(text="👤 PROFIL", callback_data="profile"), InlineKeyboardButton(text="📊 STATUS", callback_data="status")],
        [InlineKeyboardButton(text="💳 TOP UP", callback_data="topup"), InlineKeyboardButton(text="📝 AUTO FORMAT", callback_data="auto_format")],
        [InlineKeyboardButton(text="🏙️ KOTA SAYA", callback_data="kota_saya"), InlineKeyboardButton(text="➕ TAMBAH KOTA", callback_data="kota_add")],
        [InlineKeyboardButton(text="🚫 NO BLACKLIST", callback_data="blacklist_view"), InlineKeyboardButton(text="💡 SOLUSI JMO", callback_data="solusi_jmo")],
        [InlineKeyboardButton(text="🔎 CARI DATA LAINNYA", callback_data="kota_search_lain"), InlineKeyboardButton(text="📢 KEYWORD LAIN", callback_data="keyword_lain")],
        [InlineKeyboardButton(text="📞 ADMIN", callback_data="contact_admin"), InlineKeyboardButton(text="🆘 BANTUAN", callback_data="bantuan")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton(text="🔐 PANEL ADMIN", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

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

# ========== MENU AUTO FORMAT DENGAN GARIS BAWAH ==========
def auto_menu():
    """Menu Auto Format dengan garis bawah"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ BUAT FORMAT BARU", callback_data="format_create")],
        [InlineKeyboardButton(text="📝 MANUAL", callback_data="format_manual")],
        [InlineKeyboardButton(text="━━━━━━━━━━━━━━━━━━━━", callback_data="ignore")],
        [InlineKeyboardButton(text="⚙️ SET TEMPLATE", callback_data="format_setting"), 
         InlineKeyboardButton(text="🔢 SET KODE", callback_data="set_kode_format")],
        [InlineKeyboardButton(text="━━━━━━━━━━━━━━━━━━━━", callback_data="ignore")],
        [InlineKeyboardButton(text="📊 HASIL", callback_data="format_results"), 
         InlineKeyboardButton(text="👤 AKUN", callback_data="hasil_akun")],
        [InlineKeyboardButton(text="🌆 KOTA", callback_data="kota_list"), 
         InlineKeyboardButton(text="📜 HISTORY", callback_data="format_history")],
        [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")]
    ])

# Dispatcher dan FSM
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

class KeywordState(StatesGroup):
    waiting_keyword = State()

class AdminState(StatesGroup):
    waiting_blacklist_add = State()
    waiting_admin_id = State()
    waiting_blacklist_del = State()
    waiting_user_amount = State()
    waiting_delete_user = State()
    waiting_broadcast = State()


# ==================== JMO SOLUTIONS ====================
JMO_SOLUTIONS = {
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
5️⃣ Hubungi kantor cabang BPJS terdekat jika masih gagal"""
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
5️⃣ Kunjungi kantor BPJS terdekat untuk update data"""
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
5️⃣ Pastikan izin kamera aktif di pengaturan HP"""
    },
    "jht_cair": {
        "keywords": ["cair", "cairkan", "pencairan", "jht cair", "klaim jht"],
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
• Waktu proses: 7-14 hari kerja"""
    },
    "login": {
        "keywords": ["login", "tidak bisa login", "gagal login"],
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
5️⃣ Coba login di waktu lain (off-peak)"""
    },
    "otp": {
        "keywords": ["otp", "kode otp", "verifikasi otp", "sms otp"],
        "solution": """🔎 <b>MASALAH OTP JMO</b>

❌ <b>Penyebab:</b>
• Nomor HP tidak aktif
• Sinyal buruk atau tidak ada
• SMS terblokir
• Provider mengalami gangguan

✅ <b>Solusi:</b>
1️⃣ Pastikan sinyal HP bagus
2️⃣ Cek folder spam/blocked SMS
3️⃣ Tunggu 1-2 menit, jangan spam request
4️⃣ Restart HP dan coba lagi
5️⃣ Gunakan nomor HP lain jika masih gagal"""
    },
}

def get_jmo_solution(text: str) -> str:
    """Mencari solusi JMO berdasarkan keyword yang cocok"""
    text_lower = text.lower()
    
    for key, data in JMO_SOLUTIONS.items():
        for keyword in data["keywords"]:
            if keyword in text_lower:
                return data["solution"]
    
    return """🛠️ <b>BELUM DITEMUKAN SOLUSI KHUSUS</b>

Maaf, saya belum menemukan solusi yang tepat untuk masalah Anda.

📌 <b>Untuk mendapatkan solusi yang lebih akurat, silakan:</b>
1️⃣ Tulis ulang masalah dengan lebih DETAIL
2️⃣ Sertakan KODE ERROR yang muncul (contoh: 025, 026, dst)
3️⃣ Jelaskan TAHAPAN yang gagal (login, verifikasi, dll)
4️⃣ Sebutkan PESAN ERROR lengkapnya

📞 <b>Atau hubungi langsung:</b>
• Admin: @Hambali1995
• WhatsApp: 083160776091"""


# ==================== COMMAND START ====================
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    register_user(message.from_user)
    try:
        await message.answer("⏳ Memuat...", reply_markup=ReplyKeyboardRemove())
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

@dp.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    """Handler untuk tombol pemisah (garis bawah)"""
    await callback.answer()

# ==================== PROFIL & STATUS ====================
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


# ==================== TOP UP ====================
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

def has_cari_lainnya_access(user_id):
    try:
        if user_id in ADMIN_IDS or is_admin(user_id):
            return True
        sub = get_subscription(user_id)
        if not sub:
            return False
        pkg = (sub["package_code"] or "").lower()
        if pkg.startswith("cari_"):
            if sub["status"] == "unlimited":
                return True
            if sub["status"] == "active" and sub["expiry_date"]:
                try:
                    return datetime.now() < datetime.fromisoformat(sub["expiry_date"])
                except:
                    return False
        return False
    except Exception as e:
        print(f"has_cari error: {e}")
        return False

# ==================== AUTO FORMAT ====================
@dp.callback_query(F.data == "auto_format")
async def auto_format(callback: CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        nl = chr(10)
        try:
            has_access = has_auto_format_access(callback.from_user.id)
        except Exception as e:
            print(f"has_access error: {e}")
            has_access = False
        if has_access:
            try:
                await callback.message.edit_text(
                    "📝 <b>AUTO FORMAT</b>" + nl + nl + "🔓 Akses kamu aktif." + nl + nl + "Silakan pilih menu:",
                    reply_markup=auto_menu()
                )
            except Exception as e:
                print(f"edit_text active error: {e}")
                await callback.message.answer(
                    "📝 AUTO FORMAT - Akses aktif." + nl + nl + "Silakan pilih menu:",
                    reply_markup=auto_menu()
                )
        else:
            try:
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
            except Exception as e:
                print(f"edit_text locked error: {e}")
                await callback.message.answer(
                    "AUTO FORMAT TERKUNCI - Pilih paket dulu",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="PAKET 3 BULAN", callback_data="af_3m")],
                        [InlineKeyboardButton(text="KEMBALI", callback_data="back_main")]
                    ])
                )
    except Exception as e:
        print(f"auto_format crash: {e}")
        try:
            await callback.answer("Error, coba lagi")
        except:
            pass
        try:
            await callback.message.answer("📝 AUTO FORMAT - Coba lagi /start")
        except:
            pass
        return
    try:
        await callback.answer()
    except:
        pass

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


# ==================== PEMBAYARAN ====================
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
        ) VALUES(?,?,?,'SEABANK/DANA',?,?,?,'pending',?)
    """, (
        message.from_user.id, amount,
        data.get("package_code"),
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

    targets = get_all_admin_notify_ids()
    print(f"🔔 NOTIF BUKTI TF -> target={targets} | tx_id={tx_id}")
    berhasil = 0
    for admin_id in targets:
        delivered = False
        try:
            await message.bot.send_photo(
                chat_id=admin_id,
                photo=photo.file_id,
                caption=caption,
                reply_markup=keyboard
            )
            berhasil += 1
            delivered = True
            print(f"✅ Bukti #{tx_id} terkirim ke admin {admin_id}")
        except Exception as e:
            logging.exception(f"❌ Gagal kirim foto bukti #{tx_id} ke admin {admin_id}: {e}")

        if not delivered:
            try:
                await message.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "🚨 <b>BUKTI PEMBAYARAN BARU</b>\n\n"
                        f"{caption}\n\n"
                        "📸 Foto gagal diteruskan otomatis. "
                        "Buka <b>PENDING</b> di PANEL ADMIN untuk melihat dan memproses transaksi."
                    ),
                    reply_markup=keyboard
                )
                berhasil += 1
                print(f"✅ Fallback teks bukti #{tx_id} terkirim ke admin {admin_id}")
            except Exception as e2:
                logging.exception(f"❌ Fallback notifikasi #{tx_id} ke admin {admin_id} juga gagal: {e2}")

    if berhasil == 0:
        print("❌ TIDAK ADA ADMIN YANG MENERIMA NOTIFIKASI. Periksa ADMIN_IDS/ADMIN_NOTIFY_IDS di Railway.")


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
        elif pkg.startswith("cari_"):
            from datetime import timedelta
            if pkg == "cari_1w":
                expiry = datetime.now() + timedelta(days=7)
            elif pkg == "cari_1m":
                expiry = add_months(datetime.now(), 1)
            elif pkg == "cari_2m":
                expiry = add_months(datetime.now(), 2)
            elif pkg == "cari_6m":
                expiry = add_months(datetime.now(), 6)
            else:
                expiry = None
            status = "active"
            conn.execute("INSERT INTO subscriptions(telegram_id,package_code,package_name,price,start_date,expiry_date,status) VALUES(?,?,?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET package_code=excluded.package_code, package_name=excluded.package_name, price=excluded.price, start_date=excluded.start_date, expiry_date=excluded.expiry_date, status=excluded.status", (tx["telegram_id"], tx["package_code"], tx["package_name"], tx["amount"], now, expiry.isoformat() if expiry else None, status))
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
            [InlineKeyboardButton(text="❓ CARA CAIRKAN JHT", callback_data="jmo_jht_cair")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("jmo_"))
async def jmo_quick(callback: CallbackQuery, state: FSMContext):
    error_code = callback.data.split("_", 1)[1]
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
    await callback.answer("Solusi tidak ditemukan", show_alert=True)

@dp.message(JmoState.waiting_question, F.text)
async def solusi_text(message: Message, state: FSMContext):
    solution = get_jmo_solution(message.text)
    await message.answer(
        solution,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 TANYA LAGI", callback_data="solusi_jmo")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
    )


# ==================== AUTO FORMAT FUNGSI ====================
def generate_next_code(telegram_id):
    """Generate kode format berikutnya"""
    conn = db()
    row = conn.execute("SELECT * FROM format_codes WHERE telegram_id=?", (telegram_id,)).fetchone()
    if not row:
        conn.close()
        return None
    
    num = row["current_number"]
    padding = row["padding"]
    prefix = row["prefix"] or ""
    suffix = row["suffix"] or ""
    
    # Update number untuk next
    conn.execute("UPDATE format_codes SET current_number=current_number+1, updated_at=? WHERE telegram_id=?", 
                 (datetime.now().isoformat(), telegram_id))
    conn.commit()
    conn.close()
    
    code = f"{prefix} {str(num).zfill(padding)} {suffix}".strip()
    return code

def parse_kode_input(text: str):
    """Parse input kode format"""
    import re
    # Coba format: "JPG - 001" atau "JPG - 001 - SUFFIX"
    m = re.search(r'([A-Za-z0-9\s]+)\s*[-:]\s*(\d+)(?:\s*[-:]\s*(.+))?', text)
    if m:
        prefix = m.group(1).strip()
        num = int(m.group(2))
        suffix = m.group(3).strip() if m.group(3) else ""
        padding = len(m.group(2))
        return prefix, num, padding, suffix
    
    # Coba format: "ABC-001"
    m = re.search(r'([A-Za-z0-9]+)[-:]+(\d+)', text)
    if m:
        prefix = m.group(1).strip()
        num = int(m.group(2))
        padding = len(m.group(2))
        return prefix, num, padding, ""
    
    return None

def make_template_from_example(example_text: str) -> str:
    import re
    lines = example_text.splitlines()
    out=[]
    for line in lines:
        l=line
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
    result = "\n".join(out).strip()
    if "{" not in result:
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
        if '@' in l and '.' in l and not re.search(r'KAB|KEC|KEL|PT|KPJ|SALDO', l, re.I):
            continue
        if re.search(r'AKUN\s*:', l, re.I):
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


# ==================== AUTO FORMAT HANDLER ====================
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
    if not has_auto_format_access(callback.from_user.id):
        await callback.answer("🔒 AUTO FORMAT masih terkunci.", show_alert=True)
        return
    await state.clear()
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

@dp.message(FormatState.waiting_manual, F.text)
async def format_manual_receive(message: Message, state: FSMContext):
    data = await state.get_data()
    edit_id = data.get("edit_result_id")

    conn = db()
    
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
        akun = parse_data_with_akun(message.text)
        has_akun_keyword = 'AKUN' in message.text.upper()
        if akun.get('EMAIL') or has_akun_keyword:
            email_save = akun.get('EMAIL') or 'AKUN'
            pass_save = akun.get('PASSWORD') or message.text.split('AKUN')[-1].strip()[:500]
            if not akun.get('EMAIL') and has_akun_keyword:
                email_save = 'AKUN'
                pass_save = message.text.upper().split('AKUN')[-1].strip()[:500]
                if ':' in pass_save:
                    pass_save = pass_save.split(':',1)[-1].strip()
            cur.execute("INSERT INTO format_accounts(telegram_id,email,password,raw_text,result_id,created_at) VALUES(?,?,?,?,?,?)", (message.from_user.id, email_save, pass_save, f"{email_save}\n{pass_save}", result_id, datetime.now().isoformat()))

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


# ==================== SET TEMPLATE ====================
@dp.callback_query(F.data == "format_setting")
async def format_setting(callback: CallbackQuery, state: FSMContext):
    if not has_auto_format_access(callback.from_user.id):
        await callback.answer("🔒 AUTO FORMAT masih terkunci.", show_alert=True)
        return
    await state.clear()
    await state.set_state(FormatState.waiting_setting)
    
    conn = db()
    row = conn.execute(
        "SELECT template FROM format_settings WHERE telegram_id=?",
        (callback.from_user.id,)
    ).fetchone()
    conn.close()
    
    current_template = row["template"] if row else DEFAULT_TEMPLATE
    
    await callback.message.edit_text(
        "⚙️ <b>SET TEMPLATE FORMAT</b>\n\n"
        "Kirim contoh format yang kamu inginkan.\n"
        "Bot akan otomatis menyesuaikan template.\n\n"
        "📌 <b>Contoh:</b>\n"
        "<code>📍KAB : DEPOK\n📍KEC : CILODONG\n📍KEL : KALIBARU\n\n💰 SALDO : 10.000.000\n\n🆔 KELAMIN : PEREMPUAN 1992\n💳 KPJ : 2019\n🔰 SENSOR: 23****\n📆 IT : 01-07-2022\n🏛️ PT : INDONESIA MERDEKA</code>\n\n"
        "⚠️ Gunakan format KAB/KEC/KEL/SALDO/KELAMIN/KPJ/SENSOR/IT/PT\n"
        "Bot akan mengganti bagian tersebut dengan data.\n\n"
        f"📋 <b>Template saat ini:</b>\n<pre>{html.escape(current_template[:500])}</pre>\n\n"
        "Ketik template baru, atau ketik /batal untuk batal.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ BATAL", callback_data="auto_format")]
        ])
    )
    await callback.answer()

@dp.message(FormatState.waiting_setting, F.text)
async def format_setting_receive(message: Message, state: FSMContext):
    if message.text.lower() in ["/batal", "batal", "/cancel"]:
        await state.clear()
        await message.answer("❌ Dibata

lkan.", reply_markup=auto_menu())
        return
    
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
    
    dummy_input = "JAKARTA\nPENJARINGAN\nBEBAS\n14000\nPEREMPUAN 1992\n2021\n22* 23*\n02-03-2025\nSAKTI MULYA"
    preview = apply_template(template_to_save, dummy_input)
    await message.answer(
        "✅ <b>SETTING FORMAT TERSIMPAN & AKTIF</b>\n\n"
        "Bot sekarang akan <b>ngikutin 100%</b> template ini:\n\n"
        f"<pre>{html.escape(preview[:3800])}</pre>\n\n"
        "Coba BUAT FORMAT baru, hasilnya pasti ngikutin template ini!",
        reply_markup=auto_menu()
    )


# ==================== SET KODE ====================
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
            [InlineKeyboardButton(text="⬅️ BATAL", callback_data="auto_format")]
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


# ==================== CARI DATA LAINNYA ====================
@dp.callback_query(F.data == "kota_search_lain")
async def kota_search_lain_start(callback: CallbackQuery, state: FSMContext):
    """Menu CARI DATA LAINNYA - Perbaikan dengan tombol Salin & Chat"""
    try:
        await state.clear()
        nl = chr(10)
        
        try:
            has_access = has_cari_lainnya_access(callback.from_user.id)
        except Exception as e:
            print(f"has_cari check error: {e}")
            has_access = False

        if not has_access:
            await callback.message.edit_text(
                "🔒 <b>CARI DATA LAINNYA - TERKUNCI</b>" + nl + nl +
                "Untuk menggunakan fitur CARI DATA LAINNYA, kamu harus Top Up paket dulu." + nl + nl +
                "📦 <b>DAFTAR PAKET CARI DATA LAINNYA:</b>" + nl +
                "⏰ 1 Minggu — Rp 15.000" + nl +
                "📅 1 Bulan — Rp 50.000" + nl +
                "📅 2 Bulan — Rp 80.000" + nl +
                "📅 6 Bulan — Rp 250.000" + nl + nl +
                "Pilih paket di bawah untuk lanjut Top Up:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⏰ 1 Minggu - Rp 15.000", callback_data="paket_cari_1w")],
                    [InlineKeyboardButton(text="📅 1 Bulan - Rp 50.000", callback_data="paket_cari_1m")],
                    [InlineKeyboardButton(text="📅 2 Bulan - Rp 80.000", callback_data="paket_cari_2m")],
                    [InlineKeyboardButton(text="📅 6 Bulan - Rp 250.000", callback_data="paket_cari_6m")],
                    [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
                ])
            )
            await callback.answer()
            return

        await state.set_state(KotaState.waiting_cari_lainnya)
        await state.update_data(is_search_mode=True)
        
        await callback.message.edit_text(
            "🔎 <b>CARI DATA LAINNYA</b>" + nl + nl +
            "📍 <b>MASUKAN NAMA KOTA / KECAMATAN</b>" + nl +
            "💡 Contoh: <code>BANDUNG</code> atau <code>CIBIRU</code>" + nl + nl +
            "📌 Bot akan menampilkan SEMUA pesan WhatsApp dari grup" + nl +
            "   yang mengandung kata kunci tersebut." + nl + nl +
            "✍️ Ketik nama kota/kecamatan yang dicari:" + nl + nl +
            "❌ Ketik /batal untuk membatalkan.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ BATAL", callback_data="back_main")]
            ])
        )
        await callback.answer()
    except Exception as e:
        print(f"kota_search_lain crash: {e}")
        try:
            await callback.answer("Error, coba /start lagi")
        except:
            pass

@dp.message(KotaState.waiting_cari_lainnya, F.text)
async def kota_search_lain_process(message: Message, state: FSMContext):
    """Proses pencarian data lainnya - tampilkan pesan WA per pengirim"""
    query = message.text.strip()
    
    # Handle batal
    if query.lower() in ["/batal", "batal", "/cancel"]:
        await state.clear()
        await message.answer(
            "❌ Pencarian dibatalkan.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
            ])
        )
        return
    
    if len(query) < 2:
        await message.answer(
            "❌ Minimal 2 huruf. Contoh: <code>BANDUNG</code>\nKetik /batal untuk batal."
        )
        return
    
    # Cari di database WA inbox log
    like = f"%{query.upper()}%"
    
    try:
        conn = db()
        
        # Cari pesan WA yang mengandung kata kunci di message atau parsed_kab/kec
        wa_rows = conn.execute("""
            SELECT id, wa_group, wa_sender, message, parsed_kab, parsed_kec, created_at
            FROM wa_inbox_log
            WHERE UPPER(message) LIKE UPPER(?)
               OR UPPER(parsed_kab) LIKE UPPER(?)
               OR UPPER(parsed_kec) LIKE UPPER(?)
            ORDER BY created_at DESC
            LIMIT 200
        """, (like, like, like)).fetchall()
        
        # Cari juga di format_results untuk data tambahan
        format_rows = conn.execute("""
            SELECT id, result_text, created_at
            FROM format_results
            WHERE telegram_id = ? AND UPPER(result_text) LIKE UPPER(?)
            ORDER BY created_at DESC
            LIMIT 50
        """, (message.from_user.id, like)).fetchall()
        
        conn.close()
        await state.clear()
        
        if not wa_rows and not format_rows:
            await message.answer(
                f"🔎 <b>HASIL CARI: {query.upper()}</b>\n\n"
                f"❌ Tidak ada data dengan kata kunci <b>{query.upper()}</b>\n\n"
                "💡 Pastikan kata kunci yang dicari ada di pesan WhatsApp atau data format.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔍 CARI LAGI", callback_data="kota_search_lain")],
                    [InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]
                ])
            )
            return
        
        # Kirim pesan pengantar
        total_wa = len(wa_rows)
        total_format = len(format_rows)
        
        await message.answer(
            f"🔎 <b>HASIL CARI: {query.upper()}</b>\n\n"
            f"📲 Pesan WhatsApp: <b>{total_wa}</b>\n"
            f"📄 Data Format: <b>{total_format}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 CARI LAGI", callback_data="kota_search_lain")],
                [InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]
            ])
        )
        
        # ========== TAMPILKAN PESAN WA PER PENGIRIM ==========
        if wa_rows:
            # Kelompokkan berdasarkan pengirim
            grouped = {}
            for row in wa_rows:
                sender = row['wa_sender'] or '-'
                if sender not in grouped:
                    grouped[sender] = []
                grouped[sender].append(row)
            
            await message.answer(
                f"📲 <b>PESAN WHATSAPP ({total_wa} pesan dari {len(grouped)} pengirim)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            
            # Kirim per pengirim dengan tombol
            idx = 0
            for sender, rows in grouped.items():
                # Kirim 1 kotak per pengirim dengan semua pesannya
                all_messages = []
                for row in rows:
                    msg = (row['message'] or '').strip()
                    if msg:
                        all_messages.append(msg)
                
                if all_messages:
                    idx += 1
                    # Gabungkan semua pesan dari pengirim yang sama
                    combined_msg = "\n\n---\n\n".join(all_messages[:5])
                    if len(all_messages) > 5:
                        combined_msg += f"\n\n... dan {len(all_messages)-5} pesan lainnya"
                    
                    # Format kotak
                    wa_group = html.escape(str(rows[0]['wa_group'] or '-')[:50])
                    wa_sender = html.escape(str(sender)[:50])
                    created_at = rows[0]['created_at']
                    try:
                        dt = datetime.fromisoformat(created_at)
                        created_at = dt.strftime("%d/%m/%Y %H:%M")
                    except:
                        created_at = created_at[:16] if created_at else '-'
                    
                    # Ekstrak nomor untuk tombol chat
                    wa_number = extract_number_from_sender(sender)
                    
                    text_msg = (
                        f"📨 <b>PENGIRIM #{idx}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📱 Grup: {wa_group}\n"
                        f"👤 Pengirim: {wa_sender}\n"
                        f"📝 Jumlah: {len(all_messages)} pesan\n"
                        f"🕐 Terakhir: {created_at}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"{html.escape(combined_msg[:2500])}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    
                    # Tombol
                    buttons = []
                    if combined_msg:
                        buttons.append(InlineKeyboardButton(
                            text="📋 SALIN PESAN", 
                            callback_data=f"copy_wa_sender_{sender[:30]}"
                        ))
                    
                    if wa_number and len(wa_number) >= 10:
                        buttons.append(InlineKeyboardButton(
                            text="💬 CHAT PENGIRIM", 
                            url=f"https://wa.me/{wa_number}"
                        ))
                    
                    if len(buttons) > 0:
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])
                    else:
                        keyboard = None
                    
                    await message.answer(text_msg[:4000], reply_markup=keyboard)
                    await asyncio.sleep(0.1)
        
        # ========== TAMPILKAN DATA FORMAT ==========
        if format_rows:
            await message.answer(
                f"📄 <b>DATA FORMAT ({len(format_rows)})</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            
            for i, row in enumerate(format_rows[:10], 1):
                result = (row['result_text'] or '').strip()
                if len(result) > 500:
                    result = result[:500] + "..."
                
                text_fmt = (
                    f"📄 <b>FORMAT #{i}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{html.escape(result)}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📋 SALIN FORMAT", 
                        callback_data=f"copy_format_{row['id']}"
                    )]
                ])
                
                await message.answer(text_fmt[:4000], reply_markup=keyboard)
                await asyncio.sleep(0.1)
            
            if len(format_rows) > 10:
                await message.answer(f"... dan {len(format_rows)-10} format lainnya")
        
        # ========== TOMBOL CARI LAGI ==========
        await message.answer(
            "🔍 <b>PENCARIAN SELESAI</b>\n\n"
            "Ketik /start untuk kembali ke menu utama",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 CARI LAGI", callback_data="kota_search_lain")],
                [InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]
            ])
        )
        
    except Exception as e:
        logging.exception(f"Error search: {e}")
        await message.answer(
            "❌ Terjadi kesalahan saat mencari data. Silakan coba lagi.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 CARI LAGI", callback_data="kota_search_lain")],
                [InlineKeyboardButton(text="⬅️ MENU UTAMA", callback_data="back_main")]
            ])
        )


# ==================== HANDLER TOMBOL SALIN ====================
@dp.callback_query(F.data.startswith("copy_wa_sender_"))
async def copy_wa_sender_message(callback: CallbackQuery):
    """Salin pesan dari pengirim tertentu"""
    sender = callback.data.replace("copy_wa_sender_", "")
    
    try:
        conn = db()
        rows = conn.execute("""
            SELECT message FROM wa_inbox_log 
            WHERE wa_sender LIKE ? 
            ORDER BY created_at DESC 
            LIMIT 10
        """, (f"%{sender}%",)).fetchall()
        conn.close()
        
        if not rows:
            await callback.answer("Tidak ada pesan untuk disalin", show_alert=True)
            return
        
        messages = []
        for row in rows:
            msg = (row['message'] or '').strip()
            if msg:
                messages.append(msg)
        
        if not messages:
            await callback.answer("Tidak ada pesan untuk disalin", show_alert=True)
            return
        
        combined = "\n\n---\n\n".join(messages[:5])
        if len(messages) > 5:
            combined += f"\n\n... dan {len(messages)-5} pesan lainnya"
        
        await callback.message.bot.send_message(
            callback.from_user.id,
            f"📋 <b>PESAN DISALIN</b>\n\n<code>{html.escape(combined)}</code>"
        )
        await callback.answer("✅ Pesan disalin!")
        
    except Exception as e:
        logging.exception(f"Copy error: {e}")
        await callback.answer("❌ Gagal menyalin", show_alert=True)

@dp.callback_query(F.data.startswith("copy_format_"))
async def copy_format_result(callback: CallbackQuery):
    """Salin hasil format"""
    try:
        fmt_id = int(callback.data.replace("copy_format_", ""))
        
        conn = db()
        row = conn.execute(
            "SELECT result_text FROM format_results WHERE id=? AND telegram_id=?",
            (fmt_id, callback.from_user.id)
        ).fetchone()
        conn.close()
        
        if not row:
            await callback.answer("Format tidak ditemukan", show_alert=True)
            return
        
        await callback.message.bot.send_message(
            callback.from_user.id,
            f"📋 <b>FORMAT DISALIN</b>\n\n<code>{html.escape(row['result_text'][:3500])}</code>"
        )
        await callback.answer("✅ Format disalin!")
        
    except Exception as e:
        logging.exception(f"Copy format error: {e}")
        await callback.answer("❌ Gagal menyalin", show_alert=True)

@dp.callback_query(F.data.startswith("copy_result_"))
async def copy_result(callback: CallbackQuery):
    """Salin hasil format tertentu"""
    try:
        result_id = int(callback.data.replace("copy_result_", ""))
        
        conn = db()
        row = conn.execute(
            "SELECT result_text FROM format_results WHERE id=? AND telegram_id=?",
            (result_id, callback.from_user.id)
        ).fetchone()
        conn.close()
        
        if not row:
            await callback.answer("Format tidak ditemukan", show_alert=True)
            return
        
        await callback.message.bot.send_message(
            callback.from_user.id,
            f"📋 <b>FORMAT DISALIN</b>\n\n<code>{html.escape(row['result_text'][:3500])}</code>"
        )
        await callback.answer("✅ Format disalin!")
        
    except Exception as e:
        logging.exception(f"Copy format error: {e}")
        await callback.answer("❌ Gagal menyalin", show_alert=True)


# ==================== KOTA LIST ====================
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
        text += f"{i}. <b>{provinsi}</b>\n   🏛️ {kab} > 🏘️ {kec}\n   📅 Pilih: {tgl_pilih}\n\n"
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


# ==================== KEYWORD LAIN ====================
def keyword_menu_markup():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 KEYWORD SAYA", callback_data="keyword_saya")],
        [InlineKeyboardButton(text="➕ TAMBAH KEYWORD", callback_data="keyword_tambah")],
        [InlineKeyboardButton(text="🗑️ HAPUS KEYWORD", callback_data="keyword_hapus")],
        [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="back_main")],
    ])

@dp.callback_query(F.data == "keyword_lain")
async def keyword_lain_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "📢 <b>KEYWORD LAIN</b>\n\n"
        "Kelola keyword yang ingin kamu pantau dari pesan WhatsApp.\n\n"
        "🔔 Jika keyword yang kamu simpan muncul di pesan WhatsApp, bot akan langsung mengirim notifikasi ke Telegram kamu.\n\n"
        "⚠️ Keyword <b>tidak boleh mengandung nama provinsi, kota/kabupaten, atau kecamatan</b>.\n\n"
        "Pilih menu di bawah:",
        reply_markup=keyword_menu_markup()
    )
    await callback.answer()

@dp.callback_query(F.data == "keyword_tambah")
async def keyword_tambah_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(KeywordState.waiting_keyword)
    await callback.message.edit_text(
        "➕ <b>TAMBAH KEYWORD</b>\n\n"
        "Silakan ketikan keyword yang ingin kamu pantau.\n\n"
        "⚠️ Tidak boleh mengandung nama provinsi, kota/kabupaten, atau kecamatan.\n\n"
        "💡 Contoh:\n"
        "• <code>info penipu terkini</code>\n"
        "• <code>info klaim jmo</code>\n"
        "• <code>info notif jmo</code>\n"
        "• <code>link dana kaget</code>\n\n"
        "✍️ Ketik keyword sekarang.\n"
        "Ketik /batal untuk membatalkan.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="keyword_lain")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "keyword_saya")
async def keyword_saya(callback: CallbackQuery):
    conn = db()
    rows = conn.execute(
        "SELECT id, keyword, created_at FROM user_keywords WHERE telegram_id=? ORDER BY id DESC",
        (callback.from_user.id,)
    ).fetchall()
    conn.close()

    if not rows:
        text = (
            "🔑 <b>KEYWORD SAYA</b>\n\n"
            "Belum ada keyword yang kamu simpan.\n\n"
            "Tekan <b>➕ TAMBAH KEYWORD</b> untuk menambahkan keyword."
        )
    else:
        lines = ["🔑 <b>KEYWORD SAYA</b>", "", f"📊 Total: <b>{len(rows)}</b> keyword", ""]
        for i, row in enumerate(rows, 1):
            lines.append(f"{i}. 🔔 <code>{html.escape(row['keyword'])}</code>")
        text = "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=keyword_menu_markup())
    await callback.answer()

@dp.callback_query(F.data == "keyword_hapus")
async def keyword_hapus(callback: CallbackQuery):
    conn = db()
    rows = conn.execute(
        "SELECT id, keyword FROM user_keywords WHERE telegram_id=? ORDER BY id DESC",
        (callback.from_user.id,)
    ).fetchall()
    conn.close()

    if not rows:
        await callback.message.edit_text(
            "🗑️ <b>HAPUS KEYWORD</b>\n\nBelum ada keyword yang bisa dihapus.",
            reply_markup=keyword_menu_markup()
        )
        await callback.answer()
        return

    buttons = []
    for row in rows:
        label = row['keyword']
        if len(label) > 32:
            label = label[:29] + "..."
        buttons.append([InlineKeyboardButton(
            text=f"🗑️ {label}",
            callback_data=f"keyword_delete_{row['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ KEMBALI", callback_data="keyword_lain")])

    await callback.message.edit_text(
        "🗑️ <b>HAPUS KEYWORD</b>\n\nPilih keyword yang ingin dihapus:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("keyword_delete_"))
async def keyword_delete(callback: CallbackQuery):
    try:
        keyword_id = int(callback.data.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ ID keyword tidak valid", show_alert=True)
        return

    conn = db()
    row = conn.execute(
        "SELECT keyword FROM user_keywords WHERE id=? AND telegram_id=?",
        (keyword_id, callback.from_user.id)
    ).fetchone()
    if not row:
        conn.close()
        await callback.answer("❌ Keyword tidak ditemukan", show_alert=True)
        return

    conn.execute(
        "DELETE FROM user_keywords WHERE id=? AND telegram_id=?",
        (keyword_id, callback.from_user.id)
    )
    conn.commit()
    conn.close()

    await callback.answer("✅ Keyword dihapus")
    await keyword_hapus(callback)

@dp.message(KeywordState.waiting_keyword, F.text)
async def keyword_lain_save(message: Message, state: FSMContext):
    raw = message.text.strip()
    if raw.lower() in ("/batal", "batal", "/cancel"):
        await state.clear()
        await message.answer("❌ Pendaftaran keyword dibatalkan.", reply_markup=main_menu(message.from_user.id))
        return
    if len(raw) < 3 or len(raw) > 80:
        await message.answer("❌ Keyword minimal 3 dan maksimal 80 karakter. Contoh: <code>info klaim jmo</code>")
        return
    if any(ord(c) < 32 for c in raw):
        await message.answer("❌ Keyword tidak valid.")
        return
    
    conn = db()
    try:
        conn.execute("INSERT OR IGNORE INTO user_keywords(telegram_id,keyword,created_at) VALUES(?,?,?)", (message.from_user.id, raw, datetime.now().isoformat()))
        conn.commit()
    finally:
        conn.close()
    await state.clear()
    await message.answer(
        "✅ <b>KEYWORD BERHASIL DIAKTIFKAN</b>\n\n"
        f"📢 Keyword: <code>{html.escape(raw)}</code>\n\n"
        "🔔 Jika keyword ini muncul di pesan WhatsApp yang masuk, bot akan langsung mengirim notifikasi ke kamu.\n\n"
        "Kamu bisa menambahkan keyword lain dari menu <b>📢 KEYWORD LAIN</b>.",
        reply_markup=main_menu(message.from_user.id)
    )


# ==================== BLACKLIST ====================
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
    await state.set_state(KotaState.waiting_cari_lainnya)
    await state.update_data(is_blacklist_search=True)
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


# ==================== CONTACT ADMIN ====================
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


# ==================== BANTUAN ====================
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
        "• 🏙️ <b>KOTA SAYA</b> - Lihat daftar kota & kecamatan yang sudah dipilih\n"
        "• ➕ <b>TAMBAH KOTA</b> - Pilih Provinsi, Kab/Kota, Kecamatan\n"
        "• 🚫 <b>NO BLACKLIST</b> - Lihat semua nomor blacklist + fitur CARI nomor\n"
        "• 🔎 <b>CARI DATA LAINNYA</b> - Cari history WA berdasarkan nama kota\n"
        "• 💡 <b>SOLUSI JMO</b> - Solusi error JMO kode 025-033, OTP, verifikasi wajah, dll\n"
        "• 📞 <b>ADMIN</b> - Hubungi admin Telegram & WhatsApp\n\n"
        "💡 <b>Cara Pakai Auto Format:</b>\n"
        "1. Top Up dulu (minimal 10k dapat 2 quota kota)\n"
        "2. Beli paket 6 bulan/1 tahun/unlimited\n"
        "3. Masuk AUTO FORMAT > BUAT FORMAT BARU\n"
        "4. Input data sesuai template\n"
        "5. Bot akan auto format dengan kode JPG - 001 dll\n\n"
        "📞 <b>Butuh Bantuan Lebih?</b>\n"
        "Chat admin: @Hambali1995 atau WA 083160776091",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📞 HUBUNGI ADMIN", callback_data="contact_admin")],
            [InlineKeyboardButton(text="⬅️ KEMBALI KE MENU UTAMA", callback_data="back_main")]
        ])
    )
    await callback.answer()


# ==================== BACK MAIN ====================
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


# ==================== ADMIN PANEL ====================
@dp.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Kamu bukan Admin.")
        return
    await state.clear()
    await message.answer("🔐 <b>PANEL ADMIN</b>\n\nPilih menu:", reply_markup=admin_menu())

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

@dp.callback_query(F.data == "admin_user_aktif")
async def admin_user_aktif(callback: CallbackQuery):
    await admin_active(callback)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin.", show_alert=True)
        return

    conn = db()
    total = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    active = conn.execute("""
        SELECT COUNT(*) AS c
        FROM subscriptions
        WHERE status='unlimited' OR (status='active' AND expiry_date > ?)
    """, (datetime.now().isoformat(),)).fetchone()["c"]
    pending = conn.execute("SELECT COUNT(*) AS c FROM transactions WHERE status='pending'").fetchone()["c"]
    blacklist = conn.execute("SELECT COUNT(*) AS c FROM blacklist_numbers").fetchone()["c"]
    kota = conn.execute("SELECT COUNT(*) AS c FROM user_kota").fetchone()["c"]
    conn.close()

    await callback.message.edit_text(
        "📊 <b>STATS USER</b>\n\n"
        f"👥 Total user: <b>{total}</b>\n"
        f"🟢 User aktif: <b>{active}</b>\n"
        f"⏳ Transaksi pending: <b>{pending}</b>\n"
        f"🚫 Blacklist: <b>{blacklist}</b>\n"
        f"🏙️ Data kota: <b>{kota}</b>",
        reply_markup=admin_menu()
    )
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

@dp.callback_query(F.data == "admin_add_admin")
async def admin_add_admin_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminState.waiting_admin_id)
    await state.update_data(admin_action="add")
    await callback.message.edit_text(
        "👑 <b>TAMBAH ADMIN</b>\n\n"
        "Kirim Telegram ID user yang akan dijadikan admin.\n"
        "Contoh: <code>123456789</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ BATAL", callback_data="admin_cancel")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_del_admin")
async def admin_del_admin_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Bukan Admin.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminState.waiting_admin_id)
    await state.update_data(admin_action="del")
    await callback.message.edit_text(
        "❌ <b>HAPUS ADMIN</b>\n\n"
        "Kirim Telegram ID admin yang akan dihapus.\n"
        "Contoh: <code>123456789</code>\n\n"
        "ADMIN_IDS dari environment tetap menjadi admin.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ BATAL", callback_data="admin_cancel")]
        ])
    )
    await callback.answer()

@dp.message(AdminState.waiting_admin_id, F.text)
async def admin_id_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("❌ Telegram ID harus berupa angka.")
        return

    target_id = int(raw)
    data = await state.get_data()
    action = data.get("admin_action")

    if action == "add":
        conn = db()
        conn.execute(
            "INSERT OR IGNORE INTO admins(telegram_id, added_by, created_at) VALUES(?,?,?)",
            (target_id, message.from_user.id, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        await state.clear()
        await message.answer(
            f"✅ <b>ADMIN DITAMBAHKAN</b>\n\n🆔 <code>{target_id}</code>",
            reply_markup=admin_menu()
        )
        return

    if target_id in ADMIN_IDS:
        await message.answer("❌ Admin yang ada di ADMIN_IDS tidak boleh dihapus dari sini.")
        return

    conn = db()
    cur = conn.execute("DELETE FROM admins WHERE telegram_id=?", (target_id,))
    conn.commit()
    conn.close()
    await state.clear()

    if cur.rowcount:
        text = f"✅ Admin <code>{target_id}</code> berhasil dihapus."
    else:
        text = f"ℹ️ Admin <code>{target_id}</code> tidak ditemukan."
    await message.answer(text, reply_markup=admin_menu())

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


# ==================== FALLBACK ====================
@dp.message(F.text)
async def fallback(message: Message):
    words = ["jmo", "error", "kode", "025", "026", "027", "028", "029", "030", "031", "032", "033", 
             "login", "verifikasi", "kpj", "jht", "otp", "wajah", "password", "email", "aktivasi", 
             "klaim", "bpu", "kamera", "server", "lemot", "notifikasi", "daftar", "cair"]
    if any(w in message.text.lower() for w in words):
        await message.answer(get_jmo_solution(message.text))
    else:
        await message.answer("Silakan gunakan menu utama dengan /start", reply_markup=main_menu(message.from_user.id))


# ==================== WEBHOOK ROUTES ====================
def wa_logs_handler(request):
    try:
        conn = db()
        rows = conn.execute("SELECT wa_group, wa_sender, message, parsed_kab, parsed_kec, created_at FROM wa_inbox_log ORDER BY id DESC LIMIT 20").fetchall()
        conn.close()
        html_logs = "<h2>20 WA Terakhir Masuk</h2><table border=1><tr><th>Waktu</th><th>Group</th><th>Sender</th><th>KAB</th><th>KEC</th><th>Message</th></tr>"
        for r in rows:
            html_logs += f"<tr><td>{r['created_at']}</td><td>{r['wa_group']}</td><td>{r['wa_sender']}</td><td>{r['parsed_kab']}</td><td>{r['parsed_kec']}</td><td>{(r['message'] or '')[:200]}</td></tr>"
        html_logs += "</table>"
        return web.Response(text=html_logs, content_type="text/html")
    except Exception as e:
        return web.Response(text=f"Error: {e}")

async def test_forward_handler(request):
    kab = request.query.get('kab','')
    kec = request.query.get('kec','')
    txt = request.query.get('text','TEST FORWARD DARI GRUP')
    bot = request.app['bot']
    forwarded = await forward_wa_to_telegram(bot, txt, "TEST GROUP", "TEST SENDER", kab.upper(), kec.upper())
    return web.json_response({"kab": kab, "kec": kec, "forwarded": forwarded})


# ==================== MAIN ====================
async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    try:
        init_database()
        logging.info(f"✅ Database siap: {DB_PATH}")
    except Exception:
        logging.exception("❌ Gagal inisialisasi database")
        raise
    logging.info("🤖 JMO BOT V2 - GREEN-API + FILTER 2 KUNCI + FOOTER REKBER")
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    app = web.Application()
    app['bot'] = bot
    app.router.add_post('/whatsapp-webhook', whatsapp_webhook)
    app.router.add_post('/app-webhook', whatsapp_webhook)
    app.router.add_get('/whatsapp-webhook', lambda r: web.json_response({"status": True, "message": "Green-API webhook aktif"}))
    app.router.add_get('/app-webhook', lambda r: web.json_response({"status": True, "message": "Green-API webhook aktif"}))
    app.router.add_get('/', lambda r: web.json_response({"status": "ok", "filter": "KOTA+KEC WAJIB 2 KUNCI - FOOTER REKBER - NO ADMIN NOTIF"}))
    app.router.add_get('/wa-logs', wa_logs_handler)
    app.router.add_get('/test-forward', test_forward_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8000"))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"🌐 Webhook jalan di port {port}")
    try:
        await dp.start_polling(bot, skip_updates=True, polling_timeout=30)
    finally:
        await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
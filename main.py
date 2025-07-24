import logging
import sqlite3
import datetime
import asyncio
from urllib.parse import quote

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
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
# Kanka buraya o verdiğin bilgileri kendin yapıştır.
TOKEN = "8098364071:AAF_INUyGlMibiQYY0yOORX8FMXCR3HVlxo"  # SENİN BOT TOKENİN
ADMIN_ID = 7272527047  # SENİN TELEGRAM ID'N

# Kanal ve Chat Kullanıcı Adları
MAIN_CHANNEL = "@Zirvefinans_sh4"
CHAT_GROUP = "@HEROXCC_chat"
ADMIN_USERNAME = "@Heroxcredit"

# --- API Linkleri ---
AUTH_API_URL = "http://syxezerocheck.wuaze.com/api/auth2.php?card={card}"
PUAN_API_URL = "http://syxezerocheck.wuaze.com/api/puan.php?card={card}&i=1"

# --- Veritabanı Ayarları ---
DB_NAME = "bot_data.db"

def setup_database():
    """Veritabanını ve tabloları oluşturur."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
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
    cursor.execute("INSERT OR IGNORE INTO maintenance (api_name) VALUES (?)", ('Auth',))
    cursor.execute("INSERT OR IGNORE INTO maintenance (api_name) VALUES (?)", ('Puan',))
    conn.commit()
    conn.close()

# --- Yardımcı Fonksiyonlar ---

async def is_admin(user_id: int) -> bool:
    """Kullanıcının admin olup olmadığını kontrol eder."""
    return user_id == ADMIN_ID

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Kullanıcının zorunlu kanallara üye olup olmadığını kontrol eder."""
    try:
        member_main = await context.bot.get_chat_member(chat_id=MAIN_CHANNEL, user_id=user_id)
        member_chat = await context.bot.get_chat_member(chat_id=CHAT_GROUP, user_id=user_id)
        if member_main.status not in ['member', 'administrator', 'creator'] or \
           member_chat.status not in ['member', 'administrator', 'creator']:
            return False
        return True
    except Exception as e:
        logging.error(f"Üyelik kontrol hatası: {e}")
        return False

def get_user(user_id: int):
    """Veritabanından kullanıcı bilgilerini alır."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    return user_data

def update_or_create_user(user_id: int, username: str):
    """Kullanıcıyı veritabanına ekler veya günceller."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = datetime.date.today().isoformat()
    cursor.execute("""
    INSERT INTO users (user_id, username, last_credit_reset) VALUES (?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
    """, (user_id, username, today))
    conn.commit()
    conn.close()
    
def check_and_reset_credits(user_id: int):
    """Günlük kredi yenileme işlemini yapar."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    today = datetime.date.today()
    cursor.execute("SELECT last_credit_reset FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result:
        last_reset = datetime.datetime.strptime(result[0], '%Y-%m-%d').date()
        if last_reset < today:
            cursor.execute("UPDATE users SET credits = 100, last_credit_reset = ? WHERE user_id = ? AND key_id IS NULL", (today.isoformat(), user_id))
            conn.commit()
    conn.close()

def is_banned(user_id: int) -> bool:
    """Kullanıcının yasaklı olup olmadığını kontrol eder."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

# --- Admin Komutları ---
async def uret_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    try:
        _, key, sure_str = context.args; sure = int(sure_str)
        conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO keys (key_value, duration_hours) VALUES (?, ?)", (key, sure))
        conn.commit(); conn.close()
        await update.message.reply_text(f"🔑 Key başarıyla oluşturuldu!\n\nKey: `{key}`\nSüre: {sure} saat", parse_mode=ParseMode.MARKDOWN)
    except Exception: await update.message.reply_text("❌ Hatalı kullanım.\nÖrnek: `/uret YENIKEY 24`")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    try:
        user_id_to_ban = int(context.args[0])
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Belirtilmedi"
        conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO banned_users (user_id, reason) VALUES (?, ?)", (user_id_to_ban, reason))
        conn.commit(); conn.close()
        await update.message.reply_text(f"🚫 Kullanıcı (`{user_id_to_ban}`) başarıyla yasaklandı.\nSebep: {reason}", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.send_message(chat_id=user_id_to_ban, text=f"🚫 Bottan yasaklandınız.\n\n*Sebep:* {reason}\n\nİtiraz etmek için yönetici ile iletişime geçin: {ADMIN_USERNAME}", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"Kullanıcıya bildirim gönderilemedi. Hata: {e}")
    except Exception: await update.message.reply_text("❌ Hatalı kullanım.\nÖrnek: `/ban 123456789 Spam yapmak`")

async def profil_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    try:
        user_id_to_check = int(context.args[0])
        user = get_user(user_id_to_check)
        if not user:
            await update.message.reply_text("Kullanıcı bulunamadı."); return
        user_id, username, credits, _, key_id, key_expires = user
        profil_mesaji = f"👤 **Kullanıcı Profili**\n\nID: `{user_id}`\nKullanıcı Adı: @{username}\n"
        if key_id:
            expires_dt = datetime.datetime.fromisoformat(key_expires)
            kalan_sure = expires_dt - datetime.datetime.now()
            profil_mesaji += f"Kredi: Sınırsız ♾️\nAktif Key: `{key_id}`\n"
            if kalan_sure.total_seconds() > 0:
                hours, remainder = divmod(int(kalan_sure.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                profil_mesaji += f"Kalan Süre: {hours} saat {minutes} dakika\n"
            else: profil_mesaji += "Süre: Dolmuş ❌\n"
        else: profil_mesaji += f"Kredi: {credits}\n"
        await update.message.reply_text(profil_mesaji, parse_mode=ParseMode.MARKDOWN)
    except Exception: await update.message.reply_text("❌ Hatalı kullanım.\nÖrnek: `/profil 123456789`")

async def bakim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    try:
        api_name = context.args[0].capitalize()
        if api_name not in ['Auth', 'Puan']:
            await update.message.reply_text("❌ Geçersiz API ismi. 'Auth' veya 'Puan' olmalı."); return
        conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
        cursor.execute("UPDATE maintenance SET is_active = 0 WHERE api_name = ?", (api_name,)); conn.commit(); conn.close()
        await update.message.reply_text(f"🔧 `{api_name}` API'si bakıma alındı.", parse_mode=ParseMode.MARKDOWN)
    except IndexError: await update.message.reply_text("❌ Hatalı kullanım.\nÖrnek: `/bakim Auth`")
        
async def aktifet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id): return
    try:
        api_name = context.args[0].capitalize()
        if api_name not in ['Auth', 'Puan']:
            await update.message.reply_text("❌ Geçersiz API ismi. 'Auth' veya 'Puan' olmalı."); return
        conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
        cursor.execute("UPDATE maintenance SET is_active = 1 WHERE api_name = ?", (api_name,)); conn.commit(); conn.close()
        await update.message.reply_text(f"✅ `{api_name}` API'si aktif edildi.", parse_mode=ParseMode.MARKDOWN)
    except IndexError: await update.message.reply_text("❌ Hatalı kullanım.\nÖrnek: `/aktifet Puan`")

# --- Kullanıcı Komutları ve Bot Mantığı ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("🚫 Üzgünüz, bu botu kullanmaktan yasaklandınız."); return
    update_or_create_user(user.id, user.username)
    if not await check_membership(user.id, context):
        keyboard = [
            [InlineKeyboardButton("🔗 Ana Kanal", url=f"https://t.me/{MAIN_CHANNEL.replace('@', '')}")],
            [InlineKeyboardButton("💬 Sohbet Grubu", url=f"https://t.me/{CHAT_GROUP.replace('@', '')}")],
            [InlineKeyboardButton("✅ Katıldım", callback_data="join_check")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "**Zirve Finans Checker'a Hoş Geldiniz!** 🚀\n\n"
            "Botumuzu kullanmaya başlamadan önce lütfen aşağıdaki kanallara katılın. "
            "Katıldıktan sonra 'Katıldım' butonuna basarak devam edebilirsiniz.",
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
        )
    else: await send_main_menu(update, context)

async def join_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if await check_membership(query.from_user.id, context):
        await query.edit_message_text(text="✅ Teşekkürler! Kanallara katılımınız doğrulandı. Artık botu kullanabilirsiniz.")
        await send_main_menu(update, context, query.message.chat_id)
    else: await query.answer("❗️ Lütfen belirtilen tüm kanallara katıldığınızdan emin olun.", show_alert=True)

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    if chat_id is None: chat_id = update.effective_chat.id
    keyboard = [[InlineKeyboardButton("💳 Check İşlemleri", callback_data="go_to_check")],
                [InlineKeyboardButton("👤 Profilim", callback_data="show_me")],
                [InlineKeyboardButton("🔑 Key Kullan", callback_data="use_key_prompt")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=chat_id, text="**Ana Menü**\n\nLütfen yapmak istediğiniz işlemi seçin:",
                                   reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id): return
    # ADMİN İÇİN ÖZEL MESAJ
    if await is_admin(user_id):
        profil_mesaji = f"👑 **Patron Profili**\n\nKullanıcı Adı: @{update.effective_user.username}\nKredi: Sınırsız ♾️\n_Senin kredin bitmez reisim._"
        await update.message.reply_text(profil_mesaji, parse_mode=ParseMode.MARKDOWN)
        return
    # Normal kullanıcılar için devam...
    check_and_reset_credits(user_id)
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Sisteme kayıtlı değilsiniz. /start atın."); return
    _, username, credits, _, key_id, key_expires = user
    profil_mesaji = f"👤 **Profil Bilgileriniz**\n\nKullanıcı Adı: @{username}\n"
    if key_id:
        if key_expires:
            expires_dt = datetime.datetime.fromisoformat(key_expires)
            kalan_sure = expires_dt - datetime.datetime.now()
            if kalan_sure.total_seconds() > 0:
                hours, remainder = divmod(int(kalan_sure.total_seconds()), 3600); minutes, _ = divmod(remainder, 60)
                profil_mesaji += f"Kredi: Sınırsız ♾️\nKalan Süre: {hours} saat {minutes} dakika ⏰\n"
            else:
                conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
                cursor.execute("UPDATE users SET key_id = NULL, key_expires = NULL, credits = 100 WHERE user_id = ?", (user_id,))
                conn.commit(); conn.close()
                profil_mesaji += f"Kredi: 100 💳 (Key süreniz doldu)\n"
    else:
        profil_mesaji += f"Kredi: {credits} 💳\n_Krediler her gün yenilenir._"
    await update.message.reply_text(profil_mesaji, parse_mode=ParseMode.MARKDOWN)

async def key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id): return
    try:
        user_key = context.args[0]
        conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
        cursor.execute("SELECT duration_hours, is_used FROM keys WHERE key_value = ?", (user_key,))
        key_data = cursor.fetchone()
        if not key_data: await update.message.reply_text("❌ Geçersiz key."); conn.close(); return
        duration, is_used = key_data
        if is_used: await update.message.reply_text("❌ Bu key daha önce kullanılmış."); conn.close(); return
        now = datetime.datetime.now(); expires_at = now + datetime.timedelta(hours=duration)
        cursor.execute("UPDATE keys SET is_used = 1, used_by = ?, used_at = ? WHERE key_value = ?", (user_id, now, user_key))
        cursor.execute("UPDATE users SET key_id = ?, key_expires = ?, credits = 99999 WHERE user_id = ?", (user_key, expires_at.isoformat(), user_id))
        conn.commit(); conn.close()
        await update.message.reply_text(f"✅ Key aktif edildi! **{duration} saat** boyunca sınırsız check hakkı.", parse_mode=ParseMode.MARKDOWN)
    except IndexError: await update.message.reply_text("❌ Kullanım: `/key ABC-123`")

# --- CHECKER CONVERSATION HANDLER ---

CHOOSE_API, CHOOSE_CHECK_TYPE, SINGLE_CHECK, MASS_CHECK = range(4)

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id): return ConversationHandler.END
    if not await check_membership(user_id, context):
        await update.message.reply_text("❗️ Check yapabilmek için kanallara katılmalısınız. /start atın."); return ConversationHandler.END
    check_and_reset_credits(user_id)
    user_data = get_user(user_id)
    credits = user_data[2]; key_active = user_data[4] is not None
    if not await is_admin(user_id) and not key_active and credits <= 0:
        await update.message.reply_text("😔 Krediniz bitti. /key komutuyla key kullanın."); return ConversationHandler.END
    conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
    cursor.execute("SELECT api_name, is_active FROM maintenance"); maintenance_status = {row[0]: bool(row[1]) for row in cursor.fetchall()}; conn.close()
    keyboard = []
    if maintenance_status.get('Auth', True): keyboard.append([InlineKeyboardButton("💳 Auth", callback_data="api_auth")])
    else: keyboard.append([InlineKeyboardButton("🔧 Auth (Bakımda)", callback_data="disabled")])
    if maintenance_status.get('Puan', True): keyboard.append([InlineKeyboardButton("💰 Puan", callback_data="api_puan")])
    else: keyboard.append([InlineKeyboardButton("🔧 Puan (Bakımda)", callback_data="disabled")])
    keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel_check")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Lütfen kullanmak istediğiniz API'yi seçin:", reply_markup=reply_markup)
    return CHOOSE_API

async def choose_api_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    context.user_data['api'] = query.data.split('_')[1]
    keyboard = [[InlineKeyboardButton("☝️ Tekli Check", callback_data="type_single")],
                [InlineKeyboardButton("🗂️ Mass Check (TXT)", callback_data="type_mass")],
                [InlineKeyboardButton("⬅️ Geri", callback_data="go_back_api")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=f"**API: {context.user_data['api'].capitalize()}**\n\nCheck türünü seçin.", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return CHOOSE_CHECK_TYPE

async def choose_check_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    check_type = query.data.split('_')[1]
    if check_type == 'single':
        await query.edit_message_text(text="Kart bilgilerini gönder:\n`0123456789123456|03|28|123`", parse_mode=ParseMode.MARKDOWN)
        return SINGLE_CHECK
    elif check_type == 'mass':
        await query.edit_message_text(text="Kart bilgilerini içeren `.txt` dosyası gönder.", parse_mode=ParseMode.MARKDOWN)
        return MASS_CHECK
    return ConversationHandler.END

def check_card_api(card: str, api_type: str) -> str:
    try:
        url = (AUTH_API_URL if api_type == 'auth' else PUAN_API_URL).format(card=quote(card))
        response = requests.get(url, timeout=15); response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e: return f"API Hatası: {e}"
    except Exception as e: return f"Bilinmeyen Hata: {e}"

async def single_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    card_info = update.message.text; user_id = update.effective_user.id; api_type = context.user_data.get('api')
    user_data = get_user(user_id); credits = user_data[2]; key_active = user_data[4] is not None
    is_user_admin = await is_admin(user_id)
    if not is_user_admin and not key_active and credits <= 0:
        await update.message.reply_text("😔 Krediniz bitti."); return ConversationHandler.END
    parts = card_info.split('|')
    if len(parts) != 4 or not all(p.isdigit() for p in (parts[0], parts[1], parts[2], parts[3])):
        await update.message.reply_text("❌ Geçersiz format."); return SINGLE_CHECK
    if api_type == 'auth' and parts[3] == '000':
        await update.message.reply_text("❌ Auth API, CVV'si `000` olan kartları kontrol etmez."); return ConversationHandler.END
    msg = await update.message.reply_text("⏳ Kontrol ediliyor..."); api_response = check_card_api(card_info, api_type)
    await msg.edit_text(f"**Sonuç:**\n\n`{card_info}`\n`{api_response}`", parse_mode=ParseMode.MARKDOWN)
    if not is_user_admin and not key_active:
        conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
        cursor.execute("UPDATE users SET credits = credits - 1 WHERE user_id = ?", (user_id,))
        conn.commit(); conn.close()
    return ConversationHandler.END
    
async def mass_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id; api_type = context.user_data.get('api')
    user_data = get_user(user_id); credits = user_data[2]; key_active = user_data[4] is not None; is_user_admin = await is_admin(user_id)
    if not update.message.document or not update.message.document.file_name.endswith('.txt'):
        await update.message.reply_text("Lütfen `.txt` dosyası gönderin."); return MASS_CHECK
    txt_file = await update.message.document.get_file()
    cards = [line.strip() for line in (await txt_file.download_as_bytearray()).decode('utf-8').strip().split('\n') if line.strip()]
    if not cards: await update.message.reply_text("❌ Dosya boş."); return ConversationHandler.END
    if api_type == 'auth' and any(card.split('|')[-1] == '000' for card in cards if len(card.split('|')) == 4):
        await update.message.reply_text("❌ Dosyada CVV'si `000` olan kartlar var. Auth API desteklemiyor."); return ConversationHandler.END
    if not is_user_admin and not key_active and len(cards) > credits:
        await update.message.reply_text(f"😔 Yetersiz kredi. Gerekli: {len(cards)}, Mevcut: {credits}."); return ConversationHandler.END
    status_msg = await update.message.reply_text(f"✅ Dosya alındı. {len(cards)} kart kontrol ediliyor...")
    approved, declined, total_cards = [], [], len(cards)
    for i, card in enumerate(cards):
        parts = card.split('|')
        if len(parts) != 4 or not all(p.isdigit() for p in (parts[0], parts[1], parts[2], parts[3])):
            declined.append(f"{card} | Geçersiz Format"); continue
        api_response = check_card_api(card, api_type)
        if "APPROVED" in api_response.upper() or "CVV MATCHED" in api_response.upper() or "SUCCESS" in api_response.upper(): approved.append(f"{card} | {api_response}")
        else: declined.append(f"{card} | {api_response}")
        progress = i + 1; percentage = (progress / total_cards) * 100
        bar = '█' * int(10 * progress // total_cards) + '─' * (10 - int(10 * progress // total_cards))
        if progress % 5 == 0 or progress == total_cards:
            try: await status_msg.edit_text(f"⏳ `{progress}/{total_cards}`\n[{bar}] {percentage:.1f}%\n\n✅ Approved: {len(approved)}\n❌ Declined: {len(declined)}", parse_mode=ParseMode.MARKDOWN); await asyncio.sleep(0.5)
            except Exception: pass
    if not is_user_admin and not key_active:
        conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
        cursor.execute("UPDATE users SET credits = credits - ? WHERE user_id = ?", (total_cards, user_id)); conn.commit(); conn.close()
    await status_msg.delete()
    await update.message.reply_text(f"🏁 **Check Tamamlandı!**\n\nToplam: {total_cards}\n✅ Approved: {len(approved)}\n❌ Declined: {len(declined)}")
    if approved:
        with open("APPROVED.txt", "w", encoding="utf-8") as f: f.write("\n".join(approved))
        await update.message.reply_document(document=open("APPROVED.txt", "rb"), caption="✅ Approved Kartlar")
    if declined:
        with open("DECLINED.txt", "w", encoding="utf-8") as f: f.write("\n".join(declined))
        await update.message.reply_document(document=open("DECLINED.txt", "rb"), caption="❌ Declined Kartlar")
    return ConversationHandler.END

async def cancel_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); await query.edit_message_text("İşlem iptal edildi.")
    return ConversationHandler.END

async def go_back_to_api_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Bu fonksiyon check_command'ın bir kopyası gibi çalışacak ama query'yi düzenleyecek
    await check_command(update.callback_query, context)
    return CHOOSE_API

async def disabled_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Bu API şu anda bakımda.", show_alert=True)

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data == 'show_me': await me_command(query, context)
    elif query.data == 'use_key_prompt': await query.message.reply_text("`/key <anahtar>` komutunu kullanın.")

def main() -> None:
    setup_database()
    application = Application.builder().token(TOKEN).build()
    # Admin
    application.add_handler(CommandHandler("uret", uret_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("profil", profil_command))
    application.add_handler(CommandHandler("bakim", bakim_command))
    application.add_handler(CommandHandler("aktifet", aktifet_command))
    # User
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("key", key_command))
    application.add_handler(CommandHandler("me", me_command))
    # Conversation
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
    # Callbacks
    application.add_handler(CallbackQueryHandler(join_check_callback, pattern='^join_check$'))
    application.add_handler(CallbackQueryHandler(disabled_callback, pattern='^disabled$'))
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern='^(show_me|use_key_prompt)$'))
    
    print("Bot çalışıyor amk...")
    application.run_polling()

if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
    main()

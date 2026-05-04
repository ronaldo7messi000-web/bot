import logging
import sqlite3
import os
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ─── CONFIG ───────────────────────────────────────────────
BOT_TOKEN = "8689913690:AAF7IqoXXTU0VSqtmpbYmoM8KJ3_INUSfN4"
ADMIN_IDS = [6702104500]
ADMIN_USERNAME = "@Racxkiez"
BOT_USERNAME = "BrainrotTrade_uZbot"

PAGE_SIZE = 1

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brainrot.db")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── HELPERS ──────────────────────────────────────────────
def esc(text):
    if not text:
        return "—"
    for ch in ["_", "*", "`", "["]:
        text = text.replace(ch, f"\\{ch}")
    return text

def is_admin(uid):
    return uid in ADMIN_IDS

# ─── STATES ───────────────────────────────────────────────
(
    MAIN_MENU,
    SALE_PHOTO, SALE_NAME, SALE_DESC, SALE_PRICE,
    TRADE_PHOTO, TRADE_NAME, TRADE_DESC, TRADE_WANT,
    ADMIN_DELETE_ID,
    BROADCAST_MSG,
    PROMO_CREATE,
    PROMO_USE,
    ADMIN_PROMO_DESC,
) = range(14)

# ─── DB ───────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id   INTEGER PRIMARY KEY,
            username  TEXT,
            full_name TEXT
        )
    """)
    existing = {row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()}
    for col, definition in [
        ("referred_by", "INTEGER"),
        ("weekly_refs",  "INTEGER DEFAULT 0"),
        ("blocked",      "INTEGER DEFAULT 0"),
    ]:
        if col not in existing:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")

    c.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            type        TEXT NOT NULL,
            name        TEXT NOT NULL,
            description TEXT,
            price       TEXT,
            want        TEXT,
            photo_id    TEXT,
            promoted    INTEGER DEFAULT 0,
            active      INTEGER DEFAULT 1,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            code       TEXT UNIQUE NOT NULL,
            owner_id   INTEGER NOT NULL,
            use_count  INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS promo_uses (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            code     TEXT NOT NULL,
            user_id  INTEGER NOT NULL,
            UNIQUE(code, user_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    default_desc = (
        "🎁 Har hafta *dushanba* kuni top 1 va top 2 eng ko'p ishlatilgan "
        "promo kod egalariga brainrot sovg'a qilinadi!"
    )
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('promo_desc', ?)", (default_desc,))

    conn.commit()
    conn.close()
    logger.info(f"DB initialized at {DB_PATH}")

def save_user(user):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    if c.fetchone():
        c.execute("UPDATE users SET username=?, full_name=? WHERE user_id=?",
                  (user.username, user.full_name, user.id))
    else:
        c.execute("INSERT INTO users (user_id, username, full_name) VALUES (?,?,?)",
                  (user.id, user.username, user.full_name))
    conn.commit()
    conn.close()

def get_setting(key):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ""

def set_setting(key, value):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()

# ─── KEYBOARDS ────────────────────────────────────────────
def main_menu_kb(uid):
    buttons = [
        [KeyboardButton("🛒 Sotish"), KeyboardButton("🔄 Trade")],
        [KeyboardButton("📋 Sotish e'lonlari"), KeyboardButton("📋 Trade e'lonlari")],
        [KeyboardButton("🎟 Promo kod"), KeyboardButton("📞 Bog'lanish")],
    ]
    if is_admin(uid):
        buttons.append([KeyboardButton("⚙️ Admin panel")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📢 Hammaga xabar"), KeyboardButton("📊 Statistika")],
        [KeyboardButton("🗑 E'lonni o'chirish"), KeyboardButton("✅ Reklamani faollashtirish")],
        [KeyboardButton("✏️ Promo tavsifni o'zgartirish")],
        [KeyboardButton("🔙 Asosiy menyu")],
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Bekor qilish")]], resize_keyboard=True)

def promo_menu_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("➕ Promo kod yaratish"), KeyboardButton("🔑 Promo kod ishlatish")],
        [KeyboardButton("🏆 Top 5 promo kodlar"), KeyboardButton("🔙 Asosiy menyu")],
    ], resize_keyboard=True)

def listing_kb(user_id, has_more, listing_type, offset):
    buttons = [[InlineKeyboardButton("💬 Bog'lanish", url=f"tg://user?id={user_id}")]]
    if has_more:
        buttons.append([InlineKeyboardButton("➡️ Boshqa", callback_data=f"page_{listing_type}_{offset}")])
    return InlineKeyboardMarkup(buttons)

def contact_only_kb(user_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("💬 Bog'lanish", url=f"tg://user?id={user_id}")]])

# ─── /start ───────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    # Clear any leftover state so user always starts fresh
    context.user_data.clear()
    await update.message.reply_text(
        "*BrainrotTrade*'ga xush kelibsiz!\n\n"
        "Bu yerda siz brainrot *sotish* yoki *trade* qilishingiz mumkin!\n\n"
        "Quyidagi menyudan tanlang 👇",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(update.effective_user.id)
    )
    return MAIN_MENU

# ─── LISTINGS ─────────────────────────────────────────────
async def send_listing(target, row, is_last, has_more, listing_type, next_offset):
    lid, user_id, name, desc, price, want, photo_id, promoted = row
    badge = "📣 *REKLAMA* | " if promoted else ""

    if listing_type == "sale":
        caption = (
            f"{badge}🧠 *{esc(name)}*\n"
            f"🆔 E'lon ID: `{lid}`\n"
            f"💬 {esc(desc)}\n"
            f"💰 Narx: *{esc(price)}*"
        )
    else:
        caption = (
            f"{badge}🧠 *{esc(name)}*\n"
            f"🆔 E'lon ID: `{lid}`\n"
            f"💬 {esc(desc)}\n"
            f"🔄 Trade uchun: *{esc(want)}*"
        )

    kb = listing_kb(user_id, has_more, listing_type, next_offset) if is_last else contact_only_kb(user_id)

    if photo_id:
        await target.reply_photo(photo=photo_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
    else:
        await target.reply_text(caption, parse_mode="Markdown", reply_markup=kb)

async def view_listings(update: Update, context: ContextTypes.DEFAULT_TYPE, listing_type: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT l.id, l.user_id, l.name, l.description, l.price, l.want, l.photo_id, l.promoted
        FROM listings l WHERE l.type=? AND l.active=1
        ORDER BY l.promoted DESC, l.created_at DESC
        LIMIT ? OFFSET 0
    """, (listing_type, PAGE_SIZE + 1))
    rows = c.fetchall()
    conn.close()

    type_label = "Sotish" if listing_type == "sale" else "Trade"
    if not rows:
        await update.message.reply_text(
            f"😔 Hozircha *{type_label}* e'lonlari yo'q.\nBirinchi bo'lib e'lon qo'shing!",
            parse_mode="Markdown"
        )
        return

    has_more = len(rows) > PAGE_SIZE
    await update.message.reply_text(f"📋 *{type_label} e'lonlari:*", parse_mode="Markdown")
    for i, row in enumerate(rows[:PAGE_SIZE]):
        is_last = (i == PAGE_SIZE - 1)
        await send_listing(update.message, row, is_last, has_more, listing_type, PAGE_SIZE)

async def page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    listing_type, offset = parts[1], int(parts[2])

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT l.id, l.user_id, l.name, l.description, l.price, l.want, l.photo_id, l.promoted
        FROM listings l WHERE l.type=? AND l.active=1
        ORDER BY l.promoted DESC, l.created_at DESC
        LIMIT ? OFFSET ?
    """, (listing_type, PAGE_SIZE + 1, offset))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await query.message.reply_text("😔 Boshqa brainrotlar qolmadi!")
        return

    has_more = len(rows) > PAGE_SIZE
    for i, row in enumerate(rows[:PAGE_SIZE]):
        is_last = (i == PAGE_SIZE - 1)
        await send_listing(query.message, row, is_last, has_more, listing_type, offset + PAGE_SIZE)

# ─── POST SALE ────────────────────────────────────────────
async def sale_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["listing"] = {"type": "sale"}
    await update.message.reply_text("🖼 Brainrot rasmini yuboring:", reply_markup=cancel_kb())
    return SALE_PHOTO

async def sale_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        return await cancel(update, context)
    if not update.message.photo:
        await update.message.reply_text("❗ Iltimos rasm yuboring.")
        return SALE_PHOTO
    context.user_data["listing"]["photo_id"] = update.message.photo[-1].file_id
    await update.message.reply_text("✏️ Brainrot nomini kiriting:")
    return SALE_NAME

async def sale_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        return await cancel(update, context)
    context.user_data["listing"]["name"] = update.message.text
    await update.message.reply_text("💬 Izoh kiriting:")
    return SALE_DESC

async def sale_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        return await cancel(update, context)
    context.user_data["listing"]["desc"] = update.message.text
    await update.message.reply_text("💰 Narx kiriting:")
    return SALE_PRICE

async def sale_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        return await cancel(update, context)
    d = context.user_data["listing"]
    d["price"] = update.message.text
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO listings (user_id, type, name, description, price, photo_id) VALUES (?,?,?,?,?,?)",
        (update.effective_user.id, "sale", d["name"], d["desc"], d["price"], d["photo_id"])
    )
    conn.commit()
    conn.close()
    context.user_data.pop("listing", None)
    await update.message.reply_text(
        f"✅ *{esc(d['name'])}* sotish e'loni qo'shildi!",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(update.effective_user.id)
    )
    return MAIN_MENU

# ─── POST TRADE ───────────────────────────────────────────
async def trade_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["listing"] = {"type": "trade"}
    await update.message.reply_text("🖼 Brainrot rasmini yuboring:", reply_markup=cancel_kb())
    return TRADE_PHOTO

async def trade_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        return await cancel(update, context)
    if not update.message.photo:
        await update.message.reply_text("❗ Iltimos rasm yuboring.")
        return TRADE_PHOTO
    context.user_data["listing"]["photo_id"] = update.message.photo[-1].file_id
    await update.message.reply_text("✏️ Brainrot nomini kiriting:")
    return TRADE_NAME

async def trade_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        return await cancel(update, context)
    context.user_data["listing"]["name"] = update.message.text
    await update.message.reply_text("💬 Izoh kiriting:")
    return TRADE_DESC

async def trade_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        return await cancel(update, context)
    context.user_data["listing"]["desc"] = update.message.text
    await update.message.reply_text("🔄 Qaysi brainrot uchun trade qilmoqchisiz?")
    return TRADE_WANT

async def trade_want(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        return await cancel(update, context)
    d = context.user_data["listing"]
    d["want"] = update.message.text
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO listings (user_id, type, name, description, want, photo_id) VALUES (?,?,?,?,?,?)",
        (update.effective_user.id, "trade", d["name"], d["desc"], d["want"], d["photo_id"])
    )
    conn.commit()
    conn.close()
    context.user_data.pop("listing", None)
    await update.message.reply_text(
        f"✅ *{esc(d['name'])}* trade e'loni qo'shildi!",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(update.effective_user.id)
    )
    return MAIN_MENU

# ─── PROMO CODE SYSTEM ────────────────────────────────────
async def promo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = get_setting("promo_desc")
    await update.message.reply_text(
        f"🎟 *Promo Kod*\n\n{desc}",
        parse_mode="Markdown",
        reply_markup=promo_menu_kb()
    )
    # ✅ FIX: Stay in MAIN_MENU so promo sub-buttons are reachable
    return MAIN_MENU

async def promo_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✏️ Promo kodingiz nomini kiriting:\n"
        "_(faqat harflar, raqamlar va _ belgisi, max 20 belgi)_",
        parse_mode="Markdown",
        reply_markup=cancel_kb()
    )
    return PROMO_CREATE

async def promo_create_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        return await cancel(update, context)

    code = update.message.text.strip().upper()
    uid = update.effective_user.id

    import re
    if not re.match(r"^[A-Z0-9_]{1,20}$", code):
        await update.message.reply_text(
            "❗ Faqat harflar, raqamlar va _ belgisi ishlatilishi mumkin (max 20 belgi).\nQaytadan kiriting:"
        )
        return PROMO_CREATE

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT code FROM promo_codes WHERE owner_id=?", (uid,))
    existing = c.fetchone()
    if existing:
        conn.close()
        await update.message.reply_text(
            f"❗ Sizda allaqachon promo kod mavjud: *{existing[0]}*\n"
            f"Har bir foydalanuvchi faqat 1 ta promo kod yarata oladi.",
            parse_mode="Markdown",
            reply_markup=main_menu_kb(uid)
        )
        return MAIN_MENU

    c.execute("SELECT owner_id FROM promo_codes WHERE code=?", (code,))
    if c.fetchone():
        conn.close()
        await update.message.reply_text(
            f"❗ *{code}* promo kodi allaqachon band. Boshqa nom tanlang:",
            parse_mode="Markdown"
        )
        return PROMO_CREATE

    c.execute("INSERT INTO promo_codes (code, owner_id) VALUES (?,?)", (code, uid))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Promo kodingiz yaratildi: *{code}*\n\n"
        f"Do'stlaringizga yuboring — ular ishlatgan sari reytingda ko'tarilasiz!",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(uid)
    )
    return MAIN_MENU

async def promo_use_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔑 Ishlatmoqchi bo'lgan promo kodni kiriting:",
        reply_markup=cancel_kb()
    )
    return PROMO_USE

async def promo_use_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        return await cancel(update, context)

    code = update.message.text.strip().upper()
    uid = update.effective_user.id

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT owner_id FROM promo_codes WHERE code=?", (code,))
    row = c.fetchone()

    if not row:
        conn.close()
        await update.message.reply_text(
            "❗ Bunday promo kod topilmadi. Tekshirib qaytadan kiriting:"
        )
        return PROMO_USE  # ✅ Stay in PROMO_USE state so user can retry

    owner_id = row[0]

    if owner_id == uid:
        conn.close()
        await update.message.reply_text(
            "❗ O'zingizning promo kodingizni ishlata olmaysiz!",
            reply_markup=main_menu_kb(uid)
        )
        return MAIN_MENU

    # ✅ FIX: Check if already used BEFORE attempting insert
    c.execute("SELECT id FROM promo_uses WHERE code=? AND user_id=?", (code, uid))
    already_used = c.fetchone()
    if already_used:
        conn.close()
        await update.message.reply_text(
            f"❗ Siz *{code}* promo kodini allaqachon ishlatgansiz!",
            parse_mode="Markdown",
            reply_markup=main_menu_kb(uid)
        )
        return MAIN_MENU

    # Safe to insert now
    try:
        c.execute("INSERT INTO promo_uses (code, user_id) VALUES (?,?)", (code, uid))
        c.execute("UPDATE promo_codes SET use_count = use_count + 1 WHERE code=?", (code,))
        conn.commit()

        c.execute("SELECT use_count FROM promo_codes WHERE code=?", (code,))
        new_count = c.fetchone()[0]
        conn.close()

        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=f"🎉 Promo kodingiz *{code}* ishlatildi!\n📊 Jami foydalanishlar: *{new_count}*",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        await update.message.reply_text(
            f"✅ *{code}* promo kodi muvaffaqiyatli ishlatildi!",
            parse_mode="Markdown",
            reply_markup=main_menu_kb(uid)
        )

    except sqlite3.IntegrityError:
        # Fallback safety net (race condition)
        try:
            conn.close()
        except Exception:
            pass
        await update.message.reply_text(
            f"❗ Siz *{code}* promo kodini allaqachon ishlatgansiz!",
            parse_mode="Markdown",
            reply_markup=main_menu_kb(uid)
        )

    return MAIN_MENU

async def promo_top5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT p.code, p.use_count, u.username, u.full_name
        FROM promo_codes p
        LEFT JOIN users u ON p.owner_id = u.user_id
        ORDER BY p.use_count DESC
        LIMIT 5
    """)
    rows = c.fetchall()
    conn.close()

    desc = get_setting("promo_desc")
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

    if not rows or rows[0][1] == 0:
        await update.message.reply_text(
            f"😔 Hali hech qanday promo kod ishlatilmagan.\n\n_{desc}_",
            parse_mode="Markdown",
            reply_markup=promo_menu_kb()
        )
        return

    text = f"🏆 *Top 5 promo kodlar:*\n\n"
    for i, (code, count, uname, fname) in enumerate(rows):
        owner = f"@{uname}" if uname else (fname or "Noma'lum")
        text += f"{medals[i]} *{code}* — {count} marta | {esc(owner)}\n"

    text += f"\n\n📌 _{desc}_"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=promo_menu_kb())

# ─── ADMIN: EDIT PROMO DESC ───────────────────────────────
async def admin_promo_desc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = get_setting("promo_desc")
    await update.message.reply_text(
        f"✏️ Joriy promo tavsif:\n_{current}_\n\nYangi tavsifni kiriting:",
        parse_mode="Markdown",
        reply_markup=cancel_kb()
    )
    return ADMIN_PROMO_DESC

async def admin_promo_desc_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        await update.message.reply_text("❌ Bekor qilindi.", reply_markup=admin_kb())
        return MAIN_MENU
    set_setting("promo_desc", update.message.text)
    await update.message.reply_text("✅ Promo tavsif yangilandi!", reply_markup=admin_kb())
    return MAIN_MENU

# ─── ADMIN: ACTIVATE PROMO ────────────────────────────────
async def activate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        lid = int(update.message.text.replace("/activate_", "").strip())
        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE listings SET promoted=1 WHERE id=?", (lid,))
        c.execute("SELECT user_id, name FROM listings WHERE id=?", (lid,))
        row = c.fetchone()
        conn.commit()
        conn.close()
        if row:
            owner_id, name = row
            try:
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=f"🎉 *{esc(name)}* e'loningiz reklama sifatida faollashtirildi! 📣",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        await update.message.reply_text(f"✅ E'lon #{lid} reklama faollashtirildi!")
    except Exception:
        await update.message.reply_text("❗ Format: /activate_5")

# ─── ADMIN: BROADCAST ─────────────────────────────────────
async def broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        await update.message.reply_text("❌ Bekor qilindi.", reply_markup=admin_kb())
        return MAIN_MENU

    photo_id = None
    text = ""
    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
        text = update.message.caption or ""
    else:
        text = update.message.text

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    all_users = c.fetchall()
    conn.close()

    sent, failed = 0, 0
    for (uid,) in all_users:
        try:
            if photo_id:
                await context.bot.send_photo(chat_id=uid, photo=photo_id, caption=text)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "deactivated" in err or "not found" in err:
                conn2 = get_conn()
                conn2.execute("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))
                conn2.commit()
                conn2.close()
            failed += 1

    await update.message.reply_text(
        f"📢 Xabar yuborildi!\n✅ Muvaffaqiyatli: {sent}\n❌ Xato: {failed}",
        reply_markup=admin_kb()
    )
    return MAIN_MENU

# ─── ADMIN: DELETE ────────────────────────────────────────
async def admin_delete_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ Bekor qilish":
        await update.message.reply_text("❌ Bekor qilindi.", reply_markup=admin_kb())
        return MAIN_MENU
    try:
        lid = int(update.message.text.strip())
        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE listings SET active=0 WHERE id=? AND active=1", (lid,))
        if c.rowcount == 0:
            conn.close()
            await update.message.reply_text("❗ Bunday ID topilmadi yoki allaqachon o'chirilgan.")
            return ADMIN_DELETE_ID
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ E'lon #{lid} o'chirildi.", reply_markup=admin_kb())
    except ValueError:
        await update.message.reply_text("❗ To'g'ri ID kiriting.")
        return ADMIN_DELETE_ID
    return MAIN_MENU

# ─── ADMIN: STATS ─────────────────────────────────────────
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE blocked=1")
    blocked = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM listings WHERE active=1 AND type='sale'")
    sales = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM listings WHERE active=1 AND type='trade'")
    trades = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM listings WHERE promoted=1 AND active=1")
    promos = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM promo_codes")
    pcodes = c.fetchone()[0]
    conn.close()

    await update.message.reply_text(
        f"📊 *Statistika:*\n\n"
        f"👥 Jami foydalanuvchilar: *{total}*\n"
        f"🚫 Bot bloklagan: *{blocked}*\n"
        f"📨 Xabar olishi mumkin: *{total - blocked}*\n"
        f"🛒 Sotish e'lonlari: *{sales}*\n"
        f"🔄 Trade e'lonlari: *{trades}*\n"
        f"📣 Reklamalar: *{promos}*\n"
        f"🎟 Promo kodlar: *{pcodes}*",
        parse_mode="Markdown"
    )
    return MAIN_MENU

# ─── MAIN MENU ROUTER ─────────────────────────────────────
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    save_user(update.effective_user)

    if text == "🛒 Sotish":
        return await sale_start(update, context)
    elif text == "🔄 Trade":
        return await trade_start(update, context)
    elif text == "📋 Sotish e'lonlari":
        await view_listings(update, context, "sale")
    elif text == "📋 Trade e'lonlari":
        await view_listings(update, context, "trade")
    elif text == "🎟 Promo kod":
        return await promo_menu(update, context)
    elif text == "➕ Promo kod yaratish":
        return await promo_create_start(update, context)
    elif text == "🔑 Promo kod ishlatish":
        return await promo_use_start(update, context)
    elif text == "🏆 Top 5 promo kodlar":
        await promo_top5(update, context)
    elif text == "📞 Bog'lanish":
        await update.message.reply_text(
            f"📞 Admin bilan bog'lanish:\n{ADMIN_USERNAME}",
            reply_markup=main_menu_kb(uid)
        )
    elif text == "⚙️ Admin panel" and is_admin(uid):
        await update.message.reply_text("⚙️ Admin panel:", reply_markup=admin_kb())
    elif text == "📢 Hammaga xabar" and is_admin(uid):
        await update.message.reply_text("✍️ Xabar yozing (rasm yoki matn):", reply_markup=cancel_kb())
        return BROADCAST_MSG
    elif text == "🗑 E'lonni o'chirish" and is_admin(uid):
        await update.message.reply_text("🆔 O'chirish uchun e'lon ID sini kiriting:", reply_markup=cancel_kb())
        return ADMIN_DELETE_ID
    elif text == "📊 Statistika" and is_admin(uid):
        await show_stats(update, context)
    elif text == "✅ Reklamani faollashtirish" and is_admin(uid):
        await update.message.reply_text(
            "Faollashtirish uchun: `/activate_5`\n(5 o'rniga e'lon ID sini yozing)",
            parse_mode="Markdown"
        )
    elif text == "✏️ Promo tavsifni o'zgartirish" and is_admin(uid):
        return await admin_promo_desc_start(update, context)
    elif text == "🔙 Asosiy menyu":
        await update.message.reply_text("🏠 Asosiy menyu:", reply_markup=main_menu_kb(uid))
    elif text == "❌ Bekor qilish":
        await update.message.reply_text("❌ Bekor qilindi.", reply_markup=main_menu_kb(uid))

    return MAIN_MENU

async def cancel(update, context):
    context.user_data.pop("listing", None)
    await update.message.reply_text(
        "❌ Bekor qilindi.",
        reply_markup=main_menu_kb(update.effective_user.id)
    )
    return MAIN_MENU

# ─── FALLBACK: handles users who message after bot restart ─
async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    If a user sends any message outside an active conversation
    (e.g. after bot restart on Railway), send them back to main menu.
    """
    save_user(update.effective_user)
    await update.message.reply_text(
        "🏠 Asosiy menyu:",
        reply_markup=main_menu_kb(update.effective_user.id)
    )

# ─── MAIN ─────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            # ✅ FIX: Allow ANY message to start the conversation
            # so users aren't stuck after bot restart on Railway
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu),
        ],
        states={
            MAIN_MENU:        [MessageHandler(filters.TEXT | filters.PHOTO, handle_menu)],
            SALE_PHOTO:       [MessageHandler(filters.PHOTO | filters.TEXT, sale_photo)],
            SALE_NAME:        [MessageHandler(filters.TEXT, sale_name)],
            SALE_DESC:        [MessageHandler(filters.TEXT, sale_desc)],
            SALE_PRICE:       [MessageHandler(filters.TEXT, sale_price)],
            TRADE_PHOTO:      [MessageHandler(filters.PHOTO | filters.TEXT, trade_photo)],
            TRADE_NAME:       [MessageHandler(filters.TEXT, trade_name)],
            TRADE_DESC:       [MessageHandler(filters.TEXT, trade_desc)],
            TRADE_WANT:       [MessageHandler(filters.TEXT, trade_want)],
            ADMIN_DELETE_ID:  [MessageHandler(filters.TEXT, admin_delete_listing)],
            BROADCAST_MSG:    [MessageHandler(filters.TEXT | filters.PHOTO, broadcast_msg)],
            PROMO_CREATE:     [MessageHandler(filters.TEXT, promo_create_done)],
            PROMO_USE:        [MessageHandler(filters.TEXT, promo_use_done)],
            ADMIN_PROMO_DESC: [MessageHandler(filters.TEXT, admin_promo_desc_done)],
        },
        fallbacks=[
            CommandHandler("start", start),
        ],
        # ✅ Allow re-entering conversation from any state
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(page_callback, pattern=r"^page_(sale|trade)_\d+$"))
    app.add_handler(MessageHandler(
        filters.Regex(r"^/activate_\d+$") & filters.User(ADMIN_IDS),
        activate_command
    ))

    logger.info(f"Bot started. DB: {DB_PATH}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

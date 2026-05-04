import logging
import sqlite3
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
BOT_USERNAME = "BrainrotTrade_uZbot"  # without @

PAGE_SIZE = 1  # How many listings to show at once

DB_PATH = "brainrot.db"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def esc(text):
    """Escape special Markdown characters in user-submitted text."""
    if not text:
        return "—"
    for ch in ["_", "*", "`", "["]:
        text = text.replace(ch, f"\\{ch}")
    return text

# ─── STATES ───────────────────────────────────────────────
(
    MAIN_MENU,
    SALE_PHOTO, SALE_NAME, SALE_DESC, SALE_PRICE,
    TRADE_PHOTO, TRADE_NAME, TRADE_DESC, TRADE_WANT,
    PROMOTE_CONFIRM,
    ADMIN_DELETE_ID,
    BROADCAST_MSG,
) = range(12)

# ─── DB ───────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            referred_by INTEGER,
            weekly_refs INTEGER DEFAULT 0
        )
    """)
    c.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in c.fetchall()]
    if "referred_by" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
    if "weekly_refs" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN weekly_refs INTEGER DEFAULT 0")
    c.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price TEXT,
            want TEXT,
            photo_id TEXT,
            promoted INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_conn():
    return sqlite3.connect(DB_PATH)

def save_user(user, referred_by=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    exists = c.fetchone()
    if not exists:
        c.execute("INSERT INTO users (user_id, username, full_name, referred_by) VALUES (?,?,?,?)",
                  (user.id, user.username, user.full_name, referred_by))
    else:
        c.execute("UPDATE users SET username=?, full_name=? WHERE user_id=?",
                  (user.username, user.full_name, user.id))
    conn.commit()
    conn.close()
    return not exists  # True if new user

def is_admin(uid):
    return uid in ADMIN_IDS

# ─── KEYBOARDS ────────────────────────────────────────────
def main_menu_kb(uid):
    buttons = [
        [KeyboardButton("🛒 Sotish"), KeyboardButton("🔄 Trade")],
        [KeyboardButton("📋 Sotish e'lonlari"), KeyboardButton("📋 Trade e'lonlari")],
        [KeyboardButton("📞 Bog'lanish")],
    ]
    if is_admin(uid):
        buttons.append([KeyboardButton("⚙️ Admin panel")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def admin_kb():
    buttons = [
        [KeyboardButton("📢 Hammaga xabar")],
        [KeyboardButton("🗑 E'lonni o'chirish"), KeyboardButton("📊 Statistika")],
        [KeyboardButton("🔙 Asosiy menyu")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Bekor qilish")]], resize_keyboard=True)

def promote_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Reklama", callback_data="request_promo")]
    ])

def listing_kb(user_id, has_more: bool, listing_type: str, offset: int):
    """Contact button + Boshqa button if more listings exist."""
    buttons = [
        [InlineKeyboardButton("💬 Bog'lanish", url=f"tg://user?id={user_id}")]
    ]
    if has_more:
        buttons.append([
            InlineKeyboardButton("➡️ Boshqa", callback_data=f"page_{listing_type}_{offset}")
        ])
    return InlineKeyboardMarkup(buttons)

def next_page_kb(listing_type: str, offset: int):
    """Just the Boshqa button, attached to the last card of a page."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➡️ Boshqa", callback_data=f"page_{listing_type}_{offset}")]
    ])

def contact_only_kb(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Bog'lanish", url=f"tg://user?id={user_id}")]
    ])

# ─── /start ───────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    await update.message.reply_text(
        "*BrainrotTrade*'ga xush kelibsiz!\n\n"
        "Bu yerda siz brainrot *sotish* yoki *trade* qilishingiz mumkin!\n\n"
        "Quyidagi menyudan tanlang 👇",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(update.effective_user.id)
    )
    return MAIN_MENU

# ─── SEND ONE LISTING ─────────────────────────────────────
async def send_listing(bot_or_update, row, is_last: bool, has_more: bool, listing_type: str, next_offset: int, use_reply=True):
    """Send a single listing card. If is_last and there are more, show Boshqa button."""
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

    if is_last:
        kb = listing_kb(user_id, has_more, listing_type, next_offset)
    else:
        kb = contact_only_kb(user_id)

    if use_reply:
        # Called from message handler
        if photo_id:
            await bot_or_update.message.reply_photo(photo=photo_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
        else:
            await bot_or_update.message.reply_text(caption, parse_mode="Markdown", reply_markup=kb)
    else:
        # Called from callback query
        if photo_id:
            await bot_or_update.message.reply_photo(photo=photo_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
        else:
            await bot_or_update.message.reply_text(caption, parse_mode="Markdown", reply_markup=kb)

# ─── VIEW LISTINGS (first page) ───────────────────────────
async def view_listings(update: Update, context: ContextTypes.DEFAULT_TYPE, listing_type: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT l.id, l.user_id, l.name, l.description, l.price, l.want, l.photo_id, l.promoted
        FROM listings l
        WHERE l.type=? AND l.active=1
        ORDER BY l.promoted DESC, l.created_at DESC
        LIMIT ? OFFSET 0
    """, (listing_type, PAGE_SIZE + 1))  # fetch one extra to know if there's more
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
    page_rows = rows[:PAGE_SIZE]

    await update.message.reply_text(f"📋 *{type_label} e'lonlari:*", parse_mode="Markdown")

    for i, row in enumerate(page_rows):
        is_last = (i == len(page_rows) - 1)
        next_offset = PAGE_SIZE  # next page starts at PAGE_SIZE
        await send_listing(update, row, is_last, has_more, listing_type, next_offset, use_reply=True)

# ─── PAGINATION CALLBACK ──────────────────────────────────
async def page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")  # page_{type}_{offset}
    listing_type = parts[1]
    offset = int(parts[2])

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT l.id, l.user_id, l.name, l.description, l.price, l.want, l.photo_id, l.promoted
        FROM listings l
        WHERE l.type=? AND l.active=1
        ORDER BY l.promoted DESC, l.created_at DESC
        LIMIT ? OFFSET ?
    """, (listing_type, PAGE_SIZE + 1, offset))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await query.message.reply_text("😔 Boshqa brainrotlar qolmadi!")
        return

    has_more = len(rows) > PAGE_SIZE
    page_rows = rows[:PAGE_SIZE]

    for i, row in enumerate(page_rows):
        is_last = (i == len(page_rows) - 1)
        next_offset = offset + PAGE_SIZE
        await send_listing(query, row, is_last, has_more, listing_type, next_offset, use_reply=False)

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
    await update.message.reply_text("💬 Izoh kiriting (masalan: sifati, darajasi):")
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
    c.execute("""
        INSERT INTO listings (user_id, type, name, description, price, photo_id)
        VALUES (?,?,?,?,?,?)
    """, (update.effective_user.id, "sale", d["name"], d["desc"], d["price"], d["photo_id"]))
    lid = c.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ *{esc(d['name'])}* sotish e'loni qo'shildi!",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(update.effective_user.id)
    )

    context.user_data.pop("listing", None)
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
    await update.message.reply_text("💬 Izoh kiriting (masalan: sifati, darajasi):")
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
    c.execute("""
        INSERT INTO listings (user_id, type, name, description, want, photo_id)
        VALUES (?,?,?,?,?,?)
    """, (update.effective_user.id, "trade", d["name"], d["desc"], d["want"], d["photo_id"]))
    lid = c.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ *{esc(d['name'])}* trade e'loni qo'shildi!",
        parse_mode="Markdown",
        reply_markup=main_menu_kb(update.effective_user.id)
    )

    context.user_data.pop("listing", None)
    return MAIN_MENU

# ─── PROMOTE BUTTON CALLBACK ──────────────────────────────
async def promote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "request_promo":
        await query.answer()
        user = query.from_user
        uname = f"@{user.username}" if user.username else user.full_name

        # Send notification to admin
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"📣 *Reklama so'rovi!*\n\n"
                         f"👤 Foydalanuvchi: {uname} (`{user.id}`)\n\n"
                         f"E'loningizni tekshiring va approve qiling.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        await query.message.reply_text(
            f"✅ Reklama so'rovingiz adminga yuborildi!\n\n"
            f"👤 Admin: {ADMIN_USERNAME}",
            reply_markup=main_menu_kb(user.id)
        )

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
    users = c.fetchall()
    conn.close()

    sent, failed = 0, 0
    for (uid,) in users:
        try:
            if photo_id:
                await context.bot.send_photo(chat_id=uid, photo=photo_id, caption=text, parse_mode="Markdown")
            else:
                await context.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"📢 Xabar yuborildi!\n✅ {sent} ta\n❌ {failed} ta xato",
        reply_markup=admin_kb()
    )
    return MAIN_MENU

# ─── ADMIN: DELETE ────────────────────────────────────────
async def admin_delete_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Sizda bu amalni bajarish huquqi yo'q.", reply_markup=main_menu_kb(update.effective_user.id))
        return MAIN_MENU
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
            await update.message.reply_text("❗ Bunday ID topilmadi yoki e'lon allaqachon o'chirilgan.", reply_markup=admin_kb())
            return MAIN_MENU
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ E'lon #{lid} o'chirildi.", reply_markup=admin_kb())
    except ValueError:
        await update.message.reply_text("❗ To'g'ri ID kiriting.", reply_markup=admin_kb())
        return ADMIN_DELETE_ID
    return MAIN_MENU

# ─── ADMIN: STATS ─────────────────────────────────────────
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM listings WHERE active=1 AND type='sale'")
    sale_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM listings WHERE active=1 AND type='trade'")
    trade_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM listings WHERE promoted=1 AND active=1")
    promo_count = c.fetchone()[0]
    conn.close()

    await update.message.reply_text(
        f"📊 *Statistika:*\n\n"
        f"👥 Jami foydalanuvchilar: *{total_users}*\n"
        f"🛒 Faol sotish e'lonlari: *{sale_count}*\n"
        f"🔄 Faol trade e'lonlari: *{trade_count}*\n"
        f"📣 Faol reklamalar: *{promo_count}*",
        parse_mode="Markdown"
    )
    return MAIN_MENU

# ─── MAIN MENU ROUTER ─────────────────────────────────────
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "🛒 Sotish":
        return await sale_start(update, context)
    elif text == "🔄 Trade":
        return await trade_start(update, context)
    elif text == "📋 Sotish e'lonlari":
        await view_listings(update, context, "sale")
    elif text == "📋 Trade e'lonlari":
        await view_listings(update, context, "trade")
    elif text == "📞 Bog'lanish":
        await update.message.reply_text(
            f"📞 Admin bilan bog'lanish uchun:\n{ADMIN_USERNAME}",
            parse_mode="Markdown",
            reply_markup=main_menu_kb(uid)
        )
    elif text == "⚙️ Admin panel" and is_admin(uid):
        await update.message.reply_text("⚙️ Admin panel:", reply_markup=admin_kb())
    elif text == "📢 Hammaga xabar" and is_admin(uid):
        await update.message.reply_text("✍️ Xabar yozing (rasm yoki text):", reply_markup=cancel_kb())
        return BROADCAST_MSG
    elif text == "🗑 E'lonni o'chirish" and is_admin(uid):
        await update.message.reply_text("🆔 O'chirish uchun e'lon ID sini kiriting:", reply_markup=cancel_kb())
        return ADMIN_DELETE_ID
    elif text == "📊 Statistika" and is_admin(uid):
        await show_stats(update, context)
    elif text == "🔙 Asosiy menyu":
        await update.message.reply_text("🏠 Asosiy menyu:", reply_markup=main_menu_kb(uid))
    elif text == "❌ Bekor qilish":
        await update.message.reply_text("❌ Bekor qilindi.", reply_markup=main_menu_kb(uid))

    return MAIN_MENU

async def cancel(update, context):
    uid = update.effective_user.id
    context.user_data.pop("listing", None)
    await update.message.reply_text("❌ Bekor qilindi.", reply_markup=main_menu_kb(uid))
    return MAIN_MENU

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU:   [MessageHandler(filters.TEXT | filters.PHOTO, handle_menu)],
            SALE_PHOTO:  [MessageHandler(filters.PHOTO | filters.TEXT, sale_photo)],
            SALE_NAME:   [MessageHandler(filters.TEXT, sale_name)],
            SALE_DESC:   [MessageHandler(filters.TEXT, sale_desc)],
            SALE_PRICE:  [MessageHandler(filters.TEXT, sale_price)],
            TRADE_PHOTO: [MessageHandler(filters.PHOTO | filters.TEXT, trade_photo)],
            TRADE_NAME:  [MessageHandler(filters.TEXT, trade_name)],
            TRADE_DESC:  [MessageHandler(filters.TEXT, trade_desc)],
            TRADE_WANT:  [MessageHandler(filters.TEXT, trade_want)],
            ADMIN_DELETE_ID: [MessageHandler(filters.TEXT, admin_delete_listing)],
            BROADCAST_MSG:    [MessageHandler(filters.TEXT | filters.PHOTO, broadcast_msg)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(promote_callback, pattern=r"^request_promo$"))
    app.add_handler(CallbackQueryHandler(page_callback, pattern=r"^page_(sale|trade)_\d+$"))

    logger.info("Brainrot bot ishga tushdi!")
    app.run_polling()

if __name__ == "__main__":
    main()

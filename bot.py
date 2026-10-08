import os
import logging
import asyncio
from threading import Thread

import asyncpg
from flask import Flask

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# তোমার Telegram User ID
ADMIN_ID = 8721334265


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing!")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is missing!")


# =========================================================
# DATABASE
# =========================================================

db_pool = None


async def init_db():
    global db_pool

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=60,
    )

    async with db_pool.acquire() as conn:

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id SERIAL PRIMARY KEY,
                file_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT DEFAULT '',
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT DEFAULT '',
                name TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        default_categories = [
            "🎵 গান",
            "🎭 নাটক",
            "🎬 ভিডিও",
            "📸 ফটো",
            "🎥 মুভি",
        ]

        for category in default_categories:
            await conn.execute(
                """
                INSERT INTO categories (name)
                VALUES ($1)
                ON CONFLICT (name) DO NOTHING
                """,
                category,
            )

    logger.info("PostgreSQL database ready")


# =========================================================
# RENDER WEB SERVER
# =========================================================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bangla Vibe Bot is running!", 200


@web_app.route("/health")
def health():
    return "OK", 200


def run_web_server():
    port = int(os.getenv("PORT", "10000"))

    logger.info(f"🌐 Web server starting on port {port}")

    web_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# COMMON FUNCTIONS
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


async def save_contact(user):
    try:
        if db_pool is None:
            return

        username = user.username or ""
        name = user.full_name or ""

        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO contacts (user_id, username, name)
                VALUES ($1, $2, $3)
                """,
                user.id,
                username,
                name,
            )

    except Exception:
        logger.exception("Could not save contact")


# =========================================================
# START COMMAND
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    await save_contact(user)

    keyboard = [
        [
            InlineKeyboardButton("🎵 গান", callback_data="cat_🎵 গান"),
            InlineKeyboardButton("🎭 নাটক", callback_data="cat_🎭 নাটক"),
        ],
        [
            InlineKeyboardButton("🎬 ভিডিও", callback_data="cat_🎬 ভিডিও"),
            InlineKeyboardButton("📸 ফটো", callback_data="cat_📸 ফটো"),
        ],
        [
            InlineKeyboardButton("🎥 মুভি", callback_data="cat_🎥 মুভি"),
        ],
        [
            InlineKeyboardButton(
                "📩 Admin Contact",
                callback_data="admin_contact",
            )
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔥 *Bangla Vibe*\n\n"
        "স্বাগতম! ভিডিও দেখতে নিচের অপশন ব্যবহার করুন।",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


# =========================================================
# CATEGORY LIST
# =========================================================

async def show_category(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    category = query.data.replace("cat_", "", 1)

    try:

        async with db_pool.acquire() as conn:

            videos = await conn.fetch(
                """
                SELECT id, title
                FROM videos
                WHERE category = $1
                ORDER BY id DESC
                """,
                category,
            )

        if not videos:
            await query.edit_message_text(
                f"{category}\n\n"
                "এই ক্যাটাগরিতে এখনো কোনো ভিডিও নেই।"
            )
            return

        buttons = []

        for video in videos:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"▶️ {video['title']}",
                        callback_data=f"video_{video['id']}",
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    "🏠 Home",
                    callback_data="home",
                )
            ]
        )

        await query.edit_message_text(
            f"{category}\n\nভিডিও নির্বাচন করুন:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    except Exception:
        logger.exception("Category error")

        await query.edit_message_text(
            "❌ ভিডিও লোড করতে সমস্যা হয়েছে।"
        )


# =========================================================
# SHOW VIDEO
# =========================================================

async def show_video(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    try:

        video_id = int(query.data.replace("video_", "", 1))

        async with db_pool.acquire() as conn:

            video = await conn.fetchrow(
                """
                SELECT file_id, title, description, category
                FROM videos
                WHERE id = $1
                """,
                video_id,
            )

        if not video:
            await query.edit_message_text(
                "❌ ভিডিও পাওয়া যায়নি।"
            )
            return

        caption = f"🎬 {video['title']}"

        if video["description"]:
            caption += f"\n\n📝 {video['description']}"

        caption += f"\n\n📂 {video['category']}"

        await context.bot.send_video(
            chat_id=query.from_user.id,
            video=video["file_id"],
            caption=caption,
        )

    except Exception:
        logger.exception("Video error")

        await query.message.reply_text(
            "❌ ভিডিও পাঠাতে সমস্যা হয়েছে।"
        )


# =========================================================
# HOME BUTTON
# =========================================================

async def home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    keyboard = [
        [
            InlineKeyboardButton("🎵 গান", callback_data="cat_🎵 গান"),
            InlineKeyboardButton("🎭 নাটক", callback_data="cat_🎭 নাটক"),
        ],
        [
            InlineKeyboardButton("🎬 ভিডিও", callback_data="cat_🎬 ভিডিও"),
            InlineKeyboardButton("📸 ফটো", callback_data="cat_📸 ফটো"),
        ],
        [
            InlineKeyboardButton("🎥 মুভি", callback_data="cat_🎥 মুভি"),
        ],
    ]

    await query.edit_message_text(
        "🔥 *Bangla Vibe*\n\n"
        "ক্যাটাগরি নির্বাচন করুন:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "⛔ এই কমান্ড শুধু Admin-এর জন্য।"
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ ভিডিও যোগ করুন",
                callback_data="admin_add_video",
            )
        ],
        [
            InlineKeyboardButton(
                "➕ ক্যাটাগরি যোগ করুন",
                callback_data="admin_add_category",
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 ভিডিও মুছুন",
                callback_data="admin_delete_video",
            )
        ],
        [
            InlineKeyboardButton(
                "📨 Requests",
                callback_data="admin_requests",
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Contacts",
                callback_data="admin_contacts",
            )
        ],
    ]

    await update.message.reply_text(
        "👑 *Admin Panel*\n\n"
        "নিচের অপশন নির্বাচন করুন:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# =========================================================
# ADD VIDEO STATES
# =========================================================

VIDEO_FILE = 1
VIDEO_TITLE = 2
VIDEO_DESCRIPTION = 3
VIDEO_CATEGORY = 4


async def add_video_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    context.user_data.clear()

    await query.message.reply_text(
        "🎬 ভিডিওটি পাঠান।"
    )

    return VIDEO_FILE


async def receive_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    if not update.message.video:
        await update.message.reply_text(
            "❌ দয়া করে Telegram Video হিসেবে ভিডিও পাঠান।"
        )
        return VIDEO_FILE

    context.user_data["file_id"] = update.message.video.file_id

    await update.message.reply_text(
        "✅ ভিডিও পাওয়া গেছে।\n\n"
        "এখন ভিডিওর নাম/Title লিখুন।"
    )

    return VIDEO_TITLE


async def receive_title(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    title = update.message.text.strip()

    if not title:
        await update.message.reply_text(
            "❌ একটি ভিডিওর নাম লিখুন।"
        )
        return VIDEO_TITLE

    context.user_data["title"] = title

    await update.message.reply_text(
        "📝 ভিডিওর Description লিখুন।\n"
        "Description না চাইলে `skip` লিখুন।"
    )

    return VIDEO_DESCRIPTION


async def receive_description(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    description = update.message.text.strip()

    if description.lower() == "skip":
        description = ""

    context.user_data["description"] = description

    await update.message.reply_text(
        "📂 ভিডিওর Category লিখুন।\n\n"
        "উদাহরণ:\n"
        "🎵 গান\n"
        "🎭 নাটক\n"
        "🎬 ভিডিও\n"
        "📸 ফটো\n"
        "🎥 মুভি"
    )

    return VIDEO_CATEGORY


async def receive_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    category = update.message.text.strip()

    file_id = context.user_data.get("file_id")
    title = context.user_data.get("title")
    description = context.user_data.get("description", "")

    if not file_id or not title:
        await update.message.reply_text(
            "❌ তথ্য পাওয়া যায়নি। আবার চেষ্টা করুন।"
        )
        context.user_data.clear()
        return ConversationHandler.END

    try:

        async with db_pool.acquire() as conn:

            await conn.execute(
                """
                INSERT INTO categories (name)
                VALUES ($1)
                ON CONFLICT (name) DO NOTHING
                """,
                category,
            )

            await conn.execute(
                """
                INSERT INTO videos
                (file_id, title, description, category)
                VALUES ($1, $2, $3, $4)
                """,
                file_id,
                title,
                description,
                category,
            )

        await update.message.reply_text(
            "✅ ভিডিও সফলভাবে যোগ হয়েছে!\n\n"
            f"🎬 নাম: {title}\n"
            f"📂 Category: {category}"
        )

    except Exception:
        logger.exception("Could not save video")

        await update.message.reply_text(
            "❌ ভিডিও Save করতে সমস্যা হয়েছে।"
        )

    context.user_data.clear()

    return ConversationHandler.END


async def cancel_conversation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ কাজ বাতিল করা হয়েছে।"
    )

    return ConversationHandler.END


# =========================================================
# ADD CATEGORY
# =========================================================

CATEGORY_NAME = 10


async def add_category_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    await query.message.reply_text(
        "📂 নতুন Category-এর নাম লিখুন।"
    )

    return CATEGORY_NAME


async def receive_category_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    category = update.message.text.strip()

    if not category:
        await update.message.reply_text(
            "❌ Category-এর নাম লিখুন।"
        )
        return CATEGORY_NAME

    try:

        async with db_pool.acquire() as conn:

            await conn.execute(
                """
                INSERT INTO categories (name)
                VALUES ($1)
                ON CONFLICT (name) DO NOTHING
                """,
                category,
            )

        await update.message.reply_text(
            f"✅ Category যোগ হয়েছে:\n{category}"
        )

    except Exception:
        logger.exception("Category insert error")

        await update.message.reply_text(
            "❌ Category যোগ করতে সমস্যা হয়েছে।"
        )

    return ConversationHandler.END


# =========================================================
# DELETE VIDEO
# =========================================================

async def delete_video_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:

        async with db_pool.acquire() as conn:

            videos = await conn.fetch(
                """
                SELECT id, title
                FROM videos
                ORDER BY id DESC
                """
            )

        if not videos:
            await query.message.reply_text(
                "📭 কোনো ভিডিও নেই।"
            )
            return

        buttons = []

        for video in videos:

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"🗑 {video['title']}",
                        callback_data=f"delete_{video['id']}",
                    )
                ]
            )

        await query.message.reply_text(
            "🗑 যে ভিডিওটি মুছতে চান নির্বাচন করুন:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    except Exception:
        logger.exception("Delete menu error")

        await query.message.reply_text(
            "❌ ভিডিও লোড করা যায়নি।"
        )


async def delete_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:

        video_id = int(
            query.data.replace("delete_", "", 1)
        )

        async with db_pool.acquire() as conn:

            video = await conn.fetchrow(
                """
                SELECT title
                FROM videos
                WHERE id = $1
                """,
                video_id,
            )

            if not video:
                await query.message.reply_text(
                    "❌ ভিডিও পাওয়া যায়নি।"
                )
                return

            await conn.execute(
                """
                DELETE FROM videos
                WHERE id = $1
                """,
                video_id,
            )

        await query.message.reply_text(
            f"✅ ভিডিও মুছে ফেলা হয়েছে:\n{video['title']}"
        )

    except Exception:
        logger.exception("Delete video error")

        await query.message.reply_text(
            "❌ ভিডিও মুছতে সমস্যা হয়েছে।"
        )


# =========================================================
# ADMIN REQUESTS
# =========================================================

async def show_requests(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:

        async with db_pool.acquire() as conn:

            rows = await conn.fetch(
                """
                SELECT user_id, username, message, created_at
                FROM requests
                ORDER BY id DESC
                LIMIT 20
                """
            )

        if not rows:
            await query.message.reply_text(
                "📭 কোনো Request নেই।"
            )
            return

        text = "📨 *Latest Requests*\n\n"

        for row in rows:

            username = (
                f"@{row['username']}"
                if row["username"]
                else "No username"
            )

            text += (
                f"👤 {username}\n"
                f"🆔 {row['user_id']}\n"
                f"💬 {row['message']}\n"
                f"🕒 {row['created_at']}\n\n"
            )

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
        )

    except Exception:
        logger.exception("Requests error")

        await query.message.reply_text(
            "❌ Request দেখতে সমস্যা হয়েছে।"
        )


# =========================================================
# ADMIN CONTACTS
# =========================================================

async def show_contacts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:

        async with db_pool.acquire() as conn:

            rows = await conn.fetch(
                """
                SELECT user_id, username, name, created_at
                FROM contacts
                ORDER BY id DESC
                LIMIT 50
                """
            )

        if not rows:
            await query.message.reply_text(
                "📭 কোনো Contact নেই।"
            )
            return

        text = "👥 *Users / Contacts*\n\n"

        for row in rows:

            username = (
                f"@{row['username']}"
                if row["username"]
                else "No username"
            )

            text += (
                f"👤 {row['name']}\n"
                f"🔗 {username}\n"
                f"🆔 {row['user_id']}\n"
                f"🕒 {row['created_at']}\n\n"
            )

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
        )

    except Exception:
        logger.exception("Contacts error")

        await query.message.reply_text(
            "❌ Contacts দেখতে সমস্যা হয়েছে।"
        )


# =========================================================
# ADMIN CONTACT BUTTON
# =========================================================

async def admin_contact(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    await query.message.reply_text(
        "📩 Admin-এর সাথে যোগাযোগ করতে মেসেজ পাঠান।"
    )


# =========================================================
# TEXT MESSAGE
# =========================================================

async def text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    await save_contact(user)

    text = update.message.text.strip()

    if text.lower() in ["admin", "contact", "যোগাযোগ"]:

        await update.message.reply_text(
            "📩 Admin-এর সাথে যোগাযোগ করতে আপনার মেসেজটি লিখুন।"
        )

        return

    if is_admin(user.id) and text.lower() == "panel":

        await admin_command(update, context)

        return

    await update.message.reply_text(
        "ℹ️ নিচের /start চাপুন এবং একটি Category নির্বাচন করুন।"
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    data = query.data

    if data.startswith("cat_"):
        await show_category(update, context)

    elif data.startswith("video_"):
        await show_video(update, context)

    elif data.startswith("delete_"):
        await delete_video(update, context)

    elif data == "home":
        await home_callback(update, context)

    elif data == "admin_contact":
        await admin_contact(update, context)

    elif data == "admin_delete_video":
        await delete_video_menu(update, context)

    elif data == "admin_requests":
        await show_requests(update, context)

    elif data == "admin_contacts":
        await show_contacts(update, context)


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Telegram error:",
        exc_info=context.error,
    )


# =========================================================
# POST INIT
# =========================================================

async def post_init(application: Application):

    await init_db()

    logger.info("🔥 Database initialized successfully")


# =========================================================
# MAIN
# =========================================================

def main():

    # -----------------------------------------------------
    # Start Render HTTP server
    # -----------------------------------------------------

    web_thread = Thread(
        target=run_web_server,
        daemon=True,
    )

    web_thread.start()

    logger.info("🌐 Render web server started")

    # -----------------------------------------------------
    # Telegram Application
    # -----------------------------------------------------

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # -----------------------------------------------------
    # Basic handlers
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("admin", admin_command)
    )

    # -----------------------------------------------------
    # Add Video Conversation
    # -----------------------------------------------------

    add_video_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                add_video_start,
                pattern=r"^admin_add_video$",
            )
        ],
        states={
            VIDEO_FILE: [
                MessageHandler(
                    filters.VIDEO,
                    receive_video,
                )
            ],
            VIDEO_TITLE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_title,
                )
            ],
            VIDEO_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_description,
                )
            ],
            VIDEO_CATEGORY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_category,
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel_conversation,
            )
        ],
        allow_reentry=True,
    )

    application.add_handler(
        add_video_conversation
    )

    # -----------------------------------------------------
    # Add Category Conversation
    # -----------------------------------------------------

    add_category_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                add_category_start,
                pattern=r"^admin_add_category$",
            )
        ],
        states={
            CATEGORY_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_category_name,
                )
            ]
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel_conversation,
            )
        ],
        allow_reentry=True,
    )

    application.add_handler(
        add_category_conversation
    )

    # -----------------------------------------------------
    # Callback handler
    # -----------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(callback_router)
    )

    # -----------------------------------------------------
    # Text handler
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_message,
        )
    )

    # -----------------------------------------------------
    # Error handler
    # -----------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    # -----------------------------------------------------
    # Start Telegram polling
    # -----------------------------------------------------

    logger.info("🤖 Bangla Vibe Bot started")

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()

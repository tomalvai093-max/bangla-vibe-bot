import os
import logging
import asyncpg

from flask import Flask
from threading import Thread

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
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

ADMIN_ID = 8721334265

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

db_pool = None


# =========================================================
# RENDER WEB SERVER
# =========================================================

web_app = Flask(__name__)


@web_app.route("/")
def health():
    return "Bangla Vibe Bot is running!", 200


@web_app.route("/health")
def health_check():
    return "OK", 200


def run_web_server():
    port = int(os.getenv("PORT", "10000"))

    web_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# ADMIN STATES
# =========================================================

VIDEO_TITLE = 1
VIDEO_DESCRIPTION = 2
VIDEO_CATEGORY = 3

CONTACT_NAME = 4
CONTACT_LINK = 5

NEW_CATEGORY = 6


# =========================================================
# DATABASE
# =========================================================

async def init_db():
    global db_pool

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=10,
        command_timeout=10,
    )

    async with db_pool.acquire() as conn:

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id SERIAL PRIMARY KEY,
                file_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                category_id INTEGER REFERENCES categories(id)
                    ON DELETE CASCADE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_videos_category
            ON videos(category_id)
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT DEFAULT '',
                request_text TEXT NOT NULL,
                category TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                link TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
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
                INSERT INTO categories(name)
                VALUES($1)
                ON CONFLICT(name) DO NOTHING
                """,
                category,
            )

    logger.info("PostgreSQL database ready")


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def get_categories():
    async with db_pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, name
            FROM categories
            ORDER BY id
            """
        )


async def get_category(category_id):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, name
            FROM categories
            WHERE id=$1
            """,
            category_id,
        )


# =========================================================
# HOME
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    categories = await get_categories()

    buttons = []

    for category in categories:
        buttons.append([
            InlineKeyboardButton(
                category["name"],
                callback_data=f"cat:{category['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "👑 Admin Contact",
            callback_data="contacts"
        )
    ])

    text = (
        "🌑💜 <b>Bangla Vibe</b> 💗💙\n\n"
        "🔥 Welcome!\n"
        "🎬 তোমার পছন্দের Content নির্বাচন করো\n"
        "✨ Choose a Category 👇"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# CATEGORY
# =========================================================

async def category_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    category_id = int(query.data.split(":")[1])

    category = await get_category(category_id)

    if not category:
        await query.edit_message_text(
            "😔 Sorry! Category পাওয়া যায়নি।"
        )
        return

    async with db_pool.acquire() as conn:

        videos = await conn.fetch(
            """
            SELECT id, title
            FROM videos
            WHERE category_id=$1
            ORDER BY id DESC
            LIMIT 100
            """,
            category_id,
        )

    if not videos:

        request_text = category["name"]

        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO requests(
                    user_id,
                    username,
                    request_text,
                    category
                )
                VALUES($1,$2,$3,$4)
                """,
                query.from_user.id,
                query.from_user.username or "",
                request_text,
                category["name"],
            )

        try:
            await context.bot.send_message(
                ADMIN_ID,
                "📩 <b>New Content Request</b>\n\n"
                f"👤 User: @{query.from_user.username or 'Unknown'}\n"
                f"🆔 ID: <code>{query.from_user.id}</code>\n"
                f"📂 Category: {category['name']}\n\n"
                "⏳ Please add the requested content.",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Admin notification failed")

        await query.edit_message_text(
            "😔 <b>Sorry!</b>\n\n"
            f"📂 {category['name']}\n\n"
            "এই Content এখনো Admin add করেনি।\n"
            "📩 আমি Admin-এর কাছে request পাঠিয়েছি।\n"
            "⏳ খুব দ্রুত add করার চেষ্টা করা হবে।\n\n"
            "❤️ Thanks!",
            parse_mode="HTML",
        )

        return

    buttons = []

    for video in videos:
        buttons.append([
            InlineKeyboardButton(
                f"🎬 {video['title']}",
                callback_data=f"video:{video['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Back",
            callback_data="home"
        )
    ])

    await query.edit_message_text(
        f"🌑💜 <b>{category['name']}</b>\n\n"
        "🔥 Available Content:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# SEND VIDEO
# =========================================================

async def video_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    video_id = int(query.data.split(":")[1])

    async with db_pool.acquire() as conn:
        video = await conn.fetchrow(
            """
            SELECT
                v.file_id,
                v.title,
                v.description,
                c.name AS category
            FROM videos v
            JOIN categories c
            ON v.category_id=c.id
            WHERE v.id=$1
            """,
            video_id,
        )

    if not video:
        await query.message.reply_text(
            "😔 Sorry! এই video আর পাওয়া যাচ্ছে না।"
        )
        return

    caption = (
        f"🎬 <b>{video['title']}</b>\n\n"
        f"🏷️ Category: {video['category']}\n"
        f"📝 {video['description'] or 'No description'}\n\n"
        "🔥 <b>Bangla Vibe</b>"
    )

    try:

        await context.bot.send_video(
            chat_id=query.from_user.id,
            video=video["file_id"],
            caption=caption,
            parse_mode="HTML",
        )

    except Exception:
        logger.exception("Video sending failed")

        await query.message.reply_text(
            "😔 Sorry! Video এখন পাঠানো যাচ্ছে না।\n"
            "⏳ পরে আবার চেষ্টা করো।"
        )


# =========================================================
# HOME CALLBACK
# =========================================================

async def home_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    categories = await get_categories()

    buttons = [
        [
            InlineKeyboardButton(
                category["name"],
                callback_data=f"cat:{category['id']}"
            )
        ]
        for category in categories
    ]

    buttons.append([
        InlineKeyboardButton(
            "👑 Admin Contact",
            callback_data="contacts"
        )
    ])

    await query.edit_message_text(
        "🌑💜 <b>Bangla Vibe</b>\n\n"
        "📂 Category নির্বাচন করো 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Sorry! এই command শুধু Admin-এর জন্য।"
        )
        return

    buttons = [
        [
            InlineKeyboardButton(
                "🎬 Add Video",
                callback_data="admin_add_video"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑️ Delete Video",
                callback_data="admin_delete"
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Add Category",
                callback_data="admin_category"
            )
        ],
        [
            InlineKeyboardButton(
                "📩 Requests",
                callback_data="admin_requests"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 Admin Contacts",
                callback_data="admin_contacts"
            )
        ],
    ]

    await update.message.reply_text(
        "👑 <b>Bangla Vibe Admin Panel</b>\n\n"
        "⚡ Welcome Admin!\n"
        "নিচের option নির্বাচন করো 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# ADD VIDEO
# =========================================================

async def admin_add_video_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    context.user_data.clear()

    await query.message.reply_text(
        "🎬 <b>Send the video</b>\n\n"
        "Admin, এখন ভিডিওটি পাঠাও।",
        parse_mode="HTML",
    )

    return VIDEO_TITLE


async def receive_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    if not update.message.video:
        await update.message.reply_text(
            "⚠️ Please send a video file."
        )
        return VIDEO_TITLE

    context.user_data["file_id"] = update.message.video.file_id

    await update.message.reply_text(
        "🏷️ <b>Title দিন</b>\n\n"
        "যেমন: Bangla New Video",
        parse_mode="HTML",
    )

    return VIDEO_DESCRIPTION


async def receive_title(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data["title"] = update.message.text.strip()

    await update.message.reply_text(
        "📝 <b>Description দিন</b>\n\n"
        "Description না চাইলে লিখুন: <code>skip</code>",
        parse_mode="HTML",
    )

    return VIDEO_CATEGORY


async def receive_description(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text.strip()

    if text.lower() == "skip":
        text = ""

    context.user_data["description"] = text

    categories = await get_categories()

    buttons = [
        [
            InlineKeyboardButton(
                category["name"],
                callback_data=f"savecat:{category['id']}"
            )
        ]
        for category in categories
    ]

    await update.message.reply_text(
        "📂 <b>Category নির্বাচন করুন</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

    return VIDEO_CATEGORY


async def save_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    category_id = int(query.data.split(":")[1])

    data = context.user_data

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO videos(
                file_id,
                title,
                description,
                category_id
            )
            VALUES($1,$2,$3,$4)
            """,
            data["file_id"],
            data["title"],
            data.get("description", ""),
            category_id,
        )

    context.user_data.clear()

    await query.message.reply_text(
        "✅ <b>Video Added Successfully!</b> 🎉\n\n"
        "🎬 Video: Saved\n"
        "🏷️ Title: Saved\n"
        "📝 Description: Saved\n"
        "📂 Category: Saved\n\n"
        "🔥 Bangla Vibe",
        parse_mode="HTML",
    )

    return ConversationHandler.END


# =========================================================
# ADD CATEGORY
# =========================================================

async def add_category_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return ConversationHandler.END

    await query.message.reply_text(
        "➕ <b>New Category</b>\n\n"
        "Category-এর নাম পাঠাও।\n\n"
        "Example:\n"
        "⚽ Sports",
        parse_mode="HTML",
    )

    return NEW_CATEGORY


async def save_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "⚠️ Category name empty হতে পারবে না।"
        )
        return NEW_CATEGORY

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO categories(name)
            VALUES($1)
            ON CONFLICT(name) DO NOTHING
            """,
            name,
        )

    await update.message.reply_text(
        f"✅ <b>Category Added Successfully!</b>\n\n"
        f"📂 {name}",
        parse_mode="HTML",
    )

    return ConversationHandler.END


# =========================================================
# DELETE VIDEO
# =========================================================

async def delete_video_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    async with db_pool.acquire() as conn:

        videos = await conn.fetch(
            """
            SELECT v.id, v.title, c.name AS category
            FROM videos v
            JOIN categories c
            ON v.category_id=c.id
            ORDER BY v.id DESC
            LIMIT 100
            """
        )

    if not videos:
        await query.message.reply_text(
            "📭 কোনো video পাওয়া যায়নি।"
        )
        return

    buttons = []

    for video in videos:
        buttons.append([
            InlineKeyboardButton(
                f"🗑️ {video['title']}",
                callback_data=f"del:{video['id']}"
            )
        ])

    await query.message.reply_text(
        "🗑️ <b>Select Video to Delete</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def delete_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    video_id = int(query.data.split(":")[1])

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM videos
            WHERE id=$1
            """,
            video_id,
        )

    await query.message.reply_text(
        "🗑️ <b>Video Deleted Successfully!</b> ✅",
        parse_mode="HTML",
    )


# =========================================================
# REQUESTS
# =========================================================

async def show_requests(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    async with db_pool.acquire() as conn:

        requests = await conn.fetch(
            """
            SELECT id, user_id, username, request_text, category
            FROM requests
            WHERE status='pending'
            ORDER BY id DESC
            LIMIT 20
            """
        )

    if not requests:

        await query.message.reply_text(
            "📭 বর্তমানে কোনো pending request নেই।"
        )
        return

    text = "📩 <b>Pending Requests</b>\n\n"

    for req in requests:

        text += (
            f"🆔 Request #{req['id']}\n"
            f"👤 @{req['username'] or 'Unknown'}\n"
            f"📂 {req['category']}\n"
            f"💬 {req['request_text']}\n\n"
        )

    await query.message.reply_text(
        text,
        parse_mode="HTML",
    )


# =========================================================
# ADMIN CONTACTS
# =========================================================

async def show_contacts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    async with db_pool.acquire() as conn:

        contacts = await conn.fetch(
            """
            SELECT name, link
            FROM contacts
            ORDER BY id
            """
        )

    if not contacts:

        await query.message.reply_text(
            "👤 Admin contacts এখনো add করা হয়নি।"
        )
        return

    text = "👑 <b>Admin Contacts</b>\n\n"

    for contact in contacts:

        text += (
            f"🔹 <b>{contact['name']}</b>\n"
            f"🔗 {contact['link']}\n\n"
        )

    await query.message.reply_text(
        text,
        parse_mode="HTML",
    )


# =========================================================
# ADMIN CONTACT TEXT MATCHING
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text.lower().strip()

    admin_words = [
        "admin",
        "এডমিন",
        "অ্যাডমিন",
        "fb",
        "facebook",
        "ফেসবুক",
        "tiktok",
        "টিকটক",
        "whatsapp",
        "ওয়াসাব",
        "হোয়াটসঅ্যাপ",
        "telegram",
        "টেলিগ্রাম",
    ]

    if any(word in text for word in admin_words):

        async with db_pool.acquire() as conn:

            contacts = await conn.fetch(
                """
                SELECT name, link
                FROM contacts
                ORDER BY id
                """
            )

        if contacts:

            message = "👑 <b>Admin Contacts</b>\n\n"

            for contact in contacts:
                message += (
                    f"🔹 {contact['name']}\n"
                    f"🔗 {contact['link']}\n\n"
                )

        else:

            message = (
                "👑 <b>Admin</b>\n\n"
                "✈️ Telegram: @tomalchowdhury2\n\n"
                "📭 Other contacts coming soon."
            )

        await update.message.reply_text(
            message,
            parse_mode="HTML",
        )
        return

    words = {
        "🎵 গান": [
            "গান",
            "gaan",
            "song",
            "songs",
            "music",
        ],
        "🎭 নাটক": [
            "নাটক",
            "natok",
            "drama",
        ],
        "🎬 ভিডিও": [
            "ভিডিও",
            "video",
            "videos",
        ],
        "📸 ফটো": [
            "ফটো",
            "photo",
            "photos",
            "picture",
            "pic",
        ],
        "🎥 মুভি": [
            "মুভি",
            "movie",
            "movies",
            "film",
        ],
    }

    matched_category = None

    for category, aliases in words.items():

        if any(alias in text for alias in aliases):
            matched_category = category
            break

    if matched_category:

        async with db_pool.acquire() as conn:

            category = await conn.fetchrow(
                """
                SELECT id, name
                FROM categories
                WHERE name=$1
                """,
                matched_category,
            )

        if category:

            async with db_pool.acquire() as conn:

                videos = await conn.fetch(
                    """
                    SELECT id, title
                    FROM videos
                    WHERE category_id=$1
                    ORDER BY id DESC
                    LIMIT 100
                    """,
                    category["id"],
                )

            if videos:

                buttons = [
                    [
                        InlineKeyboardButton(
                            f"🎬 {video['title']}",
                            callback_data=f"video:{video['id']}"
                        )
                    ]
                    for video in videos
                ]

                await update.message.reply_text(
                    f"🔥 <b>{category['name']}</b>\n\n"
                    "Available Content 👇",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )

                return

            async with db_pool.acquire() as conn:

                await conn.execute(
                    """
                    INSERT INTO requests(
                        user_id,
                        username,
                        request_text,
                        category
                    )
                    VALUES($1,$2,$3,$4)
                    """,
                    update.effective_user.id,
                    update.effective_user.username or "",
                    text,
                    category["name"],
                )

            try:

                await context.bot.send_message(
                    ADMIN_ID,
                    "📩 <b>New User Request</b>\n\n"
                    f"👤 @{update.effective_user.username or 'Unknown'}\n"
                    f"🆔 <code>{update.effective_user.id}</code>\n"
                    f"📂 {category['name']}\n"
                    f"💬 {text}",
                    parse_mode="HTML",
                )

            except Exception:
                logger.exception(
                    "Admin request notification failed"
                )

            await update.message.reply_text(
                "😔 <b>Sorry!</b>\n\n"
                "এই Content এখনো Admin add করেনি।\n"
                "📩 Admin-এর কাছে request পাঠানো হয়েছে।\n"
                "⏳ খুব দ্রুত add করার চেষ্টা করা হবে।\n\n"
                "❤️ Thanks!",
                parse_mode="HTML",
            )

            return

    await update.message.reply_text(
        "🤔 Sorry! Request বুঝতে পারিনি।\n\n"
        "🎵 গান\n"
        "🎭 নাটক\n"
        "🎬 ভিডিও\n"
        "📸 ফটো\n"
        "🎥 মুভি\n\n"
        "অথবা Category button ব্যবহার করো।"
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if query.data == "contacts":

        await query.answer()

        async with db_pool.acquire() as conn:

            contacts = await conn.fetch(
                """
                SELECT name, link
                FROM contacts
                ORDER BY id
                """
            )

        text = "👑 <b>Admin Contacts</b>\n\n"

        if contacts:

            for contact in contacts:
                text += (
                    f"🔹 {contact['name']}\n"
                    f"🔗 {contact['link']}\n\n"
                )

        else:

            text += (
                "✈️ Telegram: @tomalchowdhury2\n\n"
                "📭 Other contacts coming soon."
            )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
        )
        return

    if query.data == "admin":

        await query.answer()

        if not is_admin(query.from_user.id):
            await query.answer(
                "⛔ Admin access only!",
                show_alert=True
            )
            return

        await admin_command_from_callback(
            update,
            context
        )
        return


async def admin_command_from_callback(
    update,
    context
):

    query = update.callback_query

    buttons = [
        [
            InlineKeyboardButton(
                "🎬 Add Video",
                callback_data="admin_add_video"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑️ Delete Video",
                callback_data="admin_delete"
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Add Category",
                callback_data="admin_category"
            )
        ],
        [
            InlineKeyboardButton(
                "📩 Requests",
                callback_data="admin_requests"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 Admin Contacts",
                callback_data="admin_contacts"
            )
        ],
    ]

    await query.edit_message_text(
        "👑 <b>Admin Panel</b>\n\n"
        "⚡ Select an option:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# ERROR
# =========================================================

async def error_handler(
    update,
    context
):

    logger.exception(
        "Unhandled error:",
        exc_info=context.error
    )


# =========================================================
# STARTUP
# =========================================================

async def post_init(application):

    await init_db()

    logger.info("🔥 Bangla Vibe Bot started")


# =========================================================
# MAIN
# =========================================================

def main():

    # Start Render web server
    web_thread = Thread(
        target=run_web_server,
        daemon=True,
    )

    web_thread.start()

    logger.info("🌐 Render web server started")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Start
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("admin", admin_command)
    )

    # Add video conversation
    video_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                admin_add_video_start,
                pattern=r"^admin_add_video$"
            )
        ],
        states={
            VIDEO_TITLE: [
                MessageHandler(
                    filters.VIDEO,
                    receive_video
                )
            ],
            VIDEO_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_title
                )
            ],
            VIDEO_CATEGORY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_description
                ),
                CallbackQueryHandler(
                    save_video,
                    pattern=r"^savecat:"
                ),
            ],
        },
        fallbacks=[],
    )

    application.add_handler(video_conversation)

    # Add category conversation
    category_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                add_category_start,
                pattern=r"^admin_category$"
            )
        ],
        states={
            NEW_CATEGORY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    save_category
                )
            ]
        },
        fallbacks=[],
    )

    application.add_handler(category_conversation)

    # Category
    application.add_handler(
        CallbackQueryHandler(
            category_callback,
            pattern=r"^cat:"
        )
    )

    # Video
    application.add_handler(
        CallbackQueryHandler(
            video_callback,
            pattern=r"^video:"
        )
    )

    # Home
    application.add_handler(
        CallbackQueryHandler(
            home_callback,
            pattern=r"^home$"
        )
    )

    # Delete menu
    application.add_handler(
        CallbackQueryHandler(
            delete_video_menu,
            pattern=r"^admin_delete$"
        )
    )

    # Delete
    application.add_handler(
        CallbackQueryHandler(
            delete_video,
            pattern=r"^del:"
        )
    )

    # Requests
    application.add_handler(
        CallbackQueryHandler(
            show_requests,
            pattern=r"^admin_requests$"
        )
    )

    # Contacts
    application.add_handler(
        CallbackQueryHandler(
            show_contacts,
            pattern=r"^admin_contacts$"
        )
    )

    # Callback router
    application.add_handler(
        CallbackQueryHandler(
            callback_router
        )
    )

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    application.add_error_handler(error_handler)

    # Run Telegram bot
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()

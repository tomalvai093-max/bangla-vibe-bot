import os
import logging
import asyncio
import json
import urllib.request
from threading import Thread

import asyncpg
from flask import Flask, Response, jsonify, request

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

ADMIN_ID = 8721334265

WEB_URL = "https://bangla-vibe-bot.onrender.com"

# তোমার Adsterra SmartLink
ADSTERRA_SMARTLINK = (
    "https://www.profitableratecpmnetwork.com/"
    "cc0kqkj3?key=96d4caaba1f61e2b9329ad0d07861cf4"
)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

db_pool = None


# =========================================================
# FLASK
# =========================================================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bangla Vibe Bot is running!", 200


@web_app.route("/health")
def health():
    return "OK", 200


@web_app.route("/app")
def app_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport"
              content="width=device-width,initial-scale=1">
        <title>Bangla Vibe</title>
    </head>
    <body>
        <h2>Bangla Vibe Web App</h2>
        <p>index.html এখনো GitHub-এ যোগ করা হয়নি।</p>
    </body>
    </html>
    """, 200


def run_web_server():
    port = int(os.getenv("PORT", "10000"))

    logger.info("🌐 Web server starting on port %s", port)

    web_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# DATABASE
# =========================================================

async def init_db():
    global db_pool

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=60,
    )

    async with db_pool.acquire() as conn:

        # -------------------------------------------------
        # CATEGORIES
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            )
        """)

        # -------------------------------------------------
        # OLD VIDEOS TABLE
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id SERIAL PRIMARY KEY,
                file_id TEXT,
                title TEXT DEFAULT '',
                description TEXT DEFAULT '',
                category TEXT DEFAULT '🎬 ভিডিও',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await conn.execute("""
            ALTER TABLE videos
            ADD COLUMN IF NOT EXISTS file_id TEXT
        """)

        await conn.execute("""
            ALTER TABLE videos
            ADD COLUMN IF NOT EXISTS title TEXT DEFAULT ''
        """)

        await conn.execute("""
            ALTER TABLE videos
            ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''
        """)

        await conn.execute("""
            ALTER TABLE videos
            ADD COLUMN IF NOT EXISTS category TEXT DEFAULT '🎬 ভিডিও'
        """)

        await conn.execute("""
            ALTER TABLE videos
            ADD COLUMN IF NOT EXISTS created_at
            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """)

        # -------------------------------------------------
        # NEW MEDIA TABLE
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS media (
                id SERIAL PRIMARY KEY,
                file_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                title TEXT DEFAULT '',
                description TEXT DEFAULT '',
                category TEXT DEFAULT '🎬 ভিডিও',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # -------------------------------------------------
        # SOCIAL LINKS
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS social_links (
                id SERIAL PRIMARY KEY,
                platform TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL
            )
        """)

        # -------------------------------------------------
        # CONTACTS
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT DEFAULT '',
                name TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await conn.execute("""
            ALTER TABLE contacts
            ADD COLUMN IF NOT EXISTS user_id BIGINT
        """)

        await conn.execute("""
            ALTER TABLE contacts
            ADD COLUMN IF NOT EXISTS username TEXT DEFAULT ''
        """)

        await conn.execute("""
            ALTER TABLE contacts
            ADD COLUMN IF NOT EXISTS name TEXT DEFAULT ''
        """)

        await conn.execute("""
            ALTER TABLE contacts
            ADD COLUMN IF NOT EXISTS created_at
            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """)

        # -------------------------------------------------
        # REQUESTS
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT DEFAULT '',
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # -------------------------------------------------
        # DEFAULT CATEGORIES
        # -------------------------------------------------

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

        # -------------------------------------------------
        # MIGRATE OLD VIDEOS INTO MEDIA
        # পুরোনো videos delete করা হবে না
        # -------------------------------------------------

        await conn.execute("""
            INSERT INTO media
            (file_id, media_type, title, description, category, created_at)
            SELECT
                v.file_id,
                'video',
                COALESCE(v.title, ''),
                COALESCE(v.description, ''),
                COALESCE(v.category, '🎬 ভিডিও'),
                COALESCE(v.created_at, CURRENT_TIMESTAMP)
            FROM videos v
            WHERE v.file_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM media m
                  WHERE m.file_id = v.file_id
                    AND m.media_type = 'video'
              )
        """)

    logger.info("PostgreSQL database ready")
    logger.info("🔥 Database initialized successfully")


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


def normalize(text):
    text = text.lower().strip()

    replacements = {
        "দেও": "দাও",
        "দেন": "দাও",
        "দিন": "দাও",
        "deo": "dao",
        "den": "dao",
        "din": "dao",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return " ".join(text.split())


def set_state(context, state):
    context.user_data["admin_state"] = state


def get_state(context):
    return context.user_data.get("admin_state")


def clear_state(context):
    keys = [
        "admin_state",
        "media_file_id",
        "media_type",
        "media_title",
        "media_description",
    ]

    for key in keys:
        context.user_data.pop(key, None)


# =========================================================
# CONTACT
# =========================================================

async def save_contact(user):
    try:
        async with db_pool.acquire() as conn:

            existing = await conn.fetchval(
                """
                SELECT id
                FROM contacts
                WHERE user_id = $1
                LIMIT 1
                """,
                user.id,
            )

            if existing:

                await conn.execute(
                    """
                    UPDATE contacts
                    SET username = $1,
                        name = $2
                    WHERE user_id = $3
                    """,
                    user.username or "",
                    user.full_name or "",
                    user.id,
                )

            else:

                await conn.execute(
                    """
                    INSERT INTO contacts
                    (user_id, username, name)
                    VALUES ($1, $2, $3)
                    """,
                    user.id,
                    user.username or "",
                    user.full_name or "",
                )

    except Exception:
        logger.exception("Contact save error")


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await save_contact(update.effective_user)

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name
            FROM categories
            ORDER BY id
            """
        )

    buttons = []
    category_buttons = []

    for row in rows:
        category_buttons.append(
            InlineKeyboardButton(
                row["name"],
                callback_data=f"category:{row['name']}",
            )
        )

    for i in range(0, len(category_buttons), 2):
        buttons.append(category_buttons[i:i + 2])

    buttons.append([
        InlineKeyboardButton(
            "📘 Admin Facebook",
            callback_data="social:facebook",
        ),
        InlineKeyboardButton(
            "🎵 Admin TikTok",
            callback_data="social:tiktok",
        ),
    ])

    await update.message.reply_text(
        "🔥 *Bangla Vibe*\n\n"
        "স্বাগতম! নিচের Category থেকে কনটেন্ট নির্বাচন করুন।",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


# =========================================================
# ADMIN PANEL
# =========================================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "⛔ এই অপশনটি শুধু Admin-এর জন্য।"
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🎬 Video যোগ",
                callback_data="admin:add_video",
            ),
            InlineKeyboardButton(
                "🖼️ Image যোগ",
                callback_data="admin:add_photo",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎵 Audio যোগ",
                callback_data="admin:add_audio",
            ),
            InlineKeyboardButton(
                "📂 Category যোগ",
                callback_data="admin:add_category",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 Media ডিলিট",
                callback_data="admin:delete_media",
            ),
            InlineKeyboardButton(
                "📋 সব Media",
                callback_data="admin:media",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 Category ডিলিট",
                callback_data="admin:delete_category",
            ),
        ],
        [
            InlineKeyboardButton(
                "📘 Facebook যোগ",
                callback_data="admin:add_facebook",
            ),
            InlineKeyboardButton(
                "🎵 TikTok যোগ",
                callback_data="admin:add_tiktok",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 Facebook ডিলিট",
                callback_data="admin:delete_facebook",
            ),
            InlineKeyboardButton(
                "🗑 TikTok ডিলিট",
                callback_data="admin:delete_tiktok",
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin:users",
            ),
        ],
    ]

    await update.message.reply_text(
        "👑 *Admin Panel*\n\n"
        "এখান থেকে Video, Image, Audio এবং Category Manage করতে পারবে।",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# =========================================================
# SOCIAL
# =========================================================

async def save_social(platform, value):

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO social_links (platform, value)
            VALUES ($1, $2)
            ON CONFLICT (platform)
            DO UPDATE SET value = EXCLUDED.value
            """,
            platform,
            value,
        )


async def get_social(platform):

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT value
            FROM social_links
            WHERE platform = $1
            """,
            platform,
        )

    return row["value"] if row else None


async def delete_social(platform, message):

    async with db_pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM social_links
            WHERE platform = $1
            """,
            platform,
        )

    if result == "DELETE 0":
        await message.reply_text(
            f"ℹ️ কোনো {platform.title()} ID/Link যোগ করা হয়নি।"
        )
    else:
        await message.reply_text(
            f"✅ {platform.title()} ID/Link ডিলিট হয়েছে।"
        )


# =========================================================
# NATURAL LANGUAGE
# =========================================================

def is_add_video_text(text):
    text = normalize(text)

    return text in [
        "ভিডিও দাও",
        "ভিডিও দে",
        "video dao",
        "video de",
        "add video",
        "add a video",
        "upload video",
        "upload a video",
        "ভিডিও যোগ",
        "ভিডিও যোগ কর",
        "ভিডিও যোগ করুন",
    ]


def is_add_photo_text(text):
    text = normalize(text)

    return text in [
        "ছবি দাও",
        "ছবি দে",
        "ছবি যোগ",
        "photo dao",
        "photo de",
        "image dao",
        "image de",
        "add photo",
        "add image",
        "upload photo",
        "upload image",
    ]


def is_add_audio_text(text):
    text = normalize(text)

    return text in [
        "অডিও দাও",
        "অডিও দে",
        "অডিও যোগ",
        "audio dao",
        "audio de",
        "add audio",
        "upload audio",
        "গান দাও",
    ]


def is_add_category_text(text):
    text = normalize(text)

    return text in [
        "category দাও",
        "category dao",
        "add category",
        "নতুন category",
        "ক্যাটাগরি দাও",
        "ক্যাটাগরি যোগ",
        "category যোগ",
    ]


def is_add_facebook_text(text):
    text = normalize(text)

    return text in [
        "facebook দাও",
        "facebook dao",
        "facebook id দাও",
        "facebook id dao",
        "facebook link দাও",
        "add facebook",
        "add facebook id",
        "ফেসবুক দাও",
        "ফেসবুক আইডি দাও",
        "ফেসবুক লিংক দাও",
    ]


def is_add_tiktok_text(text):
    text = normalize(text)

    return text in [
        "tiktok দাও",
        "tiktok dao",
        "tiktok id দাও",
        "tiktok id dao",
        "tiktok link দাও",
        "add tiktok",
        "add tiktok id",
        "টিকটক দাও",
        "টিকটক আইডি দাও",
        "টিকটক লিংক দাও",
    ]


def is_user_facebook_request(text):
    text = normalize(text)

    return text in [
        "এডমিন ফেসবুক দাও",
        "admin facebook dao",
        "admin facebook",
        "facebook admin",
        "admin facebook id",
        "এডমিন ফেসবুক আইডি দাও",
        "এডমিন ফেসবুক আইডি",
    ]


def is_user_tiktok_request(text):
    text = normalize(text)

    return text in [
        "এডমিন টিকটক দাও",
        "admin tiktok dao",
        "admin tiktok",
        "tiktok admin",
        "admin tiktok id",
        "এডমিন টিকটক আইডি দাও",
        "এডমিন টিকটক আইডি",
    ]


# =========================================================
# ADMIN MEDIA STATE
# =========================================================

async def start_media_add(message, context, media_type):

    clear_state(context)

    context.user_data["media_type"] = media_type

    if media_type == "video":
        text = "🎬 ভিডিও পাঠাও।"
    elif media_type == "photo":
        text = "🖼️ ছবি পাঠাও।"
    else:
        text = "🎵 Audio/গান পাঠাও।"

    set_state(context, "media_file")

    await message.reply_text(text)


# =========================================================
# MEDIA MESSAGE HANDLERS
# =========================================================

async def video_message(update, context):

    user = update.effective_user
    await save_contact(user)

    if not is_admin(user.id):
        await update.message.reply_text(
            "😊 Media যোগ করার অনুমতি শুধু Admin-এর আছে।"
        )
        return

    if get_state(context) != "media_file":
        await update.message.reply_text(
            "ℹ️ আগে `ভিডিও দাও` লিখো।"
        )
        return

    context.user_data["media_file_id"] = (
        update.message.video.file_id
    )

    context.user_data["media_type"] = "video"

    set_state(context, "media_title")

    await update.message.reply_text(
        "✅ ভিডিও পাওয়া গেছে!\n\n"
        "🎬 এখন ভিডিওর নাম লিখো।"
    )


async def photo_message(update, context):

    user = update.effective_user
    await save_contact(user)

    if not is_admin(user.id):
        await update.message.reply_text(
            "😊 Media যোগ করার অনুমতি শুধু Admin-এর আছে।"
        )
        return

    if get_state(context) != "media_file":
        await update.message.reply_text(
            "ℹ️ আগে `ছবি দাও` লিখো।"
        )
        return

    context.user_data["media_file_id"] = (
        update.message.photo[-1].file_id
    )

    context.user_data["media_type"] = "photo"

    set_state(context, "media_title")

    await update.message.reply_text(
        "✅ ছবি পাওয়া গেছে!\n\n"
        "🖼️ এখন ছবির নাম লিখো।"
    )


async def audio_message(update, context):

    user = update.effective_user
    await save_contact(user)

    if not is_admin(user.id):
        await update.message.reply_text(
            "😊 Media যোগ করার অনুমতি শুধু Admin-এর আছে।"
        )
        return

    if get_state(context) != "media_file":
        await update.message.reply_text(
            "ℹ️ আগে `অডিও দাও` লিখো।"
        )
        return

    file_id = None

    if update.message.audio:
        file_id = update.message.audio.file_id
    elif update.message.voice:
        file_id = update.message.voice.file_id

    if not file_id:
        await update.message.reply_text(
            "❌ Audio file পাওয়া যায়নি।"
        )
        return

    context.user_data["media_file_id"] = file_id
    context.user_data["media_type"] = "audio"

    set_state(context, "media_title")

    await update.message.reply_text(
        "✅ Audio পাওয়া গেছে!\n\n"
        "🎵 এখন Audio-এর নাম লিখো।"
    )


# =========================================================
# SAVE MEDIA
# =========================================================

async def save_media_from_state(update, context, category):

    file_id = context.user_data.get("media_file_id")
    media_type = context.user_data.get("media_type")
    title = context.user_data.get("media_title")
    description = context.user_data.get(
        "media_description",
        "",
    )

    if not file_id or not media_type or not title:
        clear_state(context)

        await update.message.reply_text(
            "❌ Media তথ্য পাওয়া যায়নি। আবার শুরু করো।"
        )
        return

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
                INSERT INTO media
                (file_id, media_type, title, description, category)
                VALUES ($1, $2, $3, $4, $5)
                """,
                file_id,
                media_type,
                title,
                description,
                category,
            )

        clear_state(context)

        await update.message.reply_text(
            "✅ Media সফলভাবে Save হয়েছে!\n\n"
            f"📌 Type: {media_type}\n"
            f"📝 নাম: {title}\n"
            f"📂 Category: {category}\n\n"
            "💾 PostgreSQL Database-এ সংরক্ষিত হয়েছে।"
        )

    except Exception:
        logger.exception("MEDIA SAVE ERROR")

        clear_state(context)

        await update.message.reply_text(
            "❌ Media Save করতে সমস্যা হয়েছে।\n"
            "Render Logs দেখো।"
        )


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(update, context):

    user = update.effective_user
    text = update.message.text.strip()

    await save_contact(user)

    # -----------------------------------------------------
    # ADMIN
    # -----------------------------------------------------

    if is_admin(user.id):

        state = get_state(context)

        if state == "media_title":

            context.user_data["media_title"] = text

            set_state(context, "media_description")

            await update.message.reply_text(
                "📝 এখন Description লিখো।\n\n"
                "Description না চাইলে `skip` লিখো।"
            )
            return

        if state == "media_description":

            if text.lower() == "skip":
                context.user_data["media_description"] = ""
            else:
                context.user_data["media_description"] = text

            set_state(context, "media_category")

            async with db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT name
                    FROM categories
                    ORDER BY id
                    """
                )

            category_text = "\n".join(
                f"• {row['name']}"
                for row in rows
            )

            await update.message.reply_text(
                "📂 এখন Category লিখো।\n\n"
                f"{category_text}"
            )
            return

        if state == "media_category":

            await save_media_from_state(
                update,
                context,
                text,
            )
            return

        if state == "add_category":

            try:

                async with db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO categories (name)
                        VALUES ($1)
                        ON CONFLICT (name) DO NOTHING
                        """,
                        text,
                    )

                clear_state(context)

                await update.message.reply_text(
                    f"✅ Category যোগ হয়েছে:\n{text}"
                )

            except Exception:
                logger.exception("CATEGORY SAVE ERROR")

                clear_state(context)

                await update.message.reply_text(
                    "❌ Category যোগ করতে সমস্যা হয়েছে।"
                )

            return

        if state == "add_facebook":

            try:
                await save_social(
                    "facebook",
                    text,
                )

                clear_state(context)

                await update.message.reply_text(
                    "✅ Admin Facebook Save হয়েছে।"
                )

            except Exception:
                logger.exception("FACEBOOK SAVE ERROR")

                clear_state(context)

                await update.message.reply_text(
                    "❌ Facebook Save করতে সমস্যা হয়েছে।"
                )

            return

        if state == "add_tiktok":

            try:
                await save_social(
                    "tiktok",
                    text,
                )

                clear_state(context)

                await update.message.reply_text(
                    "✅ Admin TikTok Save হয়েছে।"
                )

            except Exception:
                logger.exception("TIKTOK SAVE ERROR")

                clear_state(context)

                await update.message.reply_text(
                    "❌ TikTok Save করতে সমস্যা হয়েছে।"
                )

            return

        if is_add_video_text(text):
            await start_media_add(
                update.message,
                context,
                "video",
            )
            return

        if is_add_photo_text(text):
            await start_media_add(
                update.message,
                context,
                "photo",
            )
            return

        if is_add_audio_text(text):
            await start_media_add(
                update.message,
                context,
                "audio",
            )
            return

        if is_add_category_text(text):

            set_state(
                context,
                "add_category",
            )

            await update.message.reply_text(
                "📂 নতুন Category-এর নাম লিখো।"
            )
            return

        if is_add_facebook_text(text):

            set_state(
                context,
                "add_facebook",
            )

            await update.message.reply_text(
                "📘 Facebook ID বা Link পাঠাও।"
            )
            return

        if is_add_tiktok_text(text):

            set_state(
                context,
                "add_tiktok",
            )

            await update.message.reply_text(
                "🎵 TikTok ID বা Link পাঠাও।"
            )
            return

    # -----------------------------------------------------
    # USER SOCIAL
    # -----------------------------------------------------

    if is_user_facebook_request(text):

        value = await get_social("facebook")

        await update.message.reply_text(
            f"📘 Admin Facebook:\n\n{value}"
            if value
            else
            "😔 Admin এখনো Facebook ID/Link যোগ করেননি।"
        )
        return

    if is_user_tiktok_request(text):

        value = await get_social("tiktok")

        await update.message.reply_text(
            f"🎵 Admin TikTok:\n\n{value}"
            if value
            else
            "😔 Admin এখনো TikTok ID/Link যোগ করেননি।"
        )
        return

    await update.message.reply_text(
        "😊 আমি বুঝতে পারিনি।\n\n"
        "ভিডিও/ছবি/অডিও দেখতে /start চাপুন।"
    )


# =========================================================
# CATEGORY
# =========================================================

async def show_category(update, context):

    query = update.callback_query
    await query.answer()

    category = query.data.split(":", 1)[1]

    async with db_pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT id, media_type, title
            FROM media
            WHERE category = $1
            ORDER BY id DESC
            """,
            category,
        )

    if not rows:
        await query.message.reply_text(
            f"📂 {category}\n\n"
            "😔 এই Category-তে এখনো কোনো Media নেই।"
        )
        return

    buttons = []

    icons = {
        "video": "🎬",
        "photo": "🖼️",
        "audio": "🎵",
    }

    for row in rows:

        icon = icons.get(
            row["media_type"],
            "📄",
        )

        title = row["title"] or f"Media #{row['id']}"

        buttons.append([
            InlineKeyboardButton(
                f"{icon} {title}",
                callback_data=f"media:{row['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "🏠 Home",
            callback_data="home",
        )
    ])

    await query.message.reply_text(
        f"📂 {category}\n\n"
        "নিচের Media নির্বাচন করুন:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# MEDIA WEB APP BUTTON
# =========================================================

async def send_media_button(update, context):

    query = update.callback_query
    await query.answer()

    try:
        media_id = int(
            query.data.split(":")[1]
        )
    except (ValueError, IndexError):

        await query.message.reply_text(
            "❌ ভুল Media ID।"
        )
        return

    async with db_pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT id, media_type, title
            FROM media
            WHERE id = $1
            """,
            media_id,
        )

    if not row:

        await query.message.reply_text(
            "❌ Media পাওয়া যায়নি।"
        )
        return

    title = row["title"] or "Media"

    web_url = (
        f"{WEB_URL}/app"
        f"?media_id={media_id}"
    )

    button = InlineKeyboardButton(
        f"🔓 {title} দেখুন",
        web_app=WebAppInfo(
            url=web_url,
        ),
    )

    await query.message.reply_text(
        f"🔒 *{title}*\n\n"
        "কনটেন্ট দেখতে নিচের Button চাপুন।\n\n"
        "📢 প্রথমে বিজ্ঞাপনের পেজ খুলবে।",
        reply_markup=InlineKeyboardMarkup([
            [button]
        ]),
        parse_mode="Markdown",
    )


# =========================================================
# ADMIN CALLBACK
# =========================================================

async def admin_callback(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.message.reply_text(
            "⛔ শুধু Admin এই কাজটি করতে পারবে।"
        )
        return

    action = query.data

    if action == "admin:add_video":
        await start_media_add(
            query.message,
            context,
            "video",
        )
        return

    if action == "admin:add_photo":
        await start_media_add(
            query.message,
            context,
            "photo",
        )
        return

    if action == "admin:add_audio":
        await start_media_add(
            query.message,
            context,
            "audio",
        )
        return

    if action == "admin:add_category":

        set_state(
            context,
            "add_category",
        )

        await query.message.reply_text(
            "📂 নতুন Category-এর নাম লিখো।"
        )
        return

    if action == "admin:add_facebook":

        set_state(
            context,
            "add_facebook",
        )

        await query.message.reply_text(
            "📘 Facebook ID বা Link পাঠাও।"
        )
        return

    if action == "admin:add_tiktok":

        set_state(
            context,
            "add_tiktok",
        )

        await query.message.reply_text(
            "🎵 TikTok ID বা Link পাঠাও।"
        )
        return

    if action == "admin:delete_facebook":

        await delete_social(
            "facebook",
            query.message,
        )
        return

    if action == "admin:delete_tiktok":

        await delete_social(
            "tiktok",
            query.message,
        )
        return

    if action == "admin:media":

        async with db_pool.acquire() as conn:

            rows = await conn.fetch(
                """
                SELECT id, media_type, title, category
                FROM media
                ORDER BY id DESC
                """
            )

        if not rows:

            await query.message.reply_text(
                "📭 কোনো Media নেই।"
            )
            return

        icons = {
            "video": "🎬",
            "photo": "🖼️",
            "audio": "🎵",
        }

        text = "📋 *সব Media*\n\n"

        for row in rows:

            icon = icons.get(
                row["media_type"],
                "📄",
            )

            text += (
                f"{icon} #{row['id']} "
                f"{row['title'] or 'Unnamed'}\n"
                f"📂 {row['category']}\n\n"
            )

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
        )
        return

    if action == "admin:delete_media":

        async with db_pool.acquire() as conn:

            rows = await conn.fetch(
                """
                SELECT id, media_type, title
                FROM media
                ORDER BY id DESC
                """
            )

        if not rows:

            await query.message.reply_text(
                "📭 কোনো Media নেই।"
            )
            return

        icons = {
            "video": "🎬",
            "photo": "🖼️",
            "audio": "🎵",
        }

        buttons = []

        for row in rows:

            icon = icons.get(
                row["media_type"],
                "📄",
            )

            title = row["title"] or f"Media #{row['id']}"

            buttons.append([
                InlineKeyboardButton(
                    f"🗑 {icon} {title}",
                    callback_data=f"delete_media:{row['id']}",
                )
            ])

        await query.message.reply_text(
            "🗑 কোন Media ডিলিট করবে?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if action == "admin:delete_category":

        async with db_pool.acquire() as conn:

            rows = await conn.fetch(
                """
                SELECT id, name
                FROM categories
                ORDER BY id
                """
            )

        buttons = []

        for row in rows:

            buttons.append([
                InlineKeyboardButton(
                    f"🗑 {row['name']}",
                    callback_data=f"delete_category:{row['id']}",
                )
            ])

        await query.message.reply_text(
            "🗑 কোন Category ডিলিট করবে?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if action == "admin:users":

        async with db_pool.acquire() as conn:

            count = await conn.fetchval(
                "SELECT COUNT(*) FROM contacts"
            )

        await query.message.reply_text(
            f"👥 মোট User/Contact: {count}"
        )
        return


# =========================================================
# DELETE MEDIA
# =========================================================

async def delete_media_callback(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        media_id = int(
            query.data.split(":")[1]
        )
    except (ValueError, IndexError):

        await query.message.reply_text(
            "❌ ভুল Media ID।"
        )
        return

    async with db_pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT title, media_type
            FROM media
            WHERE id = $1
            """,
            media_id,
        )

        if not row:

            await query.message.reply_text(
                "❌ Media পাওয়া যায়নি।"
            )
            return

        await conn.execute(
            """
            DELETE FROM media
            WHERE id = $1
            """,
            media_id,
        )

    await query.message.reply_text(
        f"✅ Media ডিলিট হয়েছে:\n"
        f"{row['title'] or 'Unnamed'}"
    )


# =========================================================
# DELETE CATEGORY
# =========================================================

async def delete_category_callback(update, context):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    try:
        category_id = int(
            query.data.split(":")[1]
        )
    except (ValueError, IndexError):

        await query.message.reply_text(
            "❌ ভুল Category ID।"
        )
        return

    async with db_pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT name
            FROM categories
            WHERE id = $1
            """,
            category_id,
        )

        if not row:

            await query.message.reply_text(
                "❌ Category পাওয়া যায়নি।"
            )
            return

        category_name = row["name"]

        count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM media
            WHERE category = $1
            """,
            category_name,
        )

        if count > 0:

            await query.message.reply_text(
                f"⚠️ এই Category-তে {count}টি Media আছে।\n\n"
                "Media হারানো ঠেকাতে Category delete করা হয়নি।"
            )
            return

        await conn.execute(
            """
            DELETE FROM categories
            WHERE id = $1
            """,
            category_id,
        )

    await query.message.reply_text(
        f"✅ Category ডিলিট হয়েছে:\n{category_name}"
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_router(update, context):

    query = update.callback_query
    data = query.data

    if data.startswith("category:"):

        await show_category(
            update,
            context,
        )

    elif data.startswith("media:"):

        await send_media_button(
            update,
            context,
        )

    elif data.startswith("social:"):

        await query.answer()

        platform = data.split(":", 1)[1]

        value = await get_social(platform)

        if value:

            await query.message.reply_text(
                f"📌 Admin {platform.title()}:\n\n{value}"
            )
        else:

            await query.message.reply_text(
                f"😔 Admin এখনো কোনো "
                f"{platform.title()} ID/Link যোগ করেননি।"
            )

    elif data.startswith("delete_media:"):

        await delete_media_callback(
            update,
            context,
        )

    elif data.startswith("delete_category:"):

        await delete_category_callback(
            update,
            context,
        )

    elif data.startswith("admin:"):

        await admin_callback(
            update,
            context,
        )

    elif data == "home":

        await query.answer()

        await query.message.reply_text(
            "🔥 Bangla Vibe\n\n"
            "/start চাপুন।"
        )


# =========================================================
# WEB APP MEDIA API
# =========================================================

def get_media_from_database(media_id):
    """
    Flask request-এর জন্য আলাদা asyncpg connection ব্যবহার করে।
    Telegram bot-এর asyncpg pool-এ event-loop সমস্যা এড়ানো হয়।
    """

    async def load():
        conn = await asyncpg.connect(
            DATABASE_URL,
            command_timeout=30,
        )

        try:
            return await conn.fetchrow(
                """
                SELECT id, file_id, media_type,
                       title, description, category
                FROM media
                WHERE id = $1
                """,
                media_id,
            )
        finally:
            await conn.close()

    return asyncio.run(load())


@web_app.route("/api/media/<int:media_id>")
def media_api(media_id):

    try:

        row = get_media_from_database(media_id)

        if not row:
            return jsonify({
                "ok": False,
                "error": "Media not found",
            }), 404

        return jsonify({
            "ok": True,
            "id": row["id"],
            "type": row["media_type"],
            "title": row["title"] or "Media",
            "description": row["description"] or "",
            "category": row["category"] or "",
            "media_url": (
                f"{WEB_URL}/media/"
                f"{row['media_type']}/"
                f"{row['id']}"
            ),
            "ad_url": ADSTERRA_SMARTLINK,
        })

    except Exception:

        logger.exception("MEDIA API ERROR")

        return jsonify({
            "ok": False,
            "error": "Server error",
        }), 500


# =========================================================
# TELEGRAM FILE PROXY
# =========================================================

@web_app.route("/media/<media_type>/<int:media_id>")
def media_proxy(media_type, media_id):

    try:

        row = get_media_from_database(media_id)

        if not row:
            return "Media not found", 404

        if row["media_type"] != media_type:
            return "Media type mismatch", 400

        file_id = row["file_id"]

        # Telegram getFile API
        api_url = (
            f"https://api.telegram.org/bot"
            f"{BOT_TOKEN}/getFile"
            f"?file_id={urllib.parse.quote(file_id)}"
        )

        with urllib.request.urlopen(
            api_url,
            timeout=30,
        ) as response:

            data = json.loads(
                response.read().decode("utf-8")
            )

        if not data.get("ok"):
            return "Telegram file error", 502

        file_path = data["result"]["file_path"]

        file_url = (
            f"https://api.telegram.org/file/bot"
            f"{BOT_TOKEN}/{file_path}"
        )

        req = urllib.request.Request(
            file_url,
            headers={
                "User-Agent": "BanglaVibe/1.0"
            },
        )

        upstream = urllib.request.urlopen(
            req,
            timeout=60,
        )

        content_type = (
            upstream.headers.get(
                "Content-Type",
                "application/octet-stream",
            )
        )

        def generate():

            try:

                while True:

                    chunk = upstream.read(
                        1024 * 1024
                    )

                    if not chunk:
                        break

                    yield chunk

            finally:
                upstream.close()

        return Response(
            generate(),
            content_type=content_type,
        )

    except Exception:

        logger.exception(
            "MEDIA PROXY ERROR"
        )

        return "Unable to load media", 500


# =========================================================
# ERROR
# =========================================================

async def error_handler(update, context):

    logger.error(
        "Telegram error: %s",
        context.error,
        exc_info=context.error,
    )


# =========================================================
# POST INIT
# =========================================================

async def post_init(application):

    await init_db()

    logger.info(
        "🔥 Database initialized successfully"
    )


# =========================================================
# MAIN
# =========================================================

def main():

    web_thread = Thread(
        target=run_web_server,
        daemon=True,
    )

    web_thread.start()

    logger.info(
        "🌐 Render web server started"
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin_panel,
        )
    )

    # VIDEO
    application.add_handler(
        MessageHandler(
            filters.VIDEO,
            video_message,
        )
    )

    # PHOTO
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_message,
        )
    )

    # AUDIO
    application.add_handler(
        MessageHandler(
            filters.AUDIO,
            audio_message,
        )
    )

    # VOICE
    application.add_handler(
        MessageHandler(
            filters.VOICE,
            audio_message,
        )
    )

    # CALLBACK
    application.add_handler(
        CallbackQueryHandler(
            callback_router,
        )
    )

    # TEXT
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    application.add_error_handler(
        error_handler,
    )

    logger.info(
        "🤖 Bangla Vibe Bot started"
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()

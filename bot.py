import os
import asyncio
import logging
import urllib.request
import urllib.parse
from threading import Thread

import asyncpg

from flask import Flask, jsonify, Response, request

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    BotCommand,
    BotCommandScopeChat,
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

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

ADMIN_ID = 8721334265

WEB_URL = "https://bangla-vibe-bot.onrender.com"

AD_URL = (
    "https://www.profitableratecpmnetwork.com/"
    "cc0kqkj3?key=96d4caaba1f61e2b9329ad0d07861cf4"
)

PORT = int(os.environ.get("PORT", "8080"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is missing")


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

# =========================================================
# DATABASE
# =========================================================


async def db_connect():
    return await asyncpg.connect(DATABASE_URL)


async def init_db():
    conn = await db_connect()

    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS media (
                id SERIAL PRIMARY KEY,
                media_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                thumbnail_file_id TEXT,
                title TEXT NOT NULL,
                description TEXT,
                category TEXT,
                created_by BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE,
                first_name TEXT,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                media_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS social_links (
                id SERIAL PRIMARY KEY,
                platform TEXT UNIQUE NOT NULL,
                value TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Existing old contacts table migration
        try:
            await conn.execute("""
                ALTER TABLE contacts
                ADD COLUMN IF NOT EXISTS user_id BIGINT
            """)
        except Exception:
            pass

        # Default categories
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

    finally:
        await conn.close()

    logger.info("Database initialized")


def get_db_sync():
    loop = asyncio.new_event_loop()

    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(db_connect())
    finally:
        asyncio.set_event_loop(None)


# =========================================================
# HELPERS
# =========================================================


def is_admin(user_id):
    return int(user_id) == ADMIN_ID


def normalize(text):
    if not text:
        return ""

    return (
        text.strip()
        .lower()
        .replace("য়", "য়")
        .replace("ড়", "ড়")
        .replace("ঢ়", "ঢ়")
    )


def user_states(context):
    if "states" not in context.application.bot_data:
        context.application.bot_data["states"] = {}

    return context.application.bot_data["states"]


def get_state(context, user_id):
    return user_states(context).get(user_id)


def set_state(context, user_id, value):
    user_states(context)[user_id] = value


def clear_state(context, user_id):
    user_states(context).pop(user_id, None)


async def save_contact(user):
    conn = await db_connect()

    try:
        await conn.execute(
            """
            INSERT INTO contacts (user_id, first_name, username)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id)
            DO UPDATE SET
                first_name = EXCLUDED.first_name,
                username = EXCLUDED.username
            """,
            user.id,
            user.first_name or "",
            user.username or "",
        )
    finally:
        await conn.close()


# =========================================================
# COMMAND MENU
# =========================================================


async def setup_commands(application):

    # Normal users
    await application.bot.set_my_commands([
        BotCommand("start", "Bot শুরু করুন"),
    ])

    # Admin account gets /admin too
    try:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Bot শুরু করুন"),
                BotCommand("admin", "Admin Panel"),
            ],
            scope=BotCommandScopeChat(chat_id=ADMIN_ID),
        )
    except Exception as e:
        logger.warning("Could not set admin command menu: %s", e)


# =========================================================
# START
# =========================================================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user:
        await save_contact(update.effective_user)

    keyboard = [
        [
            InlineKeyboardButton(
                "🎬 ভিডিও",
                callback_data="cat:🎬 ভিডিও",
            ),
            InlineKeyboardButton(
                "🎵 গান",
                callback_data="cat:🎵 গান",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎭 নাটক",
                callback_data="cat:🎭 নাটক",
            ),
            InlineKeyboardButton(
                "📸 ফটো",
                callback_data="cat:📸 ফটো",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎥 মুভি",
                callback_data="cat:🎥 মুভি",
            ),
        ],
    ]

    await update.message.reply_text(
        "🔥 Bangla Vibe\n\n"
        "স্বাগতম!\n"
        "নিচের Category থেকে Content নির্বাচন করুন।",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================================================
# ADMIN PANEL
# =========================================================


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "⛔ এই Command শুধুমাত্র Admin-এর জন্য।"
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🎬 ভিডিও যোগ",
                callback_data="admin_add_video",
            ),
            InlineKeyboardButton(
                "📸 ছবি যোগ",
                callback_data="admin_add_photo",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎵 অডিও যোগ",
                callback_data="admin_add_audio",
            ),
            InlineKeyboardButton(
                "📁 Category",
                callback_data="admin_categories",
            ),
        ],
        [
            InlineKeyboardButton(
                "🗑 Media Delete",
                callback_data="admin_delete",
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Users",
                callback_data="admin_users",
            ),
            InlineKeyboardButton(
                "📊 All Media",
                callback_data="admin_all_media",
            ),
        ],
    ]

    await update.message.reply_text(
        "👑 Admin Panel\n\n"
        "এখান থেকে Content Manage করতে পারবে।",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================================================
# NATURAL ADMIN COMMAND
# =========================================================


def is_video_command(text):
    t = normalize(text)

    return any(
        x in t
        for x in [
            "ভিডিও দাও",
            "ভিডিও দেও",
            "ভিডিও দে",
            "ভিডিও দেন",
            "ভিডিও দিন",
            "video dao",
            "video deo",
            "video de",
            "video den",
            "add video",
            "upload video",
        ]
    )


def is_photo_command(text):
    t = normalize(text)

    return any(
        x in t
        for x in [
            "ছবি দাও",
            "ছবি দেও",
            "ছবি দে",
            "ছবি দেন",
            "photo dao",
            "photo deo",
            "add photo",
            "upload photo",
        ]
    )


def is_audio_command(text):
    t = normalize(text)

    return any(
        x in t
        for x in [
            "গান দাও",
            "গান দেও",
            "গান দে",
            "audio dao",
            "audio deo",
            "add audio",
            "upload audio",
        ]
    )


# =========================================================
# ADMIN ADD MEDIA
# =========================================================


async def start_add_media(update, context, media_type):

    user_id = update.effective_user.id

    if not is_admin(user_id):
        if update.callback_query:
            await update.callback_query.answer(
                "⛔ Admin only",
                show_alert=True,
            )
        return

    clear_state(context, user_id)

    set_state(
        context,
        user_id,
        {
            "action": "add_media",
            "media_type": media_type,
            "file_id": None,
            "thumbnail_file_id": None,
            "title": None,
            "description": None,
            "category": None,
        },
    )

    message = (
        "🎬 ভিডিও যোগ করা হচ্ছে।"
        if media_type == "video"
        else
        "📸 ছবি যোগ করা হচ্ছে।"
        if media_type == "photo"
        else
        "🎵 অডিও যোগ করা হচ্ছে।"
    )

    await update.callback_query.message.reply_text(
        message
        + "\n\n"
        + "1️⃣ প্রথমে মূল Content পাঠাও।"
    )


async def handle_admin_media_file(update, context):

    user_id = update.effective_user.id

    if not is_admin(user_id):
        return

    state = get_state(context, user_id)

    if not state or state.get("action") != "add_media":
        return False

    media_type = state["media_type"]

    file_id = None

    if media_type == "video" and update.message.video:
        file_id = update.message.video.file_id

    elif media_type == "photo" and update.message.photo:
        file_id = update.message.photo[-1].file_id

    elif media_type == "audio" and update.message.audio:
        file_id = update.message.audio.file_id

    if not file_id:
        return False

    state["file_id"] = file_id
    set_state(context, user_id, state)

    if media_type == "video":
        await update.message.reply_text(
            "✅ ভিডিও পেয়েছি।\n\n"
            "🖼 এখন ভিডিওর Thumbnail পাঠাও।\n\n"
            "Thumbnail না থাকলে একটি ছবি পাঠালেও হবে।"
        )
    else:
        await update.message.reply_text(
            "✅ Content পেয়েছি।\n\n"
            "🖼 এখন Thumbnail পাঠাও।\n\n"
            "Thumbnail না চাইলে `skip` লিখতে পারো।"
        )

    return True


async def handle_thumbnail(update, context):

    user_id = update.effective_user.id

    if not is_admin(user_id):
        return False

    state = get_state(context, user_id)

    if not state or state.get("action") != "add_media":
        return False

    thumbnail_id = None

    if update.message.photo:
        thumbnail_id = update.message.photo[-1].file_id

    elif update.message.document:
        mime = update.message.document.mime_type or ""

        if mime.startswith("image/"):
            thumbnail_id = update.message.document.file_id

    if thumbnail_id:
        state["thumbnail_file_id"] = thumbnail_id
        state["step"] = "title"

        set_state(context, user_id, state)

        await update.message.reply_text(
            "✅ Thumbnail পেয়েছি।\n\n"
            "📝 এখন ভিডিও/Content-এর Title লিখো।"
        )

        return True

    return False


# =========================================================
# TEXT STATE HANDLER
# =========================================================


async def handle_state_text(update, context):

    user_id = update.effective_user.id

    if not is_admin(user_id):
        return False

    state = get_state(context, user_id)

    if not state:
        return False

    text = (update.message.text or "").strip()

    if text.lower() == "cancel":
        clear_state(context, user_id)

        await update.message.reply_text(
            "❌ কাজ বাতিল করা হয়েছে।"
        )

        return True

    if state.get("action") == "add_media":

        # Thumbnail skip
        if not state.get("thumbnail_file_id"):
            if text.lower() == "skip":
                state["step"] = "title"

                set_state(context, user_id, state)

                await update.message.reply_text(
                    "⏭ Thumbnail বাদ দেওয়া হয়েছে।\n\n"
                    "📝 এখন Title লিখো।"
                )

                return True

        # Title
        if not state.get("title"):
            state["title"] = text
            state["step"] = "description"

            set_state(context, user_id, state)

            await update.message.reply_text(
                "📝 Description লিখো।\n\n"
                "না চাইলে `skip` লিখো।"
            )

            return True

        # Description
        if state.get("description") is None:
            state["description"] = (
                "" if text.lower() == "skip" else text
            )

            state["step"] = "category"

            set_state(context, user_id, state)

            categories = await get_categories()

            if categories:
                keyboard = [
                    [
                        InlineKeyboardButton(
                            c,
                            callback_data="choosecat:" + c,
                        )
                    ]
                    for c in categories
                ]

                await update.message.reply_text(
                    "📁 Category নির্বাচন করো:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                await update.message.reply_text(
                    "📁 Category লিখো:"
                )

            return True

    return False


# =========================================================
# CATEGORY
# =========================================================


async def get_categories():

    conn = await db_connect()

    try:
        rows = await conn.fetch(
            "SELECT name FROM categories ORDER BY id"
        )

        return [r["name"] for r in rows]

    finally:
        await conn.close()


async def save_media(state, user_id):

    conn = await db_connect()

    try:
        row = await conn.fetchrow(
            """
            INSERT INTO media
            (
                media_type,
                file_id,
                thumbnail_file_id,
                title,
                description,
                category,
                created_by
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING id
            """,
            state["media_type"],
            state["file_id"],
            state.get("thumbnail_file_id"),
            state["title"],
            state.get("description") or "",
            state.get("category") or "🎬 ভিডিও",
            user_id,
        )

        return row["id"]

    finally:
        await conn.close()


async def finish_media(update, context, category):

    user_id = update.effective_user.id

    if not is_admin(user_id):
        return

    state = get_state(context, user_id)

    if not state or state.get("action") != "add_media":
        return

    state["category"] = category

    media_id = await save_media(state, user_id)

    clear_state(context, user_id)

    await update.callback_query.message.reply_text(
        "✅ Content সফলভাবে যোগ হয়েছে!\n\n"
        f"🆔 Media ID: {media_id}\n"
        f"📁 Category: {category}\n"
        f"📝 Title: {state['title']}\n\n"
        "এখন User এটি দেখতে পারবে।"
    )


# =========================================================
# CATEGORY CALLBACK
# =========================================================


async def category_callback(update, context):

    query = update.callback_query
    await query.answer()

    category = query.data.replace("cat:", "", 1)

    await show_category(query.message, category)


async def choose_category_callback(update, context):

    query = update.callback_query
    await query.answer()

    category = query.data.replace("choosecat:", "", 1)

    await finish_media(update, context, category)


async def show_category(message, category):

    conn = await db_connect()

    try:
        rows = await conn.fetch(
            """
            SELECT id, media_type, title, description,
                   thumbnail_file_id
            FROM media
            WHERE category = $1
            ORDER BY id DESC
            LIMIT 30
            """,
            category,
        )
    finally:
        await conn.close()

    if not rows:
        await message.reply_text(
            f"📁 {category}\n\n"
            "এখনো কোনো Content যোগ করা হয়নি।"
        )
        return

    for row in rows:

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔒 ভিডিও/কনটেন্ট দেখুন",
                    web_app=WebAppInfo(
                        url=f"{WEB_URL}/app?id={row['id']}"
                    ),
                )
            ]
        ]

        title = row["title"]

        caption = (
            f"🔒 {title}\n\n"
            "কনটেন্ট দেখতে নিচের Button চাপুন।\n\n"
            "📢 প্রথমে বিজ্ঞাপনের পেজ খুলবে।"
        )

        thumb = row["thumbnail_file_id"]

        try:
            if thumb:
                await message.reply_photo(
                    photo=thumb,
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                await message.reply_text(
                    caption,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

        except Exception as e:
            logger.warning("Thumbnail send failed: %s", e)

            await message.reply_text(
                caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )


# =========================================================
# ADMIN CALLBACK
# =========================================================


async def admin_callback(update, context):

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.answer(
            "⛔ Admin only",
            show_alert=True,
        )
        return

    data = query.data

    if data == "admin_add_video":
        await start_add_media(
            update,
            context,
            "video",
        )

    elif data == "admin_add_photo":
        await start_add_media(
            update,
            context,
            "photo",
        )

    elif data == "admin_add_audio":
        await start_add_media(
            update,
            context,
            "audio",
        )

    elif data == "admin_categories":
        await admin_categories(update, context)

    elif data == "admin_users":
        await admin_users(update, context)

    elif data == "admin_all_media":
        await admin_all_media(update, context)

    elif data == "admin_delete":
        await admin_delete_menu(update, context)


# =========================================================
# ADMIN CATEGORY
# =========================================================


async def admin_categories(update, context):

    categories = await get_categories()

    keyboard = [
        [
            InlineKeyboardButton(
                "➕ Category যোগ",
                callback_data="admin_add_category",
            )
        ]
    ]

    await update.callback_query.message.reply_text(
        "📁 Categories\n\n"
        + "\n".join(categories),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def add_category_start(update, context):

    user_id = update.effective_user.id

    if not is_admin(user_id):
        return

    set_state(
        context,
        user_id,
        {
            "action": "add_category"
        },
    )

    await update.callback_query.message.reply_text(
        "📁 নতুন Category-এর নাম লিখো।"
    )


async def add_category_text(update, context):

    user_id = update.effective_user.id

    if not is_admin(user_id):
        return False

    state = get_state(context, user_id)

    if not state:
        return False

    if state.get("action") != "add_category":
        return False

    category = update.message.text.strip()

    conn = await db_connect()

    try:
        await conn.execute(
            """
            INSERT INTO categories (name)
            VALUES ($1)
            ON CONFLICT (name) DO NOTHING
            """,
            category,
        )
    finally:
        await conn.close()

    clear_state(context, user_id)

    await update.message.reply_text(
        f"✅ Category যোগ হয়েছে:\n{category}"
    )

    return True


# =========================================================
# ADMIN USERS
# =========================================================


async def admin_users(update, context):

    conn = await db_connect()

    try:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM contacts"
        )
    finally:
        await conn.close()

    await update.callback_query.message.reply_text(
        f"👥 Total Users: {count}"
    )


# =========================================================
# ADMIN MEDIA LIST
# =========================================================


async def admin_all_media(update, context):

    conn = await db_connect()

    try:
        rows = await conn.fetch(
            """
            SELECT id, title, media_type, category
            FROM media
            ORDER BY id DESC
            LIMIT 50
            """
        )
    finally:
        await conn.close()

    if not rows:
        await update.callback_query.message.reply_text(
            "কোনো Media নেই।"
        )
        return

    text = "📊 All Media\n\n"

    for row in rows:
        text += (
            f"🆔 {row['id']} | "
            f"{row['media_type']} | "
            f"{row['category']}\n"
            f"📝 {row['title']}\n\n"
        )

    await update.callback_query.message.reply_text(text)


# =========================================================
# DELETE MEDIA
# =========================================================


async def admin_delete_menu(update, context):

    conn = await db_connect()

    try:
        rows = await conn.fetch(
            """
            SELECT id, title
            FROM media
            ORDER BY id DESC
            LIMIT 30
            """
        )
    finally:
        await conn.close()

    if not rows:
        await update.callback_query.message.reply_text(
            "Delete করার মতো Media নেই।"
        )
        return

    keyboard = []

    for row in rows:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🗑 {row['id']} - {row['title'][:25]}",
                    callback_data=f"delete:{row['id']}",
                )
            ]
        )

    await update.callback_query.message.reply_text(
        "🗑 কোন Content Delete করবে?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def delete_media(update, context):

    query = update.callback_query

    if not is_admin(query.from_user.id):
        await query.answer(
            "⛔ Admin only",
            show_alert=True,
        )
        return

    media_id = int(
        query.data.replace("delete:", "", 1)
    )

    conn = await db_connect()

    try:
        result = await conn.execute(
            "DELETE FROM media WHERE id = $1",
            media_id,
        )
    finally:
        await conn.close()

    await query.answer("Deleted")

    await query.message.reply_text(
        f"🗑 Media ID {media_id} delete করা হয়েছে।"
    )


# =========================================================
# SOCIAL LINKS
# =========================================================


async def save_social(platform, value):

    conn = await db_connect()

    try:
        await conn.execute(
            """
            INSERT INTO social_links (platform, value)
            VALUES ($1,$2)
            ON CONFLICT (platform)
            DO UPDATE SET value = EXCLUDED.value
            """,
            platform,
            value,
        )
    finally:
        await conn.close()


async def get_social(platform):

    conn = await db_connect()

    try:
        value = await conn.fetchval(
            """
            SELECT value
            FROM social_links
            WHERE platform = $1
            """,
            platform,
        )

        return value

    finally:
        await conn.close()


# =========================================================
# ADMIN TEXT
# =========================================================


async def text_handler(update, context):

    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    await save_contact(update.effective_user)

    # Admin media file/text state
    if is_admin(user_id):

        state = get_state(context, user_id)

        if state:

            if state.get("action") == "add_category":
                if await add_category_text(update, context):
                    return

            if state.get("action") == "add_media":
                if await handle_state_text(update, context):
                    return

        # Natural language commands
        if is_video_command(text):
            set_state(
                context,
                user_id,
                {
                    "action": "add_media",
                    "media_type": "video",
                    "file_id": None,
                    "thumbnail_file_id": None,
                    "title": None,
                    "description": None,
                    "category": None,
                },
            )

            await update.message.reply_text(
                "🎬 ভিডিও যোগ করা হচ্ছে।\n\n"
                "প্রথমে ভিডিও পাঠাও।"
            )
            return

        if is_photo_command(text):
            set_state(
                context,
                user_id,
                {
                    "action": "add_media",
                    "media_type": "photo",
                    "file_id": None,
                    "thumbnail_file_id": None,
                    "title": None,
                    "description": None,
                    "category": None,
                },
            )

            await update.message.reply_text(
                "📸 ছবি যোগ করা হচ্ছে।\n\n"
                "প্রথমে ছবি পাঠাও।"
            )
            return

        if is_audio_command(text):
            set_state(
                context,
                user_id,
                {
                    "action": "add_media",
                    "media_type": "audio",
                    "file_id": None,
                    "thumbnail_file_id": None,
                    "title": None,
                    "description": None,
                    "category": None,
                },
            )

            await update.message.reply_text(
                "🎵 অডিও যোগ করা হচ্ছে।\n\n"
                "প্রথমে Audio পাঠাও।"
            )
            return

    # Social admin commands
    t = normalize(text)

    if t in [
        "এডমিন ফেসবুক আইডি দাও",
        "admin facebook dao",
    ]:
        value = await get_social("facebook")

        if value:
            await update.message.reply_text(
                f"📘 Admin Facebook:\n{value}"
            )
        else:
            await update.message.reply_text(
                "Facebook ID এখনো দেওয়া হয়নি।"
            )

        return

    if t in [
        "এডমিন টিকটক আইডি দাও",
        "admin tiktok dao",
    ]:
        value = await get_social("tiktok")

        if value:
            await update.message.reply_text(
                f"🎵 Admin TikTok:\n{value}"
            )
        else:
            await update.message.reply_text(
                "TikTok ID এখনো দেওয়া হয়নি।"
            )

        return


# =========================================================
# MEDIA MESSAGE HANDLER
# =========================================================


async def media_handler(update, context):

    if not update.message:
        return

    user_id = update.effective_user.id

    if not is_admin(user_id):
        return

    state = get_state(context, user_id)

    if not state:
        return

    if state.get("action") != "add_media":
        return

    # If waiting for thumbnail
    if state.get("file_id") and not state.get("title"):
        if await handle_thumbnail(update, context):
            return

    # Main content
    if await handle_admin_media_file(update, context):
        return


# =========================================================
# CALLBACK ROUTER
# =========================================================


async def callback_router(update, context):

    query = update.callback_query
    data = query.data or ""

    if data.startswith("cat:"):
        await category_callback(update, context)

    elif data.startswith("choosecat:"):
        await choose_category_callback(update, context)

    elif data.startswith("admin_"):
        if data == "admin_add_category":
            await add_category_start(update, context)
        else:
            await admin_callback(update, context)

    elif data.startswith("delete:"):
        await delete_media(update, context)

    else:
        await query.answer()


# =========================================================
# TELEGRAM FILE URL
# =========================================================


def telegram_file_url(file_id):

    url = (
        "https://api.telegram.org/bot"
        + BOT_TOKEN
        + "/getFile?file_id="
        + urllib.parse.quote(file_id)
    )

    with urllib.request.urlopen(
        url,
        timeout=30,
    ) as response:

        data = response.read().decode("utf-8")

    result = __import__("json").loads(data)

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram getFile failed"
        )

    file_path = result["result"]["file_path"]

    return (
        "https://api.telegram.org/file/bot"
        + BOT_TOKEN
        + "/"
        + file_path
    )


# =========================================================
# FLASK WEB
# =========================================================


@app.route("/")
def home():
    return "Bangla Vibe Bot is running."


@app.route("/health")
def health():
    return jsonify({
        "status": "ok"
    })


@app.route("/app")
def web_app():
    file_path = os.path.join(
        os.path.dirname(__file__),
        "index.html",
    )

    if not os.path.exists(file_path):
        return "index.html not found", 404

    with open(
        file_path,
        "r",
        encoding="utf-8",
    ) as f:
        return f.read()


# =========================================================
# API MEDIA
# =========================================================


@app.route("/api/media/<int:media_id>")
def api_media(media_id):

    conn = get_db_sync()

    try:
        row = conn.fetchrow(
            """
            SELECT id,
                   media_type,
                   file_id,
                   thumbnail_file_id,
                   title,
                   description,
                   category
            FROM media
            WHERE id = $1
            """,
            media_id,
        )

        # asyncpg coroutine handling
        loop = asyncio.new_event_loop()

        try:
            asyncio.set_event_loop(loop)
            row = loop.run_until_complete(row)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        if not row:
            return jsonify({
                "error": "Media not found"
            }), 404

        return jsonify({
            "id": row["id"],
            "media_type": row["media_type"],
            "title": row["title"],
            "description": row["description"] or "",
            "category": row["category"] or "",
            "thumbnail_url": (
                f"/thumbnail/{row['id']}"
                if row["thumbnail_file_id"]
                else ""
            ),
            "media_url": (
                f"/media/{row['media_type']}/{row['id']}"
            ),
        })

    except Exception as e:
        logger.exception("API error")

        return jsonify({
            "error": str(e)
        }), 500

    finally:
        try:
            conn.close()
        except Exception:
            pass


# =========================================================
# THUMBNAIL PROXY
# =========================================================


@app.route("/thumbnail/<int:media_id>")
def thumbnail_proxy(media_id):

    async def get_row():
        conn = await db_connect()

        try:
            return await conn.fetchrow(
                """
                SELECT thumbnail_file_id
                FROM media
                WHERE id = $1
                """,
                media_id,
            )
        finally:
            await conn.close()

    loop = asyncio.new_event_loop()

    try:
        asyncio.set_event_loop(loop)
        row = loop.run_until_complete(get_row())
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    if not row or not row["thumbnail_file_id"]:
        return "Thumbnail not found", 404

    try:
        url = telegram_file_url(
            row["thumbnail_file_id"]
        )

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
        )

        response = urllib.request.urlopen(
            req,
            timeout=60,
        )

        content = response.read()

        content_type = (
            response.headers.get(
                "Content-Type",
                "image/jpeg",
            )
        )

        return Response(
            content,
            content_type=content_type,
        )

    except Exception as e:
        logger.exception("Thumbnail proxy error")
        return "Thumbnail error", 500


# =========================================================
# VIDEO / MEDIA PROXY
# =========================================================


@app.route("/media/<media_type>/<int:media_id>")
def media_proxy(media_type, media_id):

    async def get_row():
        conn = await db_connect()

        try:
            return await conn.fetchrow(
                """
                SELECT file_id, media_type
                FROM media
                WHERE id = $1
                """,
                media_id,
            )
        finally:
            await conn.close()

    loop = asyncio.new_event_loop()

    try:
        asyncio.set_event_loop(loop)
        row = loop.run_until_complete(get_row())
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    if not row:
        return "Media not found", 404

    if row["media_type"] != media_type:
        return "Invalid media type", 400

    try:
        telegram_url = telegram_file_url(
            row["file_id"]
        )

        headers = {
            "User-Agent": "Mozilla/5.0",
        }

        # Very important for video seeking
        range_header = request.headers.get("Range")

        if range_header:
            headers["Range"] = range_header

        req = urllib.request.Request(
            telegram_url,
            headers=headers,
        )

        upstream = urllib.request.urlopen(
            req,
            timeout=120,
        )

        content_type = upstream.headers.get(
            "Content-Type",
            "application/octet-stream",
        )

        status = upstream.status

        response_headers = {}

        for header in [
            "Content-Length",
            "Content-Range",
            "Accept-Ranges",
            "Last-Modified",
            "ETag",
        ]:
            value = upstream.headers.get(header)

            if value:
                response_headers[header] = value

        def generate():

            while True:
                chunk = upstream.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                yield chunk

            try:
                upstream.close()
            except Exception:
                pass

        return Response(
            generate(),
            status=status,
            headers=response_headers,
            content_type=content_type,
        )

    except Exception as e:
        logger.exception(
            "Media proxy error: %s",
            e,
        )

        return "Media playback error", 500


# =========================================================
# ERROR
# =========================================================


@app.errorhandler(Exception)
def flask_error(error):

    logger.exception(
        "Flask error: %s",
        error,
    )

    return jsonify({
        "error": "Server error"
    }), 500


# =========================================================
# FLASK THREAD
# =========================================================


def run_flask():

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        threaded=True,
        use_reloader=False,
    )


# =========================================================
# POST INIT
# =========================================================


async def post_init(application):

    await init_db()
    await setup_commands(application)

    logger.info(
        "🤖 Bangla Vibe Bot started..."
    )


# =========================================================
# MAIN
# =========================================================


def main():

    Thread(
        target=run_flask,
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin_command,
        )
    )

    # Callback
    application.add_handler(
        CallbackQueryHandler(
            callback_router,
        )
    )

    # Photos/videos/audio/documents
    application.add_handler(
        MessageHandler(
            filters.PHOTO
            | filters.VIDEO
            | filters.AUDIO
            | filters.Document.IMAGE,
            media_handler,
        )
    )

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    logger.info(
        "🌐 Web server: http://0.0.0.0:%s",
        PORT,
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()

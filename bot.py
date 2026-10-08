import os
import asyncio
import threading
import logging
from pathlib import Path

import asyncpg
from flask import Flask, jsonify, send_from_directory, Response

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
    BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

ADMIN_ID = 8721334265

WEB_URL = os.getenv(
    "WEB_URL",
    "https://bangla-vibe-bot.onrender.com"
).rstrip("/")

PORT = int(os.getenv("PORT", "8080"))

PAGE_SIZE = 5

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("BanglaVibe")

# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent


@app.route("/")
def home():
    return """
    <!DOCTYPE html>
    <html lang="bn">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Bangla Vibe</title>
        <style>
            body{
                margin:0;
                background:#09000f;
                color:white;
                font-family:Arial,sans-serif;
                text-align:center;
                padding:40px 20px;
            }
            h1{
                color:#d946ef;
            }
        </style>
    </head>
    <body>
        <h1>🔥 Bangla Vibe</h1>
        <p>Bot is running successfully.</p>
    </body>
    </html>
    """


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "bot": "Bangla Vibe"
    })


@app.route("/app")
def web_app():
    return send_from_directory(BASE_DIR, "index.html")


# =========================================================
# DATABASE
# =========================================================

async def db_connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    return await asyncpg.connect(DATABASE_URL)


async def init_db():
    conn = await db_connect()

    try:
        # -------------------------------------------------
        # Categories
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            )
        """)

        # -------------------------------------------------
        # Media
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS media (
                id SERIAL PRIMARY KEY,
                media_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                thumb_file_id TEXT,
                title TEXT NOT NULL DEFAULT 'Untitled',
                description TEXT,
                category TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # -------------------------------------------------
        # Safe migrations for old database
        # -------------------------------------------------

        await conn.execute("""
            ALTER TABLE media
            ADD COLUMN IF NOT EXISTS thumb_file_id TEXT
        """)

        await conn.execute("""
            ALTER TABLE media
            ADD COLUMN IF NOT EXISTS title TEXT
        """)

        await conn.execute("""
            ALTER TABLE media
            ADD COLUMN IF NOT EXISTS description TEXT
        """)

        await conn.execute("""
            ALTER TABLE media
            ADD COLUMN IF NOT EXISTS category TEXT
        """)

        await conn.execute("""
            ALTER TABLE media
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()
        """)

        # Old rows without title
        await conn.execute("""
            UPDATE media
            SET title = 'Untitled'
            WHERE title IS NULL OR title = ''
        """)

        # -------------------------------------------------
        # Contacts
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            ALTER TABLE contacts
            ADD COLUMN IF NOT EXISTS user_id BIGINT
        """)

        await conn.execute("""
            ALTER TABLE contacts
            ADD COLUMN IF NOT EXISTS username TEXT
        """)

        await conn.execute("""
            ALTER TABLE contacts
            ADD COLUMN IF NOT EXISTS first_name TEXT
        """)

        await conn.execute("""
            ALTER TABLE contacts
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()
        """)

        # -------------------------------------------------
        # Requests
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                request TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # -------------------------------------------------
        # Social links
        # -------------------------------------------------

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS social_links (
                id SERIAL PRIMARY KEY,
                platform TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # -------------------------------------------------
        # Default categories
        # Only INSERT if missing.
        # NOTHING IS DELETED.
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
                INSERT INTO categories(name)
                VALUES($1)
                ON CONFLICT(name) DO NOTHING
                """,
                category
            )

        logger.info("Database initialized successfully.")

    finally:
        await conn.close()


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id):
    return int(user_id) == ADMIN_ID


async def save_contact(update: Update):
    if not update.effective_user:
        return

    user = update.effective_user

    try:
        conn = await db_connect()

        try:
            await conn.execute(
                """
                INSERT INTO contacts(
                    user_id,
                    username,
                    first_name
                )
                VALUES($1,$2,$3)
                """,
                user.id,
                user.username,
                user.first_name,
            )
        finally:
            await conn.close()

    except Exception as e:
        logger.error("save_contact error: %s", e)


# =========================================================
# KEYBOARDS
# =========================================================

def open_video_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("▶️ Open Video")
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_open_video_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("▶️ Open Video")
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_panel_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎬 Add Video",
                callback_data="add_video"
            ),
            InlineKeyboardButton(
                "📸 Add Photo",
                callback_data="add_photo"
            )
        ],
        [
            InlineKeyboardButton(
                "🎵 Add Audio",
                callback_data="add_audio"
            ),
            InlineKeyboardButton(
                "📁 Add Category",
                callback_data="add_category"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 Delete Media",
                callback_data="delete_media"
            ),
            InlineKeyboardButton(
                "📋 View Media",
                callback_data="view_media"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Users",
                callback_data="view_users"
            ),
            InlineKeyboardButton(
                "🔗 Social Links",
                callback_data="social_links"
            )
        ],
    ])


# =========================================================
# TELEGRAM COMMAND MENU
# =========================================================

async def clear_command_menus(application):
    """
    Remove previously configured command lists.

    We do NOT create a normal command menu for users.
    """

    scopes = [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
        BotCommandScopeAllChatAdministrators(),
    ]

    for scope in scopes:
        try:
            await application.bot.delete_my_commands(
                scope=scope
            )
        except Exception as e:
            logger.warning(
                "Could not delete commands for scope %s: %s",
                scope,
                e
            )

    # Admin private chat: remove command list too.
    try:
        await application.bot.delete_my_commands(
            scope=BotCommandScopeChat(
                chat_id=ADMIN_ID
            )
        )
    except Exception as e:
        logger.warning(
            "Could not clear admin command scope: %s",
            e
        )

    # Return menu button to default behavior.
    # Actual visible command menu is empty because commands
    # were deleted above.
    try:
        await application.bot.set_chat_menu_button(
            chat_id=ADMIN_ID
        )
    except Exception as e:
        logger.warning(
            "Could not reset admin menu button: %s",
            e
        )

    try:
        await application.bot.set_chat_menu_button()
    except Exception as e:
        logger.warning(
            "Could not reset default menu button: %s",
            e
        )


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await save_contact(update)

    user_id = update.effective_user.id

    # Clear temporary state
    context.user_data.clear()

    if is_admin(user_id):
        await update.message.reply_text(
            "🔥 Bangla Vibe\n\n"
            "👑 Admin mode চালু হয়েছে।\n\n"
            "▶️ Open Video চাপলে Start/Admin অপশন দেখতে পারবে।",
            reply_markup=admin_open_video_keyboard()
        )
    else:
        await update.message.reply_text(
            "🔥 Bangla Vibe\n\n"
            "স্বাগতম!\n"
            "ভিডিও/কনটেন্ট দেখতে নিচের বাটনে চাপুন।",
            reply_markup=open_video_keyboard()
        )


# =========================================================
# OPEN VIDEO
# =========================================================

async def open_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await save_contact(update)

    user_id = update.effective_user.id

    if is_admin(user_id):

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "/start",
                    callback_data="admin_start"
                ),
                InlineKeyboardButton(
                    "/admin",
                    callback_data="admin_panel"
                )
            ]
        ])

        await update.message.reply_text(
            "👑 Admin Options",
            reply_markup=keyboard
        )

    else:
        # Normal user:
        # Open Video = /start behavior
        await start(update, context)


# =========================================================
# ADMIN COMMAND
# =========================================================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.effective_user:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ এই অপশনটি শুধু Admin-এর জন্য।"
        )
        return

    context.user_data.clear()

    await update.message.reply_text(
        "👑 Bangla Vibe Admin Panel\n\n"
        "যে কাজটি করতে চাও সেটি নির্বাচন করো:",
        reply_markup=admin_panel_keyboard()
    )


# =========================================================
# CATEGORY LIST
# =========================================================

async def show_categories_message(target):
    conn = await db_connect()

    try:
        rows = await conn.fetch(
            """
            SELECT id, name
            FROM categories
            ORDER BY id ASC
            """
        )
    finally:
        await conn.close()

    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                row["name"],
                callback_data=f"usercat:{row['id']}"
            )
        ])

    if not buttons:
        buttons.append([
            InlineKeyboardButton(
                "📁 No Category",
                callback_data="noop"
            )
        ])

    keyboard = InlineKeyboardMarkup(buttons)

    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(
            "📂 একটি Category নির্বাচন করুন:",
            reply_markup=keyboard
        )
    else:
        await target.reply_text(
            "📂 একটি Category নির্বাচন করুন:",
            reply_markup=keyboard
        )


async def show_categories_callback(query):
    await show_categories_message(query)


# =========================================================
# CATEGORY MEDIA LIST
# =========================================================

async def show_category(query, category_id, page=0):

    conn = await db_connect()

    try:
        category = await conn.fetchval(
            """
            SELECT name
            FROM categories
            WHERE id=$1
            """,
            category_id
        )

        if not category:
            await query.answer("Category পাওয়া যায়নি।")
            return

        total = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM media
            WHERE category=$1
            """,
            category
        )

        rows = await conn.fetch(
            """
            SELECT
                id,
                title,
                media_type
            FROM media
            WHERE category=$1
            ORDER BY id DESC
            LIMIT $2 OFFSET $3
            """,
            category,
            PAGE_SIZE,
            page * PAGE_SIZE,
        )

    finally:
        await conn.close()

    if not rows:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🏠 Categories",
                    callback_data="categories"
                )
            ]
        ])

        await query.edit_message_text(
            f"{category}\n\n"
            "এই Category-তে এখন কোনো Content নেই।",
            reply_markup=keyboard
        )
        return

    buttons = []

    start_number = page * PAGE_SIZE + 1

    emoji_map = {
        "video": "🎬",
        "photo": "📸",
        "audio": "🎵",
    }

    for index, row in enumerate(
        rows,
        start=start_number
    ):

        emoji = emoji_map.get(
            row["media_type"],
            "📁"
        )

        title = row["title"] or "Untitled"

        if len(title) > 55:
            title = title[:52] + "..."

        buttons.append([
            InlineKeyboardButton(
                f"{index}. {emoji} {title}",
                callback_data=(
                    f"item:{row['id']}:"
                    f"{category_id}:{page}"
                )
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data=(
                    f"page:{category_id}:{page-1}"
                )
            )
        )

    if (page + 1) * PAGE_SIZE < total:
        navigation.append(
            InlineKeyboardButton(
                "➡️ See more",
                callback_data=(
                    f"page:{category_id}:{page+1}"
                )
            )
        )

    if navigation:
        buttons.append(navigation)

    buttons.append([
        InlineKeyboardButton(
            "🏠 Categories",
            callback_data="categories"
        )
    ])

    await query.edit_message_text(
        f"📂 {category}\n\n"
        "একটি Content নির্বাচন করুন:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# SELECTED MEDIA
# =========================================================

async def show_selected_media(
    query,
    media_id,
    category_id,
    page
):

    conn = await db_connect()

    try:
        row = await conn.fetchrow(
            """
            SELECT
                id,
                media_type,
                file_id,
                thumb_file_id,
                title,
                description,
                category
            FROM media
            WHERE id=$1
            """,
            media_id
        )
    finally:
        await conn.close()

    if not row:
        await query.answer(
            "Content পাওয়া যায়নি।"
        )
        return

    title = row["title"] or "Untitled"

    description = row["description"] or ""

    text = f"🎬 {title}"

    if description:
        text += f"\n\n{description}"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Content দেখুন",
                web_app=WebAppInfo(
                    url=(
                        f"{WEB_URL}/app"
                        f"?media_id={media_id}"
                    )
                )
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back to List",
                callback_data=(
                    f"page:{category_id}:{page}"
                )
            )
        ],
    ])

    # Thumbnail থাকলে thumbnail সহ পাঠানো হবে।
    if row["thumb_file_id"]:

        try:
            await query.message.reply_photo(
                photo=row["thumb_file_id"],
                caption=text,
                reply_markup=keyboard
            )

            await query.answer()
            return

        except Exception as e:
            logger.warning(
                "Thumbnail send failed: %s",
                e
            )

    # Thumbnail না থাকলে text
    await query.message.reply_text(
        text,
        reply_markup=keyboard
    )

    await query.answer()


# =========================================================
# ADMIN ADD MEDIA
# =========================================================

async def start_add_media(
    update,
    context,
    media_type
):

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ শুধু Admin এই কাজ করতে পারবে।"
        )
        return

    context.user_data.clear()

    context.user_data["admin_action"] = "add_media"
    context.user_data["media_type"] = media_type

    await update.message.reply_text(
        "📸 প্রথমে Content পাঠাও।\n\n"
        + (
            "🎬 Video পাঠাও।"
            if media_type == "video"
            else
            "📸 Photo পাঠাও।"
            if media_type == "photo"
            else
            "🎵 Audio পাঠাও।"
        )
    )


# =========================================================
# MEDIA MESSAGE
# =========================================================

async def receive_media(update, context):

    if not is_admin(update.effective_user.id):
        return

    if context.user_data.get("admin_action") != "add_media":
        return

    media_type = context.user_data.get(
        "media_type"
    )

    file_id = None

    if media_type == "video" and update.message.video:
        file_id = update.message.video.file_id

    elif media_type == "photo" and update.message.photo:
        file_id = update.message.photo[-1].file_id

    elif media_type == "audio" and update.message.audio:
        file_id = update.message.audio.file_id

    else:
        await update.message.reply_text(
            "❌ সঠিক Media পাঠাও।"
        )
        return

    context.user_data["file_id"] = file_id

    context.user_data["step"] = "thumbnail"

    await update.message.reply_text(
        "🖼 এখন Thumbnail পাঠাও।\n\n"
        "Thumbnail না দিতে চাইলে `skip` লিখো।"
    )


# =========================================================
# THUMBNAIL
# =========================================================

async def receive_thumbnail(update, context):

    if not is_admin(update.effective_user.id):
        return

    if context.user_data.get("step") != "thumbnail":
        return

    thumb_id = None

    if update.message.photo:
        thumb_id = update.message.photo[-1].file_id

    elif update.message.text:
        if update.message.text.strip().lower() == "skip":
            thumb_id = None
        else:
            await update.message.reply_text(
                "🖼 Photo পাঠাও অথবা `skip` লিখো।"
            )
            return
    else:
        await update.message.reply_text(
            "🖼 Photo পাঠাও অথবা `skip` লিখো।"
        )
        return

    context.user_data["thumb_file_id"] = thumb_id

    context.user_data["step"] = "title"

    await update.message.reply_text(
        "✏️ এখন Content-এর নাম/Title লিখো।"
    )


# =========================================================
# TITLE
# =========================================================

async def receive_title(update, context):

    if not is_admin(update.effective_user.id):
        return

    if context.user_data.get("step") != "title":
        return

    if not update.message.text:
        return

    title = update.message.text.strip()

    if not title:
        await update.message.reply_text(
            "❌ Title খালি রাখা যাবে না।"
        )
        return

    context.user_data["title"] = title

    context.user_data["step"] = "description"

    await update.message.reply_text(
        "📝 Description লিখো।\n\n"
        "না চাইলে `skip` লিখো।"
    )


# =========================================================
# DESCRIPTION
# =========================================================

async def receive_description(update, context):

    if not is_admin(update.effective_user.id):
        return

    if context.user_data.get("step") != "description":
        return

    if not update.message.text:
        return

    text = update.message.text.strip()

    if text.lower() == "skip":
        description = None
    else:
        description = text

    context.user_data["description"] = description

    context.user_data["step"] = "category"

    conn = await db_connect()

    try:
        rows = await conn.fetch(
            """
            SELECT id, name
            FROM categories
            ORDER BY id ASC
            """
        )
    finally:
        await conn.close()

    buttons = []

    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                row["name"],
                callback_data=(
                    f"savecat:{row['id']}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data="cancel_add"
        )
    ])

    await update.message.reply_text(
        "📂 কোন Category-তে রাখবে?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# SAVE MEDIA
# =========================================================

async def save_media_to_db(
    context,
    category_name
):

    conn = await db_connect()

    try:
        await conn.execute(
            """
            INSERT INTO media(
                media_type,
                file_id,
                thumb_file_id,
                title,
                description,
                category
            )
            VALUES($1,$2,$3,$4,$5,$6)
            """,
            context.user_data["media_type"],
            context.user_data["file_id"],
            context.user_data.get("thumb_file_id"),
            context.user_data.get(
                "title",
                "Untitled"
            ),
            context.user_data.get(
                "description"
            ),
            category_name,
        )

    finally:
        await conn.close()


# =========================================================
# ADD CATEGORY
# =========================================================

async def ask_category_name(
    update,
    context
):

    if not is_admin(update.effective_user.id):
        await update.callback_query.answer(
            "❌ Admin only.",
            show_alert=True
        )
        return

    context.user_data.clear()

    context.user_data["admin_action"] = "add_category"
    context.user_data["step"] = "category_name"

    await update.callback_query.answer()

    await update.callback_query.message.reply_text(
        "📁 নতুন Category-এর নাম লিখো।"
    )


async def receive_category_name(
    update,
    context
):

    if not is_admin(update.effective_user.id):
        return

    if context.user_data.get("step") != "category_name":
        return

    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "❌ Category নাম খালি হতে পারে না।"
        )
        return

    conn = await db_connect()

    try:
        await conn.execute(
            """
            INSERT INTO categories(name)
            VALUES($1)
            ON CONFLICT(name) DO NOTHING
            """,
            name
        )
    finally:
        await conn.close()

    context.user_data.clear()

    await update.message.reply_text(
        f"✅ Category তৈরি হয়েছে:\n\n{name}",
        reply_markup=admin_open_video_keyboard()
    )


# =========================================================
# DELETE MEDIA
# =========================================================

async def show_delete_media(update):

    query = update.callback_query

    conn = await db_connect()

    try:
        rows = await conn.fetch(
            """
            SELECT id, title, media_type
            FROM media
            ORDER BY id DESC
            LIMIT 50
            """
        )
    finally:
        await conn.close()

    buttons = []

    emoji_map = {
        "video": "🎬",
        "photo": "📸",
        "audio": "🎵",
    }

    for row in rows:

        emoji = emoji_map.get(
            row["media_type"],
            "📁"
        )

        title = row["title"] or "Untitled"

        if len(title) > 35:
            title = title[:32] + "..."

        buttons.append([
            InlineKeyboardButton(
                f"🗑 {emoji} {title}",
                callback_data=f"del:{row['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Admin Panel",
            callback_data="admin_panel"
        )
    ])

    await query.edit_message_text(
        "🗑 Delete করার Content নির্বাচন করো:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def delete_media(query, media_id):

    if not is_admin(query.from_user.id):
        await query.answer(
            "❌ Admin only.",
            show_alert=True
        )
        return

    conn = await db_connect()

    try:
        row = await conn.fetchrow(
            """
            SELECT title
            FROM media
            WHERE id=$1
            """,
            media_id
        )

        if not row:
            await query.answer(
                "Content পাওয়া যায়নি।",
                show_alert=True
            )
            return

        await conn.execute(
            """
            DELETE FROM media
            WHERE id=$1
            """,
            media_id
        )

    finally:
        await conn.close()

    await query.answer(
        "✅ Content deleted."
    )

    await query.edit_message_text(
        f"🗑 Deleted:\n{row['title']}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Admin Panel",
                    callback_data="admin_panel"
                )
            ]
        ])
    )


# =========================================================
# VIEW MEDIA
# =========================================================

async def show_media_list(query):

    conn = await db_connect()

    try:
        rows = await conn.fetch(
            """
            SELECT
                id,
                title,
                media_type,
                category
            FROM media
            ORDER BY id DESC
            LIMIT 50
            """
        )
    finally:
        await conn.close()

    if not rows:
        await query.edit_message_text(
            "📋 কোনো Media নেই।",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Admin Panel",
                        callback_data="admin_panel"
                    )
                ]
            ])
        )
        return

    lines = []

    for row in rows:
        emoji = {
            "video": "🎬",
            "photo": "📸",
            "audio": "🎵",
        }.get(
            row["media_type"],
            "📁"
        )

        lines.append(
            f"{row['id']}. {emoji} "
            f"{row['title']} "
            f"— {row['category'] or 'No Category'}"
        )

    text = "📋 Media List\n\n" + "\n".join(lines)

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Admin Panel",
                    callback_data="admin_panel"
                )
            ]
        ])
    )


# =========================================================
# USERS
# =========================================================

async def show_users(query):

    conn = await db_connect()

    try:
        count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM contacts
            """
        )

        rows = await conn.fetch(
            """
            SELECT
                user_id,
                username,
                first_name
            FROM contacts
            ORDER BY id DESC
            LIMIT 20
            """
        )

    finally:
        await conn.close()

    text = f"👥 Total Users: {count}\n\n"

    for row in rows:

        name = row["first_name"] or "Unknown"

        username = (
            f"@{row['username']}"
            if row["username"]
            else "No username"
        )

        text += (
            f"• {name} — "
            f"{username}\n"
            f"ID: {row['user_id']}\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Admin Panel",
                    callback_data="admin_panel"
                )
            ]
        ])
    )


# =========================================================
# SOCIAL LINKS
# =========================================================

async def show_social_links(query):

    conn = await db_connect()

    try:
        rows = await conn.fetch(
            """
            SELECT platform, value
            FROM social_links
            ORDER BY id DESC
            """
        )
    finally:
        await conn.close()

    text = "🔗 Social Links\n\n"

    if not rows:
        text += "কোনো Social Link নেই।"
    else:
        for row in rows:
            text += (
                f"• {row['platform']}: "
                f"{row['value']}\n"
            )

    text += (
        "\n\n"
        "Social link যোগ করতে চাইলে নিচে লিখতে পারো:\n"
        "facebook: তোমার লিংক\n"
        "tiktok: তোমার লিংক"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Admin Panel",
                    callback_data="admin_panel"
                )
            ]
        ])
    )


async def save_social_link(
    update,
    platform,
    value
):

    conn = await db_connect()

    try:
        await conn.execute(
            """
            INSERT INTO social_links(
                platform,
                value
            )
            VALUES($1,$2)
            """,
            platform,
            value
        )
    finally:
        await conn.close()


# =========================================================
# CALLBACKS
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    # -----------------------------------------------
    # Security:
    # Admin callbacks
    # -----------------------------------------------

    admin_callbacks = (
        "admin_panel",
        "admin_start",
        "add_video",
        "add_photo",
        "add_audio",
        "add_category",
        "delete_media",
        "view_media",
        "view_users",
        "social_links",
        "cancel_add",
    )

    if (
        data.startswith(admin_callbacks)
        and not is_admin(query.from_user.id)
    ):
        await query.answer(
            "❌ Admin only.",
            show_alert=True
        )
        return

    # -----------------------------------------------
    # Admin Start
    # -----------------------------------------------

    if data == "admin_start":

        fake_context = context

        # Callback থেকে start-এর মতো response
        await query.message.reply_text(
            "🔥 Bangla Vibe\n\n"
            "👑 Admin Start",
            reply_markup=admin_open_video_keyboard()
        )
        return

    # -----------------------------------------------
    # Admin Panel
    # -----------------------------------------------

    if data == "admin_panel":

        await query.edit_message_text(
            "👑 Bangla Vibe Admin Panel\n\n"
            "যে কাজটি করতে চাও নির্বাচন করো:",
            reply_markup=admin_panel_keyboard()
        )
        return

    # -----------------------------------------------
    # Add Video
    # -----------------------------------------------

    if data == "add_video":

        context.user_data.clear()
        context.user_data["admin_action"] = "add_media"
        context.user_data["media_type"] = "video"

        await query.message.reply_text(
            "🎬 Video পাঠাও।"
        )
        return

    # -----------------------------------------------
    # Add Photo
    # -----------------------------------------------

    if data == "add_photo":

        context.user_data.clear()
        context.user_data["admin_action"] = "add_media"
        context.user_data["media_type"] = "photo"

        await query.message.reply_text(
            "📸 Photo পাঠাও।"
        )
        return

    # -----------------------------------------------
    # Add Audio
    # -----------------------------------------------

    if data == "add_audio":

        context.user_data.clear()
        context.user_data["admin_action"] = "add_media"
        context.user_data["media_type"] = "audio"

        await query.message.reply_text(
            "🎵 Audio পাঠাও।"
        )
        return

    # -----------------------------------------------
    # Add Category
    # -----------------------------------------------

    if data == "add_category":

        context.user_data.clear()
        context.user_data["admin_action"] = "add_category"
        context.user_data["step"] = "category_name"

        await query.message.reply_text(
            "📁 নতুন Category-এর নাম লিখো।"
        )
        return

    # -----------------------------------------------
    # Delete Media
    # -----------------------------------------------

    if data == "delete_media":

        await show_delete_media(update)
        return

    # -----------------------------------------------
    # Delete specific media
    # -----------------------------------------------

    if data.startswith("del:"):

        media_id = int(
            data.split(":")[1]
        )

        await delete_media(
            query,
            media_id
        )
        return

    # -----------------------------------------------
    # View Media
    # -----------------------------------------------

    if data == "view_media":

        await show_media_list(query)
        return

    # -----------------------------------------------
    # Users
    # -----------------------------------------------

    if data == "view_users":

        await show_users(query)
        return

    # -----------------------------------------------
    # Social Links
    # -----------------------------------------------

    if data == "social_links":

        await show_social_links(query)
        return

    # -----------------------------------------------
    # Cancel
    # -----------------------------------------------

    if data == "cancel_add":

        context.user_data.clear()

        await query.edit_message_text(
            "❌ কাজ বাতিল করা হয়েছে।",
            reply_markup=admin_panel_keyboard()
        )
        return

    # -----------------------------------------------
    # Save Category for Media
    # -----------------------------------------------

    if data.startswith("savecat:"):

        if not is_admin(query.from_user.id):
            await query.answer(
                "❌ Admin only.",
                show_alert=True
            )
            return

        category_id = int(
            data.split(":")[1]
        )

        conn = await db_connect()

        try:
            category_name = await conn.fetchval(
                """
                SELECT name
                FROM categories
                WHERE id=$1
                """,
                category_id
            )
        finally:
            await conn.close()

        if not category_name:
            await query.message.reply_text(
                "❌ Category পাওয়া যায়নি।"
            )
            return

        try:
            await save_media_to_db(
                context,
                category_name
            )

            media_type = context.user_data.get(
                "media_type"
            )

            title = context.user_data.get(
                "title",
                "Untitled"
            )

            context.user_data.clear()

            await query.message.reply_text(
                "✅ Content সফলভাবে Save হয়েছে!\n\n"
                f"📌 Title: {title}\n"
                f"📂 Category: {category_name}\n"
                f"📁 Type: {media_type}",
                reply_markup=admin_open_video_keyboard()
            )

        except Exception as e:

            logger.exception(
                "Save media error"
            )

            await query.message.reply_text(
                "❌ Save করতে সমস্যা হয়েছে।"
            )

        return

    # -----------------------------------------------
    # Categories
    # -----------------------------------------------

    if data == "categories":

        await show_categories_callback(query)
        return

    # -----------------------------------------------
    # User Category
    # -----------------------------------------------

    if data.startswith("usercat:"):

        category_id = int(
            data.split(":")[1]
        )

        await show_category(
            query,
            category_id,
            0
        )
        return

    # -----------------------------------------------
    # Pagination
    # -----------------------------------------------

    if data.startswith("page:"):

        parts = data.split(":")

        category_id = int(parts[1])
        page = int(parts[2])

        await show_category(
            query,
            category_id,
            page
        )
        return

    # -----------------------------------------------
    # Selected Item
    # -----------------------------------------------

    if data.startswith("item:"):

        parts = data.split(":")

        media_id = int(parts[1])
        category_id = int(parts[2])
        page = int(parts[3])

        await show_selected_media(
            query,
            media_id,
            category_id,
            page
        )
        return

    # -----------------------------------------------
    # No-op
    # -----------------------------------------------

    if data == "noop":
        return


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    text = update.message.text.strip()

    # -------------------------------------------------
    # OPEN VIDEO
    # -------------------------------------------------

    if text == "▶️ Open Video":

        await open_video(
            update,
            context
        )
        return

    # -------------------------------------------------
    # ADMIN ONLY BELOW
    # -------------------------------------------------

    if not is_admin(update.effective_user.id):

        # Normal user-এর অন্য text হলে
        return

    # -------------------------------------------------
    # Category creation
    # -------------------------------------------------

    if context.user_data.get(
        "step"
    ) == "category_name":

        await receive_category_name(
            update,
            context
        )
        return

    # -------------------------------------------------
    # Social links
    # -------------------------------------------------

    lower = text.lower()

    if lower.startswith("facebook:"):

        value = text.split(
            ":",
            1
        )[1].strip()

        if value:
            await save_social_link(
                update,
                "facebook",
                value
            )

            await update.message.reply_text(
                "✅ Facebook link saved."
            )

        return

    if lower.startswith("tiktok:"):

        value = text.split(
            ":",
            1
        )[1].strip()

        if value:
            await save_social_link(
                update,
                "tiktok",
                value
            )

            await update.message.reply_text(
                "✅ TikTok link saved."
            )

        return

    # -------------------------------------------------
    # Media adding state
    # -------------------------------------------------

    if context.user_data.get(
        "admin_action"
    ) == "add_media":

        step = context.user_data.get(
            "step"
        )

        if step == "thumbnail":
            await receive_thumbnail(
                update,
                context
            )
            return

        if step == "title":
            await receive_title(
                update,
                context
            )
            return

        if step == "description":
            await receive_description(
                update,
                context
            )
            return


# =========================================================
# MEDIA HANDLER ROUTER
# =========================================================

async def media_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.effective_user:
        return

    if not is_admin(
        update.effective_user.id
    ):
        return

    action = context.user_data.get(
        "admin_action"
    )

    if action != "add_media":
        return

    step = context.user_data.get(
        "step"
    )

    # Main media
    if step is None:

        media_type = context.user_data.get(
            "media_type"
        )

        valid = False

        if (
            media_type == "video"
            and update.message.video
        ):
            valid = True

        elif (
            media_type == "photo"
            and update.message.photo
        ):
            valid = True

        elif (
            media_type == "audio"
            and update.message.audio
        ):
            valid = True

        if valid:
            await receive_media(
                update,
                context
            )
        else:
            await update.message.reply_text(
                "❌ সঠিক Media পাঠাও।"
            )

        return

    # Thumbnail
    if step == "thumbnail":

        if update.message.photo:
            await receive_thumbnail(
                update,
                context
            )

        return


# =========================================================
# WEB APP MEDIA API
# =========================================================

async def get_media_row(media_id):

    conn = await db_connect()

    try:
        return await conn.fetchrow(
            """
            SELECT
                id,
                media_type,
                file_id,
                thumb_file_id,
                title,
                description,
                category
            FROM media
            WHERE id=$1
            """,
            media_id
        )
    finally:
        await conn.close()


async def telegram_download(file_id):

    from telegram import Bot

    bot = Bot(
        token=BOT_TOKEN
    )

    telegram_file = await bot.get_file(
        file_id
    )

    data = await telegram_file.download_as_bytearray()

    return bytes(data)


# =========================================================
# API: MEDIA INFO
# =========================================================

@app.route("/api/media/<int:media_id>")
def api_media(media_id):

    try:
        row = asyncio.run(
            get_media_row(media_id)
        )

        if not row:
            return jsonify({
                "ok": False,
                "error": "Media not found"
            }), 404

        return jsonify({
            "ok": True,
            "id": row["id"],
            "media_type": row["media_type"],
            "title": row["title"],
            "description": row["description"],
            "category": row["category"],
            "thumbnail": (
                f"{WEB_URL}/thumbnail/{row['id']}"
                if row["thumb_file_id"]
                else None
            ),
            "media_url": (
                f"{WEB_URL}/media/{row['media_type']}/{row['id']}"
            ),
        })

    except Exception as e:

        logger.exception(
            "API media error"
        )

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# =========================================================
# THUMBNAIL PROXY
# =========================================================

@app.route("/thumbnail/<int:media_id>")
def thumbnail(media_id):

    try:

        row = asyncio.run(
            get_media_row(media_id)
        )

        if not row:
            return Response(
                "Not Found",
                status=404
            )

        thumb_id = row["thumb_file_id"]

        if not thumb_id:
            return Response(
                "No thumbnail",
                status=404
            )

        data = asyncio.run(
            telegram_download(
                thumb_id
            )
        )

        return Response(
            data,
            mimetype="image/jpeg",
            headers={
                "Cache-Control":
                    "public, max-age=3600"
            }
        )

    except Exception as e:

        logger.exception(
            "Thumbnail error"
        )

        return Response(
            "Thumbnail error",
            status=500
        )


# =========================================================
# MEDIA PROXY
# =========================================================

@app.route(
    "/media/<media_type>/<int:media_id>"
)
def media_proxy(
    media_type,
    media_id
):

    try:

        row = asyncio.run(
            get_media_row(media_id)
        )

        if not row:
            return Response(
                "Not Found",
                status=404
            )

        if row["media_type"] != media_type:
            return Response(
                "Media type mismatch",
                status=400
            )

        data = asyncio.run(
            telegram_download(
                row["file_id"]
            )
        )

        mimetype_map = {
            "video": "video/mp4",
            "photo": "image/jpeg",
            "audio": "audio/mpeg",
        }

        mimetype = mimetype_map.get(
            media_type,
            "application/octet-stream"
        )

        return Response(
            data,
            mimetype=mimetype,
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control":
                    "public, max-age=3600"
            }
        )

    except Exception as e:

        logger.exception(
            "Media proxy error"
        )

        return Response(
            "Media error",
            status=500
        )


# =========================================================
# FLASK THREAD
# =========================================================

def run_flask():

    logger.info(
        "🌐 Web server starting on port %s",
        PORT
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application: Application
):

    await init_db()

    # IMPORTANT:
    # Remove old Telegram command lists.
    await clear_command_menus(
        application
    )

    logger.info(
        "🤖 Bangla Vibe Bot initialized."
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable is missing."
        )

    # Flask server
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    # Telegram application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # -----------------------------------------------------
    # Commands
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin
        )
    )

    # -----------------------------------------------------
    # Callback
    # -----------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # -----------------------------------------------------
    # Main media
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.VIDEO
            | filters.PHOTO
            | filters.AUDIO,
            media_router
        )
    )

    # -----------------------------------------------------
    # Text
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text_handler
        )
    )

    logger.info(
        "🚀 Bangla Vibe Bot started."
    )

    # IMPORTANT:
    # Do NOT use asyncio.run() around run_polling().
    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

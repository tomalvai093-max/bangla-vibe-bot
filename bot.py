import os
import logging
import asyncio
import threading
from pathlib import Path

import asyncpg
from flask import Flask, jsonify, send_file, redirect, abort
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
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_ID = 8721334265

WEB_URL = os.getenv(
    "WEB_URL",
    "https://bangla-vibe-bot.onrender.com"
).rstrip("/")

PORT = int(os.getenv("PORT", "10000"))
BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# User-specific upload steps
admin_states = {}
pending_media = {}

DEFAULT_CATEGORIES = [
    "🎵 গান",
    "🎭 নাটক",
    "🎬 ভিডিও",
    "📸 ফটো",
    "🎥 মুভি",
]


# =========================
# DATABASE
# =========================

async def get_pool():
    if not DATABASE_URL:
        raise RuntimeError("Render-এ DATABASE_URL সেট করা নেই")

    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=30,
        timeout=30,
        statement_cache_size=0,
    )


async def init_db():
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    id SERIAL PRIMARY KEY,
                    file_id TEXT,
                    media_type TEXT,
                    title TEXT,
                    description TEXT,
                    category TEXT,
                    thumbnail_file_id TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Add missing media columns without deleting existing data.
            media_columns = {
                "file_id": "TEXT",
                "media_type": "TEXT",
                "title": "TEXT",
                "description": "TEXT",
                "category": "TEXT",
                "thumbnail_file_id": "TEXT",
                "created_at": "TIMESTAMPTZ DEFAULT NOW()",
            }

            for column, definition in media_columns.items():
                await conn.execute(
                    f"ALTER TABLE media ADD COLUMN IF NOT EXISTS "
                    f"{column} {definition}"
                )

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    phone TEXT DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    user_id BIGINT UNIQUE,
                    username TEXT,
                    first_name TEXT
                )
            """)

            # Migrate older contacts tables safely.
            contact_columns = {
                "name": "TEXT DEFAULT ''",
                "phone": "TEXT DEFAULT ''",
                "created_at": "TIMESTAMPTZ DEFAULT NOW()",
                "user_id": "BIGINT",
                "username": "TEXT",
                "first_name": "TEXT",
            }

            for column, definition in contact_columns.items():
                await conn.execute(
                    f"ALTER TABLE contacts ADD COLUMN IF NOT EXISTS "
                    f"{column} {definition}"
                )

            # Older rows may have NULL names.
            await conn.execute("""
                UPDATE contacts
                SET name = COALESCE(
                    NULLIF(name, ''),
                    NULLIF(first_name, ''),
                    NULLIF(username, ''),
                    'Telegram User'
                )
                WHERE name IS NULL OR name = ''
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS requests (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    request TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS social_links (
                    id SERIAL PRIMARY KEY,
                    title TEXT,
                    url TEXT
                )
            """)

            for category in DEFAULT_CATEGORIES:
                await conn.execute(
                    """
                    INSERT INTO categories(name)
                    VALUES($1)
                    ON CONFLICT(name) DO NOTHING
                    """,
                    category,
                )

        logger.info("Database initialized successfully")

    finally:
        await pool.close()


async def save_contact(user):
    if not user:
        return

    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            name = user.first_name or user.username or str(user.id)

            # Update an existing contact first.
            existing = await conn.fetchrow(
                "SELECT user_id FROM contacts WHERE user_id = $1",
                user.id,
            )

            if existing:
                await conn.execute(
                    """
                    UPDATE contacts
                    SET name = $1,
                        username = $2,
                        first_name = $3
                    WHERE user_id = $4
                    """,
                    name,
                    user.username,
                    user.first_name,
                    user.id,
                )
            else:
                # Include the old schema's required name/phone fields.
                await conn.execute(
                    """
                    INSERT INTO contacts
                        (name, phone, user_id, username, first_name)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    name,
                    "",
                    user.id,
                    user.username,
                    user.first_name,
                )

    finally:
        await pool.close()


async def get_categories():
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT name FROM categories ORDER BY id"
            )
            return [row["name"] for row in rows]
    finally:
        await pool.close()


async def get_media_page(category=None, page=0):
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            if category:
                rows = await conn.fetch(
                    """
                    SELECT id, title, description, category, media_type,
                           thumbnail_file_id, created_at
                    FROM media
                    WHERE category = $1
                    ORDER BY id DESC
                    LIMIT 5 OFFSET $2
                    """,
                    category,
                    page * 5,
                )
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM media WHERE category = $1",
                    category,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, title, description, category, media_type,
                           thumbnail_file_id, created_at
                    FROM media
                    ORDER BY id DESC
                    LIMIT 5 OFFSET $1
                    """,
                    page * 5,
                )
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM media"
                )

            return rows, count

    finally:
        await pool.close()


async def get_media_by_id(media_id):
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT * FROM media WHERE id = $1",
                media_id,
            )
    finally:
        await pool.close()


async def save_media(
    file_id,
    media_type,
    title,
    description,
    category,
    thumbnail_file_id=None,
):
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """
                INSERT INTO media
                    (file_id, media_type, title, description,
                     category, thumbnail_file_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                file_id,
                media_type,
                title,
                description,
                category,
                thumbnail_file_id,
            )
    finally:
        await pool.close()


async def delete_media(media_id):
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM media WHERE id = $1",
                media_id,
            )
    finally:
        await pool.close()


async def add_category(name):
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO categories(name)
                VALUES($1)
                ON CONFLICT(name) DO NOTHING
                """,
                name,
            )
    finally:
        await pool.close()


async def get_contact_count():
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM contacts"
            )
    finally:
        await pool.close()


# =========================
# TELEGRAM KEYBOARDS
# =========================

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("▶️ Open Video")],
            [KeyboardButton("📚 সব কনটেন্ট"), KeyboardButton("📂 ক্যাটাগরি")],
        ],
        resize_keyboard=True,
    )


def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ ভিডিও আপলোড"), KeyboardButton("📸 ফটো আপলোড")],
            [KeyboardButton("🎵 অডিও আপলোড"), KeyboardButton("📂 ক্যাটাগরি")],
            [KeyboardButton("➕ নতুন ক্যাটাগরি"), KeyboardButton("🗂 সব মিডিয়া")],
            [KeyboardButton("🗑 মিডিয়া ডিলিট"), KeyboardButton("👥 ইউজার সংখ্যা")],
            [KeyboardButton("🏠 হোম")],
        ],
        resize_keyboard=True,
    )


def is_admin(user_id):
    return user_id == ADMIN_ID


# =========================
# BOT COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    if user:
        try:
            await save_contact(user)
        except Exception:
            # A contact-save issue should not prevent the bot from responding.
            logger.exception("Could not save contact")

    await message.reply_text(
        "🔥 Bangla Vibe\n\n"
        "স্বাগতম! ভিডিও, গান, নাটক ও ফটো দেখতে নিচের অপশন ব্যবহার করুন।",
        reply_markup=main_keyboard(),
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ এই অপশন শুধু অ্যাডমিনের জন্য।")
        return

    await update.effective_message.reply_text(
        "👑 Admin Panel\n\nনিচের মেনু থেকে কাজ বেছে নাও।",
        reply_markup=admin_keyboard(),
    )


async def show_open_video_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Open Video", url=WEB_URL)]
    ])

    await update.effective_message.reply_text(
        "ভিডিও দেখতে নিচের বাটনে চাপ দাও:",
        reply_markup=keyboard,
    )


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = await get_categories()

    buttons = [
        [InlineKeyboardButton(name, callback_data=f"cat:{i}")]
        for i, name in enumerate(categories)
    ]

    buttons.append([
        InlineKeyboardButton("📚 সব কনটেন্ট", callback_data="all:0")
    ])

    await update.effective_message.reply_text(
        "📂 একটি ক্যাটাগরি বেছে নাও:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_media_list(
    message,
    category=None,
    page=0,
    edit=False,
):
    rows, count = await get_media_page(category, page)

    if not rows:
        text = "এখানে এখনো কোনো কনটেন্ট নেই।"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 ক্যাটাগরি", callback_data="categories")]
        ])
    else:
        lines = [
            f"📚 {category or 'সব কনটেন্ট'}",
            f"পেজ: {page + 1} | মোট: {count}",
            "",
        ]
        buttons = []

        for row in rows:
            title = row["title"] or f"Media {row['id']}"
            lines.append(f"• {title}")
            buttons.append([
                InlineKeyboardButton(
                    f"▶️ {title[:45]}",
                    callback_data=f"item:{row['id']}",
                )
            ])

        navigation = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    "⬅️ আগের",
                    callback_data=f"page:{page - 1}:{category or '*'}",
                )
            )
        if (page + 1) * 5 < count:
            navigation.append(
                InlineKeyboardButton(
                    "পরের ➡️",
                    callback_data=f"page:{page + 1}:{category or '*'}",
                )
            )
        if navigation:
            buttons.append(navigation)

        buttons.append([
            InlineKeyboardButton("📂 ক্যাটাগরি", callback_data="categories"),
            InlineKeyboardButton("🏠 সব", callback_data="all:0"),
        ])

        text = "\n".join(lines)
        markup = InlineKeyboardMarkup(buttons)

    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)


async def show_item(query, media_id):
    row = await get_media_by_id(media_id)

    if not row:
        await query.message.reply_text("এই কনটেন্টটি পাওয়া যায়নি।")
        return

    title = row["title"] or "নামহীন কনটেন্ট"
    description = row["description"] or "কোনো বিবরণ দেওয়া হয়নি।"
    media_type = row["media_type"] or "video"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 ওয়েবসাইটে দেখুন", url=f"{WEB_URL}/?media={media_id}")]
    ])

    await query.message.reply_text(
        f"📌 {title}\n\n"
        f"📂 ক্যাটাগরি: {row['category'] or 'অন্যান্য'}\n"
        f"📝 {description}\n"
        f"🎞 ধরন: {media_type}",
        reply_markup=keyboard,
    )


# =========================
# ADMIN UPLOAD FLOW
# =========================

async def begin_upload(update, media_type):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.effective_message.reply_text("⛔ শুধু অ্যাডমিন আপলোড করতে পারবেন।")
        return

    admin_states[user_id] = {
        "step": "media",
        "media_type": media_type,
    }

    pending_media.pop(user_id, None)

    await update.effective_message.reply_text(
        f"📤 এখন {media_type} পাঠাও।\n\n"
        "ভিডিও, ছবি বা অডিও ফাইল হিসেবে পাঠাতে পারো।\n"
        "বাতিল করতে /cancel লিখো।"
    )


async def handle_admin_button(update, context):
    message = update.effective_message
    user = update.effective_user
    text = (message.text or "").strip()

    if text == "▶️ Open Video":
        await show_open_video_menu(update, context)
        return

    if text == "📚 সব কনটেন্ট":
        await show_media_list(message, page=0)
        return

    if text == "📂 ক্যাটাগরি":
        await show_categories(update, context)
        return

    if text == "🏠 হোম":
        await message.reply_text("🏠 মূল মেনু", reply_markup=main_keyboard())
        return

    if not is_admin(user.id):
        return

    if text == "➕ ভিডিও আপলোড":
        await begin_upload(update, "video")
    elif text == "📸 ফটো আপলোড":
        await begin_upload(update, "photo")
    elif text == "🎵 অডিও আপলোড":
        await begin_upload(update, "audio")
    elif text == "➕ নতুন ক্যাটাগরি":
        admin_states[user.id] = {"step": "new_category"}
        await message.reply_text("নতুন ক্যাটাগরির নাম লিখো:")
    elif text == "🗂 সব মিডিয়া":
        await show_media_list(message, page=0)
    elif text == "🗑 মিডিয়া ডিলিট":
        admin_states[user.id] = {"step": "delete_media"}
        await message.reply_text("যে মিডিয়া ডিলিট করবে তার ID লিখো:")
    elif text == "👥 ইউজার সংখ্যা":
        try:
            count = await get_contact_count()
            await message.reply_text(f"👥 সংরক্ষিত ইউজার: {count}")
        except Exception:
            logger.exception("Could not count contacts")
            await message.reply_text("ডাটাবেস থেকে ইউজার সংখ্যা আনা যায়নি।")


async def handle_media_message(update, context):
    user = update.effective_user
    message = update.effective_message

    if not user or not is_admin(user.id):
        return

    state = admin_states.get(user.id)
    if not state:
        return

    if state.get("step") != "media":
        return

    file_id = None
    actual_type = state.get("media_type", "video")

    if message.video:
        file_id = message.video.file_id
        actual_type = "video"
    elif message.photo:
        file_id = message.photo[-1].file_id
        actual_type = "photo"
    elif message.audio:
        file_id = message.audio.file_id
        actual_type = "audio"
    elif message.document:
        file_id = message.document.file_id
        mime = message.document.mime_type or ""
        if mime.startswith("video/"):
            actual_type = "video"
        elif mime.startswith("image/"):
            actual_type = "photo"
        elif mime.startswith("audio/"):
            actual_type = "audio"
        else:
            await message.reply_text("ভিডিও, ছবি বা অডিও ফাইল পাঠাও।")
            return
    else:
        await message.reply_text("ভিডিও, ছবি বা অডিও পাঠাও।")
        return

    pending_media[user.id] = {
        "file_id": file_id,
        "media_type": actual_type,
        "thumbnail_file_id": None,
    }

    state["step"] = "thumbnail"

    await message.reply_text(
        "🖼️ থাম্বনেইল দিতে চাইলে একটি ছবি পাঠাও।\n"
        "না দিতে চাইলে /skip লিখো।"
    )


async def text_handler(update, context):
    user = update.effective_user
    message = update.effective_message
    text = (message.text or "").strip()

    if not user:
        return

    state = admin_states.get(user.id)

    if not state:
        await handle_admin_button(update, context)
        return

    if not is_admin(user.id):
        admin_states.pop(user.id, None)
        return

    step = state.get("step")

    if text == "/cancel":
        admin_states.pop(user.id, None)
        pending_media.pop(user.id, None)
        await message.reply_text("❎ কাজ বাতিল হয়েছে।", reply_markup=admin_keyboard())
        return

    if step == "new_category":
        if len(text) > 80:
            await message.reply_text("ক্যাটাগরির নাম ৮০ অক্ষরের মধ্যে রাখো।")
            return

        await add_category(text)
        admin_states.pop(user.id, None)
        await message.reply_text("✅ ক্যাটাগরি যোগ হয়েছে।", reply_markup=admin_keyboard())
        return

    if step == "delete_media":
        try:
            media_id = int(text)
            await delete_media(media_id)
            await message.reply_text(
                f"✅ মিডিয়া ID {media_id} ডিলিট হয়েছে।",
                reply_markup=admin_keyboard(),
            )
        except ValueError:
            await message.reply_text("শুধু মিডিয়ার সংখ্যাসূচক ID লিখো।")
            return
        except Exception:
            logger.exception("Could not delete media")
            await message.reply_text("মিডিয়া ডিলিট করা যায়নি।")
            return

        admin_states.pop(user.id, None)
        return

    if step == "title":
        item = pending_media.get(user.id)
        if not item:
            admin_states.pop(user.id, None)
            await message.reply_text("আপলোডের তথ্য পাওয়া যায়নি। আবার শুরু করো।")
            return

        item["title"] = text[:200]
        state["step"] = "description"
        await message.reply_text("📝 এবার ভিডিও/ফটোর বিবরণ লিখো। না থাকলে /skip লিখো।")
        return

    if step == "description":
        item = pending_media.get(user.id)
        if not item:
            admin_states.pop(user.id, None)
            await message.reply_text("আপলোডের তথ্য পাওয়া যায়নি। আবার শুরু করো।")
            return

        item["description"] = "" if text == "/skip" else text[:2000]
        categories = await get_categories()

        buttons = [
            [InlineKeyboardButton(name, callback_data=f"savecat:{i}")]
            for i, name in enumerate(categories)
        ]

        await message.reply_text(
            "📂 কোন ক্যাটাগরিতে রাখবে?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        state["step"] = "category"
        return

    await handle_admin_button(update, context)


async def skip_thumbnail(update, context):
    user = update.effective_user

    if not user or not is_admin(user.id):
        return

    state = admin_states.get(user.id)
    item = pending_media.get(user.id)

    if not state or state.get("step") != "thumbnail" or not item:
        await update.effective_message.reply_text("এখন থাম্বনেইল ধাপ চালু নেই।")
        return

    state["step"] = "title"
    await update.effective_message.reply_text("✍️ কনটেন্টের নাম লিখো:")


async def handle_thumbnail(update, context):
    user = update.effective_user
    message = update.effective_message

    if not user or not is_admin(user.id):
        return

    state = admin_states.get(user.id)
    item = pending_media.get(user.id)

    if not state or state.get("step") != "thumbnail" or not item:
        return

    if not message.photo:
        await message.reply_text("থাম্বনেইলের জন্য ছবি পাঠাও অথবা /skip লিখো।")
        return

    item["thumbnail_file_id"] = message.photo[-1].file_id
    state["step"] = "title"

    await message.reply_text("✍️ কনটেন্টের নাম লিখো:")


async def handle_callbacks(update, context):
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    if data == "categories":
        categories = await get_categories()
        buttons = [
            [InlineKeyboardButton(name, callback_data=f"cat:{i}")]
            for i, name in enumerate(categories)
        ]
        buttons.append([
            InlineKeyboardButton("📚 সব কনটেন্ট", callback_data="all:0")
        ])
        await query.message.reply_text(
            "📂 ক্যাটাগরি বেছে নাও:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("cat:"):
        categories = await get_categories()
        try:
            index = int(data.split(":")[1])
            category = categories[index]
        except (ValueError, IndexError):
            await query.message.reply_text("ক্যাটাগরি পাওয়া যায়নি।")
            return

        await show_media_list(query.message, category=category, page=0)
        return

    if data.startswith("all:"):
        try:
            page = int(data.split(":")[1])
        except ValueError:
            page = 0
        await show_media_list(query.message, page=page)
        return

    if data.startswith("page:"):
        parts = data.split(":", 2)

        try:
            page = int(parts[1])
            category = parts[2] if len(parts) > 2 else "*"
            if category == "*":
                category = None
        except ValueError:
            await query.message.reply_text("পেজটি পাওয়া যায়নি।")
            return

        await show_media_list(
            query.message,
            category=category,
            page=page,
        )
        return

    if data.startswith("item:"):
        try:
            media_id = int(data.split(":")[1])
        except ValueError:
            await query.message.reply_text("মিডিয়া ID সঠিক নয়।")
            return

        await show_item(query, media_id)
        return

    if data.startswith("savecat:"):
        user_id = query.from_user.id

        if not is_admin(user_id):
            await query.message.reply_text("⛔ শুধু অ্যাডমিন এই কাজ করতে পারবেন।")
            return

        state = admin_states.get(user_id)
        item = pending_media.get(user_id)

        if not state or state.get("step") != "category" or not item:
            await query.message.reply_text("আপলোডের ধাপ পাওয়া যায়নি। আবার শুরু করো।")
            return

        categories = await get_categories()

        try:
            index = int(data.split(":")[1])
            category = categories[index]
        except (ValueError, IndexError):
            await query.message.reply_text("ক্যাটাগরি পাওয়া যায়নি।")
            return

        try:
            media_id = await save_media(
                file_id=item["file_id"],
                media_type=item["media_type"],
                title=item.get("title", "নামহীন কনটেন্ট"),
                description=item.get("description", ""),
                category=category,
                thumbnail_file_id=item.get("thumbnail_file_id"),
            )

            admin_states.pop(user_id, None)
            pending_media.pop(user_id, None)

            await query.message.reply_text(
                f"✅ আপলোড সম্পন্ন!\n\n"
                f"🆔 ID: {media_id}\n"
                f"📌 নাম: {item.get('title', 'নামহীন কনটেন্ট')}\n"
                f"📂 ক্যাটাগরি: {category}",
                reply_markup=admin_keyboard(),
            )

        except Exception:
            logger.exception("Could not save media")
            await query.message.reply_text(
                "❌ মিডিয়া ডাটাবেসে সংরক্ষণ করা যায়নি। Render Logs দেখো।"
            )


# =========================
# FLASK WEBSITE / API
# =========================

@app.route("/")
def home():
    if INDEX_FILE.exists():
        return send_file(INDEX_FILE)
    return (
        "Bangla Vibe Bot web server is running, "
        "but index.html was not found.",
        200,
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "Bangla Vibe Bot"})


@app.route("/api/media")
def api_media_list():
    async def fetch_data():
        pool = await get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, title, description, category, media_type,
                           thumbnail_file_id, created_at
                    FROM media
                    ORDER BY id DESC
                """)

                result = []
                for row in rows:
                    result.append({
                        "id": row["id"],
                        "title": row["title"] or "নামহীন কনটেন্ট",
                        "description": row["description"] or "",
                        "category": row["category"] or "অন্যান্য",
                        "media_type": row["media_type"] or "video",
                        "thumbnail": (
                            f"/thumbnail/{row['id']}"
                            if row["thumbnail_file_id"] else None
                        ),
                        "url": f"/api/media/{row['id']}",
                    })

                return result
        finally:
            await pool.close()

    try:
        return jsonify(asyncio.run(fetch_data()))
    except Exception:
        logger.exception("Could not load media list")
        return jsonify({"error": "Media list unavailable"}), 500


@app.route("/api/media/<int:media_id>")
def api_media_item(media_id):
    async def fetch_data():
        pool = await get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM media WHERE id = $1",
                    media_id,
                )

                if not row:
                    return None

                return {
                    "id": row["id"],
                    "title": row["title"] or "নামহীন কনটেন্ট",
                    "description": row["description"] or "",
                    "category": row["category"] or "অন্যান্য",
                    "media_type": row["media_type"] or "video",
                    "thumbnail": (
                        f"/thumbnail/{row['id']}"
                        if row["thumbnail_file_id"] else None
                    ),
                    "url": f"/media/{row['media_type']}/{row['id']}",
                }
        finally:
            await pool.close()

    try:
        result = asyncio.run(fetch_data())
        if not result:
            return jsonify({"error": "Not found"}), 404
        return jsonify(result)
    except Exception:
        logger.exception("Could not load media item")
        return jsonify({"error": "Media unavailable"}), 500


@app.route("/thumbnail/<int:media_id>")
def thumbnail(media_id):
    async def get_file():
        pool = await get_pool()
        try:
            async with pool.acquire() as conn:
                return await conn.fetchval(
                    "SELECT thumbnail_file_id FROM media WHERE id = $1",
                    media_id,
                )
        finally:
            await pool.close()

    try:
        file_id = asyncio.run(get_file())
    except Exception:
        logger.exception("Could not load thumbnail")
        abort(500)

    if not file_id:
        abort(404)

    # The page can use this endpoint to identify thumbnail availability.
    return jsonify({"file_id": file_id})


@app.route("/media/<media_type>/<int:media_id>")
@app.route("/media/video/<int:media_id>")
@app.route("/media/audio/<int:media_id>")
@app.route("/media/photo/<int:media_id>")
def media_redirect(media_type=None, media_id=None):
    if media_id is None:
        abort(404)

    async def get_file():
        pool = await get_pool()
        try:
            async with pool.acquire() as conn:
                return await conn.fetchrow(
                    "SELECT file_id, media_type FROM media WHERE id = $1",
                    media_id,
                )
        finally:
            await pool.close()

    try:
        row = asyncio.run(get_file())
    except Exception:
        logger.exception("Could not fetch media file")
        abort(500)

    if not row or not row["file_id"]:
        abort(404)

    # Resolve Telegram file URL server-side. Do not put BOT_TOKEN in index.html.
    import httpx

    async def telegram_file_url():
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                params={"file_id": row["file_id"]},
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                return None

            path = data.get("result", {}).get("file_path")
            if not path:
                return None

            return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"

    try:
        url = asyncio.run(telegram_file_url())
    except Exception:
        logger.exception("Telegram file lookup failed")
        abort(502)

    if not url:
        abort(404)

    return redirect(url, code=302)


# =========================
# STARTUP
# =========================

async def post_init(application: Application):
    await init_db()


async def error_handler(update, context):
    logger.error(
        "Telegram update error",
        exc_info=context.error,
    )


def run_flask():
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Render Environment Variables-এ BOT_TOKEN সেট করো")

    if not DATABASE_URL:
        raise RuntimeError("Render Environment Variables-এ DATABASE_URL সেট করো")

    threading.Thread(
        target=run_flask,
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("skip", skip_thumbnail))
    application.add_handler(CommandHandler("cancel", text_handler))

    application.add_handler(CallbackQueryHandler(handle_callbacks))

    application.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.PRIVATE,
            handle_thumbnail,
        )
    )

    application.add_handler(
        MessageHandler(
            (filters.VIDEO | filters.AUDIO | filters.Document.ALL)
            & filters.ChatType.PRIVATE,
            handle_media_message,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Bangla Vibe Bot starting")
    logger.info("Web server port: %s", PORT)

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()

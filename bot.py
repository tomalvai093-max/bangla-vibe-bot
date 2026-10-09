import os
import asyncio
import threading
import logging
from pathlib import Path

import asyncpg
from flask import Flask, Response, jsonify, send_from_directory

from telegram import (
    Update,
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
    BotCommandScopeChat,
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

# =====================================================
# CONFIGURATION
# =====================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

ADMIN_ID = 8721334265
WEB_URL = "https://bangla-vibe-bot.onrender.com"
BASE_DIR = Path(__file__).resolve().parent

if not BOT_TOKEN:
    raise RuntimeError("Render Environment-এ BOT_TOKEN পাওয়া যায়নি")

if not DATABASE_URL:
    raise RuntimeError("Render Environment-এ DATABASE_URL পাওয়া যায়নি")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# =====================================================
# FLASK WEB SERVER
# =====================================================

app = Flask(__name__)


@app.route("/")
@app.route("/app")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/health")
def health():
    return "OK", 200


# =====================================================
# DATABASE
# =====================================================

async def get_pool():
    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=3,
        command_timeout=60,
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
                    media_type TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    thumbnail_file_id TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    category TEXT,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS requests (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    media_id INTEGER,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS social_links (
                    id SERIAL PRIMARY KEY,
                    platform TEXT,
                    value TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            migrations = [
                "ALTER TABLE media ADD COLUMN IF NOT EXISTS thumbnail_file_id TEXT",
                "ALTER TABLE media ADD COLUMN IF NOT EXISTS description TEXT",
                "ALTER TABLE media ADD COLUMN IF NOT EXISTS category TEXT",
                "ALTER TABLE media ADD COLUMN IF NOT EXISTS created_by BIGINT",
                "ALTER TABLE media ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
                "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS user_id BIGINT",
                "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS username TEXT",
                "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS first_name TEXT",
                "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
            ]

            for sql in migrations:
                try:
                    await conn.execute(sql)
                except Exception:
                    logger.exception("Database migration warning")

            defaults = [
                "🎵 গান",
                "🎭 নাটক",
                "🎬 ভিডিও",
                "📸 ফটো",
                "🎥 মুভি",
            ]

            # Avoid ON CONFLICT so old schemas without a UNIQUE
            # constraint can still initialize.
            for name in defaults:
                exists = await conn.fetchval(
                    "SELECT id FROM categories WHERE name=$1 LIMIT 1",
                    name,
                )
                if not exists:
                    await conn.execute(
                        "INSERT INTO categories(name) VALUES($1)",
                        name,
                    )

        logger.info("Database initialized successfully.")

    finally:
        await pool.close()


def is_admin(user_id):
    return user_id is not None and int(user_id) == ADMIN_ID


async def save_contact(user):
    """Save/update contacts without relying on ON CONFLICT."""
    if not user:
        return

    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            existing_ids = await conn.fetch(
                "SELECT id FROM contacts WHERE user_id=$1",
                user.id,
            )

            if existing_ids:
                # Update all existing matching records. No rows are deleted.
                await conn.execute(
                    """
                    UPDATE contacts
                    SET username=$1, first_name=$2
                    WHERE user_id=$3
                    """,
                    user.username,
                    user.first_name,
                    user.id,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO contacts(user_id, username, first_name)
                    VALUES($1, $2, $3)
                    """,
                    user.id,
                    user.username,
                    user.first_name,
                )
    finally:
        await pool.close()


# =====================================================
# KEYBOARDS - KEEPING THE EXISTING OPEN VIDEO MENU
# =====================================================

def main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("▶️ Open Video")]],
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("▶️ Open Video")],
            [
                KeyboardButton("🎬 ভিডিও যোগ"),
                KeyboardButton("📸 ফটো যোগ"),
            ],
            [
                KeyboardButton("🎵 অডিও যোগ"),
                KeyboardButton("📁 Category"),
            ],
            [
                KeyboardButton("➕ Category যোগ"),
                KeyboardButton("🗑 Delete"),
            ],
            [
                KeyboardButton("👥 Users"),
                KeyboardButton("📦 All Media"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


async def user_category_keyboard():
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name FROM categories ORDER BY id"
            )
    finally:
        await pool.close()

    buttons = [
        [
            InlineKeyboardButton(
                row["name"],
                callback_data=f"usercat:{row['id']}",
            )
        ]
        for row in rows
    ]

    return InlineKeyboardMarkup(buttons)


async def admin_category_keyboard():
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name FROM categories ORDER BY id"
            )
    finally:
        await pool.close()

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                row["name"],
                callback_data=f"savecat:{row['id']}",
            )
        ]
        for row in rows
    ])


# =====================================================
# GROUP DIAGNOSTICS
# =====================================================

async def log_group_updates(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message

    if chat and chat.type in ("group", "supergroup"):
        logger.info(
            "GROUP UPDATE RECEIVED: chat_id=%s user_id=%s text=%r",
            chat.id,
            user.id if user else None,
            message.text if message else None,
        )


# =====================================================
# START AND ADMIN COMMANDS
# =====================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    context.user_data.clear()

    try:
        await save_contact(user)
    except Exception:
        logger.exception("Could not save contact")
        await message.reply_text(
            "⚠️ ডাটাবেসে User তথ্য সংরক্ষণ করা যায়নি। "
            "কিছুক্ষণ পরে আবার চেষ্টা করুন।"
        )
        return

    await message.reply_text(
        "🔥 Bangla Vibe\n\n"
        "স্বাগতম! নিচের Open Video বাটন চাপুন।",
        reply_markup=(
            admin_keyboard() if is_admin(user.id)
            else main_keyboard()
        ),
    )

    keyboard = await user_category_keyboard()

    await message.reply_text(
        "📁 Category থেকে Content নির্বাচন করুন:",
        reply_markup=keyboard,
    )


async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    if not is_admin(user.id):
        await message.reply_text(
            "⛔ এই কমান্ড শুধু Admin ব্যবহার করতে পারবেন।"
        )
        return

    context.user_data.clear()

    await message.reply_text(
        "👑 Bangla Vibe Admin Panel\n\n"
        "নিচের বাটন থেকে কাজ নির্বাচন করুন।",
        reply_markup=admin_keyboard(),
    )


async def show_open_video_menu(update, context):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    if is_admin(user.id):
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "▶️ Start",
                    callback_data="menu_start",
                ),
                InlineKeyboardButton(
                    "👑 Admin Panel",
                    callback_data="menu_admin",
                ),
            ]
        ])

        await message.reply_text(
            "কোন মেনু খুলবেন?",
            reply_markup=keyboard,
        )
    else:
        keyboard = await user_category_keyboard()

        await message.reply_text(
            "📁 Category নির্বাচন করুন:",
            reply_markup=keyboard,
        )


# =====================================================
# ADMIN TEXT BUTTONS
# =====================================================

async def handle_admin_button(update, context):
    user = update.effective_user
    message = update.effective_message

    if not user or not message or not is_admin(user.id):
        return False

    text = (message.text or "").strip()

    video_commands = {
        "🎬 ভিডিও যোগ", "ভিডিও দাও", "ভিডিও দেও",
        "ভিডিও দে", "ভিডিও দেন", "ভিডিও দিন",
        "video dao", "video deo", "video de",
        "video den", "add video", "upload video",
    }

    photo_commands = {
        "📸 ফটো যোগ", "ফটো দাও", "ছবি দাও",
        "add photo", "upload photo",
    }

    audio_commands = {
        "🎵 অডিও যোগ", "অডিও দাও", "গান দাও",
        "add audio", "upload audio",
    }

    if text in video_commands | photo_commands | audio_commands:
        context.user_data.clear()
        context.user_data["step"] = "media"

        if text in video_commands:
            context.user_data["media_type"] = "video"
            prompt = "🎬 এখন Video পাঠাও।"
        elif text in photo_commands:
            context.user_data["media_type"] = "photo"
            prompt = "📸 এখন Photo পাঠাও।"
        else:
            context.user_data["media_type"] = "audio"
            prompt = "🎵 এখন Audio পাঠাও।"

        await message.reply_text(prompt)
        return True

    if text == "📁 Category":
        await message.reply_text(
            "📁 Category:",
            reply_markup=await user_category_keyboard(),
        )
        return True

    if text == "➕ Category যোগ":
        context.user_data.clear()
        context.user_data["step"] = "add_category"
        await message.reply_text("নতুন Category-এর নাম লিখুন।")
        return True

    if text == "👥 Users":
        pool = await get_pool()
        try:
            async with pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(DISTINCT user_id) "
                    "FROM contacts WHERE user_id IS NOT NULL"
                )
        finally:
            await pool.close()

        await message.reply_text(f"👥 Total Users: {count}")
        return True

    if text == "📦 All Media":
        pool = await get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, media_type, title, category
                    FROM media ORDER BY id DESC LIMIT 50
                    """
                )
        finally:
            await pool.close()

        if not rows:
            await message.reply_text("📦 কোনো Media নেই।")
            return True

        output = ["📦 All Media\n"]

        for row in rows:
            output.append(
                f"ID: {row['id']} | {row['media_type']}\n"
                f"{row['title']}\n"
                f"Category: {row['category'] or 'None'}\n"
            )

        await message.reply_text("\n".join(output)[:4000])
        return True

    if text == "🗑 Delete":
        context.user_data.clear()
        context.user_data["step"] = "delete"
        await message.reply_text("যে Media মুছতে চান তার ID লিখুন।")
        return True

    return False


# =====================================================
# MEDIA UPLOAD HANDLER
# =====================================================

async def handle_media_message(update, context):
    user = update.effective_user
    message = update.effective_message

    if not user or not message or not is_admin(user.id):
        return

    step = context.user_data.get("step")

    # Receive the main video, photo or audio.
    if step == "media":
        media_type = context.user_data.get("media_type")
        file_id = None

        if media_type == "video" and message.video:
            file_id = message.video.file_id
        elif media_type == "audio" and message.audio:
            file_id = message.audio.file_id
        elif media_type == "photo" and message.photo:
            file_id = message.photo[-1].file_id

        if not file_id:
            await message.reply_text(
                "⚠️ সঠিক Video, Photo বা Audio পাঠাও।"
            )
            return

        context.user_data["file_id"] = file_id
        context.user_data["step"] = "thumbnail"

        await message.reply_text(
            "✅ Content পেয়েছি।\n"
            "এখন Thumbnail হিসেবে একটি Photo পাঠাও।"
        )
        return

    # Receive thumbnail, save its Telegram file_id and ask for title.
    if step == "thumbnail":
        thumbnail_id = None

        if message.photo:
            thumbnail_id = message.photo[-1].file_id
        elif (
            message.document
            and message.document.mime_type
            and message.document.mime_type.startswith("image/")
        ):
            thumbnail_id = message.document.file_id

        if not thumbnail_id:
            await message.reply_text(
                "⚠️ Thumbnail হিসেবে একটি Photo বা Image পাঠাও।"
            )
            return

        context.user_data["thumbnail_file_id"] = thumbnail_id
        context.user_data["step"] = "title"

        await message.reply_text(
            "✅ Thumbnail পেয়েছি।\nএখন Content-এর Title লিখুন।"
        )
        return


# =====================================================
# TEXT FLOW
# =====================================================

async def text_handler(update, context):
    user = update.effective_user
    message = update.effective_message

    if not user or not message or not message.text:
        return

    text = message.text.strip()

    try:
        await save_contact(user)
    except Exception:
        logger.exception("Could not update contact")

    if text == "▶️ Open Video":
        await show_open_video_menu(update, context)
        return

    if not is_admin(user.id):
        return

    step = context.user_data.get("step")

    if step == "title":
        context.user_data["title"] = text
        context.user_data["step"] = "description"

        await message.reply_text(
            "Description লিখুন। না চাইলে skip লিখুন।"
        )
        return

    if step == "description":
        context.user_data["description"] = (
            "" if text.lower() == "skip" else text
        )
        context.user_data["step"] = "category"

        await message.reply_text(
            "Category নির্বাচন করুন:",
            reply_markup=await admin_category_keyboard(),
        )
        return

    if step == "thumbnail":
        await message.reply_text(
            "⚠️ আগে Thumbnail হিসেবে একটি Photo পাঠান।"
        )
        return

    if step == "add_category":
        pool = await get_pool()

        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    "SELECT id FROM categories WHERE name=$1 LIMIT 1",
                    text,
                )

                if not exists:
                    await conn.execute(
                        "INSERT INTO categories(name) VALUES($1)",
                        text,
                    )
        finally:
            await pool.close()

        context.user_data.clear()
        await message.reply_text(
            f"✅ Category যোগ হয়েছে: {text}",
            reply_markup=admin_keyboard(),
        )
        return

    if step == "delete":
        try:
            media_id = int(text)
        except ValueError:
            await message.reply_text("শুধু Media ID লিখুন।")
            return

        pool = await get_pool()

        try:
            async with pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM media WHERE id=$1",
                    media_id,
                )
        finally:
            await pool.close()

        context.user_data.clear()

        await message.reply_text(
            "✅ Media delete হয়েছে।"
            if result == "DELETE 1"
            else "❌ Media ID পাওয়া যায়নি।",
            reply_markup=admin_keyboard(),
        )
        return

    if await handle_admin_button(update, context):
        return


# =====================================================
# CATEGORY CONTENT LIST
# =====================================================

PAGE_SIZE = 5


async def show_category(query, category_id, page=0):
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            category = await conn.fetchval(
                "SELECT name FROM categories WHERE id=$1",
                category_id,
            )

            if not category:
                await query.edit_message_text(
                    "❌ Category পাওয়া যায়নি।"
                )
                return

            total = await conn.fetchval(
                "SELECT COUNT(*) FROM media WHERE category=$1",
                category,
            )

            rows = await conn.fetch(
                """
                SELECT id, title, media_type
                FROM media
                WHERE category=$1
                ORDER BY id DESC
                LIMIT $2 OFFSET $3
                """,
                category,
                PAGE_SIZE,
                PAGE_SIZE * page,
            )
    finally:
        await pool.close()

    if not total:
        await query.edit_message_text(
            f"📁 {category}\n\n"
            "এই Category-তে এখনো Content নেই।",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏠 Categories",
                        callback_data="categories",
                    )
                ]
            ]),
        )
        return

    buttons = []

    for index, row in enumerate(rows, start=page * PAGE_SIZE + 1):
        emoji = {
            "video": "🎬",
            "photo": "📸",
            "audio": "🎵",
        }.get(row["media_type"], "📁")

        title = row["title"]
        if len(title) > 45:
            title = title[:42] + "..."

        buttons.append([
            InlineKeyboardButton(
                f"{index}. {emoji} {title}",
                callback_data=f"item:{row['id']}:{category_id}:{page}",
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data=f"page:{category_id}:{page - 1}",
            )
        )

    if (page + 1) * PAGE_SIZE < total:
        navigation.append(
            InlineKeyboardButton(
                "➡️ See more",
                callback_data=f"page:{category_id}:{page + 1}",
            )
        )

    if navigation:
        buttons.append(navigation)

    buttons.append([
        InlineKeyboardButton(
            "🏠 Categories",
            callback_data="categories",
        )
    ])

    await query.edit_message_text(
        f"📁 {category}\n"
        f"মোট Content: {total}\n\n"
        "আপনার পছন্দের Content নির্বাচন করুন:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =====================================================
# SELECTED MEDIA
# =====================================================

async def show_selected_media(query, media_id, category_id, page):
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, media_type, title, description,
                       thumbnail_file_id
                FROM media WHERE id=$1
                """,
                media_id,
            )
    finally:
        await pool.close()

    if not row:
        await query.message.reply_text("❌ Content পাওয়া যায়নি।")
        return

    caption = f"🔥 Bangla Vibe\n\n🎬 {row['title']}"

    if row["description"]:
        caption += f"\n\n{row['description']}"

    media_url = f"{WEB_URL}/app?media_id={media_id}"
    chat_type = query.message.chat.type

    if chat_type == "private":
        open_button = InlineKeyboardButton(
            "▶️ Content দেখুন",
            web_app=WebAppInfo(url=media_url),
        )
    else:
        # Telegram Web App buttons are for private chats.
        open_button = InlineKeyboardButton(
            "▶️ Content দেখুন",
            url=media_url,
        )

    keyboard = InlineKeyboardMarkup([
        [open_button],
        [
            InlineKeyboardButton(
                "⬅️ Back to List",
                callback_data=f"page:{category_id}:{page}",
            )
        ],
    ])

    if row["thumbnail_file_id"]:
        try:
            await query.message.reply_photo(
                photo=row["thumbnail_file_id"],
                caption=caption[:1024],
                reply_markup=keyboard,
            )
            return
        except Exception:
            logger.exception("Thumbnail send failed")

    await query.message.reply_text(
        caption[:4000],
        reply_markup=keyboard,
    )


# =====================================================
# SAVE MEDIA AFTER CATEGORY SELECTION
# =====================================================

async def save_media(query, context, category_id):
    user = query.from_user

    if not is_admin(user.id):
        await query.message.reply_text("⛔ শুধু Admin পারবেন।")
        return

    required = [
        "media_type",
        "file_id",
        "thumbnail_file_id",
        "title",
    ]

    if any(not context.user_data.get(key) for key in required):
        context.user_data.clear()
        await query.message.reply_text(
            "❌ তথ্য অসম্পূর্ণ। আবার Content যোগ করুন।"
        )
        return

    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            category = await conn.fetchval(
                "SELECT name FROM categories WHERE id=$1",
                category_id,
            )

            if not category:
                await query.message.reply_text(
                    "❌ Category পাওয়া যায়নি।"
                )
                return

            media_id = await conn.fetchval(
                """
                INSERT INTO media(
                    media_type, file_id, thumbnail_file_id,
                    title, description, category, created_by
                )
                VALUES($1,$2,$3,$4,$5,$6,$7)
                RETURNING id
                """,
                context.user_data["media_type"],
                context.user_data["file_id"],
                context.user_data["thumbnail_file_id"],
                context.user_data["title"],
                context.user_data.get("description", ""),
                category,
                ADMIN_ID,
            )
    finally:
        await pool.close()

    context.user_data.clear()

    await query.message.reply_text(
        f"✅ Content যোগ হয়েছে!\n"
        f"🆔 ID: {media_id}\n"
        f"📁 Category: {category}",
        reply_markup=admin_keyboard(),
    )


# =====================================================
# CALLBACKS
# =====================================================

async def callback_handler(update, context):
    query = update.callback_query
    data = query.data or ""

    await query.answer()

    if data == "menu_start":
        await start(update, context)
        return

    if data == "menu_admin":
        await admin_command(update, context)
        return

    if data.startswith("savecat:"):
        if not is_admin(query.from_user.id):
            await query.message.reply_text("⛔ শুধু Admin পারবেন।")
            return

        if context.user_data.get("step") != "category":
            await query.message.reply_text(
                "⚠️ আগে Content যোগ করার প্রক্রিয়া শুরু করুন।"
            )
            return

        try:
            category_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            await query.message.reply_text("❌ Category ID ভুল।")
            return

        await save_media(query, context, category_id)
        return

    if data.startswith("usercat:"):
        try:
            category_id = int(data.split(":")[1])
        except (ValueError, IndexError):
            return

        await show_category(query, category_id, 0)
        return

    if data.startswith("page:"):
        try:
            _, category_id, page = data.split(":")
            await show_category(query, int(category_id), int(page))
        except (ValueError, IndexError):
            await query.message.reply_text("❌ Page তথ্য ভুল।")
        return

    if data.startswith("item:"):
        try:
            _, media_id, category_id, page = data.split(":")
            await show_selected_media(
                query,
                int(media_id),
                int(category_id),
                int(page),
            )
        except (ValueError, IndexError):
            await query.message.reply_text("❌ Content ID ভুল।")
        return

    if data == "categories":
        await query.edit_message_text(
            "🔥 Bangla Vibe\n\nCategory নির্বাচন করুন:",
            reply_markup=await user_category_keyboard(),
        )


# =====================================================
# TELEGRAM MEDIA DOWNLOAD
# =====================================================

async def download_file(file_id):
    async with Bot(BOT_TOKEN) as bot:
        telegram_file = await bot.get_file(file_id)
        return bytes(await telegram_file.download_as_bytearray())


def run_async(coro):
    loop = asyncio.new_event_loop()

    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@app.route("/thumbnail/<int:media_id>")
def thumbnail_api(media_id):
    async def get_row():
        pool = await get_pool()

        try:
            async with pool.acquire() as conn:
                return await conn.fetchrow(
                    "SELECT thumbnail_file_id FROM media WHERE id=$1",
                    media_id,
                )
        finally:
            await pool.close()

    try:
        row = run_async(get_row())

        if not row or not row["thumbnail_file_id"]:
            return "Not Found", 404

        data = run_async(download_file(row["thumbnail_file_id"]))

        return Response(
            data,
            mimetype="image/jpeg",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception:
        logger.exception("Thumbnail API failed")
        return "Thumbnail unavailable", 500


@app.route("/api/media/<int:media_id>")
def media_api(media_id):
    async def get_row():
        pool = await get_pool()

        try:
            async with pool.acquire() as conn:
                return await conn.fetchrow(
                    """
                    SELECT id, media_type, title, description,
                           category, thumbnail_file_id
                    FROM media WHERE id=$1
                    """,
                    media_id,
                )
        finally:
            await pool.close()

    try:
        row = run_async(get_row())

        if not row:
            return jsonify({"error": "Media not found"}), 404

        thumbnail_url = (
            f"{WEB_URL}/thumbnail/{media_id}"
            if row["thumbnail_file_id"]
            else None
        )

        return jsonify({
            "id": row["id"],
            "type": row["media_type"],
            "title": row["title"],
            "description": row["description"] or "",
            "category": row["category"] or "",
            "thumbnail": thumbnail_url,
        })
    except Exception:
        logger.exception("Media API failed")
        return jsonify({"error": "Server error"}), 500


@app.route("/media/<media_type>/<int:media_id>")
def media_api_file(media_type, media_id):
    async def get_row():
        pool = await get_pool()

        try:
            async with pool.acquire() as conn:
                return await conn.fetchrow(
                    "SELECT media_type, file_id FROM media WHERE id=$1",
                    media_id,
                )
        finally:
            await pool.close()

    try:
        row = run_async(get_row())

        if not row:
            return "Not Found", 404

        if row["media_type"] != media_type:
            return "Wrong media type", 400

        data = run_async(download_file(row["file_id"]))

        mime_types = {
            "video": "video/mp4",
            "audio": "audio/mpeg",
            "photo": "image/jpeg",
        }

        return Response(
            data,
            mimetype=mime_types.get(
                media_type,
                "application/octet-stream",
            ),
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception:
        logger.exception("Media file API failed")
        return "Media unavailable", 500


# =====================================================
# RENDER WEB SERVER
# =====================================================

def run_flask():
    port = int(os.environ.get("PORT", "10000"))

    logger.info("Web server starting on port %s", port)

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =====================================================
# ERROR HANDLER
# =====================================================

async def error_handler(update, context):
    logger.error(
        "Telegram update processing failed",
        exc_info=(
            type(context.error),
            context.error,
            context.error.__traceback__,
        ) if context.error else None,
    )


# =====================================================
# INITIALIZATION
# =====================================================

async def post_init(application):
    await init_db()

    commands = [
        BotCommand("start", "Start Bangla Vibe"),
        BotCommand("admin", "Admin Panel"),
    ]

    await application.bot.set_my_commands(commands)

    try:
        await application.bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(chat_id=ADMIN_ID),
        )
    except Exception:
        logger.exception("Admin command scope setup failed")


async def post_shutdown(application):
    logger.info("Telegram application shutting down")


# =====================================================
# MAIN
# =====================================================

def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_error_handler(error_handler)

    # Log group messages without stopping other handlers.
    application.add_handler(
        MessageHandler(filters.ALL, log_group_updates),
        group=-1,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))

    application.add_handler(CallbackQueryHandler(callback_handler))

    application.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VIDEO | filters.AUDIO,
            handle_media_message,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    threading.Thread(
        target=run_flask,
        daemon=True,
    ).start()

    logger.info("Bangla Vibe Bot starting")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()

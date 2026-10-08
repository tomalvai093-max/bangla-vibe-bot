import os
import asyncio
import threading
import logging
from pathlib import Path

import asyncpg
from flask import Flask, Response, jsonify, send_from_directory

from telegram import (
    Update,
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

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

ADMIN_ID = 8721334265

WEB_URL = "https://bangla-vibe-bot.onrender.com"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN পাওয়া যায়নি")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL পাওয়া যায়নি")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/app")
def app_page():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/health")
def health():
    return "OK", 200


# =========================================================
# DATABASE
# =========================================================

async def get_pool():
    return await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=3,
        command_timeout=30,
    )


async def init_db():

    pool = await get_pool()

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

        # পুরোনো database হলে missing columns যোগ হবে
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
            except Exception as e:
                logger.warning("Migration warning: %s", e)

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
                INSERT INTO categories(name)
                VALUES($1)
                ON CONFLICT(name) DO NOTHING
                """,
                category,
            )

    await pool.close()

    logger.info("Database initialized successfully.")


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


async def save_contact(user):

    try:

        pool = await get_pool()

        async with pool.acquire() as conn:

            existing = await conn.fetchval(
                """
                SELECT id
                FROM contacts
                WHERE user_id=$1
                """,
                user.id,
            )

            if existing:

                await conn.execute(
                    """
                    UPDATE contacts
                    SET username=$1,
                        first_name=$2
                    WHERE user_id=$3
                    """,
                    user.username,
                    user.first_name,
                    user.id,
                )

            else:

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

        await pool.close()

    except Exception as e:

        logger.error(
            "save_contact error: %s",
            e,
        )


# =========================================================
# ADMIN KEYBOARD
# =========================================================

def admin_keyboard():

    return ReplyKeyboardMarkup(
        [
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
    )


# =========================================================
# CATEGORY KEYBOARD FOR USERS
# =========================================================

async def user_category_keyboard():

    pool = await get_pool()

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT id, name
            FROM categories
            ORDER BY id ASC
            """
        )

    await pool.close()

    buttons = []

    for row in rows:

        buttons.append(
            [
                InlineKeyboardButton(
                    row["name"],
                    callback_data=f"usercat:{row['id']}",
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


# =========================================================
# CATEGORY KEYBOARD FOR ADMIN
# =========================================================

async def admin_category_keyboard():

    pool = await get_pool()

    async with pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT id, name
            FROM categories
            ORDER BY id ASC
            """
        )

    await pool.close()

    buttons = []

    for row in rows:

        buttons.append(
            [
                InlineKeyboardButton(
                    row["name"],
                    callback_data=f"savecat:{row['id']}",
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()

    user = update.effective_user

    await save_contact(user)

    keyboard = await user_category_keyboard()

    await update.message.reply_text(
        "🔥 Bangla Vibe\n\n"
        "স্বাগতম!\n"
        "নিচের Category থেকে Content নির্বাচন করুন।",
        reply_markup=keyboard,
    )


# =========================================================
# ADMIN COMMAND
# =========================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        return

    context.user_data.clear()

    await update.message.reply_text(
        "👑 Bangla Vibe Admin Panel\n\n"
        "নিচের অপশন থেকে নির্বাচন করুন।",
        reply_markup=admin_keyboard(),
    )


# =========================================================
# ADMIN TEXT BUTTONS
# =========================================================

async def handle_admin_button(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        return False

    text = (update.message.text or "").strip()

    # -------------------------
    # VIDEO
    # -------------------------

    video_commands = [
        "🎬 ভিডিও যোগ",
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

    if text in video_commands:

        context.user_data.clear()

        context.user_data["step"] = "media"
        context.user_data["media_type"] = "video"

        await update.message.reply_text(
            "🎬 Video পাঠাও।"
        )

        return True

    # -------------------------
    # PHOTO
    # -------------------------

    photo_commands = [
        "📸 ফটো যোগ",
        "ফটো দাও",
        "ছবি দাও",
        "add photo",
        "upload photo",
    ]

    if text in photo_commands:

        context.user_data.clear()

        context.user_data["step"] = "media"
        context.user_data["media_type"] = "photo"

        await update.message.reply_text(
            "📸 Photo পাঠাও।"
        )

        return True

    # -------------------------
    # AUDIO
    # -------------------------

    audio_commands = [
        "🎵 অডিও যোগ",
        "অডিও দাও",
        "গান দাও",
        "add audio",
        "upload audio",
    ]

    if text in audio_commands:

        context.user_data.clear()

        context.user_data["step"] = "media"
        context.user_data["media_type"] = "audio"

        await update.message.reply_text(
            "🎵 Audio পাঠাও।"
        )

        return True

    # -------------------------
    # CATEGORY LIST
    # -------------------------

    if text == "📁 Category":

        keyboard = await user_category_keyboard()

        await update.message.reply_text(
            "📁 Category:",
            reply_markup=keyboard,
        )

        return True

    # -------------------------
    # ADD CATEGORY
    # -------------------------

    if text == "➕ Category যোগ":

        context.user_data.clear()

        context.user_data["step"] = "add_category"

        await update.message.reply_text(
            "➕ নতুন Category-এর নাম লিখুন।"
        )

        return True

    # -------------------------
    # USERS
    # -------------------------

    if text == "👥 Users":

        pool = await get_pool()

        async with pool.acquire() as conn:

            count = await conn.fetchval(
                "SELECT COUNT(*) FROM contacts"
            )

        await pool.close()

        await update.message.reply_text(
            f"👥 Total Users: {count}"
        )

        return True

    # -------------------------
    # ALL MEDIA
    # -------------------------

    if text == "📦 All Media":

        pool = await get_pool()

        async with pool.acquire() as conn:

            rows = await conn.fetch(
                """
                SELECT
                    id,
                    media_type,
                    title,
                    category
                FROM media
                ORDER BY id DESC
                LIMIT 100
                """
            )

        await pool.close()

        if not rows:

            await update.message.reply_text(
                "📦 কোনো Media নেই।"
            )

            return True

        message = "📦 All Media\n\n"

        for row in rows:

            message += (
                f"🆔 ID: {row['id']}\n"
                f"🎬 Type: {row['media_type']}\n"
                f"📝 {row['title']}\n"
                f"📁 {row['category'] or 'None'}\n\n"
            )

        await update.message.reply_text(
            message
        )

        return True

    # -------------------------
    # DELETE
    # -------------------------

    if text == "🗑 Delete":

        context.user_data.clear()

        context.user_data["step"] = "delete"

        await update.message.reply_text(
            "🗑 যে Media delete করতে চান তার ID লিখুন।"
        )

        return True

    return False


# =========================================================
# ONE MEDIA HANDLER
# =========================================================
# Video / Photo / Audio / Thumbnail
# সব এখানে handle হবে।
# এতে handler order-এর সমস্যা হবে না।
# =========================================================

async def handle_media_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        return

    step = context.user_data.get("step")

    # =====================================================
    # STEP 1: MAIN MEDIA
    # =====================================================

    if step == "media":

        media_type = context.user_data.get("media_type")

        file_id = None

        # VIDEO
        if media_type == "video":

            if update.message.video:

                file_id = update.message.video.file_id

        # AUDIO
        elif media_type == "audio":

            if update.message.audio:

                file_id = update.message.audio.file_id

        # PHOTO
        elif media_type == "photo":

            if update.message.photo:

                file_id = update.message.photo[-1].file_id

        if not file_id:

            await update.message.reply_text(
                "⚠️ সঠিক Media পাঠাও।"
            )

            return

        context.user_data["file_id"] = file_id

        context.user_data["step"] = "thumbnail"

        await update.message.reply_text(
            "✅ Content পেয়েছি।\n\n"
            "1️⃣ এখন Thumbnail পাঠাও।\n"
            "⚠️ Thumbnail দেওয়া বাধ্যতামূলক।"
        )

        return

    # =====================================================
    # STEP 2: THUMBNAIL
    # =====================================================

    if step == "thumbnail":

        thumbnail_id = None

        # Telegram photo
        if update.message.photo:

            thumbnail_id = (
                update.message.photo[-1].file_id
            )

        # Telegram image document
        elif update.message.document:

            mime = (
                update.message.document.mime_type
                or ""
            )

            if mime.startswith("image/"):

                thumbnail_id = (
                    update.message.document.file_id
                )

        if not thumbnail_id:

            await update.message.reply_text(
                "⚠️ Thumbnail হিসেবে Photo/Image পাঠাও।"
            )

            return

        context.user_data[
            "thumbnail_file_id"
        ] = thumbnail_id

        context.user_data["step"] = "title"

        await update.message.reply_text(
            "✅ Thumbnail পেয়েছি।\n\n"
            "2️⃣ এখন Content-এর Title লিখো।\n"
            "⚠️ Title অবশ্যই দিতে হবে।"
        )

        return


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    user = update.effective_user

    await save_contact(user)

    text = (update.message.text or "").strip()

    # =====================================================
    # ADMIN STATE
    # =====================================================

    if is_admin(user.id):

        step = context.user_data.get("step")

        # ---------------------------------------------
        # TITLE
        # ---------------------------------------------

        if step == "title":

            if not text:

                await update.message.reply_text(
                    "⚠️ Title অবশ্যই দিতে হবে।"
                )

                return

            context.user_data["title"] = text

            context.user_data["step"] = "description"

            await update.message.reply_text(
                "3️⃣ Description লিখুন।\n\n"
                "Description না চাইলে শুধু লিখুন:\n"
                "skip"
            )

            return

        # ---------------------------------------------
        # DESCRIPTION
        # ---------------------------------------------

        if step == "description":

            if text.lower() == "skip":

                context.user_data["description"] = ""

            else:

                context.user_data["description"] = text

            context.user_data["step"] = "category"

            keyboard = await admin_category_keyboard()

            await update.message.reply_text(
                "4️⃣ 📁 Category নির্বাচন করুন:",
                reply_markup=keyboard,
            )

            return

        # ---------------------------------------------
        # ADD CATEGORY
        # ---------------------------------------------

        if step == "add_category":

            if not text:

                await update.message.reply_text(
                    "⚠️ Category-এর নাম লিখুন।"
                )

                return

            pool = await get_pool()

            async with pool.acquire() as conn:

                existing = await conn.fetchval(
                    """
                    SELECT id
                    FROM categories
                    WHERE LOWER(name)=LOWER($1)
                    """,
                    text,
                )

                if existing:

                    await pool.close()

                    context.user_data.clear()

                    await update.message.reply_text(
                        "⚠️ এই Category আগে থেকেই আছে।",
                        reply_markup=admin_keyboard(),
                    )

                    return

                await conn.execute(
                    """
                    INSERT INTO categories(name)
                    VALUES($1)
                    """,
                    text,
                )

            await pool.close()

            context.user_data.clear()

            await update.message.reply_text(
                f"✅ Category সফলভাবে যোগ হয়েছে:\n\n"
                f"📁 {text}",
                reply_markup=admin_keyboard(),
            )

            return

        # ---------------------------------------------
        # DELETE
        # ---------------------------------------------

        if step == "delete":

            try:

                media_id = int(text)

            except ValueError:

                await update.message.reply_text(
                    "⚠️ শুধু Media ID লিখুন।"
                )

                return

            pool = await get_pool()

            async with pool.acquire() as conn:

                result = await conn.execute(
                    """
                    DELETE FROM media
                    WHERE id=$1
                    """,
                    media_id,
                )

            await pool.close()

            context.user_data.clear()

            if result == "DELETE 1":

                await update.message.reply_text(
                    f"✅ Media ID {media_id} delete হয়েছে।",
                    reply_markup=admin_keyboard(),
                )

            else:

                await update.message.reply_text(
                    "❌ এই Media ID পাওয়া যায়নি।"
                )

            return

        # ---------------------------------------------
        # ADMIN BUTTONS
        # ---------------------------------------------

        handled = await handle_admin_button(
            update,
            context,
        )

        if handled:
            return

    # =====================================================
    # USER
    # =====================================================

    # সাধারণ user-এর text আপাতত ignore
    return


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    data = query.data or ""

    # =====================================================
    # ADMIN SAVE CATEGORY
    # =====================================================

    if data.startswith("savecat:"):

        if not is_admin(query.from_user.id):

            await query.answer(
                "শুধু Admin ব্যবহার করতে পারবেন।",
                show_alert=True,
            )

            return

        if context.user_data.get("step") != "category":

            await query.edit_message_text(
                "⚠️ এখন কোনো Content যোগ করার কাজ চলছে না।"
            )

            return

        try:

            category_id = int(
                data.split(":")[1]
            )

        except ValueError:

            await query.edit_message_text(
                "❌ Category ID ভুল।"
            )

            return

        await save_media(
            query,
            context,
            category_id,
        )

        return

    # =====================================================
    # USER CATEGORY
    # =====================================================

    if data.startswith("usercat:"):

        try:

            category_id = int(
                data.split(":")[1]
            )

        except ValueError:

            return

        await show_category(
            query,
            category_id,
        )

        return


# =========================================================
# SAVE MEDIA
# =========================================================

async def save_media(query, context, category_id):

    required = [
        "media_type",
        "file_id",
        "thumbnail_file_id",
        "title",
    ]

    for key in required:

        if not context.user_data.get(key):

            await query.edit_message_text(
                "❌ Content-এর প্রয়োজনীয় তথ্য পাওয়া যায়নি। "
                "আবার Content যোগ করুন।"
            )

            context.user_data.clear()

            return

    pool = await get_pool()

    async with pool.acquire() as conn:

        category = await conn.fetchval(
            """
            SELECT name
            FROM categories
            WHERE id=$1
            """,
            category_id,
        )

        if not category:

            await pool.close()

            await query.edit_message_text(
                "❌ Category পাওয়া যায়নি।"
            )

            context.user_data.clear()

            return

        media_id = await conn.fetchval(
            """
            INSERT INTO media(
                media_type,
                file_id,
                thumbnail_file_id,
                title,
                description,
                category,
                created_by
            )
            VALUES(
                $1,$2,$3,$4,$5,$6,$7
            )
            RETURNING id
            """,
            context.user_data["media_type"],
            context.user_data["file_id"],
            context.user_data["thumbnail_file_id"],
            context.user_data["title"],
            context.user_data.get(
                "description",
                "",
            ),
            category,
            ADMIN_ID,
        )

    await pool.close()

    context.user_data.clear()

    await query.edit_message_text(
        "✅ Content সফলভাবে যোগ হয়েছে!\n\n"
        f"🆔 Media ID: {media_id}\n"
        f"📁 Category: {category}"
    )


# =========================================================
# SHOW CATEGORY CONTENT
# =========================================================

async def show_category(query, category_id):

    pool = await get_pool()

    async with pool.acquire() as conn:

        category = await conn.fetchval(
            """
            SELECT name
            FROM categories
            WHERE id=$1
            """,
            category_id,
        )

        if not category:

            await pool.close()

            await query.edit_message_text(
                "❌ Category পাওয়া যায়নি।"
            )

            return

        rows = await conn.fetch(
            """
            SELECT
                id,
                media_type,
                title,
                description,
                thumbnail_file_id
            FROM media
            WHERE category=$1
            ORDER BY id DESC
            """,
            category,
        )

    await pool.close()

    if not rows:

        await query.edit_message_text(
            f"📁 {category}\n\n"
            "এই Category-তে এখনো কোনো Content নেই।"
        )

        return

    await query.edit_message_text(
        f"📁 {category}\n\n"
        f"মোট Content: {len(rows)}"
    )

    for row in rows:

        media_id = row["id"]

        caption = (
            "🔥 Bangla Vibe\n\n"
            f"🎬 {row['title']}"
        )

        if row["description"]:

            caption += (
                "\n\n"
                + row["description"]
            )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "▶️ Content দেখুন",
                        web_app=WebAppInfo(
                            url=(
                                f"{WEB_URL}/app"
                                f"?media_id={media_id}"
                            )
                        ),
                    )
                ]
            ]
        )

        # Thumbnail Telegram-এ দেখাবে
        if row["thumbnail_file_id"]:

            try:

                await query.message.reply_photo(
                    photo=row["thumbnail_file_id"],
                    caption=caption,
                    reply_markup=keyboard,
                )

            except Exception as e:

                logger.error(
                    "Thumbnail send error: %s",
                    e,
                )

                await query.message.reply_text(
                    caption,
                    reply_markup=keyboard,
                )

        else:

            await query.message.reply_text(
                caption,
                reply_markup=keyboard,
            )


# =========================================================
# TELEGRAM FILE DOWNLOAD
# =========================================================

async def download_file(file_id):

    from telegram import Bot

    bot = Bot(BOT_TOKEN)

    telegram_file = await bot.get_file(
        file_id
    )

    data = await telegram_file.download_as_bytearray()

    return bytes(data)


def run_async(coro):

    loop = asyncio.new_event_loop()

    try:

        asyncio.set_event_loop(loop)

        return loop.run_until_complete(coro)

    finally:

        loop.close()


# =========================================================
# THUMBNAIL API
# =========================================================

@app.route("/thumbnail/<int:media_id>")
def thumbnail_api(media_id):

    async def get_row():

        pool = await get_pool()

        async with pool.acquire() as conn:

            row = await conn.fetchrow(
                """
                SELECT thumbnail_file_id
                FROM media
                WHERE id=$1
                """,
                media_id,
            )

        await pool.close()

        return row

    row = run_async(
        get_row()
    )

    if not row:

        return "Not Found", 404

    if not row["thumbnail_file_id"]:

        return "Thumbnail Not Found", 404

    data = run_async(
        download_file(
            row["thumbnail_file_id"]
        )
    )

    return Response(
        data,
        mimetype="image/jpeg",
        headers={
            "Cache-Control":
                "public, max-age=3600"
        },
    )


# =========================================================
# MEDIA API
# =========================================================

@app.route("/api/media/<int:media_id>")
def media_api(media_id):

    async def get_row():

        pool = await get_pool()

        async with pool.acquire() as conn:

            row = await conn.fetchrow(
                """
                SELECT
                    id,
                    media_type,
                    title,
                    description,
                    category,
                    thumbnail_file_id
                FROM media
                WHERE id=$1
                """,
                media_id,
            )

        await pool.close()

        return row

    row = run_async(
        get_row()
    )

    if not row:

        return jsonify(
            {
                "error": "Media not found"
            }
        ), 404

    thumbnail_url = None

    if row["thumbnail_file_id"]:

        thumbnail_url = (
            f"{WEB_URL}/thumbnail/{media_id}"
        )

    return jsonify(
        {
            "id": row["id"],
            "type": row["media_type"],
            "title": row["title"],
            "description":
                row["description"] or "",
            "category":
                row["category"] or "",
            "thumbnail":
                thumbnail_url,
        }
    )


# =========================================================
# MEDIA FILE API
# =========================================================

@app.route("/media/<media_type>/<int:media_id>")
def media_api_file(media_type, media_id):

    async def get_row():

        pool = await get_pool()

        async with pool.acquire() as conn:

            row = await conn.fetchrow(
                """
                SELECT
                    media_type,
                    file_id
                FROM media
                WHERE id=$1
                """,
                media_id,
            )

        await pool.close()

        return row

    row = run_async(
        get_row()
    )

    if not row:

        return "Not Found", 404

    if row["media_type"] != media_type:

        return "Wrong media type", 400

    data = run_async(
        download_file(
            row["file_id"]
        )
    )

    mime_types = {
        "video": "video/mp4",
        "audio": "audio/mpeg",
        "photo": "image/jpeg",
    }

    mime = mime_types.get(
        media_type,
        "application/octet-stream",
    )

    return Response(
        data,
        mimetype=mime,
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control":
                "public, max-age=3600",
        },
    )


# =========================================================
# FLASK SERVER
# =========================================================

def run_flask():

    port = int(
        os.environ.get(
            "PORT",
            "10000",
        )
    )

    logger.info(
        "Web server starting on port %s",
        port,
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# BOT POST INIT
# =========================================================

async def post_init(application):

    await init_db()

    await application.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "Start Bangla Vibe",
            ),
            BotCommand(
                "admin",
                "Admin Panel",
            ),
        ]
    )

    try:

        await application.bot.set_my_commands(
            [
                BotCommand(
                    "start",
                    "Start Bangla Vibe",
                ),
                BotCommand(
                    "admin",
                    "Admin Panel",
                ),
            ],
            scope=BotCommandScopeChat(
                chat_id=ADMIN_ID
            ),
        )

    except Exception as e:

        logger.warning(
            "Admin command scope error: %s",
            e,
        )


# =========================================================
# MAIN
# =========================================================
# গুরুত্বপূর্ণ:
# এখানে asyncio.run() ব্যবহার করা হয়নি।
# run_polling নিজেই event loop পরিচালনা করবে।
# =========================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # /start
    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    # /admin
    application.add_handler(
        CommandHandler(
            "admin",
            admin_command,
        )
    )

    # Inline buttons
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # সব Media একই handler-এ
    application.add_handler(
        MessageHandler(
            (
                filters.PHOTO
                | filters.VIDEO
                | filters.AUDIO
                | filters.Document.IMAGE
            ),
            handle_media_message,
        )
    )

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            text_handler,
        )
    )

    # Flask আলাদা thread
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True,
    )

    flask_thread.start()

    print(
        "🤖 Bangla Vibe Bot started..."
    )

    print(
        "🌐 Web server started..."
    )

    # asyncio.run() নয়
    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

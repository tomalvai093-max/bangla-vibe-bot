import os
import asyncio
import logging
from pathlib import Path

import asyncpg
from flask import Flask, Response, jsonify, request, send_from_directory

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN পাওয়া যায়নি")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL পাওয়া যায়নি")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent


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

        # Safe migrations
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

        for cat in default_categories:
            await conn.execute(
                """
                INSERT INTO categories(name)
                VALUES($1)
                ON CONFLICT(name) DO NOTHING
                """,
                cat,
            )

    await pool.close()


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


async def save_contact(user):
    try:
        pool = await get_pool()

        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT id FROM contacts WHERE user_id=$1",
                user.id,
            )

            if exists:
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
                    VALUES($1,$2,$3)
                    """,
                    user.id,
                    user.username,
                    user.first_name,
                )

        await pool.close()

    except Exception as e:
        logger.error("save_contact error: %s", e)


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
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    await save_contact(user)

    keyboard = await category_keyboard()

    await update.message.reply_text(
        "🔥 Bangla Vibe\n\n"
        "স্বাগতম!\n"
        "নিচের Category থেকে Content নির্বাচন করুন।",
        reply_markup=keyboard,
    )


async def category_keyboard():

    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name FROM categories ORDER BY id"
        )

    await pool.close()

    buttons = []

    for row in rows:
        buttons.append(
            [
                InlineKeyboardButton(
                    row["name"],
                    callback_data=f"cat:{row['id']}",
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


# =========================================================
# ADMIN
# =========================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        return

    await update.message.reply_text(
        "👑 Admin Panel\n\n"
        "নিচের অপশন নির্বাচন করুন।",
        reply_markup=admin_keyboard(),
    )


async def admin_button_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        return

    text = (update.message.text or "").strip()

    # -----------------------------------------
    # ADD VIDEO
    # -----------------------------------------

    if text in [
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
    ]:
        context.user_data.clear()
        context.user_data["media_step"] = "media"
        context.user_data["media_type"] = "video"

        await update.message.reply_text(
            "🎬 Video পাঠাও।"
        )
        return

    # -----------------------------------------
    # ADD PHOTO
    # -----------------------------------------

    if text in [
        "📸 ফটো যোগ",
        "ফটো দাও",
        "ছবি দাও",
        "add photo",
        "upload photo",
    ]:
        context.user_data.clear()
        context.user_data["media_step"] = "media"
        context.user_data["media_type"] = "photo"

        await update.message.reply_text(
            "📸 Photo পাঠাও।"
        )
        return

    # -----------------------------------------
    # ADD AUDIO
    # -----------------------------------------

    if text in [
        "🎵 অডিও যোগ",
        "অডিও দাও",
        "গান দাও",
        "add audio",
        "upload audio",
    ]:
        context.user_data.clear()
        context.user_data["media_step"] = "media"
        context.user_data["media_type"] = "audio"

        await update.message.reply_text(
            "🎵 Audio পাঠাও।"
        )
        return

    # -----------------------------------------
    # CATEGORY LIST
    # -----------------------------------------

    if text == "📁 Category":

        keyboard = await category_keyboard()

        await update.message.reply_text(
            "📁 Category নির্বাচন করুন:",
            reply_markup=keyboard,
        )
        return

    # -----------------------------------------
    # ADD CATEGORY
    # -----------------------------------------

    if text in [
        "➕ Category যোগ",
        "category add",
        "add category",
    ]:
        context.user_data.clear()
        context.user_data["category_step"] = True

        await update.message.reply_text(
            "➕ নতুন Category-এর নাম লিখুন।"
        )
        return

    # -----------------------------------------
    # USERS
    # -----------------------------------------

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
        return

    # -----------------------------------------
    # ALL MEDIA
    # -----------------------------------------

    if text == "📦 All Media":

        pool = await get_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, media_type, title, category
                FROM media
                ORDER BY id DESC
                LIMIT 50
                """
            )

        await pool.close()

        if not rows:
            await update.message.reply_text(
                "📦 কোনো Media নেই।"
            )
            return

        msg = "📦 All Media\n\n"

        for row in rows:
            msg += (
                f"ID: {row['id']}\n"
                f"Type: {row['media_type']}\n"
                f"Title: {row['title']}\n"
                f"Category: {row['category'] or 'None'}\n\n"
            )

        await update.message.reply_text(msg)
        return

    # -----------------------------------------
    # DELETE
    # -----------------------------------------

    if text == "🗑 Delete":

        context.user_data.clear()
        context.user_data["delete_step"] = True

        await update.message.reply_text(
            "🗑 যে Media delete করতে চান তার ID লিখুন।"
        )
        return


# =========================================================
# MEDIA UPLOAD HANDLER
# =========================================================

async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        return

    step = context.user_data.get("media_step")

    if step != "media":
        return

    media_type = context.user_data.get("media_type")

    file_id = None

    if media_type == "video" and update.message.video:
        file_id = update.message.video.file_id

    elif media_type == "audio" and update.message.audio:
        file_id = update.message.audio.file_id

    elif media_type == "photo" and update.message.photo:
        file_id = update.message.photo[-1].file_id

    if not file_id:
        await update.message.reply_text(
            "⚠️ সঠিক Media পাঠাও।"
        )
        return

    context.user_data["file_id"] = file_id
    context.user_data["media_step"] = "thumbnail"

    await update.message.reply_text(
        "✅ Content পেয়েছি।\n\n"
        "1️⃣ এখন Thumbnail পাঠাও।\n"
        "⚠️ Thumbnail দেওয়া বাধ্যতামূলক।"
    )


# =========================================================
# THUMBNAIL
# =========================================================

async def thumbnail_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        return

    if context.user_data.get("media_step") != "thumbnail":
        return

    thumb_id = None

    if update.message.photo:
        thumb_id = update.message.photo[-1].file_id

    elif (
        update.message.document
        and update.message.document.mime_type
        and update.message.document.mime_type.startswith("image/")
    ):
        thumb_id = update.message.document.file_id

    if not thumb_id:
        await update.message.reply_text(
            "⚠️ Thumbnail হিসেবে একটি Photo/Image পাঠাও।"
        )
        return

    context.user_data["thumbnail_file_id"] = thumb_id
    context.user_data["media_step"] = "title"

    await update.message.reply_text(
        "✅ Thumbnail পেয়েছি।\n\n"
        "2️⃣ এখন Content-এর Title লিখো।\n"
        "⚠️ Title অবশ্যই দিতে হবে।"
    )


# =========================================================
# TEXT STATE HANDLER
# =========================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not update.message:
        return

    user = update.effective_user

    await save_contact(user)

    text = (update.message.text or "").strip()

    # ============================================
    # ADMIN ADD CATEGORY
    # ============================================

    if is_admin(user.id) and context.user_data.get("category_step"):

        if not text:
            return

        pool = await get_pool()

        try:
            await pool.execute(
                """
                INSERT INTO categories(name)
                VALUES($1)
                ON CONFLICT(name) DO NOTHING
                """,
                text,
            )

            await update.message.reply_text(
                f"✅ Category যোগ হয়েছে:\n\n{text}",
                reply_markup=admin_keyboard(),
            )

        except Exception as e:
            logger.error(e)

            await update.message.reply_text(
                "❌ Category যোগ করতে সমস্যা হয়েছে।"
            )

        finally:
            await pool.close()

        context.user_data.clear()
        return

    # ============================================
    # DELETE MEDIA
    # ============================================

    if is_admin(user.id) and context.user_data.get("delete_step"):

        try:
            media_id = int(text)

            pool = await get_pool()

            result = await pool.execute(
                "DELETE FROM media WHERE id=$1",
                media_id,
            )

            await pool.close()

            if result == "DELETE 1":
                await update.message.reply_text(
                    f"✅ Media ID {media_id} delete হয়েছে।",
                    reply_markup=admin_keyboard(),
                )
            else:
                await update.message.reply_text(
                    "❌ এই ID পাওয়া যায়নি।"
                )

        except:
            await update.message.reply_text(
                "⚠️ সঠিক Media ID লিখুন।"
            )

        context.user_data.clear()
        return

    # ============================================
    # MEDIA TITLE
    # ============================================

    if is_admin(user.id) and context.user_data.get("media_step") == "title":

        if not text:
            await update.message.reply_text(
                "⚠️ Title অবশ্যই দিতে হবে।"
            )
            return

        context.user_data["title"] = text
        context.user_data["media_step"] = "description"

        await update.message.reply_text(
            "3️⃣ Description লিখুন।\n\n"
            "Description না চাইলে শুধু `skip` লিখুন।"
        )
        return

    # ============================================
    # MEDIA DESCRIPTION
    # ============================================

    if is_admin(user.id) and context.user_data.get("media_step") == "description":

        if text.lower() == "skip":
            context.user_data["description"] = ""
        else:
            context.user_data["description"] = text

        context.user_data["media_step"] = "category"

        keyboard = await admin_category_buttons()

        await update.message.reply_text(
            "4️⃣ 📁 Category নির্বাচন করুন:",
            reply_markup=keyboard,
        )
        return

    # ============================================
    # NATURAL ADMIN COMMANDS
    # ============================================

    if is_admin(user.id):

        low = text.lower()

        if (
            "facebook" in low
            and ("admin" in low or "এডমিন" in text)
        ):
            await send_social(update, "facebook")
            return

        if (
            "tiktok" in low
            and ("admin" in low or "এডমিন" in text)
        ):
            await send_social(update, "tiktok")
            return

        await admin_button_text(update, context)


# =========================================================
# ADMIN CATEGORY BUTTONS
# =========================================================

async def admin_category_buttons():

    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name FROM categories ORDER BY id"
        )

    await pool.close()

    buttons = []

    for row in rows:
        buttons.append(
            [
                InlineKeyboardButton(
                    row["name"],
                    callback_data=f"choosecat:{row['id']}",
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


# =========================================================
# CATEGORY CALLBACK
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    data = query.data

    # ============================================
    # ADMIN SELECT CATEGORY
    # ============================================

    if data.startswith("choosecat:"):

        if not is_admin(query.from_user.id):
            await query.answer(
                "শুধু Admin ব্যবহার করতে পারবেন।",
                show_alert=True,
            )
            return

        if context.user_data.get("media_step") != "category":
            await query.edit_message_text(
                "⚠️ এখন কোনো Media যোগ করার কাজ চলছে না।"
            )
            return

        try:
            category_id = int(data.split(":")[1])
        except:
            return

        await finish_media(
            update,
            context,
            category_id,
        )
        return

    # ============================================
    # USER CATEGORY
    # ============================================

    if data.startswith("cat:"):

        try:
            category_id = int(data.split(":")[1])
        except:
            return

        await show_category(
            update,
            category_id,
        )
        return


# =========================================================
# SAVE MEDIA
# =========================================================

async def finish_media(update, context, category_id):

    query = update.callback_query

    pool = await get_pool()

    async with pool.acquire() as conn:

        category_name = await conn.fetchval(
            "SELECT name FROM categories WHERE id=$1",
            category_id,
        )

        if not category_name:
            await pool.close()

            await query.edit_message_text(
                "❌ Category পাওয়া যায়নি।"
            )
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
            VALUES($1,$2,$3,$4,$5,$6,$7)
            RETURNING id
            """,
            context.user_data["media_type"],
            context.user_data["file_id"],
            context.user_data["thumbnail_file_id"],
            context.user_data["title"],
            context.user_data.get("description", ""),
            category_name,
            ADMIN_ID,
        )

    await pool.close()

    context.user_data.clear()

    await query.edit_message_text(
        "✅ Content সফলভাবে যোগ হয়েছে!\n\n"
        f"🆔 ID: {media_id}\n"
        f"📁 Category: {category_name}"
    )


# =========================================================
# SHOW CATEGORY
# =========================================================

async def show_category(update, category_id):

    query = update.callback_query

    pool = await get_pool()

    async with pool.acquire() as conn:

        category = await conn.fetchval(
            "SELECT name FROM categories WHERE id=$1",
            category_id,
        )

        rows = await conn.fetch(
            """
            SELECT id, media_type, file_id, thumbnail_file_id,
                   title, description
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
        title = row["title"]
        description = row["description"] or ""

        caption = (
            f"🔥 Bangla Vibe\n\n"
            f"🎬 {title}\n"
        )

        if description:
            caption += f"\n{description}"

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "▶️ Content দেখুন",
                        web_app=__import__(
                            "telegram"
                        ).WebAppInfo(
                            url=f"{WEB_URL}/app?media_id={media_id}"
                        ),
                    )
                ]
            ]
        )

        # Thumbnail থাকলে thumbnail-ই Telegram-এ দেখাবে
        if row["thumbnail_file_id"]:

            try:
                await query.message.reply_photo(
                    photo=row["thumbnail_file_id"],
                    caption=caption,
                    reply_markup=keyboard,
                )
            except Exception as e:
                logger.error(
                    "thumbnail send error: %s",
                    e,
                )

        else:
            await query.message.reply_text(
                caption,
                reply_markup=keyboard,
            )


# =========================================================
# SOCIAL LINKS
# =========================================================

async def send_social(update, platform):

    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT value
            FROM social_links
            WHERE platform=$1
            ORDER BY id DESC
            LIMIT 1
            """,
            platform,
        )

    await pool.close()

    if row:
        await update.message.reply_text(
            f"📱 Admin {platform.title()}:\n\n"
            f"{row['value']}"
        )
    else:
        await update.message.reply_text(
            f"❌ এখনো Admin {platform.title()} দেওয়া হয়নি।"
        )


# =========================================================
# TELEGRAM FILE PROXY
# =========================================================

def run_async(coro):

    loop = asyncio.new_event_loop()

    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def get_file_bytes(file_id):

    from telegram import Bot

    bot = Bot(BOT_TOKEN)

    tg_file = await bot.get_file(file_id)

    data = await tg_file.download_as_bytearray()

    return bytes(data)


@app.route("/thumbnail/<int:media_id>")
def thumbnail(media_id):

    async def fetch():

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

    row = run_async(fetch())

    if not row or not row["thumbnail_file_id"]:
        return "Not Found", 404

    data = run_async(
        get_file_bytes(row["thumbnail_file_id"])
    )

    return Response(
        data,
        mimetype="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=3600"
        },
    )


@app.route("/api/media/<int:media_id>")
def api_media(media_id):

    async def fetch():

        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, media_type, title,
                       description, category,
                       thumbnail_file_id
                FROM media
                WHERE id=$1
                """,
                media_id,
            )

        await pool.close()

        return row

    row = run_async(fetch())

    if not row:
        return jsonify({"error": "Media not found"}), 404

    return jsonify(
        {
            "id": row["id"],
            "type": row["media_type"],
            "title": row["title"],
            "description": row["description"] or "",
            "category": row["category"] or "",
            "thumbnail": (
                f"{WEB_URL}/thumbnail/{row['id']}"
                if row["thumbnail_file_id"]
                else None
            ),
        }
    )


@app.route("/media/<media_type>/<int:media_id>")
def media_file(media_type, media_id):

    async def fetch():

        pool = await get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT media_type, file_id
                FROM media
                WHERE id=$1
                """,
                media_id,
            )

        await pool.close()

        return row

    row = run_async(fetch())

    if not row:
        return "Not Found", 404

    if row["media_type"] != media_type:
        return "Wrong media type", 400

    data = run_async(
        get_file_bytes(row["file_id"])
    )

    mime = {
        "video": "video/mp4",
        "audio": "audio/mpeg",
        "photo": "image/jpeg",
    }.get(media_type, "application/octet-stream")

    return Response(
        data,
        mimetype=mime,
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
        },
    )


# =========================================================
# MAIN
# =========================================================

async def post_init(application):

    await init_db()

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Start Bangla Vibe"),
            BotCommand("admin", "Admin Panel"),
        ]
    )

    try:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Start Bangla Vibe"),
                BotCommand("admin", "Admin Panel"),
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


def run_flask():

    port = int(os.environ.get("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


async def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("admin", admin_command)
    )

    # Callback buttons
    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    # Thumbnail handler আগে
    application.add_handler(
        MessageHandler(
            filters.PHOTO |
            filters.Document.IMAGE,
            thumbnail_handler,
        ),
        group=0,
    )

    # Main media handler
    application.add_handler(
        MessageHandler(
            filters.VIDEO |
            filters.AUDIO,
            media_handler,
        ),
        group=1,
    )

    # Text/state/admin handler
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        ),
        group=2,
    )

    flask_thread = asyncio.to_thread(
        run_flask
    )

    await asyncio.gather(
        flask_thread,
        application.run_polling(
            drop_pending_updates=True
        ),
    )


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("Bot stopped.")

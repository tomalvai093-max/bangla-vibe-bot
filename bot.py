import os
import logging
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
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# তোমার Telegram User ID
ADMIN_ID = 8721334265

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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
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
            CREATE TABLE IF NOT EXISTS social_links (
                id SERIAL PRIMARY KEY,
                platform TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE NOT NULL,
                username TEXT DEFAULT '',
                name TEXT DEFAULT '',
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
# HELPERS
# =========================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


def normalize(text):
    text = text.lower().strip()

    replacements = {
        "  ": " ",
        "দেও": "দাও",
        "দেন": "দাও",
        "দিন": "দাও",
        "deo": "dao",
        "den": "dao",
        "din": "dao",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


async def save_contact(user):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO contacts
                (user_id, username, name)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    name = EXCLUDED.name
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

    keyboard = [
        [
            InlineKeyboardButton(
                "🎵 গান",
                callback_data="category:🎵 গান",
            ),
            InlineKeyboardButton(
                "🎭 নাটক",
                callback_data="category:🎭 নাটক",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎬 ভিডিও",
                callback_data="category:🎬 ভিডিও",
            ),
            InlineKeyboardButton(
                "📸 ফটো",
                callback_data="category:📸 ফটো",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎥 মুভি",
                callback_data="category:🎥 মুভি",
            ),
        ],
        [
            InlineKeyboardButton(
                "📘 Admin Facebook",
                callback_data="social:facebook",
            ),
            InlineKeyboardButton(
                "🎵 Admin TikTok",
                callback_data="social:tiktok",
            ),
        ],
    ]

    await update.message.reply_text(
        "🔥 *Bangla Vibe*\n\n"
        "স্বাগতম! ভিডিও দেখতে নিচের অপশন ব্যবহার করুন।",
        reply_markup=InlineKeyboardMarkup(keyboard),
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
                "🎬 ভিডিও যোগ",
                callback_data="admin:add_video",
            ),
            InlineKeyboardButton(
                "🗑 ভিডিও ডিলিট",
                callback_data="admin:delete_video",
            ),
        ],
        [
            InlineKeyboardButton(
                "📂 Category যোগ",
                callback_data="admin:add_category",
            ),
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
                "📋 সব Category",
                callback_data="admin:categories",
            ),
            InlineKeyboardButton(
                "📹 সব ভিডিও",
                callback_data="admin:videos",
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
        "এখান থেকে সবকিছু Manage করতে পারবে।",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# =========================================================
# ADMIN STATES
# =========================================================

def set_state(context, state):
    context.user_data["admin_state"] = state


def get_state(context):
    return context.user_data.get("admin_state")


def clear_state(context):
    context.user_data.pop("admin_state", None)
    context.user_data.pop("video_file_id", None)
    context.user_data.pop("video_title", None)
    context.user_data.pop("video_description", None)


# =========================================================
# ADMIN CALLBACKS
# =========================================================

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.message.reply_text(
            "⛔ শুধু Admin এই কাজটি করতে পারবে।"
        )
        return

    action = query.data

    # -----------------------------------------------------
    # ADD VIDEO
    # -----------------------------------------------------

    if action == "admin:add_video":

        clear_state(context)
        set_state(context, "video_file")

        await query.message.reply_text(
            "🎬 ভিডিও পাঠাও।\n\n"
            "ভিডিও পাঠানোর পর আমি নাম, Description এবং Category চাইব।"
        )

    # -----------------------------------------------------
    # DELETE VIDEO
    # -----------------------------------------------------

    elif action == "admin:delete_video":

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title
                FROM videos
                ORDER BY id DESC
                """
            )

        if not rows:
            await query.message.reply_text(
                "📭 কোনো ভিডিও নেই।"
            )
            return

        buttons = []

        for row in rows:
            buttons.append([
                InlineKeyboardButton(
                    f"🗑 {row['title']}",
                    callback_data=f"delete_video:{row['id']}",
                )
            ])

        await query.message.reply_text(
            "🗑 কোন ভিডিও ডিলিট করবে?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    # -----------------------------------------------------
    # ADD CATEGORY
    # -----------------------------------------------------

    elif action == "admin:add_category":

        set_state(context, "add_category")

        await query.message.reply_text(
            "📂 নতুন Category-এর নাম লিখো।\n\n"
            "উদাহরণ: ⚽ খেলাধুলা"
        )

    # -----------------------------------------------------
    # DELETE CATEGORY
    # -----------------------------------------------------

    elif action == "admin:delete_category":

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name
                FROM categories
                ORDER BY id
                """
            )

        if not rows:
            await query.message.reply_text(
                "📭 কোনো Category নেই।"
            )
            return

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

    # -----------------------------------------------------
    # ADD FACEBOOK
    # -----------------------------------------------------

    elif action == "admin:add_facebook":

        set_state(context, "add_facebook")

        await query.message.reply_text(
            "📘 Facebook ID বা Profile Link পাঠাও।"
        )

    # -----------------------------------------------------
    # ADD TIKTOK
    # -----------------------------------------------------

    elif action == "admin:add_tiktok":

        set_state(context, "add_tiktok")

        await query.message.reply_text(
            "🎵 TikTok ID বা Profile Link পাঠাও।"
        )

    # -----------------------------------------------------
    # DELETE FACEBOOK
    # -----------------------------------------------------

    elif action == "admin:delete_facebook":

        await delete_social("facebook", query.message)

    # -----------------------------------------------------
    # DELETE TIKTOK
    # -----------------------------------------------------

    elif action == "admin:delete_tiktok":

        await delete_social("tiktok", query.message)

    # -----------------------------------------------------
    # CATEGORIES
    # -----------------------------------------------------

    elif action == "admin:categories":

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT name
                FROM categories
                ORDER BY id
                """
            )

        if not rows:
            await query.message.reply_text(
                "📭 কোনো Category নেই।"
            )
            return

        text = "📂 *Categories*\n\n"

        for i, row in enumerate(rows, 1):
            text += f"{i}. {row['name']}\n"

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # VIDEOS
    # -----------------------------------------------------

    elif action == "admin:videos":

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title, category
                FROM videos
                ORDER BY id DESC
                """
            )

        if not rows:
            await query.message.reply_text(
                "📭 কোনো ভিডিও নেই।"
            )
            return

        text = "📹 *Videos*\n\n"

        for row in rows:
            text += (
                f"🆔 {row['id']}\n"
                f"🎬 {row['title']}\n"
                f"📂 {row['category']}\n\n"
            )

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
        )

    # -----------------------------------------------------
    # USERS
    # -----------------------------------------------------

    elif action == "admin:users":

        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM contacts"
            )

        await query.message.reply_text(
            f"👥 মোট User/Contact: {count}"
        )


# =========================================================
# SOCIAL FUNCTIONS
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


async def delete_social(platform, message):

    async with db_pool.acquire() as conn:

        result = await conn.execute(
            """
            DELETE FROM social_links
            WHERE platform = $1
            """,
            platform,
        )

    if result.endswith("0"):
        await message.reply_text(
            f"ℹ️ এখনো কোনো {platform.title()} ID/Link যোগ করা হয়নি।"
        )
    else:
        await message.reply_text(
            f"✅ {platform.title()} ID/Link ডিলিট করা হয়েছে।"
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


# =========================================================
# SHOW SOCIAL TO USER
# =========================================================

async def show_social(
    update: Update,
    platform: str,
):

    query = update.callback_query
    await query.answer()

    value = await get_social(platform)

    if value:

        if platform == "facebook":
            title = "📘 Admin Facebook"

        else:
            title = "🎵 Admin TikTok"

        await query.message.reply_text(
            f"{title}\n\n"
            f"{value}"
        )

    else:

        await query.message.reply_text(
            f"😔 দুঃখিত!\n\n"
            f"Admin এখনো কোনো {platform.title()} ID/Link যোগ করেননি।\n"
            f"পরে আবার চেষ্টা করুন। ❤️"
        )


# =========================================================
# DELETE VIDEO
# =========================================================

async def delete_video_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    video_id = int(
        query.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT title
            FROM videos
            WHERE id = $1
            """,
            video_id,
        )

        if not row:
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
        f"✅ ভিডিও ডিলিট হয়েছে:\n{row['title']}"
    )


# =========================================================
# DELETE CATEGORY
# =========================================================

async def delete_category_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        return

    category_id = int(
        query.data.split(":")[1]
    )

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

        # Category-এর ভিডিও আগে মুছছি না।
        # শুধু category delete করছি।
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
# CATEGORY DISPLAY
# =========================================================

async def show_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    category = query.data.split(":", 1)[1]

    async with db_pool.acquire() as conn:

        rows = await conn.fetch(
            """
            SELECT id, title
            FROM videos
            WHERE category = $1
            ORDER BY id DESC
            """,
            category,
        )

    if not rows:

        await query.message.reply_text(
            f"📂 {category}\n\n"
            "😔 এই Category-তে এখনো কোনো ভিডিও নেই।"
        )

        return

    buttons = []

    for row in rows:

        buttons.append([
            InlineKeyboardButton(
                f"▶️ {row['title']}",
                callback_data=f"video:{row['id']}",
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
        "ভিডিও নির্বাচন করুন:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================================================
# SEND VIDEO
# =========================================================

async def send_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query
    await query.answer()

    video_id = int(
        query.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        row = await conn.fetchrow(
            """
            SELECT file_id, title, description, category
            FROM videos
            WHERE id = $1
            """,
            video_id,
        )

    if not row:

        await query.message.reply_text(
            "❌ ভিডিও পাওয়া যায়নি।"
        )

        return

    caption = f"🎬 {row['title']}"

    if row["description"]:
        caption += f"\n\n📝 {row['description']}"

    caption += f"\n\n📂 {row['category']}"

    try:

        await context.bot.send_video(
            chat_id=query.from_user.id,
            video=row["file_id"],
            caption=caption,
        )

    except Exception:
        logger.exception("Video send error")

        await query.message.reply_text(
            "❌ ভিডিও পাঠাতে সমস্যা হয়েছে।"
        )


# =========================================================
# ADMIN NATURAL LANGUAGE
# =========================================================

def is_add_video_text(text):

    text = normalize(text)

    phrases = [
        "ভিডিও দাও",
        "ভিডিও দে",
        "ভিডিও দিন",
        "video dao",
        "video de",
        "video din",
        "add video",
        "add a video",
        "upload video",
        "upload a video",
        "ভিডিও যোগ",
        "ভিডিও যোগ কর",
    ]

    return text in phrases


def is_add_category_text(text):

    text = normalize(text)

    phrases = [
        "category দাও",
        "category dao",
        "add category",
        "নতুন category",
        "ক্যাটাগরি দাও",
        "ক্যাটাগরি যোগ",
        "category যোগ",
    ]

    return text in phrases


def is_add_facebook_text(text):

    text = normalize(text)

    phrases = [
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

    return text in phrases


def is_add_tiktok_text(text):

    text = normalize(text)

    phrases = [
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

    return text in phrases


def is_user_facebook_request(text):

    text = normalize(text)

    phrases = [
        "এডমিন ফেসবুক দাও",
        "admin facebook dao",
        "admin facebook",
        "facebook admin",
        "admin facebook id",
        "এডমিন ফেসবুক আইডি দাও",
        "এডমিন ফেসবুক আইডি",
    ]

    return text in phrases


def is_user_tiktok_request(text):

    text = normalize(text)

    phrases = [
        "এডমিন টিকটক দাও",
        "admin tiktok dao",
        "admin tiktok",
        "tiktok admin",
        "admin tiktok id",
        "এডমিন টিকটক আইডি দাও",
        "এডমিন টিকটক আইডি",
    ]

    return text in phrases


# =========================================================
# TEXT HANDLER
# =========================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user
    text = update.message.text.strip()

    await save_contact(user)

    # -----------------------------------------------------
    # ADMIN STATE
    # -----------------------------------------------------

    if is_admin(user.id):

        state = get_state(context)

        # =============================================
        # VIDEO FILE
        # =============================================

        if state == "video_file":

            await update.message.reply_text(
                "❌ এখানে একটি Telegram Video পাঠাও।"
            )

            return

        # =============================================
        # VIDEO TITLE
        # =============================================

        if state == "video_title":

            context.user_data["video_title"] = text

            set_state(context, "video_description")

            await update.message.reply_text(
                "📝 এখন Description লিখো।\n\n"
                "Description না চাইলে `skip` লিখো।"
            )

            return

        # =============================================
        # VIDEO DESCRIPTION
        # =============================================

        if state == "video_description":

            description = ""

            if text.lower() != "skip":
                description = text

            context.user_data["video_description"] = description

            set_state(context, "video_category")

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
                f"{category_text}\n\n"
                "চাইলে নতুন Category-এর নামও দিতে পারো।"
            )

            return

        # =============================================
        # VIDEO CATEGORY
        # =============================================

        if state == "video_category":

            file_id = context.user_data.get(
                "video_file_id"
            )

            title = context.user_data.get(
                "video_title"
            )

            description = context.user_data.get(
                "video_description",
                "",
            )

            category = text

            if not file_id or not title:

                clear_state(context)

                await update.message.reply_text(
                    "❌ ভিডিওর তথ্য পাওয়া যায়নি। আবার শুরু করো।"
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
                        INSERT INTO videos
                        (file_id, title, description, category)
                        VALUES ($1, $2, $3, $4)
                        """,
                        file_id,
                        title,
                        description,
                        category,
                    )

                clear_state(context)

                await update.message.reply_text(
                    "✅ ভিডিও সফলভাবে Save হয়েছে!\n\n"
                    f"🎬 নাম: {title}\n"
                    f"📂 Category: {category}"
                )

            except Exception as e:

                logger.exception(
                    "VIDEO SAVE ERROR"
                )

                clear_state(context)

                await update.message.reply_text(
                    "❌ ভিডিও Save করতে সমস্যা হয়েছে।\n\n"
                    "Database connection বা তথ্যের কোনো সমস্যা হয়েছে।"
                )

            return

        # =============================================
        # ADD CATEGORY
        # =============================================

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
                    f"✅ নতুন Category যোগ হয়েছে:\n{text}"
                )

            except Exception:
                logger.exception(
                    "ADD CATEGORY ERROR"
                )

                clear_state(context)

                await update.message.reply_text(
                    "❌ Category যোগ করতে সমস্যা হয়েছে।"
                )

            return

        # =============================================
        # FACEBOOK
        # =============================================

        if state == "add_facebook":

            try:

                await save_social(
                    "facebook",
                    text,
                )

                clear_state(context)

                await update.message.reply_text(
                    "✅ Admin Facebook ID/Link Save হয়েছে।\n\n"
                    f"📘 {text}"
                )

            except Exception:
                logger.exception(
                    "FACEBOOK SAVE ERROR"
                )

                clear_state(context)

                await update.message.reply_text(
                    "❌ Facebook ID Save করতে সমস্যা হয়েছে।"
                )

            return

        # =============================================
        # TIKTOK
        # =============================================

        if state == "add_tiktok":

            try:

                await save_social(
                    "tiktok",
                    text,
                )

                clear_state(context)

                await update.message.reply_text(
                    "✅ Admin TikTok ID/Link Save হয়েছে।\n\n"
                    f"🎵 {text}"
                )

            except Exception:
                logger.exception(
                    "TIKTOK SAVE ERROR"
                )

                clear_state(context)

                await update.message.reply_text(
                    "❌ TikTok ID Save করতে সমস্যা হয়েছে।"
                )

            return

        # =============================================
        # ADMIN NATURAL COMMANDS
        # =============================================

        if is_add_video_text(text):

            clear_state(context)

            set_state(
                context,
                "video_file",
            )

            await update.message.reply_text(
                "🎬 ভিডিও পাঠাও।"
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
                "📘 Facebook ID বা Profile Link পাঠাও।"
            )

            return

        if is_add_tiktok_text(text):

            set_state(
                context,
                "add_tiktok",
            )

            await update.message.reply_text(
                "🎵 TikTok ID বা Profile Link পাঠাও।"
            )

            return

    # -----------------------------------------------------
    # USER SOCIAL REQUESTS
    # -----------------------------------------------------

    if is_user_facebook_request(text):

        value = await get_social("facebook")

        if value:

            await update.message.reply_text(
                f"📘 Admin Facebook:\n\n{value}"
            )

        else:

            await update.message.reply_text(
                "😔 দুঃখিত!\n\n"
                "Admin এখনো কোনো Facebook ID/Link যোগ করেননি।\n"
                "পরে আবার চেষ্টা করুন। ❤️"
            )

        return

    if is_user_tiktok_request(text):

        value = await get_social("tiktok")

        if value:

            await update.message.reply_text(
                f"🎵 Admin TikTok:\n\n{value}"
            )

        else:

            await update.message.reply_text(
                "😔 দুঃখিত!\n\n"
                "Admin এখনো কোনো TikTok ID/Link যোগ করেননি।\n"
                "পরে আবার চেষ্টা করুন। ❤️"
            )

        return

    # -----------------------------------------------------
    # NORMAL USER
    # -----------------------------------------------------

    await update.message.reply_text(
        "😊 আমি বুঝতে পারিনি।\n\n"
        "ভিডিও দেখতে /start চাপুন।"
    )


# =========================================================
# VIDEO MESSAGE HANDLER
# =========================================================

async def video_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not is_admin(user.id):

        await update.message.reply_text(
            "😊 ভিডিও যোগ করার অনুমতি শুধু Admin-এর আছে।"
        )

        return

    state = get_state(context)

    if state != "video_file":

        await update.message.reply_text(
            "ℹ️ ভিডিও যোগ করতে আগে লিখুন:\n\n"
            "ভিডিও দাও"
        )

        return

    context.user_data["video_file_id"] = (
        update.message.video.file_id
    )

    set_state(
        context,
        "video_title",
    )

    await update.message.reply_text(
        "✅ ভিডিও পাওয়া গেছে!\n\n"
        "🎬 এখন ভিডিওর নাম লিখো।"
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

    if data.startswith("category:"):
        await show_category(
            update,
            context,
        )

    elif data.startswith("video:"):
        await send_video(
            update,
            context,
        )

    elif data.startswith("social:"):

        platform = data.split(":", 1)[1]

        await show_social(
            update,
            platform,
        )

    elif data.startswith("delete_video:"):

        await delete_video_callback(
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
            "ভিডিও দেখতে /start চাপুন।"
        )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.error(
        "Telegram error: %s",
        context.error,
        exc_info=context.error,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    # Render web server
    web_thread = Thread(
        target=run_web_server,
        daemon=True,
    )

    web_thread.start()

    logger.info(
        "🌐 Render web server started"
    )

    # Telegram
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
            admin_panel,
        )
    )

    # Video message
    application.add_handler(
        MessageHandler(
            filters.VIDEO,
            video_message,
        )
    )

    # Callback buttons
    application.add_handler(
        CallbackQueryHandler(
            callback_router
        )
    )

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )

    # Errors
    application.add_error_handler(
        error_handler
    )

    logger.info(
        "🤖 Bangla Vibe Bot started"
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application: Application,
):

    await init_db()

    logger.info(
        "🔥 Database initialized successfully"
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()

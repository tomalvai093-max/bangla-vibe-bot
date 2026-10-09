#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Viral Zone 🔥 — Telegram Bot & Mini App Backend
=================================================
- Telegram Bot: python-telegram-bot (v20+)
- Database: PostgreSQL (asyncpg)
- Web Server / API: aiohttp (serves Mini App & JSON APIs on Render $PORT)
- Admin ID: 8721334265
- Persistent Reply Button: "▶️ Open Video 🔥" (WebAppInfo)
- Full Admin Panel: Media Upload (Video/Photo/Audio), Categories, Content Delete, Stats
"""

import os
import sys
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

from aiohttp import web, ClientSession
import asyncpg
from dotenv import load_dotenv

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    MenuButtonWebApp,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Load environment variables
load_dotenv()

# Logging Configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("ViralZone")

# Environment & Config
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PORT = int(os.getenv("PORT", "8080"))
MINI_APP_URL = os.getenv(
    "MINI_APP_URL",
    "https://bangla-vibe-bot.onrender.com"
).strip()

# Admin Telegram ID (Fixed to specified ID with env fallback)
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "8721334265").strip()
try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    ADMIN_ID = 8721334265

# Fix postgres:// URL prefix for asyncpg
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Global Database Pool
db_pool: Optional[asyncpg.Pool] = None

# Conversation States for Admin Workflow
(
    WAITING_MEDIA_FILE,
    WAITING_THUMBNAIL,
    WAITING_TITLE,
    WAITING_DESCRIPTION,
    WAITING_CATEGORY,
    WAITING_NEW_CATEGORY_NAME,
    WAITING_DELETE_CONTENT_ID,
) = range(7)

# Temporary memory for admin multi-step uploads
upload_cache: Dict[int, Dict[str, Any]] = {}

# Default Categories
DEFAULT_CATEGORIES = [
    {"name": "🎵 গান", "icon": "🎵"},
    {"name": "🎭 নাটক", "icon": "🎭"},
    {"name": "🎬 ভিডিও", "icon": "🎬"},
    {"name": "📸 ফটো", "icon": "📸"},
    {"name": "🎥 মুভি", "icon": "🎥"},
]


# ==============================================================================
# Database Initialization & Helpers (Safe & Backward-Compatible)
# ==============================================================================

async def init_database():
    """
    Initializes PostgreSQL connection pool and safely updates schema without dropping existing tables.
    Includes explicit fix for:
    'NotNullViolationError: null value in column "name" of relation "contacts" violates not-null constraint'
    """
    global db_pool
    if not DATABASE_URL:
        logger.warning("DATABASE_URL is not provided! Using in-memory fallback for local preview.")
        return

    try:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            command_timeout=60,
        )
        logger.info("Connected to PostgreSQL successfully.")

        async with db_pool.acquire() as conn:
            # 1. Contacts / Users Table (Safely create or align columns)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id BIGSERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE,
                    name TEXT NOT NULL DEFAULT 'Viral Zone User',
                    username TEXT,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Ensure 'telegram_id' column exists if contacts was an older custom table
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='contacts' AND column_name='telegram_id'
                    ) THEN
                        ALTER TABLE contacts ADD COLUMN telegram_id BIGINT UNIQUE;
                    END IF;
                END $$;
            """)

            # Ensure 'name' column exists and has default
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='contacts' AND column_name='name'
                    ) THEN
                        ALTER TABLE contacts ADD COLUMN name TEXT NOT NULL DEFAULT 'User';
                    ELSE
                        ALTER TABLE contacts ALTER COLUMN name SET DEFAULT 'User';
                    END IF;
                END $$;
            """)

            # 2. Categories Table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    icon TEXT DEFAULT '🎬',
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Seed default categories if none exist
            for cat in DEFAULT_CATEGORIES:
                await conn.execute("""
                    INSERT INTO categories (name, icon)
                    VALUES ($1, $2)
                    ON CONFLICT (name) DO NOTHING;
                """, cat["name"], cat["icon"])

            # 3. Contents Table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS contents (
                    id SERIAL PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    category TEXT NOT NULL,
                    thumbnail_file_id TEXT,
                    views INT DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Add optional views column if missing
            await conn.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='contents' AND column_name='views'
                    ) THEN
                        ALTER TABLE contents ADD COLUMN views INT DEFAULT 0;
                    END IF;
                END $$;
            """)

            logger.info("Database schema verified and ready.")

    except Exception as e:
        logger.error(f"Error during database initialization: {e}", exc_info=True)


async def save_or_update_contact(user) -> bool:
    """
    Saves user to the contacts table safely without triggering NotNullViolationError.
    Guarantees 'name' is NEVER NULL.
    """
    if not db_pool:
        return False

    # Extract best available name
    full_name = ""
    if hasattr(user, "full_name") and user.full_name:
        full_name = user.full_name.strip()
    elif hasattr(user, "first_name") and user.first_name:
        full_name = user.first_name.strip()
    elif hasattr(user, "username") and user.username:
        full_name = f"@{user.username}"

    if not full_name:
        full_name = f"User_{user.id}"

    username = user.username or ""

    try:
        async with db_pool.acquire() as conn:
            # Query table columns to adapt to existing schema
            col_records = await conn.fetch("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'contacts';
            """)
            col_names = [r["column_name"] for r in col_records]

            if "telegram_id" in col_names:
                await conn.execute("""
                    INSERT INTO contacts (telegram_id, name, username, created_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (telegram_id) DO UPDATE
                    SET name = EXCLUDED.name,
                        username = EXCLUDED.username;
                """, user.id, full_name, username)
            else:
                # If old contacts table had 'id' as the telegram ID
                await conn.execute("""
                    INSERT INTO contacts (id, name, username, created_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (id) DO UPDATE
                    SET name = EXCLUDED.name,
                        username = EXCLUDED.username;
                """, user.id, full_name, username)

            return True
    except Exception as e:
        logger.error(f"Failed to record contact for user {user.id}: {e}")
        return False


async def get_all_categories() -> List[str]:
    """Returns list of all category names from database."""
    if not db_pool:
        return [c["name"] for c in DEFAULT_CATEGORIES]
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT name FROM categories ORDER BY id ASC;")
            return [r["name"] for r in rows] if rows else [c["name"] for c in DEFAULT_CATEGORIES]
    except Exception as e:
        logger.error(f"Error fetching categories: {e}")
        return [c["name"] for c in DEFAULT_CATEGORIES]


# ==============================================================================
# Telegram Bot Handlers
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /start command:
    - Saves user safely to contacts table
    - Sets persistent Reply Keyboard button '▶️ Open Video 🔥'
    - Configures chat Menu Button
    - Sends welcoming Bengali message
    """
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Record contact safely
    await save_or_update_contact(user)

    # 1. Permanent Reply Keyboard Button
    keyboard = [
        [
            KeyboardButton(
                text="▶️ Open Video 🔥",
                web_app=WebAppInfo(url=MINI_APP_URL)
            )
        ]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )

    # 2. Set Telegram Chat Menu Button
    try:
        await context.bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(
                text="▶️ Viral Zone 🔥",
                web_app=WebAppInfo(url=MINI_APP_URL)
            )
        )
    except Exception as e:
        logger.debug(f"Could not set menu button: {e}")

    welcome_text = (
        f"🔥 *স্বাগতম, {user.first_name or 'বন্ধু'}!*\n\n"
        "✨ *Viral Zone* — আপনার বিনোদনের প্রিমিয়াম প্ল্যাটফর্ম।\n\n"
        "🎬 এখানে আপনি পাবেন:\n"
        "• এক্সক্লুসিভ ভাইরাল ভিডিও\n"
        "• জনপ্রিয় বাংলা নাটক ও গান\n"
        "• এইচডি ফটো ও নতুন মুভি কালেকশন\n\n"
        "👇 *নিচের বাটনে চাপ দিয়ে সরাসরি উপভোগ করুন:*"
    )

    await update.message.reply_text(
        text=welcome_text,
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles /admin command:
    - Verifies user is Admin ID 8721334265
    - If non-admin, denies access
    - If admin, opens admin management panel
    """
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ এই কমান্ড শুধু Admin-এর জন্য।")
        return

    admin_keyboard = [
        [
            InlineKeyboardButton("📤 ভিডিও আপলোড", callback_data="admin_upload_video"),
            InlineKeyboardButton("📸 ফটো আপলোড", callback_data="admin_upload_photo"),
        ],
        [
            InlineKeyboardButton("🎵 অডিও আপলোড", callback_data="admin_upload_audio"),
            InlineKeyboardButton("🏷️ নতুন ক্যাটাগরি", callback_data="admin_new_category"),
        ],
        [
            InlineKeyboardButton("📋 কনটেন্ট তালিকা", callback_data="admin_list_contents"),
            InlineKeyboardButton("🗑️ কনটেন্ট ডিলিট", callback_data="admin_delete_content"),
        ],
        [
            InlineKeyboardButton("📊 মোট পরিসংখ্যান", callback_data="admin_stats"),
            InlineKeyboardButton("🌐 Mini App লিংক", callback_data="admin_app_info"),
        ],
        [
            InlineKeyboardButton("📢 চ্যানেলে পোস্ট ও লিঙ্ক", callback_data="admin_channel_share"),
            InlineKeyboardButton("🔙 মেনু বন্ধ করুন", callback_data="admin_close"),
        ]
    ]

    await update.message.reply_text(
        "⚡ *Viral Zone — অ্যাডমিন কন্ট্রোল প্যানেল*\n\n"
        "আপনার প্রয়োজনীয় অ্যাকশনটি নির্বাচন করুন:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(admin_keyboard),
    )


# ==============================================================================
# Admin Upload Conversation Flow
# ==============================================================================

async def admin_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered by clicking Upload Video, Photo, or Audio."""
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ অননুমোদিত অ্যাকশন।")
        return ConversationHandler.END

    media_type = query.data.replace("admin_upload_", "")
    upload_cache[query.from_user.id] = {
        "media_type": media_type,
        "file_id": None,
        "thumbnail_file_id": None,
        "title": "",
        "description": "",
        "category": "",
    }

    type_labels = {
        "video": "ভিডিও ফাইল বা ফরোয়ার্ড করা ভিডিও",
        "photo": "ছবি/ফটো",
        "audio": "গান/অডিও ফাইল",
    }
    label = type_labels.get(media_type, "মিডিয়া ফাইল")

    cancel_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ বাতিল করুন", callback_data="admin_cancel_flow")]
    ])

    await query.edit_message_text(
        f"📤 *ধাপ ১: {label} পাঠান*\n\n"
        f"দয়া করে আপনার {label} এই চ্যাটে পাঠান:",
        parse_mode="Markdown",
        reply_markup=cancel_btn,
    )
    return WAITING_MEDIA_FILE


async def admin_received_media_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives the video, photo, or audio file from admin."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID or user_id not in upload_cache:
        return ConversationHandler.END

    message = update.message
    cache = upload_cache[user_id]
    media_type = cache["media_type"]

    file_id = None
    thumbnail_id = None

    if media_type == "video" and message.video:
        file_id = message.video.file_id
        if message.video.thumbnail:
            thumbnail_id = message.video.thumbnail.file_id
    elif media_type == "photo" and message.photo:
        file_id = message.photo[-1].file_id
    elif media_type == "audio" and (message.audio or message.voice):
        file_id = (message.audio or message.voice).file_id
    elif message.document:
        # Fallback for video/audio sent as document
        file_id = message.document.file_id
        if message.document.thumbnail:
            thumbnail_id = message.document.thumbnail.file_id

    if not file_id:
        await message.reply_text(
            f"⚠️ সঠিক {media_type} পাওয়া যায়নি। অনুগ্রহ করে পুনরায় ফাইলটি পাঠান অথবা /admin দিন।"
        )
        return WAITING_MEDIA_FILE

    cache["file_id"] = file_id
    cache["thumbnail_file_id"] = thumbnail_id

    skip_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ থাম্বনেইল স্কিপ করুন", callback_data="skip_thumbnail")],
        [InlineKeyboardButton("❌ বাতিল", callback_data="admin_cancel_flow")]
    ])

    await message.reply_text(
        "✅ ফাইল গৃহীত হয়েছে!\n\n"
        "🖼️ *ধাপ ২: কাস্টম থাম্বনেইল ফটো পাঠান*\n"
        "(যদি প্রয়োজন না হয় তবে 'স্কিপ' বাটনে চাপ দিন):",
        parse_mode="Markdown",
        reply_markup=skip_btn,
    )
    return WAITING_THUMBNAIL


async def admin_received_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives custom thumbnail photo."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID or user_id not in upload_cache:
        return ConversationHandler.END

    if update.message.photo:
        upload_cache[user_id]["thumbnail_file_id"] = update.message.photo[-1].file_id

    cancel_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ বাতিল", callback_data="admin_cancel_flow")]
    ])

    await update.message.reply_text(
        "📝 *ধাপ ৩: কনটেন্টের নাম (Title) লিখুন:*\n\n"
        "একটি সুন্দর আকর্ষণীয় শিরোনাম লিখে মেসেজ পাঠান:",
        parse_mode="Markdown",
        reply_markup=cancel_btn,
    )
    return WAITING_TITLE


async def admin_skip_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin chose to skip custom thumbnail."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id != ADMIN_ID or user_id not in upload_cache:
        return ConversationHandler.END

    cancel_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ বাতিল", callback_data="admin_cancel_flow")]
    ])

    await query.edit_message_text(
        "📝 *ধাপ ৩: কনটেন্টের নাম (Title) লিখুন:*\n\n"
        "একটি সুন্দর আকর্ষণীয় শিরোনাম লিখে মেসেজ পাঠান:",
        parse_mode="Markdown",
        reply_markup=cancel_btn,
    )
    return WAITING_TITLE


async def admin_received_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives content title."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID or user_id not in upload_cache:
        return ConversationHandler.END

    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("⚠️ শিরোনাম খালি হতে পারে না। নাম লিখে পাঠান:")
        return WAITING_TITLE

    upload_cache[user_id]["title"] = title

    skip_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ বিবরণ স্কিপ করুন", callback_data="skip_description")],
        [InlineKeyboardButton("❌ বাতিল", callback_data="admin_cancel_flow")]
    ])

    await update.message.reply_text(
        "📄 *ধাপ ৪: কনটেন্টের বিবরণ (Description) লিখুন:*\n\n"
        "বিবরণ লিখে পাঠান অথবা 'স্কিপ' বাটনে চাপ দিন:",
        parse_mode="Markdown",
        reply_markup=skip_btn,
    )
    return WAITING_DESCRIPTION


async def admin_received_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives content description."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID or user_id not in upload_cache:
        return ConversationHandler.END

    desc = update.message.text.strip()
    upload_cache[user_id]["description"] = desc

    return await present_category_selection(update.message, user_id)


async def admin_skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin skips description."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id != ADMIN_ID or user_id not in upload_cache:
        return ConversationHandler.END

    upload_cache[user_id]["description"] = ""
    return await present_category_selection(query, user_id)


async def present_category_selection(target, user_id: int):
    """Displays category selection inline keyboard."""
    categories = await get_all_categories()

    buttons = []
    row = []
    for cat in categories:
        row.append(InlineKeyboardButton(cat, callback_data=f"sel_cat_{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("❌ বাতিল", callback_data="admin_cancel_flow")])
    reply_markup = InlineKeyboardMarkup(buttons)

    text = (
        "🏷️ *ধাপ ৫: ক্যাটাগরি নির্বাচন করুন:*\n\n"
        "কোন ক্যাটাগরিতে এই কনটেন্ট যুক্ত করতে চান?"
    )

    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

    return WAITING_CATEGORY


async def admin_selected_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves completed content to database."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id != ADMIN_ID or user_id not in upload_cache:
        await query.edit_message_text("⚠️ সেশন মেয়াদোত্তীর্ণ হয়েছে। পুনরায় শুরু করতে /admin দিন।")
        return ConversationHandler.END

    category_name = query.data.replace("sel_cat_", "")
    cache = upload_cache.pop(user_id)

    # Save to PostgreSQL
    content_id = None
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    INSERT INTO contents 
                    (file_id, media_type, title, description, category, thumbnail_file_id, views, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, 0, NOW())
                    RETURNING id;
                """,
                    cache["file_id"],
                    cache["media_type"],
                    cache["title"],
                    cache["description"],
                    category_name,
                    cache["thumbnail_file_id"]
                )
                if row:
                    content_id = row["id"]
        except Exception as e:
            logger.error(f"Failed to insert content into database: {e}", exc_info=True)

    success_text = (
        "🎉 *কনটেন্ট সফলভাবে যুক্ত হয়েছে!*\n\n"
        f"🆔 *ID:* `{content_id or 'SAVED'}`\n"
        f"📌 *শিরোনাম:* {cache['title']}\n"
        f"🏷️ *ক্যাটাগরি:* {category_name}\n"
        f"📂 *টাইপ:* {cache['media_type'].capitalize()}\n\n"
        "Mini App-এ এটি এখন সবার জন্য দৃশ্যমান। 🔥"
    )

    await query.edit_message_text(success_text, parse_mode="Markdown")
    return ConversationHandler.END


async def admin_cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels any admin multi-step flow."""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
        upload_cache.pop(user_id, None)
        await query.edit_message_text("❌ অপারেশন বাতিল করা হয়েছে।")
    return ConversationHandler.END


# ==============================================================================
# Category Creation Flow
# ==============================================================================

async def admin_new_category_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts admin for new category name."""
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    cancel_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ বাতিল", callback_data="admin_cancel_flow")]
    ])

    await query.edit_message_text(
        "🏷️ *নতুন ক্যাটাগরি তৈরি করুন*\n\n"
        "ক্যাটাগরির নাম ও ইমোজি লিখে পাঠান (যেমন: 🍿 কমেডি):",
        parse_mode="Markdown",
        reply_markup=cancel_btn,
    )
    return WAITING_NEW_CATEGORY_NAME


async def admin_save_new_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves new category to database."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return ConversationHandler.END

    cat_name = update.message.text.strip()
    if not cat_name:
        await update.message.reply_text("⚠️ ক্যাটাগরির নাম খালি হতে পারে না।")
        return WAITING_NEW_CATEGORY_NAME

    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO categories (name, icon)
                    VALUES ($1, '🎬')
                    ON CONFLICT (name) DO NOTHING;
                """, cat_name)
        except Exception as e:
            logger.error(f"Error creating category: {e}")

    await update.message.reply_text(
        f"✅ ক্যাটাগরি *{cat_name}* সফলভাবে তৈরি হয়েছে!",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ==============================================================================
# Content Delete Flow
# ==============================================================================

async def admin_delete_content_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompts admin for ID of content to delete."""
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    cancel_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ বাতিল", callback_data="admin_cancel_flow")]
    ])

    await query.edit_message_text(
        "🗑️ *কনটেন্ট ডিলিট করুন*\n\n"
        "যে কনটেন্টটি ডিলিট করতে চান তার সংখ্যাসূচক *ID* লিখে পাঠান:",
        parse_mode="Markdown",
        reply_markup=cancel_btn,
    )
    return WAITING_DELETE_CONTENT_ID


async def admin_execute_delete_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes specified content by ID from database."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return ConversationHandler.END

    text = update.message.text.strip()
    try:
        content_id = int(text)
    except ValueError:
        await update.message.reply_text("⚠️ অনুগ্রহ করে শুধুমাত্র সংখ্যাসূচক ID পাঠান (যেমন: 5):")
        return WAITING_DELETE_CONTENT_ID

    deleted = False
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                res = await conn.execute("DELETE FROM contents WHERE id = $1;", content_id)
                if "DELETE 1" in res:
                    deleted = True
        except Exception as e:
            logger.error(f"Error deleting content {content_id}: {e}")

    if deleted:
        await update.message.reply_text(f"✅ কনটেন্ট ID `{content_id}` সফলভাবে মুছে ফেলা হয়েছে।", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ ID `{content_id}` এর কোনো কনটেন্ট পাওয়া যায়নি।", parse_mode="Markdown")

    return ConversationHandler.END


# ==============================================================================
# Admin Stats & Listings
# ==============================================================================

async def admin_list_contents_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays last 10 contents with their IDs."""
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    text = "📋 *সর্বশেষ কনটেন্ট তালিকা:*\n\n"
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, title, category, media_type, views, created_at 
                    FROM contents 
                    ORDER BY id DESC 
                    LIMIT 10;
                """)
                if rows:
                    for r in rows:
                        text += f"🔹 *ID:* `{r['id']}` | {r['title']}\n"
                        text += f"   🏷️ {r['category']} | 📂 {r['media_type']} | 👁️ {r['views'] or 0} views\n\n"
                else:
                    text += "কোনো কনটেন্ট পাওয়া যায়নি।"
        except Exception as e:
            text += f"ডেটাবেস ত্রুটি: {e}"
    else:
        text += "ডেটাবেস সংযোগ সক্রিয় নয়।"

    back_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data="admin_return")]
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back_btn)


async def admin_stats_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays real-time database counts."""
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    total_users = 0
    total_contents = 0
    cat_summary = ""

    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                users_row = await conn.fetchrow("SELECT COUNT(*) as count FROM contacts;")
                if users_row:
                    total_users = users_row["count"]

                contents_row = await conn.fetchrow("SELECT COUNT(*) as count FROM contents;")
                if contents_row:
                    total_contents = contents_row["count"]

                cat_rows = await conn.fetch("""
                    SELECT category, COUNT(*) as count 
                    FROM contents 
                    GROUP BY category 
                    ORDER BY count DESC;
                """)
                for r in cat_rows:
                    cat_summary += f"  • {r['category']}: {r['count']} টি\n"
        except Exception as e:
            logger.error(f"Error fetching stats: {e}")

    stats_text = (
        "📊 *Viral Zone রিয়েল-টাইম পরিসংখ্যান*\n\n"
        f"👥 *মোট গ্রাহক (Contacts):* {total_users} জন\n"
        f"🎬 *মোট কনটেন্ট:* {total_contents} টি\n\n"
        f"📂 *ক্যাটাগরি অনুযায়ী:* \n{cat_summary or '  তথ্য নেই'}\n"
        f"🌐 *Mini App URL:* `{MINI_APP_URL}`\n"
    )

    back_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data="admin_return")]
    ])
    await query.edit_message_text(stats_text, parse_mode="Markdown", reply_markup=back_btn)


async def admin_app_info_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays Mini App configuration and BotFather guidelines."""
    query = update.callback_query
    await query.answer()

    info_text = (
        "🌐 *Telegram Mini App কনফিগারেশন*\n\n"
        f"🔗 *বর্তমান URL:* `{MINI_APP_URL}`\n\n"
        "💡 *BotFather সেটিংস টিপস:*\n"
        "1. @BotFather এ যান\n"
        "2. `/mybots` -> আপনার বট নির্বাচন করুন\n"
        "3. *Bot Settings* -> *Menu Button* -> *Configure Menu Button*\n"
        f"4. URL সেট করুন: `{MINI_APP_URL}`\n"
        "5. বাটন নাম দিন: `▶️ Open Video 🔥`\n"
    )
    back_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data="admin_return")]
    ])
    await query.edit_message_text(info_text, parse_mode="Markdown", reply_markup=back_btn)


async def admin_channel_share_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays instructions and direct links for using Mini App in Telegram Channels."""
    query = update.callback_query
    await query.answer()

    bot_info = await context.bot.get_me()
    bot_username = bot_info.username or "BanglaVibeBot"

    share_text = (
        "📢 *টেলিগ্রাম চ্যানেলে Viral Zone Mini App যুক্ত করার নিয়ম:*\n\n"
        "১️⃣ *পদ্ধতি ১: চ্যানেলের পোস্টে বাটন যুক্ত করা*\n"
        "আপনার চ্যানেলের পোস্ট বা ভিডিও প্রিভিউর নিচে বাটন দিতে পারেন:\n"
        f"• বাটন নাম: `▶️ Open Video 🔥`\n"
        f"• লিংক: `https://t.me/{bot_username}?startapp=channel`\n"
        "(মেম্বাররা বাটনে ক্লিক করলে সরাসরি চ্যানেলের ভেতরেই মিনি অ্যাপ খুলবে!)\n\n"
        "২️⃣ *পদ্ধতি ২: BotFather দিয়ে Direct Mini App লিংক তৈরি*\n"
        "• @BotFather এ যান -> `/newapp` দিন\n"
        f"• বট সিলেক্ট করে নাম দিন: `Viral Zone`\n"
        f"• URL দিন: `{MINI_APP_URL}`\n"
        "• শর্টনেম দিন (যেমন: `app`)\n"
        f"👉 সরাসরি চ্যানেলে দেওয়ার লিংক হবে: `t.me/{bot_username}/app`\n\n"
        "৩️⃣ *পদ্ধতি ৩: চ্যানেলের Bio ও পিন করা মেসেজে লিংক*\n"
        f"চ্যানেল ডেসক্রিপশনে যুক্ত করুন:\n`🔥 সব ভাইরাল ভিডিও দেখতে: t.me/{bot_username}?startapp`\n"
    )

    share_btn = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 চ্যানেলে টেস্ট পোস্ট পাঠান", switch_inline_query="🔥 সেরা ভাইরাল ভিডিও দেখতে ক্লিক করুন!")
        ],
        [
            InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data="admin_return")
        ]
    ])

    await query.edit_message_text(share_text, parse_mode="Markdown", reply_markup=share_btn)


async def admin_return_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Returns to admin main menu."""
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return

    admin_keyboard = [
        [
            InlineKeyboardButton("📤 ভিডিও আপলোড", callback_data="admin_upload_video"),
            InlineKeyboardButton("📸 ফটো আপলোড", callback_data="admin_upload_photo"),
        ],
        [
            InlineKeyboardButton("🎵 অডিও আপলোড", callback_data="admin_upload_audio"),
            InlineKeyboardButton("🏷️ নতুন ক্যাটাগরি", callback_data="admin_new_category"),
        ],
        [
            InlineKeyboardButton("📋 কনটেন্ট তালিকা", callback_data="admin_list_contents"),
            InlineKeyboardButton("🗑️ কনটেন্ট ডিলিট", callback_data="admin_delete_content"),
        ],
        [
            InlineKeyboardButton("📊 মোট পরিসংখ্যান", callback_data="admin_stats"),
            InlineKeyboardButton("🌐 Mini App লিংক", callback_data="admin_app_info"),
        ],
        [
            InlineKeyboardButton("📢 চ্যানেলে পোস্ট ও লিঙ্ক", callback_data="admin_channel_share"),
            InlineKeyboardButton("🔙 মেনু বন্ধ করুন", callback_data="admin_close"),
        ]
    ]

    await query.edit_message_text(
        "⚡ *Viral Zone — অ্যাডমিন কন্ট্রোল প্যানেল*\n\n"
        "আপনার প্রয়োজনীয় অ্যাকশনটি নির্বাচন করুন:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(admin_keyboard),
    )


async def admin_close_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Closes admin inline menu."""
    query = update.callback_query
    await query.answer()
    await query.delete_message()


# ==============================================================================
# aiohttp Web Server & REST API Endpoints
# ==============================================================================

async def serve_index(request: web.Request):
    """Serves standalone_mini_app.html or index.html for Telegram Mini App."""
    for filename in ["standalone_mini_app.html", "index.html"]:
        index_path = os.path.join(os.path.dirname(__file__), filename)
        if os.path.exists(index_path):
            return web.FileResponse(index_path)
    return web.Response(
        text="<h1>Viral Zone 🔥 Mini App is ready!</h1>",
        content_type="text/html",
    )


async def handle_health(request: web.Request):
    """Health check endpoint for Render."""
    return web.json_response({
        "status": "ok",
        "app": "Viral Zone 🔥",
        "timestamp": datetime.utcnow().isoformat(),
        "database": "connected" if db_pool else "in-memory-fallback",
    })


async def handle_categories_api(request: web.Request):
    """Returns category list in JSON."""
    categories = await get_all_categories()
    return web.json_response({
        "success": True,
        "categories": categories,
    }, headers={"Access-Control-Allow-Origin": "*"})


async def handle_contents_api(request: web.Request):
    """
    Returns paginated contents (5 per page) with optional category filter.
    Query params: category, page (default 1), limit (default 5)
    """
    category = request.query.get("category", "").strip()
    try:
        page = max(1, int(request.query.get("page", "1")))
    except ValueError:
        page = 1

    try:
        limit = max(1, min(20, int(request.query.get("limit", "5"))))
    except ValueError:
        limit = 5

    offset = (page - 1) * limit
    items = []
    total_items = 0

    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                if category and category != "all" and category != "সব":
                    count_row = await conn.fetchrow(
                        "SELECT COUNT(*) as count FROM contents WHERE category = $1;",
                        category,
                    )
                    rows = await conn.fetch("""
                        SELECT id, file_id, media_type, title, description, category, 
                               thumbnail_file_id, views, created_at
                        FROM contents 
                        WHERE category = $1 
                        ORDER BY id DESC 
                        LIMIT $2 OFFSET $3;
                    """, category, limit, offset)
                else:
                    count_row = await conn.fetchrow("SELECT COUNT(*) as count FROM contents;")
                    rows = await conn.fetch("""
                        SELECT id, file_id, media_type, title, description, category, 
                               thumbnail_file_id, views, created_at
                        FROM contents 
                        ORDER BY id DESC 
                        LIMIT $1 OFFSET $2;
                    """, limit, offset)

                total_items = count_row["count"] if count_row else 0
                for r in rows:
                    items.append({
                        "id": r["id"],
                        "file_id": r["file_id"],
                        "media_type": r["media_type"],
                        "title": r["title"],
                        "description": r["description"] or "",
                        "category": r["category"],
                        "thumbnail_file_id": r["thumbnail_file_id"],
                        "views": r["views"] or 0,
                        "created_at": r["created_at"].isoformat() if r["created_at"] else "",
                        "stream_url": f"/api/media/{r['file_id']}",
                    })
        except Exception as e:
            logger.error(f"Error fetching contents: {e}", exc_info=True)

    total_pages = max(1, (total_items + limit - 1) // limit) if total_items > 0 else 1

    return web.json_response({
        "success": True,
        "page": page,
        "limit": limit,
        "total_items": total_items,
        "total_pages": total_pages,
        "items": items,
    }, headers={"Access-Control-Allow-Origin": "*"})


async def handle_media_stream_api(request: web.Request):
    """
    Proxy endpoint: Resolves Telegram file_id to streaming URL safely without exposing BOT_TOKEN to frontend.
    """
    file_id = request.match_info.get("file_id", "").strip()
    if not file_id or not BOT_TOKEN:
        return web.Response(status=404, text="Media not found")

    try:
        telegram_api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}"
        async with ClientSession() as session:
            async with session.get(telegram_api_url) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    return web.Response(status=404, text="Telegram file lookup failed")
                file_path = data["result"]["file_path"]
                download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                raise web.HTTPFound(download_url)
    except web.HTTPFound as redirect:
        raise redirect
    except Exception as e:
        logger.error(f"Media stream proxy error: {e}")
        return web.Response(status=500, text="Internal streaming error")


def create_web_application() -> web.Application:
    """Builds and configures the aiohttp Web Application."""
    app = web.Application()
    app.router.add_get("/", serve_index)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/categories", handle_categories_api)
    app.router.add_get("/api/contents", handle_contents_api)
    app.router.add_get("/api/media/{file_id}", handle_media_stream_api)
    return app


# ==============================================================================
# Main Application Runner
# ==============================================================================

async def main():
    """
    Main entry point:
    1. Initializes PostgreSQL database connection
    2. Starts aiohttp web server on Render PORT
    3. Starts python-telegram-bot application with polling
    """
    logger.info("Starting Viral Zone 🔥 Backend...")

    # 1. Database
    await init_database()

    # 2. Web Server
    web_app = create_web_application()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server is running on http://0.0.0.0:{PORT}")

    # 3. Telegram Bot Application
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN is missing! Set BOT_TOKEN in Render Environment Variables.")
        # Keep web server running even if BOT_TOKEN is missing so Render health check succeeds!
        while True:
            await asyncio.sleep(3600)

    bot_app = Application.builder().token(BOT_TOKEN).build()

    # Admin Conversation Handlers for Upload
    upload_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_upload_start, pattern=r"^admin_upload_(video|photo|audio)$"),
        ],
        states={
            WAITING_MEDIA_FILE: [
                MessageHandler(filters.VIDEO | filters.PHOTO | filters.AUDIO | filters.VOICE | filters.Document.ALL, admin_received_media_file),
            ],
            WAITING_THUMBNAIL: [
                MessageHandler(filters.PHOTO, admin_received_thumbnail),
                CallbackQueryHandler(admin_skip_thumbnail, pattern=r"^skip_thumbnail$"),
            ],
            WAITING_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_received_title),
            ],
            WAITING_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_received_description),
                CallbackQueryHandler(admin_skip_description, pattern=r"^skip_description$"),
            ],
            WAITING_CATEGORY: [
                CallbackQueryHandler(admin_selected_category, pattern=r"^sel_cat_"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(admin_cancel_flow, pattern=r"^admin_cancel_flow$"),
            CommandHandler("cancel", admin_cancel_flow),
        ],
        per_message=False,
    )

    # Category Creation Flow
    new_cat_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_new_category_prompt, pattern=r"^admin_new_category$"),
        ],
        states={
            WAITING_NEW_CATEGORY_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_new_category),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(admin_cancel_flow, pattern=r"^admin_cancel_flow$"),
        ],
        per_message=False,
    )

    # Delete Content Flow
    delete_content_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_delete_content_prompt, pattern=r"^admin_delete_content$"),
        ],
        states={
            WAITING_DELETE_CONTENT_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_execute_delete_content),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(admin_cancel_flow, pattern=r"^admin_cancel_flow$"),
        ],
        per_message=False,
    )

    # Register Handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("admin", admin_command))

    bot_app.add_handler(upload_conv)
    bot_app.add_handler(new_cat_conv)
    bot_app.add_handler(delete_content_conv)

    # Admin Callback Queries
    bot_app.add_handler(CallbackQueryHandler(admin_list_contents_cb, pattern=r"^admin_list_contents$"))
    bot_app.add_handler(CallbackQueryHandler(admin_stats_cb, pattern=r"^admin_stats$"))
    bot_app.add_handler(CallbackQueryHandler(admin_app_info_cb, pattern=r"^admin_app_info$"))
    bot_app.add_handler(CallbackQueryHandler(admin_channel_share_cb, pattern=r"^admin_channel_share$"))
    bot_app.add_handler(CallbackQueryHandler(admin_return_cb, pattern=r"^admin_return$"))
    bot_app.add_handler(CallbackQueryHandler(admin_close_cb, pattern=r"^admin_close$"))

    logger.info("Initializing Telegram Bot Polling...")
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Viral Zone 🔥 Telegram Bot is now active and polling!")

    # Keep application running indefinitely
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        logger.info("Shutting down cleanly...")
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        await runner.cleanup()
        if db_pool:
            await db_pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot process exited.")

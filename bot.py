import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 8721334265

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# =========================
# DEFAULT CATEGORIES
# =========================

CATEGORIES = [
    "🎵 গান",
    "🎭 নাটক",
    "🎬 ভিডিও",
    "📸 ফটো",
    "🎥 মুভি",
]

# Temporary in-memory data.
# Database will be added in the next step.
VIDEOS = {}
REQUESTS = []


# =========================
# ADMIN CHECK
# =========================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []

    for category in CATEGORIES:
        keyboard.append([
            InlineKeyboardButton(
                category,
                callback_data=f"cat:{category}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "👑 Admin",
            callback_data="admin"
        )
    ])

    text = (
        "🌑💜 <b>Bangla Vibe</b> 💗💙\n\n"
        "🔥 Welcome to Bangla Vibe!\n"
        "🎬 তোমার পছন্দের Content খুঁজে নাও\n"
        "✨ Category select করো নিচ থেকে 👇"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================
# CATEGORY MENU
# =========================

async def category_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    category = query.data.replace("cat:", "", 1)

    items = VIDEOS.get(category, [])

    if not items:
        text = (
            "😔 <b>Sorry!</b>\n\n"
            f"📂 Category: <b>{category}</b>\n\n"
            "এই content এখনো Admin add করেনি।\n"
            "📩 তোমার request Admin-এর কাছে পাঠানো হয়েছে।\n"
            "⏳ খুব দ্রুত add করার চেষ্টা করা হবে।\n\n"
            "❤️ ধন্যবাদ!"
        )

        REQUESTS.append({
            "user_id": query.from_user.id,
            "username": query.from_user.username,
            "category": category,
        })

        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    "📩 <b>New Content Request</b>\n\n"
                    f"👤 User: @{query.from_user.username or 'Unknown'}\n"
                    f"🆔 ID: <code>{query.from_user.id}</code>\n"
                    f"📂 Category: {category}",
                    parse_mode="HTML",
                )
            except Exception:
                logging.exception("Could not send admin request.")

        await query.edit_message_text(
            text,
            parse_mode="HTML",
        )
        return

    buttons = []

    for index, video in enumerate(items):
        buttons.append([
            InlineKeyboardButton(
                f"🎬 {video['title']}",
                callback_data=f"video:{category}:{index}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("⬅️ Back", callback_data="home")
    ])

    await query.edit_message_text(
        f"💜 <b>{category}</b>\n\n"
        "🎬 Available Content:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================
# VIDEO
# =========================

async def video_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    try:
        _, category, index = query.data.split(":", 2)
        index = int(index)

        video = VIDEOS[category][index]

        caption = (
            f"🎬 <b>{video['title']}</b>\n\n"
            f"📂 Category: {category}\n"
            f"📝 {video.get('description', 'No description')}\n\n"
            "🔥 <b>Bangla Vibe</b>"
        )

        await context.bot.send_video(
            chat_id=query.from_user.id,
            video=video["file_id"],
            caption=caption,
            parse_mode="HTML",
        )

    except Exception:
        logging.exception("Video sending error")

        await query.message.reply_text(
            "😔 Sorry! ভিডিওটি এখন পাঠানো যাচ্ছে না।\n"
            "⏳ একটু পরে আবার চেষ্টা করো।"
        )


# =========================
# HOME
# =========================

async def home_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [
            InlineKeyboardButton(
                category,
                callback_data=f"cat:{category}"
            )
        ]
        for category in CATEGORIES
    ]

    await query.edit_message_text(
        "🌑💜 <b>Bangla Vibe</b>\n\n"
        "📂 Category select করো 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================
# ADMIN PANEL
# =========================

async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.answer(
            "⛔ তুমি Admin নও!",
            show_alert=True
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Categories",
                callback_data="admin_categories"
            )
        ],
        [
            InlineKeyboardButton(
                "📩 Requests",
                callback_data="admin_requests"
            )
        ],
    ]

    await query.edit_message_text(
        "👑 <b>Admin Panel</b>\n\n"
        "⚡ Welcome Admin!\n"
        "নিচের option নির্বাচন করো 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================
# ADMIN COMMAND
# =========================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "⛔ Sorry! এই command শুধু Admin-এর জন্য।"
        )
        return

    await update.message.reply_text(
        "👑 <b>Bangla Vibe Admin</b>\n\n"
        "⚡ Admin system এখন প্রস্তুত হচ্ছে।\n\n"
        "পরের ধাপে আমরা যুক্ত করব:\n"
        "🎬 Video Upload\n"
        "🏷️ Title\n"
        "📝 Description\n"
        "📂 Category\n"
        "➕ New Category\n"
        "🗑️ Delete Video\n"
        "👤 Social ID Management",
        parse_mode="HTML",
    )


# =========================
# TEXT MATCHING
# =========================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text.lower().strip()

    if any(word in text for word in [
        "admin",
        "এডমিন",
        "অ্যাডমিন",
    ]):
        await update.message.reply_text(
            "👑 <b>Admin Contact</b>\n\n"
            "📘 Facebook: Coming Soon\n"
            "🎵 TikTok: Coming Soon\n"
            "🟢 WhatsApp: Coming Soon\n"
            "✈️ Telegram: @tomalchowdhury2\n\n"
            "✨ Admin আরও contact পরে যোগ করতে পারবে।",
            parse_mode="HTML",
        )
        return

    if any(word in text for word in [
        "গান", "gaan", "song"
    ]):
        category = "🎵 গান"

    elif any(word in text for word in [
        "নাটক", "natok", "drama"
    ]):
        category = "🎭 নাটক"

    elif any(word in text for word in [
        "মুভি", "movie", "film"
    ]):
        category = "🎥 মুভি"

    elif any(word in text for word in [
        "ভিডিও", "video"
    ]):
        category = "🎬 ভিডিও"

    elif any(word in text for word in [
        "ফটো", "photo", "picture"
    ]):
        category = "📸 ফটো"

    else:
        await update.message.reply_text(
            "🤔 Sorry! আমি তোমার request পুরোপুরি বুঝতে পারিনি।\n\n"
            "📂 গান / নাটক / ভিডিও / ফটো / মুভি লিখে চেষ্টা করো।"
        )
        return

    items = VIDEOS.get(category, [])

    if not items:
        REQUESTS.append({
            "user_id": update.effective_user.id,
            "username": update.effective_user.username,
            "category": category,
            "text": text,
        })

        await update.message.reply_text(
            "😔 <b>Sorry!</b>\n\n"
            f"📂 {category}\n"
            "এই content এখনো Admin add করেনি।\n"
            "📩 তোমার request Admin-এর কাছে পাঠানো হয়েছে।\n"
            "⏳ খুব দ্রুত add করার চেষ্টা করা হবে। ❤️",
            parse_mode="HTML",
        )

        try:
            await context.bot.send_message(
                ADMIN_ID,
                "📩 <b>New Request</b>\n\n"
                f"👤 @{update.effective_user.username or 'Unknown'}\n"
                f"🆔 <code>{update.effective_user.id}</code>\n"
                f"📂 {category}\n"
                f"💬 {text}",
                parse_mode="HTML",
            )
        except Exception:
            logging.exception("Request notification failed.")

        return

    buttons = [
        [
            InlineKeyboardButton(
                f"🎬 {v['title']}",
                callback_data=f"video:{category}:{i}"
            )
        ]
        for i, v in enumerate(items)
    ]

    await update.message.reply_text(
        f"🔥 <b>{category}</b>\n\n"
        "Available content 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    logging.exception(
        "Telegram update error:",
        exc_info=context.error
    )


# =========================
# MAIN
# =========================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(
        CallbackQueryHandler(
            category_callback,
            pattern=r"^cat:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            video_callback,
            pattern=r"^video:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            home_callback,
            pattern=r"^home$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^admin$"
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    app.add_error_handler(error_handler)

    print("🔥 Bangla Vibe Bot started...")
    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()

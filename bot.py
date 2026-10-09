import os
import logging
from threading import Thread

from flask import Flask
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

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lstrip("@")
WEB_APP_URL = os.getenv("WEB_APP_URL", "").strip()

# Web server for Render
web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Live Store Bot is running!", 200


@web_app.route("/health")
def health():
    return {"status": "ok"}, 200


def run_web_server():
    port = int(os.getenv("PORT", "10000"))
    web_app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False,
    )


# Main menu
def main_menu():
    keyboard = [
        [
            InlineKeyboardButton("🎵 গান", callback_data="songs"),
            InlineKeyboardButton("💃 নাচ", callback_data="dance"),
        ],
        [
            InlineKeyboardButton("🎬 নাটক", callback_data="drama"),
            InlineKeyboardButton("✍️ ক্যাপশন", callback_data="captions"),
        ],
        [
            InlineKeyboardButton("❓ সাহায্য", callback_data="help"),
            InlineKeyboardButton("👤 Admin Contact", callback_data="admin"),
        ],
    ]

    if WEB_APP_URL.startswith("https://"):
        keyboard.append([
            InlineKeyboardButton(
                "🛍️ Open Live Store",
                web_app=WebAppInfo(url=WEB_APP_URL),
            )
        ])

    return InlineKeyboardMarkup(keyboard)


# Start command
async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message:
        await message.reply_text(
            "✨ Welcome to Live Store!\n\n"
            "স্বাগতম! নিচের মেনু থেকে তোমার পছন্দের "
            "ক্যাটাগরি বেছে নাও।",
            reply_markup=main_menu(),
        )


# Help command
async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if message:
        await message.reply_text(
            "❓ কী সাহায্য প্রয়োজন, লিখে পাঠাও।\n"
            "অথবা নিচের মেনু থেকে একটি অপশন বেছে নাও।",
            reply_markup=main_menu(),
        )


# Button responses
async def menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    responses = {
        "songs": "🎵 গান\n\nকী ধরনের গান খুঁজছ, লিখে পাঠাও।",
        "dance": "💃 নাচ\n\nনাচ সম্পর্কিত কী খুঁজছ, লিখে পাঠাও।",
        "drama": "🎬 নাটক\n\nনাটক সম্পর্কিত অনুরোধ লিখে পাঠাও।",
        "captions": "✍️ ক্যাপশন\n\nছবি বা ভিডিওর বিষয় লিখে পাঠাও।",
        "help": "❓ সাহায্য\n\nতোমার প্রশ্নটি লিখে পাঠাও।",
        "admin": (
            f"👤 Admin Contact\n\nযোগাযোগ: @{ADMIN_USERNAME}"
            if ADMIN_USERNAME
            else "👤 Admin Contact\n\n"
                 "Admin username সেট করা হয়নি।"
        ),
    }

    await query.message.reply_text(
        responses.get(
            query.data,
            "মেনু থেকে একটি অপশন নির্বাচন করো।",
        ),
        reply_markup=main_menu(),
    )


# Automatic replies
async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message or not message.text:
        return

    text = message.text.strip().lower()

    if any(word in text for word in ("হাই", "হ্যালো", "hello", "hi")):
        reply = "👋 হ্যালো! Live Store বটে স্বাগতম। কী সাহায্য লাগবে?"

    elif any(word in text for word in ("গান", "song", "music")):
        reply = "🎵 কী ধরনের গান খুঁজছ? বিস্তারিত লিখে পাঠাও।"

    elif any(word in text for word in ("নাচ", "dance")):
        reply = "💃 নাচ সম্পর্কিত কী খুঁজছ? লিখে পাঠাও।"

    elif any(word in text for word in ("নাটক", "drama")):
        reply = "🎬 নাটক সম্পর্কে তোমার অনুরোধ লিখে পাঠাও।"

    elif any(word in text for word in ("ক্যাপশন", "caption")):
        reply = "✍️ ছবির বা ভিডিওর বিষয় লিখো।"

    elif any(word in text for word in ("সাহায্য", "help")):
        reply = "❓ নিচের মেনু থেকে Help নির্বাচন করো।"

    else:
        reply = (
            "💜 তোমার মেসেজ পেয়েছি!\n"
            "গান, নাচ, নাটক বা ক্যাপশন সম্পর্কে জানতে "
            "নিচের মেনু ব্যবহার করো।"
        )

    await message.reply_text(
        reply,
        reply_markup=main_menu(),
    )


# Bot commands
async def post_init(application: Application):
    await application.bot.set_my_commands([
        ("start", "Open Live Store menu"),
        ("help", "Get help"),
    ])


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is required"
        )

    # Start web server for Render
    web_thread = Thread(
        target=run_web_server,
        daemon=True,
    )
    web_thread.start()

    # Start Telegram bot
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(menu_callback))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info("Live Store Telegram bot is starting...")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()

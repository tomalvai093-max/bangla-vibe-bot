import os
import logging
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lstrip("@")
WEB_APP_URL = os.getenv("WEB_APP_URL", "").strip()


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if message:
        await message.reply_text(
            "✨ Welcome to Live Store!\n\n"
            "তোমার পছন্দের ক্যাটাগরি বেছে নাও।",
            reply_markup=main_menu(),
        )


async def menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    responses = {
        "songs": "🎵 গান\n\nগান সম্পর্কিত অনুরোধ লিখো।",
        "dance": "💃 নাচ\n\nনাচের ভিডিও বা তথ্যের অনুরোধ লিখো।",
        "drama": "🎬 নাটক\n\nনাটক সম্পর্কিত অনুরোধ লিখো।",
        "captions": "✍️ ক্যাপশন\n\nকী ধরনের ক্যাপশন চাও, লিখে পাঠাও।",
        "help": (
            "❓ সাহায্য\n\n"
            "মেনু থেকে ক্যাটাগরি নির্বাচন করো অথবা তোমার প্রশ্ন লিখে পাঠাও।"
        ),
        "admin": (
            f"👤 Admin Contact\n\n"
            f"যোগাযোগ: @{ADMIN_USERNAME}"
            if ADMIN_USERNAME
            else "👤 Admin Contact\n\nAdmin username এখনো সেট করা হয়নি।"
        ),
    }

    text = responses.get(query.data, "মেনু থেকে একটি অপশন নির্বাচন করো।")

    await query.message.reply_text(
        text,
        reply_markup=main_menu(),
    )


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
        reply = "🎵 কী ধরনের গান খুঁজছ? তোমার পছন্দ লিখে পাঠাও।"
    elif any(word in text for word in ("নাচ", "dance")):
        reply = "💃 নাচ সম্পর্কিত কী খুঁজছ? বিস্তারিত লিখে পাঠাও।"
    elif any(word in text for word in ("নাটক", "drama")):
        reply = "🎬 কোন ধরনের নাটক খুঁজছ? তোমার অনুরোধ পাঠাও।"
    elif any(word in text for word in ("ক্যাপশন", "caption")):
        reply = "✍️ ছবির বা ভিডিওর বিষয় লিখো, আমি ক্যাপশন তৈরিতে সাহায্য করব।"
    elif any(word in text for word in ("সাহায্য", "help")):
        reply = "❓ সাহায্যের জন্য নিচের মেনু থেকে Help নির্বাচন করো।"
    else:
        reply = (
            "💜 তোমার মেসেজ পেয়েছি!\n"
            "গান, নাচ, নাটক বা ক্যাপশন সম্পর্কে জানতে নিচের মেনু ব্যবহার করো।"
        )

    await message.reply_text(reply, reply_markup=main_menu())


async def post_init(application: Application):
    await application.bot.set_my_commands([
        ("start", "Open Live Store menu"),
        ("help", "Get help"),
    ])


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_message:
        await update.effective_message.reply_text(
            "কী সাহায্য প্রয়োজন, লিখে পাঠাও।",
            reply_markup=main_menu(),
        )


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is required"
        )

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

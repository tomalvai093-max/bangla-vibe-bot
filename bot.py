import os
import asyncio
import threading
import logging

import requests
import psycopg2
from psycopg2.extras import RealDictCursor

from flask import (
    Flask, request, jsonify, Response, stream_with_context
)

from telegram import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
    Update,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("live-store")

# =========================
# ENVIRONMENT VARIABLES
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
WEB_APP_URL = os.getenv("WEB_APP_URL", "").strip().rstrip("/")
ADSTERRA_URL = os.getenv("ADSTERRA_URL", "").strip()

if not all([BOT_TOKEN, DATABASE_URL, ADMIN_TELEGRAM_ID, WEB_APP_URL]):
    raise RuntimeError(
        "Render Environment-এ BOT_TOKEN, DATABASE_URL, "
        "ADMIN_TELEGRAM_ID এবং WEB_APP_URL দিতে হবে।"
    )

if not ADMIN_TELEGRAM_ID.isdigit():
    raise RuntimeError("ADMIN_TELEGRAM_ID অবশ্যই সংখ্যার Telegram ID হতে হবে।")

ADMIN_ID = int(ADMIN_TELEGRAM_ID)

app = Flask(__name__, static_folder="public")

# =========================
# DATABASE
# =========================

def connect_db():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=15,
        sslmode="require",
    )


def setup_db():
    conn = connect_db()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS live_store_videos (
                        id SERIAL PRIMARY KEY,
                        title TEXT NOT NULL,
                        video_file TEXT NOT NULL DEFAULT '',
                        thumbnail_file TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)

                cur.execute("""
                    ALTER TABLE live_store_videos
                    ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'অন্যান্য'
                """)

                cur.execute("""
                    ALTER TABLE live_store_videos
                    ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''
                """)

                cur.execute("""
                    ALTER TABLE live_store_videos
                    ADD COLUMN IF NOT EXISTS video_file_id TEXT NOT NULL DEFAULT ''
                """)

                cur.execute("""
                    ALTER TABLE live_store_videos
                    ADD COLUMN IF NOT EXISTS thumbnail_file_id TEXT NOT NULL DEFAULT ''
                """)

    finally:
        conn.close()


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


# =========================
# TELEGRAM KEYBOARDS
# =========================

def make_menu(admin=False):
    open_button = KeyboardButton(
        "▶ Open now",
        web_app=WebAppInfo(url=WEB_APP_URL),
    )

    if admin:
        rows = [
            [open_button],
            [KeyboardButton("📤 ভিডিও আপলোড")],
            [KeyboardButton("❌ আপলোড বাতিল")],
            [KeyboardButton("🎬 ভিডিও তালিকা")],
        ]
    else:
        rows = [
            [open_button],
            [KeyboardButton("🎵 গান"), KeyboardButton("💃 নাচ")],
            [KeyboardButton("🎬 নাটক"), KeyboardButton("✍️ ক্যাপশন")],
            [KeyboardButton("🆘 সাহায্য"), KeyboardButton("👤 অ্যাডমিন যোগাযোগ")],
        ]

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
    )


# =========================
# TELEGRAM COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    context.user_data.pop("upload_step", None)
    context.user_data.pop("upload_data", None)

    if is_admin(update):
        message = (
            "🔐 Live Store Admin\n\n"
            "ভিডিও আপলোড করতে 📤 ভিডিও আপলোড চাপুন।\n"
            "ভিডিও দেখতে ▶ Open now চাপুন।"
        )
    else:
        message = (
            "🌌 স্বাগতম Live Store-এ!\n\n"
            "ভিডিও দেখতে ▶ Open now চাপুন।"
        )

    await update.message.reply_text(
        message,
        reply_markup=make_menu(is_admin(update)),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if is_admin(update):
        text = (
            "Admin নির্দেশনা:\n"
            "/upload - ভিডিও আপলোড\n"
            "/cancel - আপলোড বাতিল\n"
            "/videos - ভিডিও তালিকা"
        )
    else:
        text = (
            "Live Store ব্যবহার করতে ▶ Open now চাপুন।\n"
            "সহায়তার জন্য অ্যাডমিনের সঙ্গে যোগাযোগ করুন।"
        )

    await update.message.reply_text(text)


async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if not is_admin(update):
        await update.message.reply_text("⛔ এই সুবিধা শুধু Admin-এর জন্য।")
        return

    context.user_data["upload_step"] = "video"
    context.user_data["upload_data"] = {}

    await update.message.reply_text(
        "📤 ভিডিও আপলোড শুরু হয়েছে।\n\n"
        "এখন Telegram-এ ভিডিওটি পাঠাও।\n"
        "ভিডিও ফাইল হিসেবে পাঠালেও হবে।"
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("upload_step", None)
    context.user_data.pop("upload_data", None)

    if update.message:
        await update.message.reply_text(
            "আপলোড বাতিল করা হয়েছে।",
            reply_markup=make_menu(is_admin(update)),
        )


async def videos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if not is_admin(update):
        await update.message.reply_text("⛔ শুধু Admin এই তালিকা দেখতে পারবে।")
        return

    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, category
                FROM live_store_videos
                ORDER BY id DESC
                LIMIT 30
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        await update.message.reply_text("এখনো কোনো ভিডিও আপলোড করা হয়নি।")
        return

    lines = ["🎬 সর্বশেষ ভিডিওগুলো:"]
    for video_id, title, category in rows:
        lines.append(f"#{video_id} — {title} [{category}]")

    await update.message.reply_text("\n".join(lines))


# =========================
# ADMIN VIDEO UPLOAD FLOW
# =========================

async def handle_media(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    if not is_admin(update):
        await update.message.reply_text("⛔ ভিডিও আপলোডের অনুমতি নেই।")
        return

    step = context.user_data.get("upload_step")
    data = context.user_data.setdefault("upload_data", {})

    if step == "video":
        message = update.message

        if message.video:
            data["video_file_id"] = message.video.file_id

        elif (
            message.document
            and (message.document.mime_type or "").startswith("video/")
        ):
            data["video_file_id"] = message.document.file_id

        else:
            await message.reply_text(
                "একটি ভিডিও পাঠাও। MP4 ভিডিও হলে ভালো।"
            )
            return

        context.user_data["upload_step"] = "thumbnail"

        await message.reply_text(
            "✅ ভিডিও পেয়েছি।\n\n"
            "এখন ভিডিওটির Thumbnail ছবি পাঠাও।\n"
            "Gallery থেকে ছবি বেছে পাঠাতে পারো।"
        )
        return

    if step == "thumbnail":
        message = update.message

        if message.photo:
            data["thumbnail_file_id"] = message.photo[-1].file_id

        elif (
            message.document
            and (message.document.mime_type or "").startswith("image/")
        ):
            data["thumbnail_file_id"] = message.document.file_id

        else:
            await message.reply_text(
                "একটি ছবি পাঠাও। JPG বা PNG ব্যবহার করো।"
            )
            return

        context.user_data["upload_step"] = "title"

        await message.reply_text("📝 এখন ভিডিওর নাম লেখো।")
        return

    await update.message.reply_text(
        "আগে 📤 ভিডিও আপলোড বাটনে চাপো।",
        reply_markup=make_menu(True),
    )


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.message.text:
        return

    message = update.message
    text = message.text.strip()
    lower = text.lower()

    if text == "❌ আপলোড বাতিল":
        await cancel_command(update, context)
        return

    if text == "📤 ভিডিও আপলোড":
        await upload_command(update, context)
        return

    if text == "🎬 ভিডিও তালিকা":
        await videos_command(update, context)
        return

    # Upload workflow
    step = context.user_data.get("upload_step")

    if step:
        if not is_admin(update):
            context.user_data.pop("upload_step", None)
            context.user_data.pop("upload_data", None)
            await message.reply_text("⛔ অনুমতি নেই।")
            return

        data = context.user_data.setdefault("upload_data", {})

        if step == "title":
            if len(text) < 1:
                await message.reply_text("ভিডিওর নাম লিখো।")
                return

            data["title"] = text[:200]
            context.user_data["upload_step"] = "category"

            await message.reply_text(
                "📂 Category লেখো।\n"
                "উদাহরণ: নাটক, গান, নাচ, কমেডি"
            )
            return

        if step == "category":
            data["category"] = text[:80]
            context.user_data["upload_step"] = "description"

            await message.reply_text(
                "📝 ভিডিওর Description লেখো।\n"
                "না দিতে চাইলে /skip পাঠাও।"
            )
            return

        if step == "description":
            await save_video_record(
                update,
                context,
                description=text,
            )
            return

    # Normal user/admin menu replies
    if "গান" in lower:
        answer = "🎵 গান বিভাগ"
    elif "নাচ" in lower:
        answer = "💃 নাচ বিভাগ"
    elif "নাটক" in lower:
        answer = "🎬 নাটক বিভাগ"
    elif "ক্যাপশন" in lower:
        answer = "✍️ সময় বদলায়, অভিজ্ঞতা মানুষকে বদলে দেয়।"
    elif "সাহায্য" in lower:
        answer = "🆘 সাহায্যের জন্য অ্যাডমিনের সঙ্গে যোগাযোগ করুন।"
    elif "অ্যাডমিন" in lower or "যোগাযোগ" in lower:
        answer = "অ্যাডমিনের সঙ্গে যোগাযোগ করতে বটের মালিকের দেওয়া তথ্য ব্যবহার করুন।"
    else:
        answer = "Live Store ব্যবহার করতে ▶ Open now চাপুন।"

    await message.reply_text(
        answer,
        reply_markup=make_menu(is_admin(update)),
    )


async def skip_description(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    if not is_admin(update):
        await update.message.reply_text("⛔ অনুমতি নেই।")
        return

    if context.user_data.get("upload_step") != "description":
        await update.message.reply_text("এখন Description দেওয়ার ধাপ নয়।")
        return

    await save_video_record(update, context, description="")


async def save_video_record(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    description: str,
):
    if not update.message:
        return

    data = context.user_data.get("upload_data", {})

    required = [
        "video_file_id",
        "thumbnail_file_id",
        "title",
        "category",
    ]

    if not all(data.get(key) for key in required):
        await update.message.reply_text(
            "কিছু তথ্য পাওয়া যায়নি। /upload দিয়ে আবার শুরু করো।"
        )
        context.user_data.pop("upload_step", None)
        context.user_data.pop("upload_data", None)
        return

    conn = connect_db()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO live_store_videos
                        (
                            title,
                            category,
                            description,
                            video_file_id,
                            thumbnail_file_id,
                            video_file,
                            thumbnail_file
                        )
                    VALUES (%s, %s, %s, %s, %s, '', '')
                    RETURNING id
                """, (
                    data["title"],
                    data["category"],
                    description[:2000],
                    data["video_file_id"],
                    data["thumbnail_file_id"],
                ))

                new_id = cur.fetchone()[0]

    except Exception:
        log.exception("Failed to save uploaded video")
        await update.message.reply_text(
            "❌ ভিডিও সংরক্ষণ করা যায়নি। Render Logs পরীক্ষা করো।"
        )
        return

    finally:
        conn.close()

    context.user_data.pop("upload_step", None)
    context.user_data.pop("upload_data", None)

    await update.message.reply_text(
        f"✅ ভিডিও সফলভাবে যোগ হয়েছে!\n"
        f"ID: {new_id}\n"
        f"নাম: {data['title']}\n"
        f"Category: {data['category']}\n\n"
        "User-রা Live Store Web App-এ ভিডিওটি দেখতে পারবে।",
        reply_markup=make_menu(True),
    )


# =========================
# PUBLIC VIDEO API
# =========================

@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.get("/health")
def health():
    return "Live Store is running", 200


@app.get("/api/config")
def public_config():
    return jsonify({
        "name": "Live Store",
        "ad_url": ADSTERRA_URL,
    })


@app.get("/api/videos")
def list_videos():
    conn = connect_db()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    id, title, category, description, created_at
                FROM live_store_videos
                WHERE video_file_id <> ''
                  AND thumbnail_file_id <> ''
                ORDER BY id DESC
            """)
            rows = [dict(row) for row in cur.fetchall()]

        for row in rows:
            row["video_url"] = f"/media/{row['id']}"
            row["thumbnail_url"] = f"/thumbnail/{row['id']}"
            row["created_at"] = str(row["created_at"])

        return jsonify(rows)

    finally:
        conn.close()


def get_file_id(video_id: int, column: str):
    allowed = {"video_file_id", "thumbnail_file_id"}

    if column not in allowed:
        return None

    conn = connect_db()

    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {column} FROM live_store_videos WHERE id = %s",
                (video_id,),
            )
            row = cur.fetchone()

        return row[0] if row else None

    finally:
        conn.close()


def get_telegram_file_path(file_id: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile"

    result = requests.get(
        url,
        params={"file_id": file_id},
        timeout=30,
    )
    result.raise_for_status()

    payload = result.json()

    if not payload.get("ok"):
        raise RuntimeError("Telegram getFile request failed")

    return payload["result"]["file_path"]


def proxy_telegram_file(file_id: str, allow_range: bool):
    try:
        file_path = get_telegram_file_path(file_id)

        file_url = (
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        )

        headers = {}
        if allow_range and request.headers.get("Range"):
            headers["Range"] = request.headers["Range"]

        upstream = requests.get(
            file_url,
            headers=headers,
            stream=True,
            timeout=(15, 120),
        )

        if upstream.status_code not in (200, 206):
            upstream.close()
            return jsonify({"error": "Telegram media download failed"}), 502

        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        response = Response(
            stream_with_context(generate()),
            status=upstream.status_code,
            content_type=upstream.headers.get(
                "Content-Type",
                "video/mp4" if allow_range else "image/jpeg",
            ),
        )

        for header in (
            "Content-Length",
            "Content-Range",
            "Accept-Ranges",
        ):
            if header in upstream.headers:
                response.headers[header] = upstream.headers[header]

        response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["X-Content-Type-Options"] = "nosniff"

        return response

    except Exception:
        log.exception("Telegram media proxy failed")
        return jsonify({
            "error": (
                "মিডিয়া লোড হয়নি। Telegram file size এবং Render Logs পরীক্ষা করুন।"
            )
        }), 502


@app.get("/media/<int:video_id>")
def play_video(video_id):
    file_id = get_file_id(video_id, "video_file_id")

    if not file_id:
        return jsonify({"error": "ভিডিও পাওয়া যায়নি"}), 404

    return proxy_telegram_file(file_id, allow_range=True)


@app.get("/thumbnail/<int:video_id>")
def get_thumbnail(video_id):
    file_id = get_file_id(video_id, "thumbnail_file_id")

    if not file_id:
        return jsonify({"error": "Thumbnail পাওয়া যায়নি"}), 404

    return proxy_telegram_file(file_id, allow_range=False)


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "ফাইলের আকার অনুমোদিত সীমার বেশি।"}), 413


# =========================
# RUN
# =========================

def run_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


def main():
    setup_db()

    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    bot = Application.builder().token(BOT_TOKEN).build()

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("help", help_command))
    bot.add_handler(CommandHandler("upload", upload_command))
    bot.add_handler(CommandHandler("cancel", cancel_command))
    bot.add_handler(CommandHandler("videos", videos_command))
    bot.add_handler(CommandHandler("skip", skip_description))

    bot.add_handler(
        MessageHandler(
            filters.VIDEO | filters.PHOTO | filters.Document.ALL,
            handle_media,
        )
    )

    bot.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    log.info("Live Store bot and web app starting")
    bot.run_polling()


if __name__ == "__main__":
    main()

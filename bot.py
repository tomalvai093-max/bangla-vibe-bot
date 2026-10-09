import os
import uuid
import threading
import logging
from pathlib import Path
from functools import wraps

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, session, send_from_directory
from werkzeug.utils import secure_filename
from telegram import (
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# =========================
# ENVIRONMENT VARIABLES
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")

ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
WEB_APP_URL = os.getenv("WEB_APP_URL", "").strip().rstrip("/")

# Render Environment-এ UPLOAD_DIR=/tmp/uploads দেওয়া যেতে পারে।
# স্থায়ী ফাইলের জন্য Render Persistent Disk প্রয়োজন।
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

if not all([
    BOT_TOKEN,
    DATABASE_URL,
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
    SECRET_KEY,
]):
    raise RuntimeError(
        "Render Environment Variables অসম্পূর্ণ। "
        "BOT_TOKEN, DATABASE_URL, ADMIN_USERNAME, "
        "ADMIN_PASSWORD ও SECRET_KEY সেট করুন।"
    )

app = Flask(__name__, static_folder="public")
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# =========================
# DATABASE
# =========================

def connect_db():
    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=10,
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
                        video_file TEXT NOT NULL,
                        thumbnail_file TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    ALTER TABLE live_store_videos
                    ADD COLUMN IF NOT EXISTS video_file
                    TEXT NOT NULL DEFAULT ''
                """)
                cur.execute("""
                    ALTER TABLE live_store_videos
                    ADD COLUMN IF NOT EXISTS thumbnail_file
                    TEXT NOT NULL DEFAULT ''
                """)
    finally:
        conn.close()


# =========================
# ADMIN AUTHENTICATION
# =========================

def admin_only(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return jsonify({
                "error": "প্রথমে Admin Login করুন"
            }), 401
        return fn(*args, **kwargs)
    return wrapper


def save_upload(file, allowed):
    if not file or not file.filename:
        raise ValueError("ফাইল নির্বাচন করা হয়নি")

    original = secure_filename(file.filename)
    ext = Path(original).suffix.lower()

    if ext not in allowed:
        raise ValueError("এই ফাইলের ফরম্যাট অনুমোদিত নয়")

    filename = uuid.uuid4().hex + ext
    file.save(UPLOAD_DIR / filename)
    return filename


# =========================
# WEBSITE ROUTES
# =========================

@app.get("/")
def home():
    return send_from_directory("public", "index.html")


@app.get("/admin")
def admin_page():
    return send_from_directory("public", "admin.html")


@app.get("/health")
def health():
    return "Live Store is running", 200


@app.get("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.get("/api/videos")
def list_videos():
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, title, video_file, thumbnail_file, created_at
                FROM live_store_videos
                ORDER BY id DESC
            """)
            rows = [dict(row) for row in cur.fetchall()]

        for row in rows:
            row["video_url"] = "/uploads/" + row["video_file"]
            row["thumbnail_url"] = (
                "/uploads/" + row["thumbnail_file"]
                if row["thumbnail_file"] else ""
            )
            row["created_at"] = str(row["created_at"])

        return jsonify(rows)
    finally:
        conn.close()


@app.post("/api/admin/login")
def login():
    data = request.get_json(silent=True) or {}

    if (
        data.get("username") == ADMIN_USERNAME
        and data.get("password") == ADMIN_PASSWORD
    ):
        session.clear()
        session["admin"] = True
        return jsonify({"ok": True})

    return jsonify({
        "error": "Username অথবা Password ভুল"
    }), 401


@app.get("/api/admin/session")
def check_login():
    return jsonify({
        "logged_in": bool(session.get("admin"))
    })


@app.post("/api/admin/logout")
@admin_only
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.post("/api/videos")
@admin_only
def add_video():
    title = (request.form.get("title") or "").strip()

    if not title:
        return jsonify({
            "error": "ভিডিওর নাম লিখুন"
        }), 400

    video_name = None
    thumb_name = None

    try:
        video_name = save_upload(
            request.files.get("video"),
            VIDEO_EXTS,
        )
        thumb_name = save_upload(
            request.files.get("thumbnail"),
            IMAGE_EXTS,
        )

        conn = connect_db()
        try:
            with conn:
                with conn.cursor(
                    cursor_factory=RealDictCursor
                ) as cur:
                    cur.execute("""
                        INSERT INTO live_store_videos
                            (title, video_file, thumbnail_file)
                        VALUES (%s, %s, %s)
                        RETURNING id
                    """, (
                        title[:200],
                        video_name,
                        thumb_name,
                    ))
                    new_id = cur.fetchone()["id"]
        finally:
            conn.close()

        return jsonify({
            "ok": True,
            "id": new_id,
        }), 201

    except ValueError as exc:
        for name in [video_name, thumb_name]:
            if name:
                (UPLOAD_DIR / name).unlink(missing_ok=True)

        return jsonify({"error": str(exc)}), 400

    except Exception:
        log.exception("Video upload failed")

        for name in [video_name, thumb_name]:
            if name:
                (UPLOAD_DIR / name).unlink(missing_ok=True)

        return jsonify({
            "error": (
                "আপলোড হয়নি। Render Logs, ফাইলের ফরম্যাট, "
                "UPLOAD_DIR ও DATABASE_URL পরীক্ষা করুন।"
            )
        }), 500


@app.put("/api/videos/<int:video_id>")
@admin_only
def rename_video(video_id):
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()

    if not title:
        return jsonify({
            "error": "নতুন নাম লিখুন"
        }), 400

    conn = connect_db()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE live_store_videos
                    SET title = %s
                    WHERE id = %s
                """, (title[:200], video_id))

                if cur.rowcount == 0:
                    return jsonify({
                        "error": "ভিডিও পাওয়া যায়নি"
                    }), 404

        return jsonify({"ok": True})
    finally:
        conn.close()


@app.delete("/api/videos/<int:video_id>")
@admin_only
def delete_video(video_id):
    conn = connect_db()
    try:
        with conn:
            with conn.cursor(
                cursor_factory=RealDictCursor
            ) as cur:
                cur.execute("""
                    DELETE FROM live_store_videos
                    WHERE id = %s
                    RETURNING video_file, thumbnail_file
                """, (video_id,))

                row = cur.fetchone()

                if not row:
                    return jsonify({
                        "error": "ভিডিও পাওয়া যায়নি"
                    }), 404

                files = [
                    row["video_file"],
                    row["thumbnail_file"],
                ]
    finally:
        conn.close()

    for name in files:
        if name:
            (UPLOAD_DIR / name).unlink(missing_ok=True)

    return jsonify({"ok": True})


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({
        "error": "ফাইল ১০০ MB-এর বেশি। ছোট ফাইল দিন।"
    }), 413


# =========================
# TELEGRAM MENU
# =========================

MENU = [
    ["🎵 গান", "💃 নাচ"],
    ["🎬 নাটক", "✍️ ক্যাপশন"],
    ["🆘 সাহায্য", "👤 অ্যাডমিন যোগাযোগ"],
]


def make_web_buttons(user_id):
    """
    Open now: সব User-এর জন্য।
    Admin Panel: শুধু নির্দিষ্ট Telegram ID-এর জন্য।
    """

    if not WEB_APP_URL:
        return None

    buttons = [
        [
            InlineKeyboardButton(
                "▶ Open now",
                url=WEB_APP_URL,
            )
        ]
    ]

    if (
        ADMIN_TELEGRAM_ID
        and str(user_id) == ADMIN_TELEGRAM_ID
    ):
        buttons.append([
            InlineKeyboardButton(
                "⚙️ Admin Panel",
                url=WEB_APP_URL + "/admin",
            )
        ])

    return InlineKeyboardMarkup(buttons)


async def start(update, context):
    user = update.effective_user

    if not user or not update.message:
        return

    await update.message.reply_text(
        "স্বাগতম Live Store Bot-এ! 🎬\n\n"
        "ভিডিও দেখতে ▶ Open now চাপুন।",
        reply_markup=make_web_buttons(user.id),
    )

    await update.message.reply_text(
        "নিচের মেনু থেকে আপনার পছন্দ বেছে নিন।",
        reply_markup=ReplyKeyboardMarkup(
            MENU,
            resize_keyboard=True,
        ),
    )


async def help_command(update, context):
    if not update.message:
        return

    await update.message.reply_text(
        "মেনু থেকে গান, নাচ, নাটক, ক্যাপশন বা সাহায্য বেছে নিন।"
    )


async def auto_reply(update, context):
    if not update.message or not update.message.text:
        return

    msg = update.message.text.lower()

    if "গান" in msg:
        answer = "🎵 গান বিভাগ"
    elif "নাচ" in msg:
        answer = "💃 নাচ বিভাগ"
    elif "নাটক" in msg:
        answer = "🎬 নাটক বিভাগ"
    elif "ক্যাপশন" in msg:
        answer = (
            "✍️ ক্যাপশন:\n"
            "সময় বদলায়, অভিজ্ঞতা মানুষকে বদলে দেয়।"
        )
    elif "সাহায্য" in msg:
        answer = "🆘 সাহায্যের জন্য অ্যাডমিনের সঙ্গে যোগাযোগ করুন।"
    elif "অ্যাডমিন" in msg or "যোগাযোগ" in msg:
        answer = "অ্যাডমিন: @" + ADMIN_USERNAME.lstrip("@")
    else:
        answer = "মেসেজ পেয়েছি 😊 নিচের মেনু ব্যবহার করুন।"

    await update.message.reply_text(
        answer,
        reply_markup=ReplyKeyboardMarkup(
            MENU,
            resize_keyboard=True,
        ),
    )


# =========================
# START WEB SERVER + BOT
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
    bot.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            auto_reply,
        )
    )

    log.info("Live Store web server and Telegram bot starting")
    bot.run_polling()


if __name__ == "__main__":
    main()

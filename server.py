import os
from urllib.parse import urlparse

import psycopg2
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")


def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg2.connect(DATABASE_URL)


def setup_database():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS live_store_videos (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)


@app.get("/")
def home():
    return jsonify({
        "app": "Live Store",
        "status": "running"
    })


@app.get("/api/videos")
def get_videos():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, url, description, created_at
                FROM live_store_videos
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()

    videos = [
        {
            "id": row[0],
            "title": row[1],
            "url": row[2],
            "description": row[3],
            "created_at": row[4].isoformat()
        }
        for row in rows
    ]

    return jsonify(videos)


@app.post("/api/videos")
def add_video():
    if not ADMIN_API_KEY:
        return jsonify({"error": "Admin key is not configured"}), 503

    supplied_key = request.headers.get("X-Admin-Key", "")

    if supplied_key != ADMIN_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    url = str(data.get("url", "")).strip()
    description = str(data.get("description", "")).strip()

    parsed = urlparse(url)

    if not title or parsed.scheme not in ("http", "https") or not parsed.netloc:
        return jsonify({
            "error": "A title and valid HTTP/HTTPS URL are required"
        }), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO live_store_videos (title, url, description)
                VALUES (%s, %s, %s)
                RETURNING id
            """, (title, url, description))
            video_id = cur.fetchone()[0]

    return jsonify({
        "success": True,
        "id": video_id,
        "message": "Video added"
    }), 201


@app.delete("/api/videos/<int:video_id>")
def delete_video(video_id):
    if not ADMIN_API_KEY:
        return jsonify({"error": "Admin key is not configured"}), 503

    supplied_key = request.headers.get("X-Admin-Key", "")

    if supplied_key != ADMIN_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM live_store_videos WHERE id = %s RETURNING id",
                (video_id,)
            )
            deleted = cur.fetchone()

    if not deleted:
        return jsonify({"error": "Video not found"}), 404

    return jsonify({"success": True, "message": "Video deleted"})


if __name__ == "__main__":
    setup_database()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

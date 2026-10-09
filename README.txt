LIVE STORE UPDATED FILES

1) Use one Render Web Service named live-store. It runs bot.py (Telegram bot + Flask website/API).
2) Required environment variables:
   BOT_TOKEN = Telegram bot token
   DATABASE_URL = your existing PostgreSQL URL (do not share publicly)
   ADMIN_TELEGRAM_ID = your numeric Telegram user ID
   ADMIN_API_KEY = strong private password for the web Admin Panel
   ADSTERRA_URL = your Adsterra URL
   WEB_APP_URL = https://YOUR-LIVE-STORE.onrender.com (same service URL)
3) Keep your existing PostgreSQL database. Startup runs CREATE/ALTER migrations. Do not delete the database.
4) Upload the contents of this package to the GitHub repository, preserving public/ folder.
5) Update Render to use this single service. Stop/remove the old separate API and static services only after the new service is working.
6) Visit https://YOUR-LIVE-STORE.onrender.com/admin to manage URL-based videos and ad cards.
7) Telegram bot: /start, /admin. Admin video upload flow: send video -> thumbnail photo -> title -> category -> description or /skip.
8) Telegram Bot API has file download size limitations; for very large videos, use a direct HTTPS video URL in the Admin Panel.
9) Opening an ad link is not proof the ad was completed. A verified ad-completion gate requires an Adsterra-supported callback/SDK; this project does not falsely claim completion.

import os, asyncio, threading, logging
from urllib.parse import quote
import requests, psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, send_from_directory, Response, abort
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('live-store')
BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, 'public')
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
ADMIN_RAW = os.getenv('ADMIN_TELEGRAM_ID', '').strip()
WEB_APP_URL = os.getenv('WEB_APP_URL', '').strip().rstrip('/')
ADSTERRA_URL = os.getenv('ADSTERRA_URL', '').strip()
ADMIN_API_KEY = os.getenv('ADMIN_API_KEY', '').strip()
PORT = int(os.getenv('PORT', '10000'))
for key, value in [('BOT_TOKEN', BOT_TOKEN), ('DATABASE_URL', DATABASE_URL), ('WEB_APP_URL', WEB_APP_URL), ('ADMIN_API_KEY', ADMIN_API_KEY)]:
    if not value: raise RuntimeError(f'{key} environment variable is required')
if not ADMIN_RAW.isdigit(): raise RuntimeError('ADMIN_TELEGRAM_ID must be your numeric Telegram user ID')
ADMIN_ID = int(ADMIN_RAW)
app = Flask(__name__, static_folder=PUBLIC, static_url_path='')
upload_state, admin_state = {}, {}

def db(): return psycopg2.connect(DATABASE_URL, sslmode='require', cursor_factory=RealDictCursor)
def setup_db():
    conn=db()
    try:
        with conn:
            with conn.cursor() as c:
                c.execute('''CREATE TABLE IF NOT EXISTS categories(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,name VARCHAR(100) NOT NULL UNIQUE,thumbnail_url TEXT,thumbnail_file_id TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
                c.execute('ALTER TABLE categories ADD COLUMN IF NOT EXISTS thumbnail_url TEXT')
                c.execute('ALTER TABLE categories ADD COLUMN IF NOT EXISTS thumbnail_file_id TEXT')
                c.execute('''CREATE TABLE IF NOT EXISTS videos(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,title VARCHAR(255) NOT NULL,description TEXT NOT NULL DEFAULT '',video_url TEXT,thumbnail_url TEXT,video_file_id TEXT,thumbnail_file_id TEXT,category_id BIGINT REFERENCES categories(id) ON DELETE SET NULL,is_active BOOLEAN NOT NULL DEFAULT TRUE,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
                for col, typ in [('description',"TEXT NOT NULL DEFAULT ''"),('video_url','TEXT'),('thumbnail_url','TEXT'),('video_file_id','TEXT'),('thumbnail_file_id','TEXT'),('category_id','BIGINT REFERENCES categories(id) ON DELETE SET NULL'),('is_active','BOOLEAN NOT NULL DEFAULT TRUE'),('updated_at','TIMESTAMPTZ NOT NULL DEFAULT NOW()')]: c.execute(f'ALTER TABLE videos ADD COLUMN IF NOT EXISTS {col} {typ}')
                c.execute('''CREATE TABLE IF NOT EXISTS ad_cards(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,title VARCHAR(255) NOT NULL,description TEXT NOT NULL DEFAULT '',thumbnail_url TEXT,target_url TEXT NOT NULL DEFAULT '',is_active BOOLEAN NOT NULL DEFAULT TRUE,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
                c.execute('''CREATE TABLE IF NOT EXISTS app_settings(setting_key VARCHAR(100) PRIMARY KEY,setting_value TEXT NOT NULL DEFAULT '',updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
                c.execute("INSERT INTO categories(name) VALUES('Music'),('Dance'),('Drama'),('Entertainment') ON CONFLICT(name) DO NOTHING")
                c.execute("INSERT INTO app_settings(setting_key,setting_value) VALUES('app_name','Live Store'),('app_title','Live Store | Premium Video') ON CONFLICT(setting_key) DO NOTHING")
    finally: conn.close()

def is_admin(uid): return uid == ADMIN_ID
def user_keyboard(admin=False):
    rows=[[KeyboardButton('▶ Open now', web_app=WebAppInfo(url=WEB_APP_URL))],[KeyboardButton('/Start')]]
    if admin: rows.append([KeyboardButton('/Admin')])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, persistent=True)

async def send_categories(update, context):
    conn=db()
    try:
        with conn.cursor() as c:
            c.execute('''SELECT c.name, COALESCE(c.thumbnail_file_id,(SELECT v.thumbnail_file_id FROM videos v WHERE v.category_id=c.id AND v.thumbnail_file_id IS NOT NULL ORDER BY v.id DESC LIMIT 1)) AS photo FROM categories c ORDER BY c.name''')
            rows=c.fetchall()
    finally: conn.close()
    for row in rows:
        url=f"{WEB_APP_URL}/?category={quote(row['name'])}"
        markup=InlineKeyboardMarkup([[InlineKeyboardButton('▶ Open category', web_app=WebAppInfo(url=url))]])
        caption=f"📂 {row['name']}\nএই ক্যাটাগরির ভিডিও দেখতে চাপুন।"
        try:
            if row.get('photo'): await context.bot.send_photo(update.effective_chat.id, row['photo'], caption=caption, reply_markup=markup)
            else: await context.effective_message.reply_text(caption, reply_markup=markup)
        except Exception: await context.effective_message.reply_text(caption, reply_markup=markup)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin=is_admin(update.effective_user.id)
    await update.effective_message.reply_text('👑 Live Store Admin ready.' if admin else '🎬 Welcome to Live Store!\nনিচের বাটন থেকে ভিডিও দেখুন।', reply_markup=user_keyboard(admin))
    if not admin: await send_categories(update, context)

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text('⛔ এই কমান্ডটি শুধু অ্যাডমিনের জন্য।'); return
    keys=[[KeyboardButton('▶ Open now',web_app=WebAppInfo(url=WEB_APP_URL))],[KeyboardButton('📤 ভিডিও আপলোড'),KeyboardButton('➕ ক্যাটাগরি')],[KeyboardButton('➕ বিজ্ঞাপন'),KeyboardButton('🎬 ভিডিও তালিকা')],[KeyboardButton('🗑 ভিডিও মুছুন'),KeyboardButton('❌ বাতিল')],[KeyboardButton('/Start'),KeyboardButton('/Admin')]]
    await update.effective_message.reply_text('🔐 Live Store Admin\nভিডিও আপলোড: ভিডিও → thumbnail → নাম → category → description (/skip)।',reply_markup=ReplyKeyboardMarkup(keys,resize_keyboard=True,persistent=True))

async def cancel_cmd(update, context):
    uid=update.effective_user.id; upload_state.pop(uid,None); admin_state.pop(uid,None)
    await update.effective_message.reply_text('কাজ বাতিল হয়েছে।',reply_markup=user_keyboard(is_admin(uid)))

async def videos_cmd(update, context):
    if not is_admin(update.effective_user.id): await update.effective_message.reply_text('শুধু অ্যাডমিন ব্যবহার করতে পারবেন।'); return
    conn=db()
    try:
        with conn.cursor() as c: c.execute('SELECT v.id,v.title,c.name AS category FROM videos v LEFT JOIN categories c ON c.id=v.category_id WHERE v.is_active=TRUE ORDER BY v.id DESC LIMIT 40'); rows=c.fetchall()
    finally: conn.close()
    await update.effective_message.reply_text('🎬 ভিডিও তালিকা\n'+'\n'.join(f"#{r['id']} — {r['title']} ({r.get('category') or 'Other'})" for r in rows) if rows else 'কোনো ভিডিও নেই।')

async def save_video(update,state,description):
    conn=db()
    try:
        with conn:
            with conn.cursor() as c:
                c.execute('INSERT INTO categories(name) VALUES(%s) ON CONFLICT(name) DO NOTHING',(state['category'],))
                c.execute('SELECT id,thumbnail_file_id FROM categories WHERE name=%s',(state['category'],)); cat=c.fetchone()
                c.execute('INSERT INTO videos(title,description,category_id,video_file_id,thumbnail_file_id,is_active) VALUES(%s,%s,%s,%s,%s,TRUE)',(state['title'],description,cat['id'],state['video_file_id'],state['thumbnail_file_id']))
                if state.get('thumbnail_file_id') and not cat.get('thumbnail_file_id'): c.execute('UPDATE categories SET thumbnail_file_id=%s WHERE id=%s',(state['thumbnail_file_id'],cat['id']))
    finally: conn.close()
    await update.effective_message.reply_text('✅ ভিডিও Live Store-এ সেভ হয়েছে।')

async def handle_media(update,context):
    uid=update.effective_user.id
    if not is_admin(uid): return
    msg=update.effective_message; state=upload_state.get(uid); ast=admin_state.get(uid)
    if state:
        if state['step']=='video':
            if msg.video: state['video_file_id']=msg.video.file_id
            elif msg.document and (msg.document.mime_type or '').startswith('video/'): state['video_file_id']=msg.document.file_id
            else: await msg.reply_text('ভিডিও ফাইল পাঠাও।'); return
            state['step']='thumbnail'; await msg.reply_text('এখন thumbnail ছবি পাঠাও।'); return
        if state['step']=='thumbnail':
            if not msg.photo: await msg.reply_text('Thumbnail হিসেবে ছবি পাঠাও।'); return
            state['thumbnail_file_id']=msg.photo[-1].file_id; state['step']='title'; await msg.reply_text('ভিডিওর নাম পাঠাও।'); return
    if ast and ast.get('step') in ('category_photo','ad_photo'):
        if not msg.photo: await msg.reply_text('একটি ছবি পাঠাও।'); return
        fid=msg.photo[-1].file_id; url=f'{WEB_APP_URL}/telegram-photo/{quote(fid,safe="")}'
        conn=db()
        try:
            with conn:
                with conn.cursor() as c:
                    if ast['step']=='category_photo':
                        c.execute('INSERT INTO categories(name,thumbnail_url,thumbnail_file_id) VALUES(%s,%s,%s) ON CONFLICT(name) DO UPDATE SET thumbnail_url=EXCLUDED.thumbnail_url,thumbnail_file_id=EXCLUDED.thumbnail_file_id',(ast['name'],url,fid))
                    else:
                        ast['thumbnail_url']=url; ast['step']='ad_title'
        finally: conn.close()
        if ast['step']=='category_photo': admin_state.pop(uid,None); await msg.reply_text('✅ ক্যাটাগরি thumbnail-সহ সেভ হয়েছে।')
        else: await msg.reply_text('বিজ্ঞাপনের শিরোনাম পাঠাও।')

async def handle_text(update,context):
    uid=update.effective_user.id; text=(update.effective_message.text or '').strip(); admin=is_admin(uid)
    if text.lower() in ('/start','🚀 start'): await start_cmd(update,context); return
    if text.lower() in ('/admin','🔐 admin'): await admin_cmd(update,context); return
    if text in ('❌ বাতিল','/cancel','❌ আপলোড বাতিল'): await cancel_cmd(update,context); return
    if text=='/skip':
        st=upload_state.get(uid)
        if admin and st and st.get('step')=='description': await save_video(update,st,''); upload_state.pop(uid,None)
        return
    if not admin: return
    if text=='📤 ভিডিও আপলোড': upload_state[uid]={'step':'video'}; await update.effective_message.reply_text('প্রথমে ভিডিও ফাইল পাঠাও।'); return
    if text=='🎬 ভিডিও তালিকা': await videos_cmd(update,context); return
    if text=='➕ ক্যাটাগরি': admin_state[uid]={'step':'category_name'}; await update.effective_message.reply_text('ক্যাটাগরির নাম পাঠাও।'); return
    if text=='➕ বিজ্ঞাপন': admin_state[uid]={'step':'ad_photo'}; await update.effective_message.reply_text('বিজ্ঞাপনের thumbnail ছবি পাঠাও।'); return
    if text=='🗑 ভিডিও মুছুন': admin_state[uid]={'step':'delete_video'}; await update.effective_message.reply_text('/videos দিয়ে ID দেখে ভিডিও ID পাঠাও।'); return
    st=upload_state.get(uid)
    if st:
        if st['step']=='title': st['title']=text[:255]; st['step']='category'; await update.effective_message.reply_text('ক্যাটাগরির নাম পাঠাও (Drama/Music/Dance)।'); return
        if st['step']=='category': st['category']=text[:100]; st['step']='description'; await update.effective_message.reply_text('Description পাঠাও, অথবা /skip।'); return
        if st['step']=='description': await save_video(update,st,text); upload_state.pop(uid,None); return
    ast=admin_state.get(uid)
    if ast:
        if ast['step']=='category_name': ast['name']=text[:100]; ast['step']='category_photo'; await update.effective_message.reply_text('ক্যাটাগরির thumbnail ছবি পাঠাও।'); return
        if ast['step']=='ad_title': ast['title']=text[:255]; ast['step']='ad_description'; await update.effective_message.reply_text('বিজ্ঞাপনের description পাঠাও।'); return
        if ast['step']=='ad_description': ast['description']=text; ast['step']='ad_url'; await update.effective_message.reply_text('বিজ্ঞাপনের target URL পাঠাও, অথবা /skip দিলে ADSTERRA_URL ব্যবহার হবে।'); return
        if ast['step']=='ad_url':
            target=ADSTERRA_URL if text=='/skip' else text
            if not target.startswith(('https://','http://')): await update.effective_message.reply_text('সঠিক http/https URL পাঠাও।'); return
            conn=db()
            try:
                with conn:
                    with conn.cursor() as c: c.execute('INSERT INTO ad_cards(title,description,thumbnail_url,target_url) VALUES(%s,%s,%s,%s)',(ast['title'],ast.get('description',''),ast.get('thumbnail_url'),target))
            finally: conn.close()
            admin_state.pop(uid,None); await update.effective_message.reply_text('✅ বিজ্ঞাপনের কার্ড সেভ হয়েছে।'); return
        if ast['step']=='delete_video':
            if not text.isdigit(): await update.effective_message.reply_text('সংখ্যাসূচক ভিডিও ID পাঠাও।'); return
            conn=db()
            try:
                with conn:
                    with conn.cursor() as c: c.execute('UPDATE videos SET is_active=FALSE,updated_at=NOW() WHERE id=%s',(int(text),)); n=c.rowcount
            finally: conn.close()
            admin_state.pop(uid,None); await update.effective_message.reply_text('✅ ভিডিও মুছে দেওয়া হয়েছে।' if n else 'ভিডিও ID পাওয়া যায়নি।'); return

@app.get('/')
def home(): return send_from_directory(PUBLIC,'index.html')
@app.get('/admin')
def admin_page(): return send_from_directory(BASE,'admin.html')
@app.get('/health')
def health(): return jsonify(ok=True,app='Live Store')
@app.get('/api/config')
def config(): return jsonify(name='Live Store',title='Live Store | Premium Video',ad_url=ADSTERRA_URL)

def tg_path(fid):
    r=requests.get(f'https://api.telegram.org/bot{BOT_TOKEN}/getFile',params={'file_id':fid},timeout=20); r.raise_for_status(); data=r.json()
    if not data.get('ok'): raise RuntimeError('Telegram file lookup failed')
    return data['result']['file_path']
def proxy_file(fid,ctype):
    path=tg_path(fid); r=requests.get(f'https://api.telegram.org/file/bot{BOT_TOKEN}/{path}',stream=True,timeout=60); r.raise_for_status()
    return Response(r.iter_content(128*1024),content_type=r.headers.get('Content-Type',ctype))
@app.get('/telegram-photo/<path:fid>')
def telegram_photo(fid):
    try: return proxy_file(fid,'image/jpeg')
    except Exception: abort(404)
@app.get('/media/<int:vid>')
def media(vid):
    conn=db()
    try:
        with conn.cursor() as c: c.execute('SELECT video_url,video_file_id FROM videos WHERE id=%s AND is_active=TRUE',(vid,)); r=c.fetchone()
    finally: conn.close()
    if not r: abort(404)
    if r.get('video_url'): return Response(status=302,headers={'Location':r['video_url']})
    if not r.get('video_file_id'): abort(404)
    try: return proxy_file(r['video_file_id'],'video/mp4')
    except Exception: abort(502)
@app.get('/thumbnail/<int:vid>')
def thumbnail(vid):
    conn=db()
    try:
        with conn.cursor() as c: c.execute('SELECT thumbnail_url,thumbnail_file_id FROM videos WHERE id=%s AND is_active=TRUE',(vid,)); r=c.fetchone()
    finally: conn.close()
    if not r: abort(404)
    if r.get('thumbnail_url'): return Response(status=302,headers={'Location':r['thumbnail_url']})
    if not r.get('thumbnail_file_id'): abort(404)
    try: return proxy_file(r['thumbnail_file_id'],'image/jpeg')
    except Exception: abort(502)
@app.get('/api/videos')
def api_videos():
    category=(request.args.get('category') or '').strip(); conn=db()
    try:
        with conn.cursor() as c:
            q='SELECT v.id,v.title,v.description,v.video_url,v.thumbnail_url,v.video_file_id,v.thumbnail_file_id,c.name AS category,v.created_at FROM videos v LEFT JOIN categories c ON c.id=v.category_id WHERE v.is_active=TRUE'; params=[]
            if category: q+=' AND LOWER(c.name)=LOWER(%s)'; params.append(category)
            q+=' ORDER BY v.created_at DESC'; c.execute(q,params); rows=c.fetchall()
    finally: conn.close()
    return jsonify([{'id':r['id'],'title':r['title'],'description':r['description'] or '', 'category':r['category'] or 'Other','video_url':r['video_url'] or f"/media/{r['id']}",'thumbnail_url':r['thumbnail_url'] or f"/thumbnail/{r['id']}"} for r in rows])
@app.get('/api/categories')
def api_categories():
    conn=db()
    try:
        with conn.cursor() as c:
            c.execute('''SELECT c.id,c.name,COALESCE(c.thumbnail_url,(SELECT v.thumbnail_url FROM videos v WHERE v.category_id=c.id AND v.is_active=TRUE AND v.thumbnail_url IS NOT NULL ORDER BY v.id DESC LIMIT 1)) AS image_url,(SELECT COUNT(*) FROM videos v WHERE v.category_id=c.id AND v.is_active=TRUE) AS video_count FROM categories c ORDER BY c.name'''); rows=c.fetchall()
    finally: conn.close()
    return jsonify([dict(r) for r in rows])
@app.get('/api/ads')
def api_ads():
    conn=db()
    try:
        with conn.cursor() as c: c.execute('SELECT id,title,description,thumbnail_url,target_url FROM ad_cards WHERE is_active=TRUE ORDER BY id DESC'); rows=c.fetchall()
    finally: conn.close()
    return jsonify(rows)
def authorized(): return bool(ADMIN_API_KEY and request.headers.get('X-Admin-Key')==ADMIN_API_KEY)
@app.post('/api/admin/login')
def admin_login():
    d=request.get_json(silent=True) or {}
    if not ADMIN_API_KEY or d.get('password')!=ADMIN_API_KEY: return jsonify(error='Invalid admin password'),401
    return jsonify(logged_in=True,token=ADMIN_API_KEY)
@app.get('/api/admin/session')
def admin_session():
    if not authorized(): return jsonify(logged_in=False),401
    return jsonify(logged_in=True)
@app.post('/api/videos')
def create_video():
    if not authorized(): return jsonify(error='Unauthorized'),401
    d=request.get_json(silent=True) or {}; title=(d.get('title') or '').strip(); url=(d.get('video_url') or d.get('url') or '').strip(); category=(d.get('category') or 'Entertainment').strip()[:100]
    if not title or not url.startswith(('http://','https://')): return jsonify(error='Title and valid video URL required'),400
    conn=db()
    try:
        with conn:
            with conn.cursor() as c:
                c.execute('INSERT INTO categories(name) VALUES(%s) ON CONFLICT(name) DO NOTHING',(category,)); c.execute('SELECT id FROM categories WHERE name=%s',(category,)); cid=c.fetchone()['id']
                c.execute('INSERT INTO videos(title,description,video_url,thumbnail_url,category_id) VALUES(%s,%s,%s,%s,%s) RETURNING id',(title[:255],(d.get('description') or '').strip(),url,(d.get('thumbnail_url') or '').strip() or None,cid)); vid=c.fetchone()['id']
    finally: conn.close()
    return jsonify(success=True,id=vid),201
@app.put('/api/videos/<int:vid>')
def update_video(vid):
    if not authorized(): return jsonify(error='Unauthorized'),401
    title=((request.get_json(silent=True) or {}).get('title') or '').strip()
    if not title: return jsonify(error='Title required'),400
    conn=db()
    try:
        with conn:
            with conn.cursor() as c: c.execute('UPDATE videos SET title=%s,updated_at=NOW() WHERE id=%s AND is_active=TRUE',(title[:255],vid)); n=c.rowcount
    finally: conn.close()
    return (jsonify(success=True) if n else (jsonify(error='Not found'),404))
@app.delete('/api/videos/<int:vid>')
def delete_video(vid):
    if not authorized(): return jsonify(error='Unauthorized'),401
    conn=db()
    try:
        with conn:
            with conn.cursor() as c: c.execute('UPDATE videos SET is_active=FALSE,updated_at=NOW() WHERE id=%s',(vid,)); n=c.rowcount
    finally: conn.close()
    return (jsonify(success=True) if n else (jsonify(error='Not found'),404))
@app.post('/api/ads')
def create_ad():
    if not authorized(): return jsonify(error='Unauthorized'),401
    d=request.get_json(silent=True) or {}; title=(d.get('title') or '').strip(); target=(d.get('target_url') or ADSTERRA_URL).strip()
    if not title or not target.startswith(('http://','https://')): return jsonify(error='Title and valid target URL required'),400
    conn=db()
    try:
        with conn:
            with conn.cursor() as c: c.execute('INSERT INTO ad_cards(title,description,thumbnail_url,target_url) VALUES(%s,%s,%s,%s) RETURNING id',(title[:255],(d.get('description') or '').strip(),(d.get('thumbnail_url') or '').strip() or None,target)); aid=c.fetchone()['id']
    finally: conn.close()
    return jsonify(success=True,id=aid),201
@app.delete('/api/ads/<int:aid>')
def delete_ad(aid):
    if not authorized(): return jsonify(error='Unauthorized'),401
    conn=db()
    try:
        with conn:
            with conn.cursor() as c: c.execute('UPDATE ad_cards SET is_active=FALSE WHERE id=%s',(aid,)); n=c.rowcount
    finally: conn.close()
    return (jsonify(success=True) if n else (jsonify(error='Not found'),404))

async def skip_cmd(update,context):
    uid=update.effective_user.id; st=upload_state.get(uid)
    if is_admin(uid) and st and st.get('step')=='description': await save_video(update,st,''); upload_state.pop(uid,None)
    else: await update.effective_message.reply_text('এখন skip করার কিছু নেই।')
def bot_thread():
    async def runner():
        bot=Application.builder().token(BOT_TOKEN).build()
        bot.add_handler(CommandHandler('start',start_cmd)); bot.add_handler(CommandHandler('admin',admin_cmd)); bot.add_handler(CommandHandler('videos',videos_cmd)); bot.add_handler(CommandHandler('cancel',cancel_cmd)); bot.add_handler(CommandHandler('skip',skip_cmd))
        bot.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL,handle_media))
        bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_text))
        await bot.initialize(); await bot.start(); await bot.updater.start_polling(drop_pending_updates=True)
        while True: await asyncio.sleep(3600)
    asyncio.run(runner())
if __name__=='__main__':
    setup_db()
    threading.Thread(target=bot_thread,daemon=True,name='telegram-bot').start()
    app.run(host='0.0.0.0',port=PORT,debug=False,use_reloader=False)

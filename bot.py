import os
import sqlite3
import datetime as dt
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

DB_FILE = "data.db"

MORNING_TIME = dt.time(hour=9, minute=0)
MIDDAY_TIME  = dt.time(hour=12, minute=0)
EVENING_TIME = dt.time(hour=20, minute=30)

DEFAULT_TZ = "Europe/London"

TZ_CHOICES = [
    ("UK — London", "Europe/London"),
    ("Ireland — Dublin", "Europe/Dublin"),
    ("Canada — Eastern", "America/Toronto"),
    ("Canada — Central", "America/Winnipeg"),
    ("Canada — Mountain", "America/Edmonton"),
    ("Canada — Pacific", "America/Vancouver"),
]

QUOTES = [
    "Discipline is choosing between what you want now and what you want most. — Abraham Lincoln",
    "Success is the sum of small efforts, repeated day in and day out. — Robert Collier",
    "The future depends on what you do today. — Mahatma Gandhi",
    "It always seems impossible until it’s done. — Nelson Mandela",
    "The secret of getting ahead is getting started. — Mark Twain",
]

def load_token():
    token = os.getenv("BOT_TOKEN")
    if token:
        return token
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                if line.startswith("BOT_TOKEN="):
                    return line.split("=",1)[1].strip()
    raise RuntimeError("BOT_TOKEN not found")

def db():
    return sqlite3.connect(DB_FILE)

def init_db():
    with db() as c:
        cur = c.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            timezone TEXT DEFAULT 'Europe/London'
        )
        """)
        c.commit()

def ensure_user(uid):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)",(uid,))
        c.commit()

def get_tz(uid):
    with db() as c:
        r=c.execute("SELECT timezone FROM users WHERE user_id=?",(uid,)).fetchone()
        return r[0] if r else DEFAULT_TZ

def set_tz(uid,tz):
    with db() as c:
        c.execute("UPDATE users SET timezone=? WHERE user_id=?",(tz,uid))
        c.commit()

def tz_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t,callback_data=z)] for t,z in TZ_CHOICES]
    )

async def start(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    await update.message.reply_text(
        "Daily Income Coach\n\n"
        "/timezone — выбрать часовой пояс"
    )

async def timezone_cmd(update:Update, ctx):
    await update.message.reply_text("Выбери часовой пояс:", reply_markup=tz_keyboard())

async def tz_callback(update:Update, ctx):
    q=update.callback_query
    await q.answer()
    set_tz(q.from_user.id, q.data)
    await q.edit_message_text(f"Готово ✅ Часовой пояс: {q.data}")

async def morning_job(ctx):
    uid=ctx.job.chat_id
    tz=ZoneInfo(get_tz(uid))
    today=dt.datetime.now(tz).date()
    quote=QUOTES[today.toordinal()%len(QUOTES)]
    await ctx.bot.send_message(uid,f"☀️ {quote}\n\nЧто ты сделаешь сегодня для дохода?")

async def midday_job(ctx):
    await ctx.bot.send_message(ctx.job.chat_id,"🕛 Ты работаешь сегодня над своей целью?")

async def evening_job(ctx):
    await ctx.bot.send_message(ctx.job.chat_id,"🌙 Что ты сделал сегодня для дохода?")

def schedule(app,uid):
    tz=ZoneInfo(get_tz(uid))
    jq=app.job_queue
    jq.run_daily(morning_job, MORNING_TIME.replace(tzinfo=tz), chat_id=uid)
    jq.run_daily(midday_job, MIDDAY_TIME.replace(tzinfo=tz), chat_id=uid)
    jq.run_daily(evening_job, EVENING_TIME.replace(tzinfo=tz), chat_id=uid)

async def on_message(update:Update, ctx):
    schedule(ctx.application, update.effective_user.id)
    await update.message.reply_text("Принято 👍")

def main():
    init_db()
    app=Application.builder().token(load_token()).build()

    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("timezone",timezone_cmd))
    app.add_handler(CallbackQueryHandler(tz_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,on_message))

    app.run_polling()

if __name__=="__main__":
    main()

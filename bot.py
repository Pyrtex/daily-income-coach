import os
import sqlite3
import random
from datetime import time
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from zoneinfo import ZoneInfo

DB_FILE = "data.db"

QUOTES = [
    "Discipline is choosing between what you want now and what you want most.",
    "Success is the sum of small efforts repeated day in and day out.",
    "Don't watch the clock; do what it does. Keep going.",
    "Your future is created by what you do today, not tomorrow.",
    "Small progress is still progress."
]

def load_token():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN not found")
    return token

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            timezone TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_user_tz(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT timezone FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_user_tz(user_id, tz):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, timezone) VALUES (?, ?)", (user_id, tz))
    conn.commit()
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send /settz Europe/Vilnius to set your timezone.")

async def settz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Example: /settz Europe/Vilnius")
        return
    tz = context.args[0]
    try:
        ZoneInfo(tz)
    except:
        await update.message.reply_text("Invalid timezone.")
        return
    set_user_tz(update.effective_user.id, tz)
    await update.message.reply_text(f"Timezone set to {tz}")

async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text="🌅 Morning. What ONE thing will you do today to increase your income?"
    )

async def midday_job(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text="🕛 Are you working today toward your main goal?"
    )

async def evening_job(context: ContextTypes.DEFAULT_TYPE):
    quote = random.choice(QUOTES)
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"🌙 Evening reflection.\nQuote of the day:\n\n\"{quote}\""
    )

async def register_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tz = get_user_tz(user_id)
    if not tz:
        await update.message.reply_text("Set timezone first with /settz Europe/Vilnius")
        return

    tzinfo = ZoneInfo(tz)

    jq = context.application.job_queue
    jq.run_daily(morning_job, time=time(9,0,tzinfo=tzinfo), chat_id=user_id)
    jq.run_daily(midday_job, time=time(12,0,tzinfo=tzinfo), chat_id=user_id)
    jq.run_daily(evening_job, time=time(20,0,tzinfo=tzinfo), chat_id=user_id)

    await update.message.reply_text("Daily reminders registered.")

def main():
    init_db()
    token = load_token()
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settz", settz))
    app.add_handler(CommandHandler("register", register_jobs))

    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()

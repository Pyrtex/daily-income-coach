import os
import sqlite3
import random
from datetime import datetime, time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

DB_FILE = "data.db"

QUOTES = [
    "Discipline beats motivation.",
    "Small steps every day compound into big results.",
    "Action creates clarity.",
    "Progress > perfection.",
    "Consistency is a superpower.",
    "Focus on the next action, not the whole mountain.",
    "Your income follows your value — build value daily.",
    "Start before you feel ready.",
    "What gets measured gets improved.",
    "Done is better than perfect."
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

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            streak INTEGER DEFAULT 0,
            last_day TEXT,
            tz TEXT DEFAULT 'Europe/Vilnius'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_quote (
            day TEXT PRIMARY KEY,
            quote TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sent_flags (
            user_id INTEGER,
            day TEXT,
            kind TEXT,
            PRIMARY KEY(user_id, day, kind)
        )
    """)
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT streak, last_day, tz FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return 0, None, "Europe/Vilnius"
    return row[0], row[1], row[2]

def save_user(user_id, streak, last_day, tz):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO users(user_id, streak, last_day, tz)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            streak=excluded.streak,
            last_day=excluded.last_day,
            tz=excluded.tz
    """, (user_id, streak, last_day, tz))
    conn.commit()
    conn.close()

def get_or_set_daily_quote(day):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT quote FROM daily_quote WHERE day=?", (day,))
    row = c.fetchone()
    if row:
        conn.close()
        return row[0]
    q = random.choice(QUOTES)
    c.execute("INSERT INTO daily_quote(day, quote) VALUES(?,?)", (day, q))
    conn.commit()
    conn.close()
    return q

def today_str(tz):
    return datetime.now(tz).strftime("%Y-%m-%d")

def was_sent(user_id, day, kind):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM sent_flags WHERE user_id=? AND day=? AND kind=?", (user_id, day, kind))
    r = c.fetchone()
    conn.close()
    return bool(r)

def mark_sent(user_id, day, kind):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO sent_flags VALUES(?,?,?)", (user_id, day, kind))
    conn.commit()
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Daily Income Coach ✅\n\n"
        "Set your timezone:\n"
        "/settz Europe/Vilnius\n"
        "/settz Europe/London\n"
        "/settz America/New_York\n\n"
        "Commands:\n"
        "/morning\n"
        "/evening\n"
        "/quote"
    )

async def settz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /settz Europe/Vilnius")
        return
    tzname = context.args[0]
    try:
        ZoneInfo(tzname)
    except Exception:
        await update.message.reply_text("Invalid timezone. Example: Europe/Vilnius")
        return
    streak, last_day, _ = get_user(update.effective_user.id)
    save_user(update.effective_user.id, streak, last_day, tzname)
    await update.message.reply_text(f"Timezone set to {tzname}")

async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, _, tzname = get_user(update.effective_user.id)
    tz = ZoneInfo(tzname)
    day = today_str(tz)
    q = get_or_set_daily_quote(day)
    await update.message.reply_text(
        f"☀️ Morning\n\n💬 Quote of the day:\n“{q}”\n\n"
        "What ONE thing will you do today to increase your income?"
    )

async def evening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, _, tzname = get_user(update.effective_user.id)
    tz = ZoneInfo(tzname)
    day = today_str(tz)
    q = get_or_set_daily_quote(day)
    await update.message.reply_text(
        f"🌙 Evening\n\n💬 Quote of the day:\n“{q}”\n\n"
        "What did you do today to increase your income?"
    )

async def quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, _, tzname = get_user(update.effective_user.id)
    tz = ZoneInfo(tzname)
    day = today_str(tz)
    q = get_or_set_daily_quote(day)
    await update.message.reply_text(f"💬 Quote of the day:\n“{q}”")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    streak, last_day, tzname = get_user(user_id)
    tz = ZoneInfo(tzname)
    today = today_str(tz)
    if last_day != today:
        streak += 1
    save_user(user_id, streak, today, tzname)
    await update.message.reply_text(f"🔥 Streak: {streak}")

async def scheduler(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, tz FROM users")
    users = c.fetchall()
    conn.close()

    for uid, tzname in users:
        tz = ZoneInfo(tzname)
        now = datetime.now(tz)
        day = today_str(tz)

        if now.hour == 8 and not was_sent(uid, day, "morning"):
            mark_sent(uid, day, "morning")
            await context.bot.send_message(uid, "☀️ Morning! Use /morning")

        if now.hour == 12 and not was_sent(uid, day, "midday"):
            mark_sent(uid, day, "midday")
            await context.bot.send_message(uid, "🕛 Are you working today to reach your goal?")

        if now.hour == 21 and not was_sent(uid, day, "evening"):
            mark_sent(uid, day, "evening")
            await context.bot.send_message(uid, "🌙 Evening! Use /evening")

def main():
    init_db()
    token = load_token()
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settz", settz))
    app.add_handler(CommandHandler("morning", morning))
    app.add_handler(CommandHandler("evening", evening))
    app.add_handler(CommandHandler("quote", quote))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.job_queue.run_repeating(scheduler, interval=60, first=10)

    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()

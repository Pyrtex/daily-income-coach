import os
import sqlite3
import random
from datetime import datetime, time, timezone

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

def load_token() -> str:
    token = os.getenv("BOT_TOKEN")
    if token:
        return token
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("BOT_TOKEN not found (env var BOT_TOKEN or .env)")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            streak INTEGER DEFAULT 0,
            last_day TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_quote (
            day TEXT PRIMARY KEY,
            quote TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS midday_checkin_sent (
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            PRIMARY KEY(user_id, day)
        )
    """)
    conn.commit()
    conn.close()

def today_str(tz: timezone) -> str:
    return datetime.now(tz).strftime("%Y-%m-%d")

def get_or_set_daily_quote(day: str) -> str:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT quote FROM daily_quote WHERE day=?", (day,))
    row = c.fetchone()
    if row:
        conn.close()
        return row[0]
    quote = random.choice(QUOTES)
    c.execute("INSERT INTO daily_quote(day, quote) VALUES(?, ?)", (day, quote))
    conn.commit()
    conn.close()
    return quote

def get_user(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT streak, last_day FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return 0, None
    return int(row[0] or 0), row[1]

def save_user(user_id: int, streak: int, last_day: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO users (user_id, streak, last_day)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            streak=excluded.streak,
            last_day=excluded.last_day
    """, (user_id, streak, last_day))
    conn.commit()
    conn.close()

def calc_streak(prev_day: str | None, current_streak: int, tz: timezone) -> int:
    t = today_str(tz)
    if prev_day == t:
        return current_streak
    return current_streak + 1

def mark_midday_sent(user_id: int, day: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO midday_checkin_sent(user_id, day) VALUES(?, ?)", (user_id, day))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Daily Income Coach ✅\n\n"
        "Commands:\n"
        "/morning — morning prompt + quote of the day\n"
        "/evening — evening prompt + quote of the day\n"
        "/quote — show quote of the day\n\n"
        "Midday check-in at 12:00 UTC is enabled for users who have interacted with the bot."
    )

async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz = timezone.utc
    day = today_str(tz)
    q = get_or_set_daily_quote(day)
    await update.message.reply_text(f"💬 Quote of the day: “{q}”")

async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz = timezone.utc
    day = today_str(tz)
    q = get_or_set_daily_quote(day)
    await update.message.reply_text(
        f"☀️ Morning\n\n"
        f"💬 Quote of the day: “{q}”\n\n"
        f"What ONE thing will you do today to increase your income?"
    )

async def cmd_evening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz = timezone.utc
    day = today_str(tz)
    q = get_or_set_daily_quote(day)
    await update.message.reply_text(
        f"🌙 Evening\n\n"
        f"💬 Quote of the day: “{q}”\n\n"
        f"What did you do today to increase your income? Reply with ONE sentence."
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz = timezone.utc
    user_id = update.effective_user.id
    streak, last_day = get_user(user_id)
    new_streak = calc_streak(last_day, streak, tz)
    save_user(user_id, new_streak, today_str(tz))
    await update.message.reply_text(f"✅ Noted.\n🔥 Streak: {new_streak}")

async def midday_checkin_job(context: ContextTypes.DEFAULT_TYPE):
    tz = timezone.utc
    day = today_str(tz)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    user_ids = [row[0] for row in c.fetchall()]
    conn.close()

    for uid in user_ids:
        if not mark_midday_sent(uid, day):
            continue
        try:
            await context.bot.send_message(
                chat_id=uid,
                text="🕛 Midday check-in:\n\nAre you working today to reach your goal?"
            )
        except Exception:
            pass

def main():
    init_db()
    token = load_token()
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("morning", cmd_morning))
    app.add_handler(CommandHandler("evening", cmd_evening))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.job_queue.run_daily(midday_checkin_job, time=time(hour=12, minute=0, tzinfo=timezone.utc))

    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()

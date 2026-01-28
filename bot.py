import os
import sqlite3
from datetime import time
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

DB_FILE = "data.db"

def load_token():
    token = None
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    if not token:
        raise RuntimeError("BOT_TOKEN not found in .env")
    return token

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "user_id INTEGER PRIMARY KEY, "
        "streak INTEGER DEFAULT 0, "
        "last_answer TEXT, "
        "tz TEXT)"
    )
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT streak, tz FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"streak": row[0] if row[0] is not None else 0, "tz": row[1]}
    return {"streak": 0, "tz": None}

def save_user(user_id, streak=None, last_answer=None, tz=None):
    current = get_user(user_id)
    if streak is None:
        streak = current["streak"]
    if tz is None:
        tz = current["tz"]

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO users (user_id, streak, last_answer, tz) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET streak=?, last_answer=?, tz=?",
        (user_id, streak, last_answer, tz, streak, last_answer, tz)
    )
    conn.commit()
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Daily Income Coach.\n\n"
        "Commands:\n"
        "/morning — send morning prompt now\n"
        "/evening — send evening check now\n"
        "/settz — enable auto messages (08:00 & 21:00, Europe/Vilnius)\n"
        "/mytz — show timezone status\n"
    )

async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Morning. What ONE thing will you do today to increase your income?")

async def evening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Evening check. Did you complete today’s action? Reply YES or NO.")

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text not in ["YES", "NO"]:
        return

    user_id = update.message.from_user.id
    user = get_user(user_id)
    streak = user["streak"]

    if text == "YES":
        streak += 1
        reply = f"Logged. Streak: {streak}"
    else:
        streak = 0
        reply = "Zero day. Streak reset."

    save_user(user_id, streak=streak, last_answer=text)
    await update.message.reply_text(reply)

async def send_morning_job(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data["user_id"]
    await context.bot.send_message(chat_id=user_id, text="Morning. What ONE thing will you do today to increase your income?")

async def send_evening_job(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data["user_id"]
    await context.bot.send_message(chat_id=user_id, text="Evening check. Did you complete today’s action? Reply YES or NO.")

async def settz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    tz = "Europe/Vilnius"
    save_user(user_id, tz=tz)

    # remove old scheduled jobs
    for j in context.job_queue.get_jobs_by_name(f"morning_{user_id}"):
        j.schedule_removal()
    for j in context.job_queue.get_jobs_by_name(f"evening_{user_id}"):
        j.schedule_removal()

    # schedule new jobs (server local time; on your Mac it matches your timezone)
    context.job_queue.run_daily(
        send_morning_job,
        time=time(hour=8, minute=0),
        name=f"morning_{user_id}",
        data={"user_id": user_id},
    )
    context.job_queue.run_daily(
        send_evening_job,
        time=time(hour=21, minute=0),
        name=f"evening_{user_id}",
        data={"user_id": user_id},
    )

    await update.message.reply_text("OK. Auto messages scheduled for 08:00 and 21:00 (Europe/Vilnius).")

async def mytz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user = get_user(user_id)
    await update.message.reply_text(f"Timezone: {user['tz'] if user['tz'] else 'not set'}")

def main():
    init_db()
    token = load_token()
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("morning", morning))
    app.add_handler(CommandHandler("evening", evening))
    app.add_handler(CommandHandler("settz", settz))
    app.add_handler(CommandHandler("mytz", mytz))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer))

    print("Bot started. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()

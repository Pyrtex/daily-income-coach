import os
import sqlite3
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

DB_FILE = "data.db"

def load_token():
    token = os.getenv("BOT_TOKEN")
    if token:
        return token

    token = None
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    if not token:
        raise RuntimeError("BOT_TOKEN not found (env var BOT_TOKEN or .env)")
    return token

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, streak INTEGER, last_answer TEXT)")
    conn.commit()
    conn.close()

def get_streak(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT streak FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def save_streak(user_id, streak, answer):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO users (user_id, streak, last_answer) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET streak=?, last_answer=?",
        (user_id, streak, answer, streak, answer)
    )
    conn.commit()
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Daily Income Coach. Use /morning and /evening.")

async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Morning. What ONE thing will you do today to increase your income?")

async def evening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Did you complete today’s action? Reply YES or NO.")

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text not in ["YES", "NO"]:
        return
    user_id = update.message.from_user.id
    streak = get_streak(user_id)
    if text == "YES":
        streak += 1
        reply = f"Logged. Streak: {streak}"
    else:
        streak = 0
        reply = "Zero day. Streak reset."
    save_streak(user_id, streak, text)
    await update.message.reply_text(reply)

def main():
    init_db()
    token = load_token()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("morning", morning))
    app.add_handler(CommandHandler("evening", evening))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer))
    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()

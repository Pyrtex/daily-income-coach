
import os
import sqlite3
import random
import time as time_mod
from datetime import time
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

DB_FILE = "data.db"

TRIAL_DAYS = 3
TRIAL_SECONDS = TRIAL_DAYS * 24 * 60 * 60

SUBSCRIBE_CALLBACK = "SUBSCRIBE"

QUOTES = [
    "Discipline beats motivation.",
    "Small steps every day.",
    "Focus on the next action, not the whole mountain.",
    "Your habits decide your future.",
    "Action creates confidence.",
]


# ---------------- Helpers ----------------
def now_ts() -> int:
    return int(time_mod.time())


def seconds_to_human(sec: int) -> str:
    days = sec // 86400
    hours = (sec % 86400) // 3600
    mins = (sec % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def load_token() -> str:
    token = os.getenv("BOT_TOKEN")
    if token:
        return token.strip()

    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()

    raise RuntimeError("BOT_TOKEN not found (set env var BOT_TOKEN or create .env with BOT_TOKEN=...)")


def load_admin_ids() -> set[int]:
    """
    ADMIN_IDS=123,456,789
    """
    raw = os.getenv("ADMIN_IDS", "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


ADMIN_IDS = load_admin_ids()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """
    Buttons at the bottom (works like a simple menu).
    """
    return ReplyKeyboardMarkup(
        [
            ["/start", "/status"],
            ["/timezone", "/subscribe"],
            ["/help"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Choose a command…",
    )


def subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Subscribe", callback_data=SUBSCRIBE_CALLBACK)]]
    )


def help_text() -> str:
    return (
        "📋 Commands\n"
        "/start — activate schedule\n"
        "/timezone — set your time zone\n"
        "/status — show your status\n"
        "/subscribe — subscription info\n"
        "/help — show this menu\n"
    )


# ---------------- DB ----------------
def init_db() -> None:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            timezone TEXT,
            trial_start_ts INTEGER,
            trial_end_ts INTEGER,
            expired_notified INTEGER DEFAULT 0,
            subscribed_until_ts INTEGER
        )
        """
    )

    # Safe migrations for older DBs
    for ddl in [
        "ALTER TABLE users ADD COLUMN trial_start_ts INTEGER",
        "ALTER TABLE users ADD COLUMN trial_end_ts INTEGER",
        "ALTER TABLE users ADD COLUMN expired_notified INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN subscribed_until_ts INTEGER",
    ]:
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


def ensure_user_row(user_id: int) -> None:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


def set_timezone(user_id: int, tz_name: str) -> None:
    ensure_user_row(user_id)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET timezone=? WHERE user_id=?", (tz_name, user_id))
    conn.commit()
    conn.close()


def ensure_trial(user_id: int) -> None:
    ensure_user_row(user_id)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT trial_start_ts, trial_end_ts FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()

    if not row or not row[0] or not row[1]:
        start = now_ts()
        end = start + TRIAL_SECONDS
        c.execute(
            "UPDATE users SET trial_start_ts=?, trial_end_ts=?, expired_notified=0 WHERE user_id=?",
            (start, end, user_id),
        )

    conn.commit()
    conn.close()


def get_user(user_id: int) -> dict | None:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        SELECT user_id, timezone, trial_start_ts, trial_end_ts, expired_notified, subscribed_until_ts
        FROM users
        WHERE user_id=?
        """,
        (user_id,),
    )
    row = c.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "user_id": int(row[0]),
        "timezone": row[1],
        "trial_start_ts": int(row[2]) if row[2] else None,
        "trial_end_ts": int(row[3]) if row[3] else None,
        "expired_notified": int(row[4]) if row[4] is not None else 0,
        "subscribed_until_ts": int(row[5]) if row[5] else None,
    }


def set_expired_notified(user_id: int, value: int) -> None:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET expired_notified=? WHERE user_id=?", (value, user_id))
    conn.commit()
    conn.close()


def set_subscription_until(user_id: int, until_ts: int | None) -> None:
    ensure_user_row(user_id)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if until_ts is None:
        c.execute("UPDATE users SET subscribed_until_ts=NULL WHERE user_id=?", (user_id,))
    else:
        c.execute("UPDATE users SET subscribed_until_ts=? WHERE user_id=?", (until_ts, user_id))
    conn.commit()
    conn.close()


def get_all_users_with_timezones() -> list[tuple[int, str]]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        SELECT user_id, timezone
        FROM users
        WHERE timezone IS NOT NULL AND timezone <> ''
        """
    )
    rows = c.fetchall()
    conn.close()
    return [(int(uid), str(tz)) for uid, tz in rows]


def is_subscription_active(user: dict) -> tuple[bool, int]:
    """
    Returns (active, seconds_left).
    """
    until = user.get("subscribed_until_ts")
    if not until:
        return (False, 0)
    left = max(0, until - now_ts())
    return (left > 0), left


def is_trial_active(user: dict) -> tuple[bool, int]:
    end = user.get("trial_end_ts") or 0
    left = max(0, end - now_ts())
    return (left > 0), left


def has_access(user: dict) -> tuple[bool, str, int]:
    """
    Returns (access, source, seconds_left).
    source: 'subscription' | 'trial' | 'none'
    """
    sub_active, sub_left = is_subscription_active(user)
    if sub_active:
        return (True, "subscription", sub_left)

    trial_active, trial_left = is_trial_active(user)
    if trial_active:
        return (True, "trial", trial_left)

    return (False, "none", 0)


# ---------------- Scheduling ----------------
def remove_user_jobs(app: Application, user_id: int) -> None:
    for job in app.job_queue.jobs():
        if job.name and job.name.startswith(f"user:{user_id}:"):
            job.schedule_removal()


def reschedule(app: Application, user_id: int, tz_name: str) -> None:
    tz = ZoneInfo(tz_name)

    remove_user_jobs(app, user_id)

    app.job_queue.run_daily(
        morning_job,
        time=time(hour=8, minute=0, tzinfo=tz),
        chat_id=user_id,
        name=f"user:{user_id}:morning",
        data={"user_id": user_id},
    )
    app.job_queue.run_daily(
        midday_job,
        time=time(hour=12, minute=0, tzinfo=tz),
        chat_id=user_id,
        name=f"user:{user_id}:midday",
        data={"user_id": user_id},
    )
    app.job_queue.run_daily(
        evening_job,
        time=time(hour=20, minute=0, tzinfo=tz),
        chat_id=user_id,
        name=f"user:{user_id}:evening",
        data={"user_id": user_id},
    )


async def handle_no_access(ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    user = get_user(user_id)
    if not user:
        return

    # stop scheduled jobs
    remove_user_jobs(ctx.application, user_id)

    if user.get("expired_notified", 0) == 0:
        set_expired_notified(user_id, 1)
        await ctx.bot.send_message(
            chat_id=user_id,
            text=(
                "⛔ Your free trial has expired.\n\n"
                "To continue receiving reminders, you need an active subscription.\n"
                "Click Subscribe or type /subscribe."
            ),
            reply_markup=subscribe_keyboard(),
        )


# ---------------- Jobs ----------------
async def morning_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = ctx.job.data["user_id"]
    user = get_user(user_id)
    if not user:
        return

    access, source, left = has_access(user)
    if not access:
        await handle_no_access(ctx, user_id)
        return

    quote = random.choice(QUOTES)
    await ctx.bot.send_message(
        chat_id=user_id,
        text=f"☀️ Morning quote:\n“{quote}”\n\nWhat ONE thing will you do today to increase your income?",
    )


async def midday_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = ctx.job.data["user_id"]
    user = get_user(user_id)
    if not user:
        return

    access, source, left = has_access(user)
    if not access:
        await handle_no_access(ctx, user_id)
        return

    await ctx.bot.send_message(
        chat_id=user_id,
        text="🕛 Midday check:\nWhat have you done so far today to reach your goal?",
    )


async def evening_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = ctx.job.data["user_id"]
    user = get_user(user_id)
    if not user:
        return

    access, source, left = has_access(user)
    if not access:
        await handle_no_access(ctx, user_id)
        return

    quote = random.choice(QUOTES)
    await ctx.bot.send_message(
        chat_id=user_id,
        text=f"🌙 Evening quote:\n“{quote}”\n\nWhat did you do today that moved you closer to your goal?",
    )


# ---------------- Commands (User) ----------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    ensure_trial(user_id)
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Internal error.", reply_markup=main_menu_keyboard())
        return

    access, source, left = has_access(user)
    if not access:
        await update.message.reply_text(help_text(), reply_markup=main_menu_keyboard())
        await handle_no_access(ctx, user_id)
        return

    tz = user.get("timezone")
    if not tz:
        await update.message.reply_text(
            "Welcome! First, set your time zone using /timezone.",
            reply_markup=main_menu_keyboard(),
        )
        await update.message.reply_text(help_text(), reply_markup=main_menu_keyboard())
        return

    reschedule(ctx.application, user_id, tz)

    if source == "trial":
        left_txt = f"Trial time left: {seconds_to_human(left)}"
    else:
        left_txt = f"Subscription time left: {seconds_to_human(left)}"

    await update.message.reply_text(
        "✅ Schedule is active (08:00 / 12:00 / 20:00 local time).\n"
        f"{left_txt}\n"
        "Use /timezone to change your time zone.",
        reply_markup=main_menu_keyboard(),
    )
    await update.message.reply_text(help_text(), reply_markup=main_menu_keyboard())


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(help_text(), reply_markup=main_menu_keyboard())


async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ensure_trial(user_id)
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("No data yet. Use /start.", reply_markup=main_menu_keyboard())
        return

    tz = user.get("timezone") or "Not set"
    access, source, left = has_access(user)

    if source == "subscription":
        access_line = f"Access: ✅ Subscription ({seconds_to_human(left)} left)"
    elif source == "trial":
        access_line = f"Access: ✅ Trial ({seconds_to_human(left)} left)"
    else:
        access_line = "Access: ⛔ EXPIRED (no active subscription)"

    await update.message.reply_text(
        "📌 Status\n"
        f"- Time zone: {tz}\n"
        f"- {access_line}\n"
        "- Schedule: 08:00 / 12:00 / 20:00 (local)\n",
        reply_markup=main_menu_keyboard(),
    )


async def subscribe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✅ Subscription is not connected to payments yet.\n\n"
        "Next step: payment integration (Stripe/Telegram Payments).\n"
        "For now, this is a placeholder screen.",
        reply_markup=main_menu_keyboard(),
    )


async def subscribe_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✅ Subscription is not connected to payments yet.\n\n"
        "Next step: payment integration (Stripe/Telegram Payments).\n"
        "For now, type /subscribe."
    )


async def timezone_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ensure_trial(user_id)
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Internal error.", reply_markup=main_menu_keyboard())
        return

    access, source, left = has_access(user)
    if not access:
        await update.message.reply_text(help_text(), reply_markup=main_menu_keyboard())
        await handle_no_access(ctx, user_id)
        return

    keyboard = [
        [InlineKeyboardButton("🇬🇧 UK (London)", callback_data="TZ:Europe/London")],
        [InlineKeyboardButton("🇮🇪 Ireland (Dublin)", callback_data="TZ:Europe/Dublin")],
        [InlineKeyboardButton("🇨🇦 Canada (Toronto)", callback_data="TZ:America/Toronto")],
        [InlineKeyboardButton("🇨🇦 Canada (Vancouver)", callback_data="TZ:America/Vancouver")],
        [InlineKeyboardButton("🇨🇦 Canada (Edmonton)", callback_data="TZ:America/Edmonton")],
        [InlineKeyboardButton("🇨🇦 Canada (Winnipeg)", callback_data="TZ:America/Winnipeg")],
        [InlineKeyboardButton("🇨🇦 Canada (Halifax)", callback_data="TZ:America/Halifax")],
        [InlineKeyboardButton("🇨🇦 Canada (St Johns)", callback_data="TZ:America/St_Johns")],
    ]
    await update.message.reply_text(
        "Choose your time zone:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def tz_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    ensure_trial(user_id)
    user = get_user(user_id)
    if not user:
        await query.edit_message_text("Internal error.")
        return

    access, source, left = has_access(user)
    if not access:
        await query.edit_message_text("Trial expired. Type /subscribe.")
        await handle_no_access(ctx, user_id)
        return

    tz_name = query.data.split("TZ:", 1)[1].strip()
    set_timezone(user_id, tz_name)
    set_expired_notified(user_id, 0)

    reschedule(ctx.application, user_id, tz_name)

    await query.edit_message_text(
        f"✅ Time zone saved: {tz_name}\n"
        "Schedule: 08:00 / 12:00 / 20:00 (your local time)."
    )


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    ensure_trial(user_id)
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Internal error.", reply_markup=main_menu_keyboard())
        return

    access, source, left = has_access(user)
    if not access:
        await update.message.reply_text(
            "⛔ Your trial has expired. Click Subscribe or type /subscribe.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await update.message.reply_text("👍 Received", reply_markup=main_menu_keyboard())


# ---------------- Commands (Admin) ----------------
async def whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"Your user_id: {user_id}\nAdmin: {'YES' if is_admin(user_id) else 'NO'}",
        reply_markup=main_menu_keyboard(),
    )


async def admin_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Access denied.", reply_markup=main_menu_keyboard())
        return

    await update.message.reply_text(
        "🔐 Admin commands\n"
        "/whoami — show your user_id\n"
        "/activate <user_id> <days> — grant subscription for N days\n"
        "/lifetime <user_id> — grant subscription for 10 years\n"
        "/revoke <user_id> — remove subscription\n"
        "/admin — show this help\n",
        reply_markup=main_menu_keyboard(),
    )


def parse_int(s: str) -> int | None:
    s = s.strip()
    if s.isdigit():
        return int(s)
    return None


async def activate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("Access denied.", reply_markup=main_menu_keyboard())
        return

    # /activate <user_id> <days>
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /activate <user_id> <days>", reply_markup=main_menu_keyboard())
        return

    uid = parse_int(ctx.args[0])
    days = parse_int(ctx.args[1])
    if uid is None or days is None or days <= 0:
        await update.message.reply_text("Usage: /activate <user_id> <days>", reply_markup=main_menu_keyboard())
        return

    until = now_ts() + days * 86400
    set_subscription_until(uid, until)
    set_expired_notified(uid, 0)

    # reschedule if timezone exists
    u = get_user(uid)
    if u and u.get("timezone"):
        reschedule(ctx.application, uid, u["timezone"])

    await update.message.reply_text(f"✅ Granted {days} day(s) to user {uid}.", reply_markup=main_menu_keyboard())
    try:
        await ctx.bot.send_message(chat_id=uid, text=f"✅ Subscription activated for {days} day(s). Type /start.")
    except Exception:
        pass


async def lifetime(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("Access denied.", reply_markup=main_menu_keyboard())
        return

    # /lifetime <user_id>
    if len(ctx.args) < 1:
        await update.message.reply_text("Usage: /lifetime <user_id>", reply_markup=main_menu_keyboard())
        return

    uid = parse_int(ctx.args[0])
    if uid is None:
        await update.message.reply_text("Usage: /lifetime <user_id>", reply_markup=main_menu_keyboard())
        return

    # 10 years
    until = now_ts() + 10 * 365 * 86400
    set_subscription_until(uid, until)
    set_expired_notified(uid, 0)

    u = get_user(uid)
    if u and u.get("timezone"):
        reschedule(ctx.application, uid, u["timezone"])

    await update.message.reply_text(f"✅ Granted LIFETIME (10y) to user {uid}.", reply_markup=main_menu_keyboard())
    try:
        await ctx.bot.send_message(chat_id=uid, text="✅ Subscription activated (lifetime). Type /start.")
    except Exception:
        pass


async def revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("Access denied.", reply_markup=main_menu_keyboard())
        return

    # /revoke <user_id>
    if len(ctx.args) < 1:
        await update.message.reply_text("Usage: /revoke <user_id>", reply_markup=main_menu_keyboard())
        return

    uid = parse_int(ctx.args[0])
    if uid is None:
        await update.message.reply_text("Usage: /revoke <user_id>", reply_markup=main_menu_keyboard())
        return

    set_subscription_until(uid, None)

    await update.message.reply_text(f"✅ Subscription revoked for user {uid}.", reply_markup=main_menu_keyboard())
    try:
        await ctx.bot.send_message(chat_id=uid, text="⛔ Subscription revoked. Trial may be expired. Type /subscribe.")
    except Exception:
        pass


# ---------------- Telegram Menu Commands (BotFather-like) ----------------
async def set_bot_commands(app: Application) -> None:
    """
    This creates the Telegram "Menu" button with commands list.
    """
    commands = [
        ("start", "Activate schedule"),
        ("timezone", "Set your time zone"),
        ("status", "Show your status"),
        ("subscribe", "Subscription info"),
        ("help", "Show commands menu"),
        ("whoami", "Show your user_id"),
    ]
    try:
        await app.bot.set_my_commands(commands)
    except Exception as e:
        print(f"set_my_commands failed: {e}")


# ---------------- Restore after restart ----------------
async def post_init(app: Application) -> None:
    init_db()
    await set_bot_commands(app)

    # Restore schedules for users WITH timezone AND access (trial/subscription)
    for user_id, tz_name in get_all_users_with_timezones():
        u = get_user(user_id)
        if not u:
            continue
        access, source, left = has_access(u)
        if access:
            try:
                reschedule(app, user_id, tz_name)
            except Exception as e:
                print(f"Restore failed for user_id={user_id} tz={tz_name}: {e}")


def main() -> None:
    app = Application.builder().token(load_token()).post_init(post_init).build()

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("timezone", timezone_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    app.add_handler(CommandHandler("whoami", whoami))

    # Admin commands
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("activate", activate))
    app.add_handler(CommandHandler("lifetime", lifetime))
    app.add_handler(CommandHandler("revoke", revoke))

    # Callbacks
    app.add_handler(CallbackQueryHandler(tz_callback, pattern=r"^TZ:"))
    app.add_handler(CallbackQueryHandler(subscribe_callback, pattern=r"^SUBSCRIBE$"))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()

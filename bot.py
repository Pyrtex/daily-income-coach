import os
import sqlite3
import random
import logging
from datetime import datetime, timedelta, time as dtime, timezone
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================
DB_FILE = "data.db"

TRIAL_DAYS = 3
SUB_DAYS = 30

DEFAULT_SCHEDULE_HOURS = (8, 12, 20)  # local time: 08:00 / 12:00 / 20:00

# Quotes with authors (text, author)
QUOTES = [
    ("Discipline is choosing between what you want now and what you want most.", "Abraham Lincoln"),
    ("We are what we repeatedly do. Excellence, then, is not an act, but a habit.", "Will Durant"),
    ("The secret of getting ahead is getting started.", "Mark Twain"),
    ("It always seems impossible until it’s done.", "Nelson Mandela"),
    ("Success is the product of daily habits—not once-in-a-lifetime transformations.", "James Clear"),
    ("If you are not willing to risk the usual, you will have to settle for the ordinary.", "Jim Rohn"),
    ("Action is the foundational key to all success.", "Pablo Picasso"),
    ("Do what you can, with what you have, where you are.", "Theodore Roosevelt"),
]

# Logging (Render logs)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("daily-income-coach")


# =========================
# TIME HELPERS
# =========================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def dt_from_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


# =========================
# ENV HELPERS
# =========================
def env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


def get_bot_token() -> str:
    token = env("BOT_TOKEN")
    if token:
        return token

    # Optional local .env support
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()

    raise RuntimeError("BOT_TOKEN not found (set BOT_TOKEN in environment or .env)")


def get_admin_ids() -> set[int]:
    raw = env("ADMIN_IDS", "")
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            pass
    return ids


def is_admin(user_id: int) -> bool:
    return user_id in get_admin_ids()


def get_payment_provider_token() -> str | None:
    return env("PAYMENT_PROVIDER_TOKEN")


def get_payment_currency() -> str:
    return (env("PAYMENT_CURRENCY", "USD") or "USD").upper()


def get_sub_price() -> str:
    return env("SUB_PRICE", "5.99") or "5.99"


def price_to_minor_units(price_str: str) -> int:
    # For USD: 5.99 -> 599 (cents)
    # This assumes a 2-decimal currency (USD/EUR/GBP etc.)
    try:
        value = float(price_str.replace(",", "."))
    except Exception:
        value = 5.99
    minor = int(round(value * 100))
    return max(minor, 1)


# =========================
# DB
# =========================
def db_connect():
    return sqlite3.connect(DB_FILE)


def init_db():
    conn = db_connect()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
          user_id INTEGER PRIMARY KEY,
          timezone TEXT,
          trial_start TEXT,
          subscription_until TEXT,
          last_prompt TEXT,
          last_prompt_at TEXT
        )
        """
    )
    # Simple migration: ensure columns exist
    c.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in c.fetchall()}

    if "trial_start" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN trial_start TEXT")
    if "subscription_until" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN subscription_until TEXT")
    if "last_prompt" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN last_prompt TEXT")
    if "last_prompt_at" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN last_prompt_at TEXT")

    conn.commit()
    conn.close()


def ensure_user(user_id: int):
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT user_id, trial_start FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute(
            "INSERT INTO users(user_id, timezone, trial_start, subscription_until, last_prompt, last_prompt_at) VALUES(?, ?, ?, ?, ?, ?)",
            (user_id, None, dt_to_iso(now_utc()), None, None, None),
        )
    else:
        if not row[1]:
            c.execute(
                "UPDATE users SET trial_start=? WHERE user_id=?",
                (dt_to_iso(now_utc()), user_id),
            )
    conn.commit()
    conn.close()


def set_timezone(user_id: int, tz_name: str):
    ensure_user(user_id)
    conn = db_connect()
    c = conn.cursor()
    c.execute("UPDATE users SET timezone=? WHERE user_id=?", (tz_name, user_id))
    conn.commit()
    conn.close()


def get_timezone(user_id: int) -> str | None:
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT timezone FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def get_trial_start(user_id: int) -> datetime | None:
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT trial_start FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dt_from_iso(row[0]) if row and row[0] else None


def get_subscription_until(user_id: int) -> datetime | None:
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT subscription_until FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dt_from_iso(row[0]) if row and row[0] else None


def set_subscription_until(user_id: int, until_dt: datetime | None):
    ensure_user(user_id)
    conn = db_connect()
    c = conn.cursor()
    c.execute(
        "UPDATE users SET subscription_until=? WHERE user_id=?",
        (dt_to_iso(until_dt), user_id),
    )
    conn.commit()
    conn.close()


def extend_subscription(user_id: int, days: int):
    now = now_utc()
    current = get_subscription_until(user_id)
    base = current if current and current > now else now
    new_until = base + timedelta(days=days)
    set_subscription_until(user_id, new_until)
    return new_until


def set_last_prompt(user_id: int, prompt_type: str):
    ensure_user(user_id)
    conn = db_connect()
    c = conn.cursor()
    c.execute(
        "UPDATE users SET last_prompt=?, last_prompt_at=? WHERE user_id=?",
        (prompt_type, dt_to_iso(now_utc()), user_id),
    )
    conn.commit()
    conn.close()


def get_last_prompt(user_id: int) -> tuple[str | None, datetime | None]:
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT last_prompt, last_prompt_at FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None, None
    return (row[0] if row[0] else None, dt_from_iso(row[1]) if row[1] else None)


def access_state(user_id: int) -> tuple[bool, str]:
    """
    Returns (allowed, label)
    label examples:
    - "Subscription (12d 3h left)"
    - "Trial (2d 23h left)"
    - "Expired"
    """
    ensure_user(user_id)
    now = now_utc()

    sub_until = get_subscription_until(user_id)
    if sub_until and sub_until > now:
        left = sub_until - now
        return True, f"Subscription ({format_timedelta(left)} left)"

    trial_start = get_trial_start(user_id)
    if trial_start:
        trial_until = trial_start + timedelta(days=TRIAL_DAYS)
        if trial_until > now:
            left = trial_until - now
            return True, f"Trial ({format_timedelta(left)} left)"

    return False, "Expired"


def format_timedelta(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    mins = (total_seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


# =========================
# UI
# =========================
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton("/start"), KeyboardButton("/status")],
        [KeyboardButton("/timezone"), KeyboardButton("/subscribe")],
        [KeyboardButton("/help")],
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


def build_timezone_keyboard() -> InlineKeyboardMarkup:
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
    return InlineKeyboardMarkup(keyboard)


# =========================
# SCHEDULING
# =========================
def reschedule(app: Application, user_id: int, tz_name: str):
    tz = ZoneInfo(tz_name)

    # Remove previous jobs for this user
    for job in app.job_queue.jobs():
        if job.name and job.name.startswith(f"user:{user_id}:"):
            job.schedule_removal()

    # PTB JobQueue uses time with tzinfo (NOT timezone=...)
    app.job_queue.run_daily(
        morning_job,
        time=dtime(hour=DEFAULT_SCHEDULE_HOURS[0], minute=0, tzinfo=tz),
        name=f"user:{user_id}:morning",
        data={"user_id": user_id},
    )
    app.job_queue.run_daily(
        midday_job,
        time=dtime(hour=DEFAULT_SCHEDULE_HOURS[1], minute=0, tzinfo=tz),
        name=f"user:{user_id}:midday",
        data={"user_id": user_id},
    )
    app.job_queue.run_daily(
        evening_job,
        time=dtime(hour=DEFAULT_SCHEDULE_HOURS[2], minute=0, tzinfo=tz),
        name=f"user:{user_id}:evening",
        data={"user_id": user_id},
    )


def make_quote() -> tuple[str, str]:
    text, author = random.choice(QUOTES)
    return text, author


async def morning_job(ctx: ContextTypes.DEFAULT_TYPE):
    user_id = ctx.job.data["user_id"]
    allowed, _ = access_state(user_id)
    if not allowed:
        return

    quote, author = make_quote()
    set_last_prompt(user_id, "morning")

    await ctx.bot.send_message(
        chat_id=user_id,
        text=(
            "☀️ Morning quote:\n"
            f"“{quote}”\n"
            f"— {author}\n\n"
            "What ONE thing will you do today to increase your income?"
        ),
    )


async def midday_job(ctx: ContextTypes.DEFAULT_TYPE):
    user_id = ctx.job.data["user_id"]
    allowed, _ = access_state(user_id)
    if not allowed:
        return

    set_last_prompt(user_id, "midday")

    await ctx.bot.send_message(
        chat_id=user_id,
        text="🕛 Midday check: What have you done so far today to move toward your goal?",
    )


async def evening_job(ctx: ContextTypes.DEFAULT_TYPE):
    user_id = ctx.job.data["user_id"]
    allowed, _ = access_state(user_id)
    if not allowed:
        return

    quote, author = make_quote()
    set_last_prompt(user_id, "evening")

    await ctx.bot.send_message(
        chat_id=user_id,
        text=(
            "🌙 Evening quote:\n"
            f"“{quote}”\n"
            f"— {author}\n\n"
            "What did you do today that moved you closer to your goal?"
        ),
    )


# =========================
# REACTION LOGIC
# =========================
def reaction_for(prompt_type: str | None, user_text: str) -> str:
    t = (user_text or "").strip()
    low = t.lower()

    if not t:
        return "Got it. If you want, send one sentence — what exactly happened?"

    # very short / vague answers
    if len(t) <= 3 or low in {"ok", "yes", "no", "nothing", "none", "idk"}:
        return "Thanks. Make it specific: what is the next *small* action (5–10 minutes) you can do now?"

    if "nothing" in low or "didn't" in low or "cant" in low or "can't" in low:
        return "Honest. Pick the smallest step: send 1 message, make 1 call, or apply to 1 job. Which one will you do?"

    # prompt-specific reactions
    if prompt_type == "morning":
        return "Nice. Lock it in: when exactly will you do it today (time + place)?"
    if prompt_type == "midday":
        return "Good. What is the next step you will do *before the next hour*?"
    if prompt_type == "evening":
        return "Good job. What will you improve tomorrow (one change, not a list)?"

    # fallback
    return "Got it. What is your next small step?"


# =========================
# COMMANDS (USER)
# =========================
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    msg = (
        "📌 *Commands*\n"
        "/start — activate schedule\n"
        "/status — show your access & schedule\n"
        "/timezone — set your local time zone\n"
        "/subscribe — pay and unlock access\n"
        "/whoami — show your user_id\n"
        "/help — this help\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())


async def whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    admin = "YES" if is_admin(uid) else "NO"
    await update.message.reply_text(f"Your user_id: {uid}\nAdmin: {admin}", reply_markup=main_menu_keyboard())


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    tz = get_timezone(user_id)
    if not tz:
        await update.message.reply_text(
            "Welcome! First, set your time zone so reminders arrive at the right local time.\n\nUse /timezone.",
            reply_markup=main_menu_keyboard(),
        )
        return

    allowed, label = access_state(user_id)
    if not allowed:
        await update.message.reply_text(
            "⛔ Access expired.\n\nUse /subscribe to unlock access.",
            reply_markup=main_menu_keyboard(),
        )
        return

    reschedule(ctx.application, user_id, tz)
    await update.message.reply_text(
        "✅ Schedule is active (08:00 / 12:00 / 20:00 local time).\n"
        f"Access: ✅ {label}\n"
        "Use /timezone to change your time zone.",
        reply_markup=main_menu_keyboard(),
    )


async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    tz = get_timezone(user_id) or "Not set"
    allowed, label = access_state(user_id)
    schedule_line = "08:00 / 12:00 / 20:00 (local)" if tz != "Not set" else "Not active (set /timezone)"

    msg = (
        "📌 *Status*\n"
        f"- Time zone: `{tz}`\n"
        f"- Access: {'✅ ' if allowed else '⛔ '} {label}\n"
        f"- Schedule: {schedule_line}\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())


async def timezone_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id)
    await update.message.reply_text(
        "Choose your time zone:",
        reply_markup=build_timezone_keyboard(),
    )


async def tz_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tz_name = query.data.split("TZ:", 1)[1].strip()
    user_id = query.from_user.id

    set_timezone(user_id, tz_name)

    allowed, label = access_state(user_id)
    if allowed:
        reschedule(ctx.application, user_id, tz_name)

    text = (
        f"✅ Time zone saved: {tz_name}\n"
        f"Access: {'✅ ' if allowed else '⛔ '} {label}\n"
        "Schedule: 08:00 / 12:00 / 20:00 (your local time)."
    )
    await query.edit_message_text(text)


# =========================
# PAYMENTS (TELEGRAM PAYMENTS)
# =========================
async def subscribe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Sends an invoice to pay inside Telegram.
    After successful payment: subscription is extended by SUB_DAYS days.
    """
    user_id = update.effective_user.id
    ensure_user(user_id)

    provider_token = get_payment_provider_token()
    if not provider_token:
        await update.message.reply_text(
            "⚠️ Payments are not configured yet.",
            reply_markup=main_menu_keyboard(),
        )
        return

    currency = get_payment_currency()
    price_minor = price_to_minor_units(get_sub_price())

    title = "Daily Income Coach — 30-day access"
    description = "Unlock full access for 30 days. Schedule: 08:00 / 12:00 / 20:00 (local time)."
    prices = [LabeledPrice(label="30-day subscription", amount=price_minor)]
    payload = f"sub:{user_id}:{int(now_utc().timestamp())}"

    await ctx.bot.send_invoice(
        chat_id=user_id,
        title=title,
        description=description,
        payload=payload,
        provider_token=provider_token,
        currency=currency,
        prices=prices,
        start_parameter="subscribe",
    )


async def precheckout_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    try:
        await query.answer(ok=True)
    except Exception as e:
        log.exception("PreCheckoutQuery answer failed: %s", e)


async def successful_payment_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    new_until = extend_subscription(user_id, SUB_DAYS)
    tz = get_timezone(user_id)

    if tz:
        try:
            reschedule(ctx.application, user_id, tz)
        except Exception as e:
            log.exception("Reschedule failed after payment: %s", e)

    until_str = new_until.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        "✅ Payment received.\n"
        f"Subscription active until: {until_str}\n\n"
        "Type /start to confirm schedule is active."
    )
    await update.message.reply_text(msg, reply_markup=main_menu_keyboard())


# =========================
# ADMIN COMMANDS (BY USER_ID ONLY)
# =========================
def require_admin(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not is_admin(uid):
            await update.message.reply_text("⛔ Admin only.", reply_markup=main_menu_keyboard())
            return
        return await func(update, ctx)

    return wrapper


@require_admin
async def admin_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👑 *Admin commands*\n"
        "/activate <user_id> <days> — grant subscription\n"
        "/lifetime <user_id> — grant 10 years\n"
        "/revoke <user_id> — revoke subscription\n"
        "/whoami — show your user_id\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())


@require_admin
async def activate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("Usage: /activate <user_id> <days>", reply_markup=main_menu_keyboard())
        return
    try:
        target_id = int(ctx.args[0])
        days = int(ctx.args[1])
        if days <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("Invalid args. Example: /activate 123456 30", reply_markup=main_menu_keyboard())
        return

    extend_subscription(target_id, days)
    tz = get_timezone(target_id)
    if tz:
        try:
            reschedule(ctx.application, target_id, tz)
        except Exception as e:
            log.exception("Reschedule failed in /activate: %s", e)

    await update.message.reply_text(f"✅ Activated user {target_id} for {days} day(s).", reply_markup=main_menu_keyboard())
    try:
        await ctx.bot.send_message(chat_id=target_id, text=f"✅ Subscription activated for {days} day(s). Type /start.")
    except Exception:
        pass


@require_admin
async def lifetime(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 1:
        await update.message.reply_text("Usage: /lifetime <user_id>", reply_markup=main_menu_keyboard())
        return
    try:
        target_id = int(ctx.args[0])
    except Exception:
        await update.message.reply_text("Invalid user_id. Example: /lifetime 123456", reply_markup=main_menu_keyboard())
        return

    extend_subscription(target_id, 3650)  # ~10 years
    tz = get_timezone(target_id)
    if tz:
        try:
            reschedule(ctx.application, target_id, tz)
        except Exception as e:
            log.exception("Reschedule failed in /lifetime: %s", e)

    await update.message.reply_text(f"✅ Lifetime activated for user {target_id}.", reply_markup=main_menu_keyboard())
    try:
        await ctx.bot.send_message(chat_id=target_id, text="✅ Lifetime subscription activated. Type /start.")
    except Exception:
        pass


@require_admin
async def revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 1:
        await update.message.reply_text("Usage: /revoke <user_id>", reply_markup=main_menu_keyboard())
        return
    try:
        target_id = int(ctx.args[0])
    except Exception:
        await update.message.reply_text("Invalid user_id. Example: /revoke 123456", reply_markup=main_menu_keyboard())
        return

    set_subscription_until(target_id, None)

    # remove jobs
    for job in ctx.application.job_queue.jobs():
        if job.name and job.name.startswith(f"user:{target_id}:"):
            job.schedule_removal()

    await update.message.reply_text(f"✅ Revoked subscription for user {target_id}.", reply_markup=main_menu_keyboard())
    try:
        await ctx.bot.send_message(chat_id=target_id, text="⛔ Subscription revoked. Use /subscribe to unlock access.")
    except Exception:
        pass


# =========================
# MESSAGE HANDLER (REACTION)
# =========================
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    allowed, label = access_state(user_id)
    if not allowed:
        await update.message.reply_text(
            "⛔ Access expired.\nUse /subscribe to unlock access.",
            reply_markup=main_menu_keyboard(),
        )
        return

    user_text = update.message.text or ""
    prompt_type, prompt_at = get_last_prompt(user_id)

    # If user replies long after prompt, treat it as general message
    if prompt_at and (now_utc() - prompt_at) > timedelta(hours=18):
        prompt_type = None

    reply = reaction_for(prompt_type, user_text)
    await update.message.reply_text(reply, reply_markup=main_menu_keyboard())


# =========================
# ERROR HANDLER
# =========================
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error: %s", ctx.error)


# =========================
# BOOTSTRAP
# =========================
def main():
    init_db()
    token = get_bot_token()

    app = Application.builder().token(token).build()

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("timezone", timezone_cmd))
    app.add_handler(CallbackQueryHandler(tz_callback, pattern=r"^TZ:"))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    app.add_handler(CommandHandler("whoami", whoami))

    # Payments
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    # Admin
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("activate", activate))
    app.add_handler(CommandHandler("lifetime", lifetime))
    app.add_handler(CommandHandler("revoke", revoke))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    # Errors
    app.add_error_handler(error_handler)

    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()

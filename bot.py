import os
import sqlite3
import random
import logging
from datetime import datetime, timedelta, time as dtime
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

# local schedule for each user's chosen timezone
DEFAULT_SCHEDULE_HOURS = (8, 12, 20)  # 08:00 / 12:00 / 20:00 local time

QUOTES = [
    "Discipline beats motivation.",
    "Small steps every day.",
    "Focus on the next action, not the whole mountain.",
    "Your habits decide your future.",
]

# Logging (Render logs)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("daily-income-coach")


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
    raw = env("ADMIN_IDS", "") or ""
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
    # stored as string like "5.99"
    return env("SUB_PRICE", "5.99") or "5.99"


def price_to_minor_units(price_str: str) -> int:
    """
    For USD: 5.99 -> 599 (cents)
    We assume 2-decimal currency.
    """
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
          subscription_until TEXT
        )
        """
    )

    # migration safety
    c.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in c.fetchall()}
    if "timezone" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN timezone TEXT")
    if "trial_start" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN trial_start TEXT")
    if "subscription_until" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN subscription_until TEXT")

    conn.commit()
    conn.close()


def ensure_user(user_id: int):
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT user_id, trial_start FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute(
            "INSERT INTO users(user_id, timezone, trial_start, subscription_until) VALUES(?, ?, ?, ?)",
            (user_id, None, datetime.utcnow().isoformat(), None),
        )
    else:
        if not row[1]:
            c.execute(
                "UPDATE users SET trial_start=? WHERE user_id=?",
                (datetime.utcnow().isoformat(), user_id),
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
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except Exception:
        return None


def get_subscription_until(user_id: int) -> datetime | None:
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT subscription_until FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except Exception:
        return None


def set_subscription_until(user_id: int, until_dt: datetime | None):
    ensure_user(user_id)
    conn = db_connect()
    c = conn.cursor()
    c.execute(
        "UPDATE users SET subscription_until=? WHERE user_id=?",
        (until_dt.isoformat() if until_dt else None, user_id),
    )
    conn.commit()
    conn.close()


def extend_subscription(user_id: int, days: int) -> datetime:
    now = datetime.utcnow()
    current = get_subscription_until(user_id)
    base = current if current and current > now else now
    new_until = base + timedelta(days=days)
    set_subscription_until(user_id, new_until)
    return new_until


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


def access_state(user_id: int) -> tuple[bool, str]:
    """
    Returns (allowed, label)
    """
    ensure_user(user_id)
    now = datetime.utcnow()

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
def remove_jobs_for_user(app: Application, user_id: int):
    for job in app.job_queue.jobs():
        if job.name and job.name.startswith(f"user:{user_id}:"):
            job.schedule_removal()


def reschedule(app: Application, user_id: int, tz_name: str):
    tz = ZoneInfo(tz_name)

    remove_jobs_for_user(app, user_id)

    # PTB: use time= with tzinfo inside datetime.time, NOT timezone=...
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


async def morning_job(ctx: ContextTypes.DEFAULT_TYPE):
    user_id = ctx.job.data["user_id"]
    allowed, _ = access_state(user_id)
    if not allowed:
        return
    quote = random.choice(QUOTES)
    await ctx.bot.send_message(
        chat_id=user_id,
        text=f"☀️ Morning quote:\n“{quote}”\n\nWhat ONE thing will you do today to increase your income?",
    )


async def midday_job(ctx: ContextTypes.DEFAULT_TYPE):
    user_id = ctx.job.data["user_id"]
    allowed, _ = access_state(user_id)
    if not allowed:
        return
    await ctx.bot.send_message(
        chat_id=user_id,
        text="🕛 Midday check: What have you done so far today to move toward your goal?",
    )


async def evening_job(ctx: ContextTypes.DEFAULT_TYPE):
    user_id = ctx.job.data["user_id"]
    allowed, _ = access_state(user_id)
    if not allowed:
        return
    quote = random.choice(QUOTES)
    await ctx.bot.send_message(
        chat_id=user_id,
        text=f"🌙 Evening quote:\n“{quote}”\n\nWhat did you do today that moved you closer to your goal?",
    )


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
    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(),
    )


async def whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    admin = "YES" if is_admin(uid) else "NO"
    await update.message.reply_text(
        f"Your user_id: {uid}\nAdmin: {admin}",
        reply_markup=main_menu_keyboard(),
    )


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)

    # show menu always on first entry
    tz = get_timezone(user_id)
    if not tz:
        await update.message.reply_text(
            "Welcome!\n\n1) Choose your time zone with /timezone\n2) Then type /start again",
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
        f"✅ Schedule is active (08:00 / 12:00 / 20:00 local time).\n"
        f"Access: ✅ {label}\n"
        f"Use /timezone to change your time zone.",
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
    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(),
    )


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
        f"Schedule: 08:00 / 12:00 / 20:00 (your local time)."
    )
    await query.edit_message_text(text)


# =========================
# PAYMENTS (TELEGRAM PAYMENTS via Smart Glocal)
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
            "⚠️ Payments are not configured yet (PAYMENT_PROVIDER_TOKEN missing).",
            reply_markup=main_menu_keyboard(),
        )
        return

    currency = get_payment_currency()
    price_minor = price_to_minor_units(get_sub_price())

    title = "Daily Income Coach — 30-day access"
    description = "Unlock full access for 30 days. Schedule: 08:00 / 12:00 / 20:00 (local time)."

    prices = [LabeledPrice(label="30-day subscription", amount=price_minor)]
    payload = f"sub:{user_id}:{int(datetime.utcnow().timestamp())}"

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
    # Telegram requires answering pre-checkout queries
    query = update.pre_checkout_query
    try:
        await query.answer(ok=True)
    except Exception as e:
        log.exception("PreCheckoutQuery answer failed: %s", e)


async def successful_payment_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Payment succeeded -> extend subscription and (re)schedule if timezone exists.
    """
    user_id = update.effective_user.id
    ensure_user(user_id)

    new_until = extend_subscription(user_id, SUB_DAYS)
    tz = get_timezone(user_id)
    if tz:
        try:
            reschedule(ctx.application, user_id, tz)
        except Exception as e:
            log.exception("Reschedule failed after payment: %s", e)

    until_str = new_until.strftime("%Y-%m-%d %H:%M UTC")
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
    remove_jobs_for_user(ctx.application, target_id)

    await update.message.reply_text(f"✅ Revoked subscription for user {target_id}.", reply_markup=main_menu_keyboard())
    try:
        await ctx.bot.send_message(chat_id=target_id, text="⛔ Subscription revoked. Use /subscribe to unlock access.")
    except Exception:
        pass


# =========================
# MESSAGE HANDLER
# =========================
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Use the menu buttons or type /help.", reply_markup=main_menu_keyboard())


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

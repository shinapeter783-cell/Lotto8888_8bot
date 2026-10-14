import os
import io
import json
import random
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "")
FOOTBALL_API_URL = "https://v3.football.api-sports.io/fixtures"
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
BOT_USERNAME = "Lotto8888_8bot"
BOT_NAME = "Lotto8888 Lucky"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- PERSISTENCE ----------
DATA_FILE = Path("data.json")

def load_data():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            pass
    return {
        "checkin": {},
        "points": {},
        "streak": {},
        "referrals": {},
        "referred_by": {},
        "redeemed_promos": {},
        "giveaway_entries": [],
        "giveaway_active": False,
        "flash_promo": None,
        "user_lang": {},
        "lucky_today": {},      # uid -> {date: "YYYY-MM-DD", number: "1234"}
        "lucky_draw_entries": [],  # uid list for today's virtual draw
    }

def save_data():
    DATA_FILE.write_text(json.dumps(DB, indent=2))

DB = load_data()

# ---------- PROMO CODES ----------
PROMOS = {
    "LOTTO8888":  {"points": 30,  "uses_left": 9999, "expires": None, "label": "Welcome Bonus",       "tier": "welcome"},
    "HELLO8888":  {"points": 15,  "uses_left": 9999, "expires": None, "label": "First Visit Bonus",   "tier": "welcome"},
    "GOAL8888":   {"points": 20,  "uses_left": 999,  "expires": None, "label": "Football Fan Bonus",  "tier": "football"},
    "MATCHDAY":   {"points": 25,  "uses_left": 999,  "expires": None, "label": "Match Day Promo",     "tier": "football"},
    "STREAK7":    {"points": 70,  "uses_left": 500,  "expires": None, "label": "7-Day Streak Reward", "tier": "streak"},
    "LOYAL8888":  {"points": 50,  "uses_left": 500,  "expires": None, "label": "Loyalty Reward",      "tier": "streak"},
    "WEEKEND88":  {"points": 40,  "uses_left": 300,  "expires": None, "label": "Weekend Special",     "tier": "weekend"},
    "VIP8888":    {"points": 150, "uses_left": 100,  "expires": None, "label": "VIP Community Promo", "tier": "vip"},
}

DAILY_ROTATION = ["LOTTO8888", "GOAL8888", "STREAK7", "WEEKEND88", "MATCHDAY", "LOYAL8888", "VIP8888"]

def get_daily_promo():
    idx = datetime.utcnow().toordinal() % len(DAILY_ROTATION)
    code = DAILY_ROTATION[idx]
    return code, PROMOS[code]

# ---------- HELPERS ----------
def get_points(uid):
    return DB["points"].get(str(uid), 0)

def add_points(uid, amount):
    uid = str(uid)
    DB["points"][uid] = DB["points"].get(uid, 0) + amount
    save_data()

def is_admin(uid):
    return uid in ADMIN_IDS

async def _reply(update: Update, text, **kwargs):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML", **kwargs)
    else:
        await update.message.reply_text(text, parse_mode="HTML", **kwargs)

# ---------- CONVERSATION STATES ----------
(GRAMMAR_WAIT, TTS_WAIT, TRANSLATE_WAIT) = range(3)

# ==========================================================
#                🎰 LUCKY NUMBER PICKER
# ==========================================================
def generate_daily_lucky(uid, date_str):
    """
    Deterministic 4-digit 'lucky number' per user per day.
    Uses a hash of (uid + date) so it looks random but is stable.
    """
    seed = f"{uid}-{date_str}-lotto8888"
    h = hashlib.sha256(seed.encode()).hexdigest()
    num = int(h[:8], 16) % 10000
    return f"{num:04d}"

async def lucky(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    today = datetime.utcnow().strftime("%Y-%m-%d")

    entry = DB["lucky_today"].get(str(uid))
    if not entry or entry.get("date") != today:
        number = generate_daily_lucky(uid, today)
        DB["lucky_today"][str(uid)] = {"date": today, "number": number}
        save_data()
    else:
        number = entry["number"]

    digits = " • ".join(number)
    text = (
        f"🎰 <b>Your Lucky Number Today</b>\n\n"
        f"<code>{digits}</code>\n\n"
        f"🍀 Full number: <b>{number}</b>\n"
        f"📅 Date: {today}\n\n"
        f"<i>For fun only — no real gambling.</i>\n"
        f"Come back tomorrow for a new number!"
    )
    await _reply(update, text)

# ==========================================================
#                🍀 VIRTUAL LUCKY DRAW (ENTERTAINMENT ONLY)
# ==========================================================
async def lucky_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Reset daily entries
    today_entries = [e for e in DB["lucky_draw_entries"] if e.get("date") == today]
    if not any(e["uid"] == uid for e in today_entries):
        DB["lucky_draw_entries"].append({"uid": uid, "date": today})
        save_data()
        # Bonus: match user's lucky number digits
        entry = DB["lucky_today"].get(str(uid), {})
        number = entry.get("number", "0000")
        # Count how many digits match the daily "house" number
        house_seed = f"{today}-house-lotto8888"
        house_hash = hashlib.sha256(house_seed.encode()).hexdigest()
        house_number = f"{int(house_hash[:8], 16) % 10000:04d}"

        matches = sum(1 for a, b in zip(number, house_number) if a == b)
        if matches == 4:
            reward = 200
            tier = "🎉 JACKPOT!"
        elif matches == 3:
            reward = 100
            tier = "🔥 Almost!"
        elif matches == 2:
            reward = 40
            tier = "✨ Nice!"
        elif matches == 1:
            reward = 15
            tier = "🍀 Lucky!"
        else:
            reward = 5
            tier = "🎲 Better luck tomorrow!"

        add_points(uid, reward)
        msg = (
            f"🍀 <b>Virtual Lucky Draw</b>\n\n"
            f"🎰 Your number: <b>{number}</b>\n"
            f"🏛 House number: <b>{house_number}</b>\n"
            f"🎯 Matches: <b>{matches}/4</b>\n\n"
            f"{tier}\n"
            f"💰 You earned <b>{reward} points</b>\n\n"
            f"<i>Entertainment only. No real money involved.</i>"
        )
    else:
        msg = "✅ You already played today's virtual draw!\nCome back tomorrow 🎰"

    await _reply(update, msg)

# ==========================================================
#                /start
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg.replace("ref_", ""))
                if referrer_id != uid and str(uid) not in DB["referred_by"]:
                    DB["referred_by"][str(uid)] = referrer_id
                    DB["referrals"].setdefault(str(referrer_id), []).append(uid)
                    add_points(referrer_id, 20)
                    add_points(uid, 10)
                    save_data()
                    try:
                        await context.bot.send_message(
                            referrer_id,
                            "🎉 Someone joined via your link! +20 points",
                        )
                    except Exception:
                        pass
            except Exception:
                pass

    keyboard = [
        [InlineKeyboardButton("🎰 Lucky Number", callback_data="lucky"),
         InlineKeyboardButton("🍀 Lucky Draw", callback_data="lucky_draw")],
        [InlineKeyboardButton("🔥 Promo", callback_data="promo"),
         InlineKeyboardButton("⚽ Football", callback_data="football")],
        [InlineKeyboardButton("🔴 Live", callback_data="live"),
         InlineKeyboardButton("🎁 Giveaway", callback_data="giveaway_join")],
        [InlineKeyboardButton("✅ Check-in", callback_data="checkin"),
         InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
        [InlineKeyboardButton("✍️ Grammar", callback_data="grammar"),
         InlineKeyboardButton("🔊 TTS", callback_data="tts")],
        [InlineKeyboardButton("🌍 Translate", callback_data="translate"),
         InlineKeyboardButton("👥 Referrals", callback_data="referrals")],
        [InlineKeyboardButton("💎 My Points", callback_data="points"),
         InlineKeyboardButton("📖 Help", callback_data="help")],
    ]

    text = (
        f"👋 Hello <b>{user.first_name}</b>!\n\n"
        f"Welcome to <b>{BOT_NAME}</b> 🎰⚽🔥✍️🔊\n\n"
        "What I can do:\n"
        "• 🎰 <b>Lucky Number</b> — Your daily 4-digit number\n"
        "• 🍀 <b>Lucky Draw</b> — Virtual draw for points\n"
        "• 🔥 <b>Promo Codes</b> — Redeem for points\n"
        "• ⚽ <b>Football</b> — Live scores & fixtures\n"
        "• 🎁 <b>Giveaway</b> — Community draws\n"
        "• ✅ <b>Check-in</b> — Daily rewards\n"
        "• ✍️ <b>Grammar</b> — Fix your writing\n"
        "• 🔊 <b>TTS</b> — Text → voice message\n"
        "• 🌍 <b>Translate</b> — Multi-language\n\n"
        f"💎 Your points: <b>{get_points(uid)}</b>\n\n"
        f"<i>⚠️ Entertainment only — no real gambling.</i>"
    )
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# ==========================================================
#                /help
# ==========================================================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 <b>Commands</b>\n\n"
        "/start – Main menu\n"
        "/lucky – Today's lucky number\n"
        "/luckydraw – Enter virtual draw\n"
        "/football – Today's fixtures\n"
        "/live – Live matches\n"
        "/giveaway – Join giveaway\n"
        "/checkin – Daily reward\n"
        "/points – Your balance\n"
        "/promo – Featured promo\n"
        "/redeem CODE – Redeem code\n"
        "/flash – Flash promo\n"
        "/invite – Referral link\n"
        "/referrals – Invite count\n"
        "/leaderboard – Top 10\n"
        "/grammar – Fix grammar\n"
        "/tts – Text to voice\n"
        "/translate – Translate text\n"
        "/cancel – Cancel action\n\n"
        "⚠️ <i>All features are for entertainment only. No real-money gambling.</i>"
    )
    await _reply(update, text)

# ==========================================================
#                FOOTBALL
# ==========================================================
def fetch_fixtures(live_only=False):
    if not FOOTBALL_API_KEY:
        return None
    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    params = {"live": "all"} if live_only else {"date": datetime.utcnow().strftime("%Y-%m-%d")}
    try:
        r = requests.get(FOOTBALL_API_URL, headers=headers, params=params, timeout=10)
        return r.json().get("response", [])
    except Exception as e:
        logger.error(f"Football API error: {e}")
        return None

def format_fixtures(fixtures, live_only=False):
    if fixtures is None:
        return "⚠️ Football data temporarily unavailable."
    if not fixtures:
        return "📭 No matches right now."
    lines = ["⚽ <b>Live Matches</b>\n" if live_only else "⚽ <b>Today's Fixtures</b>\n"]
    for f in fixtures[:10]:
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        gh, ga = f["goals"]["home"], f["goals"]["away"]
        status = f["fixture"]["status"]["short"]
        league = f["league"]["name"]
        score = f"{gh or 0} - {ga or 0}" if (live_only or status in ("1H","2H","HT","ET","P")) else "vs"
        lines.append(f"🏆 {league}\n   {home}  <b>{score}</b>  {away}\n")
    return "\n".join(lines)

async def football(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    await msg.reply_text("🔎 Fetching today's fixtures...")
    fixtures = fetch_fixtures(False)
    await msg.reply_text(format_fixtures(fixtures), parse_mode="HTML", disable_web_page_preview=True)

async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    await msg.reply_text("🔎 Fetching live matches...")
    fixtures = fetch_fixtures(True)
    await msg.reply_text(format_fixtures(fixtures, True), parse_mode="HTML", disable_web_page_preview=True)

# ==========================================================
#                GIVEAWAY
# ==========================================================
async def giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await join_giveaway(update, context)

async def join_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not DB["giveaway_active"]:
        msg = "❌ No giveaway running right now."
    elif uid in DB["giveaway_entries"]:
        msg = "✅ You're already in! Good luck 🍀"
    else:
        DB["giveaway_entries"].append(uid)
        save_data()
        msg = f"🎉 You joined! Total entries: {len(DB['giveaway_entries'])}"
    await _reply(update, msg)

async def start_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Not authorized.")
    DB["giveaway_active"] = True
    DB["giveaway_entries"] = []
    save_data()
    await update.message.reply_text("🎉 New giveaway started!")

async def end_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Not authorized.")
    if not DB["giveaway_entries"]:
        DB["giveaway_active"] = False
        save_data()
        return await update.message.reply_text("❌ No participants.")
    winner_id = random.choice(DB["giveaway_entries"])
    try:
        w = await context.bot.get_chat(winner_id)
        name = w.first_name
    except Exception:
        name = f"User {winner_id}"
    await update.message.reply_text(f"🏆 <b>Winner:</b> {name}! 🎉", parse_mode="HTML")
    DB["giveaway_active"] = False
    DB["giveaway_entries"] = []
    save_data()

# ==========================================================
#                CHECK-IN
# ==========================================================
async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    now = datetime.utcnow()
    last = DB["checkin"].get(uid)

    if last:
        last_dt = datetime.fromisoformat(last)
        if now - last_dt < timedelta(hours=24):
            remain = timedelta(hours=24) - (now - last_dt)
            h, r = divmod(int(remain.total_seconds()), 3600)
            m = r // 60
            return await _reply(update, f"⏳ Come back in <b>{h}h {m}m</b>.")
        if now - last_dt < timedelta(hours=48):
            DB["streak"][uid] = DB["streak"].get(uid, 0) + 1
        else:
            DB["streak"][uid] = 1
    else:
        DB["streak"][uid] = 1

    streak = DB["streak"][uid]
    base = random.randint(5, 15)
    streak_bonus = min(streak, 7) * 2
    total = base + streak_bonus
    DB["checkin"][uid] = now.isoformat()
    add_points(uid, total)

    msg = (
        f"✅ <b>Check-in!</b>\n\n"
        f"Base: <b>{base}</b> pts\n"
        f"🔥 Streak (day {streak}): <b>+{streak_bonus}</b>\n"
        f"💰 Earned: <b>{total}</b>\n"
        f"💎 Total: <b>{get_points(uid)}</b>"
    )
    await _reply(update, msg)

# ==========================================================
#                POINTS
# ==========================================================
async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    streak = DB["streak"].get(str(uid), 0)
    refs = len(DB["referrals"].get(str(uid), []))
    msg = (
        f"💎 <b>Your Stats</b>\n\n"
        f"Points: <b>{get_points(uid)}</b>\n"
        f"🔥 Streak: <b>{streak} day(s)</b>\n"
        f"👥 Referrals: <b>{refs}</b>"
    )
    await _reply(update, msg)

# ==========================================================
#                PROMO
# ==========================================================
async def promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code, info = get_daily_promo()
    text = (
        f"🔥 <b>Today's Featured Promo</b>\n\n"
        f"🏷️ Label: <b>{info['label']}</b>\n"
        f"🎁 Reward: <b>{info['points']} points</b>\n"
        f"⏳ Uses left: <b>{info['uses_left']}</b>\n\n"
        f"💡 Redeem:\n<code>/redeem {code}</code>"
    )
    await _reply(update, text)

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not context.args:
        return await _reply(update, "❌ Usage: <code>/redeem CODE</code>")
    code = context.args[0].upper().strip()

    flash = DB.get("flash_promo")
    if flash and flash["code"] == code:
        if datetime.utcnow() > datetime.fromisoformat(flash["expires"]):
            return await _reply(update, "❌ Flash promo expired.")
        redeemed = DB["redeemed_promos"].setdefault(uid, [])
        if code in redeemed:
            return await _reply(update, "⚠️ Already redeemed.")
        redeemed.append(code)
        add_points(uid, flash["points"])
        save_data()
        return await _reply(update,
            f"⚡ <b>Flash Redeemed!</b>\n\n+{flash['points']} pts\n💎 Total: <b>{get_points(uid)}</b>")

    if code not in PROMOS:
        return await _reply(update, "❌ Invalid code.")
    p = PROMOS[code]
    if p["uses_left"] <= 0:
        return await _reply(update, "❌ Code fully used.")
    redeemed = DB["redeemed_promos"].setdefault(uid, [])
    if code in redeemed:
        return await _reply(update, "⚠️ Already redeemed.")
    p["uses_left"] -= 1
    redeemed.append(code)
    add_points(uid, p["points"])
    save_data()
    await _reply(update,
        f"🎉 <b>Redeemed!</b>\n\nCode: <code>{code}</code>\n"
        f"Label: <b>{p['label']}</b>\n+{p['points']} pts\n💎 Total: <b>{get_points(uid)}</b>")

# ==========================================================
#                FLASH PROMO
# ==========================================================
async def flash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flash_data = DB.get("flash_promo")
    if not flash_data:
        return await _reply(update, "⚡ <b>No flash promo active.</b>")
    exp = datetime.fromisoformat(flash_data["expires"])
    if datetime.utcnow() > exp:
        DB["flash_promo"] = None
        save_data()
        return await _reply(update, "⚡ Flash expired.")
    mins = int((exp - datetime.utcnow()).total_seconds() // 60)
    text = (
        f"⚡ <b>FLASH PROMO!</b>\n\n"
        f"🎁 Reward: <b>{flash_data['points']} points</b>\n"
        f"⏰ Expires in: <b>{mins} min</b>\n\n"
        f"💡 <code>/redeem {flash_data['code']}</code>"
    )
    await _reply(update, text)

async def start_flash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Not authorized.")
    if len(context.args) < 3:
        return await update.message.reply_text("Usage: /startflash CODE POINTS MINUTES")
    try:
        code = context.args[0].upper()
        pts = int(context.args[1])
        mins = int(context.args[2])
    except Exception:
        return await update.message.reply_text("❌ Invalid arguments.")
    expires = (datetime.utcnow() + timedelta(minutes=mins)).isoformat()
    DB["flash_promo"] = {"code": code, "points": pts, "expires": expires}
    save_data()
    users = set(list(DB["points"].keys()) + list(DB["checkin"].keys()))
    sent = 0
    for uid in users:
        try:
            await context.bot.send_message(
                int(uid),
                f"⚡ <b>FLASH PROMO!</b>\nCode: <code>{code}</code>\n"
                f"Reward: <b>{pts} pts</b>\n⏰ {mins} min!\n\n/redeem {code}",
                parse_mode="HTML",
            )
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"⚡ Sent to {sent} users.")

# ==========================================================
#                REFERRALS
# ==========================================================
async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    text = (
        f"👥 <b>Invite & Earn!</b>\n\n"
        f"Your link:\n<code>{link}</code>\n\n"
        f"• You: <b>+20 pts</b>\n• Friend: <b>+10 pts</b>"
    )
    await _reply(update, text)

async def referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    count = len(DB["referrals"].get(uid, []))
    await _reply(update,
        f"👥 <b>Your Referrals</b>\n\nInvited: <b>{count}</b>\nEarned: <b>{count * 20}</b>")

# ==========================================================
#                LEADERBOARD
# ==========================================================
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pts = DB["points"]
    top = sorted(pts.items(), key=lambda x: x[1], reverse=True)[:10]
    if not top:
        return await _reply(update, "🏆 No users yet.")
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Top 10</b>\n"]
    for i, (uid, score) in enumerate(top):
        prefix = medals[i] if i < 3 else f"{i+1}."
        try:
            u = await context.bot.get_chat(int(uid))
            name = u.first_name or u.username or f"User {uid}"
        except Exception:
            name = f"User {uid}"
        lines.append(f"{prefix} {name} — <b>{score}</b> 💎")
    await _reply(update, "\n".join(lines))

# ==========================================================
#                BROADCAST
# ==========================================================
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Not authorized.")
    if not context.args:
        return await update.message.reply_text("Usage: /broadcast MSG")
    msg = " ".join(context.args)
    users = set(list(DB["points"].keys()) + list(DB["checkin"].keys()))
    sent, failed = 0, 0
    for uid in users:
        try:
            await context.bot.send_message(int(uid), f"📢 {msg}")
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Sent: {sent} | ❌ Failed: {failed}")

# ==========================================================
#                ✍️ GRAMMAR CHECKER
# ==========================================================
async def grammar_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "✍️ <b>Grammar Checker</b>\n\n"
        "Send me any English text and I'll fix grammar, spelling, and punctuation.\n\n"
        "Example: <i>she dont likes apples</i>\n\n"
        "Use /cancel to exit."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")
    return GRAMMAR_WAIT

async def grammar_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return GRAMMAR_WAIT
    await update.message.reply_text("✍️ Checking grammar...")
    corrected, note = fix_grammar(user_text)
    msg = (
        f"✍️ <b>Grammar Check</b>\n\n"
        f"<b>Original:</b>\n<i>{user_text}</i>\n\n"
        f"<b>Corrected:</b>\n<b>{corrected}</b>\n\n"
        f"<i>{note}</i>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")
    return ConversationHandler.END

def fix_grammar(text: str):
    try:
        r = requests.post(
            "https://api.languagetool.org/v2/check",
            data={"text": text, "language": "en-US"},
            timeout=8,
        )
        data = r.json()
        matches = data.get("matches", [])
        if not matches:
            return text, "✅ No issues found!"
        corrected = text
        for m in sorted(matches, key=lambda x: x["offset"], reverse=True):
            if not m.get("replacements"):
                continue
            best = m["replacements"][0]["value"]
            start = m["offset"]
            end = start + m["length"]
            corrected = corrected[:start] + best + corrected[end:]
        return corrected, f"🔧 Fixed {len(matches)} issue(s)."
    except Exception as e:
        logger.error(f"Grammar API error: {e}")
        fallback = text
        for a, b in [(" i ", " I "), (" dont ", " don't "), (" doesnt ", " doesn't "),
                     (" cant ", " can't "), (" wont ", " won't "), (" im ", " I'm ")]:
            fallback = fallback.replace(a, b)
        if fallback != text:
            return fallback, "⚠️ Offline mode — basic fixes applied."
        return text, "⚠️ Grammar API unavailable."

# ==========================================================
#                🔊 TTS
# ==========================================================
async def tts_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔊 <b>Text-to-Speech</b>\n\n"
        "Send text and I'll convert it to a voice message.\n"
        "• Max ~200 characters\n• English supported\n\n"
        "Use /cancel to exit."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")
    return TTS_WAIT

async def tts_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return TTS_WAIT
    if len(user_text) > 200:
        await update.message.reply_text("❌ Too long (max 200 chars).")
        return TTS_WAIT
    await update.message.reply_text("🔊 Generating voice...")
    audio_bytes = await generate_tts(user_text)
    if audio_bytes is None:
        await update.message.reply_text("❌ TTS failed.")
        return ConversationHandler.END
    await update.message.reply_voice(voice=InputFile(io.BytesIO(audio_bytes), filename="tts.mp3"))
    return ConversationHandler.END

async def generate_tts(text: str):
    try:
        url = "https://translate.google.com/translate_tts"
        params = {"ie": "UTF-8", "q": text, "tl": "en", "client": "tw-ob"}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Referer": "https://translate.google.com/",
        }
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code == 200 and r.content:
            return r.content
        return None
    except Exception as e:
        logger.error(f"TTS error: {e}")
        return None

# ==========================================================
#                🌍 TRANSLATOR
# ==========================================================
LANG_MAP = {
    "en": "English", "km": "Khmer", "th": "Thai", "vi": "Vietnamese",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "fr": "French",
    "es": "Spanish", "de": "German", "ru": "Russian", "ar": "Arabic",
    "hi": "Hindi", "id": "Indonesian", "ms": "Malay", "pt": "Portuguese",
}

async def translate_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard, row = [], []
    for code, name in LANG_MAP.items():
        row.append(InlineKeyboardButton(name, callback_data=f"tl_{code}"))
        if len(row) == 4:
            keyboard.append(row); row = []
    if row:
        keyboard.append(row)
    text = "🌍 <b>Translator</b>\n\nPick a target language, then send your text."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return TRANSLATE_WAIT

async def translate_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.replace("tl_", "")
    context.user_data["target_lang"] = lang
    await query.message.reply_text(
        f"🌍 Target: <b>{LANG_MAP.get(lang, lang)}</b>\n\nSend text.\n/cancel to exit.",
        parse_mode="HTML")
    return TRANSLATE_WAIT

async def translate_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return TRANSLATE_WAIT
    target = context.user_data.get("target_lang", "en")
    await update.message.reply_text("🌍 Translating...")
    translated = await do_translate(user_text, target)
    if not translated:
        await update.message.reply_text("❌ Translation failed.")
        return ConversationHandler.END
    msg = (
        f"🌍 <b>→ {LANG_MAP.get(target, target)}</b>\n\n"
        f"<b>Original:</b>\n<i>{user_text}</i>\n\n"
        f"<b>Translated:</b>\n<b>{translated}</b>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")
    return ConversationHandler.END

async def do_translate(text, target):
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        return "".join(seg[0] for seg in data[0] if seg[0])
    except Exception as e:
        logger.error(f"Translate error: {e}")
        return None

# ==========================================================
#                CANCEL + ROUTER
# ==========================================================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == "lucky":
        await lucky(update, context)
    elif data == "lucky_draw":
        await lucky_draw(update, context)
    elif data == "football":
        await update.callback_query.answer()
        fixtures = fetch_fixtures(False)
        await update.callback_query.message.reply_text(
            format_fixtures(fixtures), parse_mode="HTML", disable_web_page_preview=True)
    elif data == "live":
        await update.callback_query.answer()
        fixtures = fetch_fixtures(True)
        await update.callback_query.message.reply_text(
            format_fixtures(fixtures, True), parse_mode="HTML", disable_web_page_preview=True)
    elif data == "giveaway_join":
        await join_giveaway(update, context)
    elif data == "checkin":
        await checkin(update, context)
    elif data == "promo":
        await promo(update, context)
    elif data == "referrals":
        await referrals(update, context)
    elif data == "points":
        await points(update, context)
    elif data == "leaderboard":
        await leaderboard(update, context)
    elif data == "help":
        await help_command(update, context)
    elif data == "grammar":
        await grammar_entry(update, context)
    elif data == "tts":
        await tts_entry(update, context)
    elif data == "translate":
        await translate_entry(update, context)

# ==========================================================
#                MAIN
# ==========================================================
def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN not set!")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("lucky", lucky))
    app.add_handler(CommandHandler("luckydraw", lucky_draw))
    app.add_handler(CommandHandler("football", football))
    app.add_handler(CommandHandler("live", live))
    app.add_handler(CommandHandler("giveaway", giveaway))
    app.add_handler(CommandHandler("startgiveaway", start_giveaway))
    app.add_handler(CommandHandler("endgiveaway", end_giveaway))
    app.add_handler(CommandHandler("checkin", checkin))
    app.add_handler(CommandHandler("points", points))
    app.add_handler(CommandHandler("promo", promo))
    app.add_handler(CommandHandler("redeem", redeem))
    app.add_handler(CommandHandler("flash", flash))
    app.add_handler(CommandHandler("startflash", start_flash))
    app.add_handler(CommandHandler("invite", invite))
    app.add_handler(CommandHandler("referrals", referrals))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("broadcast", broadcast))

    grammar_conv = ConversationHandler(
        entry_points=[
            CommandHandler("grammar", grammar_entry),
            CallbackQueryHandler(grammar_entry, pattern="^grammar$"),
        ],
        states={GRAMMAR_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, grammar_process)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(grammar_conv)

    tts_conv = ConversationHandler(
        entry_points=[
            CommandHandler("tts", tts_entry),
            CallbackQueryHandler(tts_entry, pattern="^tts$"),
        ],
        states={TTS_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, tts_process)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(tts_conv)

    translate_conv = ConversationHandler(
        entry_points=[
            CommandHandler("translate", translate_entry),
            CallbackQueryHandler(translate_entry, pattern="^translate$"),
        ],
        states={
            TRANSLATE_WAIT: [
                CallbackQueryHandler(translate_pick, pattern="^tl_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, translate_process),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(translate_conv)

    app.add_handler(CallbackQueryHandler(button_router))

    logger.info(f"🤖 {BOT_NAME} is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

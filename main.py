import os
import re
import asyncio
import logging
import time
import sys
# Fix Windows cp1252 emoji crash when redirecting
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")
    sys.stderr.reconfigure(encoding="utf-8", errors="ignore")
except: pass
# Force utf-8 console output for emoji logs (Windows)
try:
    os.environ["PYTHONIOENCODING"] = "utf-8"
except: pass
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.error import BadRequest

import config
import database as db
from db.mongo import get_db as mongo_get_db
import ai_engine
import moderation as mod
import automod
import self_update
from features import tattoo as tattoo_feature
from features.context import get_user_details, get_reply_context, get_tag_context
from features import web as web_feature
import security
import reliability
import observability
observability.setup()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- helpers ----------
async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if await mod.is_admin(update):
        return True
    await update.effective_message.reply_text("🌸 Gomen... admins only 🌸")
    return False

def extract_args_text(update: Update):
    msg = update.effective_message
    if not msg.text: return ""
    parts = msg.text.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""

# ---------- HINATA PERSONA HELPERS ----------
def is_mikey_user(user_id: int) -> bool:
    return user_id == config.OWNER_ID and config.OWNER_ID != 0

HINATA_NAME_PATTERN = re.compile(r"\b(hinata|hina|hyuga)\b", re.I)
def is_hinata_mention(text: str) -> bool:
    return bool(HINATA_NAME_PATTERN.search(text))  # hina catches hina, hinata, hyuga etc — any case

# Mikey silent-read: python heuristic + AI fallback — reads all Mikey msgs, responds only if needed (hina/hyuga always, else help/code/moderation)
MIKEY_NEED_RE = re.compile(r"\b(help|code|python|javascript|fix|explain|need|please|hina|hyuga|ban|kick|mute|warn|pin|delete|lock|purge|promote|batao|kar do|sunao|bolo)\b", re.I)
def mikey_needs_hinata_py(text: str, is_reply_to_bot: bool, contains_hinata: bool) -> bool:
    low = text.lower()
    if contains_hinata: return True
    if is_reply_to_bot: return True
    if MIKEY_NEED_RE.search(low): return True
    if text.strip().endswith("?"): return True
    if len(text.strip()) > 80: return False  # long casual story, likely not for her
    return False

async def mikey_needs_hinata_ai(text: str) -> bool:
    # AI fallback for ambiguous Mikey msgs w/o name — asks minimax if she should answer
    if not config.AI_API_KEY: return False
    try:
        from ai_engine import get_client
        client = get_client()
        prompt = f"Mikey (owner) said in group: \"{text[:300]}\" — Should Hinata (loyal gentle Hyuga) respond? Criteria: responds if asked, needs help, moderation, or seems directed at her. Casual chatter between others = NO. Answer ONLY JSON {{\"need\": true/false}}"
        resp = await client.chat.completions.create(
            model=config.AI_MODEL,
            messages=[{"role":"system","content":"You are a JSON classifier. Output {\"need\": true} or {\"need\": false} only."},{"role":"user","content":prompt}],
            temperature=0.1, max_tokens=20,
        )
        import json
        raw = resp.choices[0].message.content.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m: raw = m.group(0)
        data = json.loads(raw)
        return bool(data.get("need", False))
    except:
        return False

HINATA_START_PRIVATE = """🌸 *Kon'nichiwa, Mikey-kun...* I'm Hinata 🌸

Your gentle guardian — Byakugan active, I read all your messages but speak only when you need me 🌸

*Mikey-kun, no commands needed — just speak my name (hina/hinata/hyuga any case):*
• `Hina kick this person` (reply) — I shield you 🌸
• `Hina give him power to kick` (reply) — I ask `hai?` then bless 🌸
• `Hyuga give @user ban and mute power` — delegate
• `Hina take his kick power` — revoke
• `Hina allow @user` — let me talk to them with preset (no AI) 🌸
• `Hina show powers` / `Hina list allowed` — see blessed
• `/dashboard` — full management UI (locks/welcome/rules/flood/filters) 🌸

*Also naturally:* `kick this person`, `ban him`, `mute for 2 hours` — I understand.
*I read all your msgs, Mikey-kun, but stay silent unless you need me — call `hina` when you do 🌸
"""

HINATA_START_GROUP = "🌸 Kon'nichiwa... I'm Hinata (hina/hyuga) — Mikey-kun's guardian 🌸 Say `Hina kick` or `Hinata give him kick power` (reply). I read all Mikey-kun's msgs but speak only when needed. `/dashboard` for UI 🌸"
HINATA_HELP = (
    "🌸 *Hinata's Gentle Guide* 🌸\n\n"
    "*For Mikey-kun — Power Delegation (no commands needed!):*\n"
    "Say my name naturally, I ask confirmation 🌸\n"
    "• `Hinata give him power to kick` (reply) — grants kick\n"
    "• `Hinata give @user ban and mute power` — grants multiple\n"
    "• `Hinata give him full power` / `all powers` — grants everything\n"
    "• `Hinata take his ban power` (reply) — revokes one\n"
    "• `Hinata remove all powers from @user` — clears\n"
    "• `Hinata show powers` — list blessed\n"
    "• `Hina allow @user` / `Hyuga allow him` (reply) — preset talk w/o AI 🌸\n"
    "• `Hina list allowed` — show preset-allowed\n"
    "I always ask: *Mikey-kun, should I?* → `yes`/`hai` or ✅\n\n"
    "*Moderation (blessed can use, say my name hina/hyuga any case):*\n"
    "`Hina kick this person` (reply) `Hyuga ban him for 2 days`\n"
    "`Hina mute him 2h`, `Hina warn karo`, `Hina pin this`, `Hina purge`\n"
    "→ No slash needed! Classic /kick still works for TG admins.\n\n"
    "*Mikey silent-read:* I read all your msgs, Mikey-kun, but speak only if you need me (py→AI decides) 🌸\n"
    "*Preset w/o AI:* Allowed users get preset python reply, not LLM — cheap & instant.\n\n"
    "*Dashboard UI:* `/dashboard` / `/settings` — locks, welcome, rules, flood, filters, powers, allowed (all buttons) 🌸\n"
    "*Locks:* `all, media, sticker, gif, link, photo, video, forward, poll, invite`\n"
    "*Pull & Reverse:* `Hina pull` (reply → name+ID), `Hina info @user`, `Hina reverse`/`undo` → undoes last ban/mute/warn/pin/lock/promote/power 🌸\n"
    "*Powers you can grant:* kick, ban, mute, warn, pin, del, purge, promote, lock, all\n"
)

# ---------- START / HELP ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_type = update.effective_chat.type
    is_mikey = is_mikey_user(update.effective_user.id)
    # P2 — auto-save Mikey info on /start, sanitized
    if is_mikey:
        try:
            u = update.effective_user
            uname = security.sanitize_text(u.username or "", 64)
            fname = security.sanitize_text(u.full_name or "", 200)
            await db.save_owner_info(u.id, uname, fname)
            config.OWNER_USERNAME = uname
            config.OWNER_FULLNAME = fname
            # P2 — also clean stale raw values from Mongo once on /start
            try:
                from db.mongo import get_db as _gdb
                gdb = _gdb()
                await gdb["owner"].update_one(
                    {"owner_id": u.id},
                    {"$set": {"username": uname, "full_name": fname}},
                )
            except: pass
        except: pass
    if chat_type == ChatType.PRIVATE:
        kb = [
            [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{context.bot.username}?startgroup=true")],
            [InlineKeyboardButton("📖 Commands", callback_data="help"), InlineKeyboardButton("⚙️ Powers", callback_data="powers")]
        ]
        if is_mikey:
            text = HINATA_START_PRIVATE
        else:
            text = (
                "🌸 *Kon'nichiwa... I'm Hinata* 🌸\n\n"
                "Mikey-kun's loyal guardian — I watch over groups with gentle strength.\n\n"
                "*Say my name:*\n"
                "• `Hinata kick this person` (reply)\n"
                "• `Hinata ban him for 2 days`\n"
                "• If Mikey-kun gave you power, I will obey you too 🌸\n\n"
                "Ask Mikey-kun: `Hinata give me kick power` — he can bless you 🌸"
            )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
    else:
        if is_mikey:
            await update.message.reply_text(f"🌸 Mikey-kun... I'm here, always by your side. Say `Hinata give him kick power` to bless someone 🌸")
        else:
            await update.message.reply_text(f"🌸 Kon'nichiwa... I'm Hinata, Mikey-kun's guardian 🌸 Use /help — I will help gently.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HINATA_HELP, parse_mode=ParseMode.MARKDOWN)

# ---------- CLASSIC MODERATION COMMANDS (Telegram admin only) ----------
async def kick_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    target = "reply"
    if context.args and context.args[0].startswith("@"): target = context.args[0]
    elif context.args and context.args[0].isdigit(): target = context.args[0]
    await mod.do_kick(update, context, target, " ".join(context.args[1:]) if target.startswith("@") else " ".join(context.args))

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    target = "reply"
    reason = ""
    duration = None
    if context.args:
        if context.args[0].startswith("@") or context.args[0].isdigit():
            target = context.args[0]
            rest = " ".join(context.args[1:])
            m = re.match(r"(\d+\s*[smhdw]|forever|permanent)\s*(.*)", rest)
            if m:
                duration = m.group(1)
                reason = m.group(2)
            else:
                reason = rest
        else:
            m = re.match(r"(\d+\s*[smhdw]|forever)\s*(.*)", " ".join(context.args))
            if m:
                duration = m.group(1)
                reason = m.group(2)
            else:
                reason = " ".join(context.args)
    await mod.do_ban(update, context, target, reason, duration)

async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    if not context.args: await update.message.reply_text("Usage: /unban <user_id> or reply"); return
    await mod.do_unban(update, context, context.args[0])

async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    target = "reply"
    duration = "1h"
    reason = ""
    if context.args:
        if context.args[0].startswith("@") or context.args[0].isdigit():
            target = context.args[0]
            if len(context.args) > 1 and re.match(r"^\d+[smhdw]$", context.args[1].lower()):
                duration = context.args[1]
                reason = " ".join(context.args[2:])
            else:
                reason = " ".join(context.args[1:])
        else:
            if re.match(r"^\d+[smhdw]$", context.args[0].lower()):
                duration = context.args[0]
                reason = " ".join(context.args[1:])
            else:
                reason = " ".join(context.args)
    await mod.do_mute(update, context, target, duration, reason)

async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    await mod.do_unmute(update, context, "reply")

async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    reason = " ".join(context.args) if context.args else ""
    await mod.do_warn(update, context, "reply", reason)

async def warns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_msg = update.message.reply_to_message
    if target_msg and target_msg.from_user:
        uid = target_msg.from_user.id
        user = target_msg.from_user
    elif context.args and context.args[0].isdigit():
        uid = int(context.args[0])
        user = None
    else:
        uid = update.effective_user.id
        user = update.effective_user
    count, reasons = await db.get_warns(update.effective_chat.id, uid)
    limit = await db.get_warn_limit(update.effective_chat.id)
    name = user.mention_html() if user else str(uid)
    await update.message.reply_text(f"⚠️ Warns for {name}: {count}/{limit}\n{reasons if reasons else 'No reasons'}", parse_mode="HTML")

async def resetwarns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    await mod.do_warn_reset(update, context, "reply")

async def warns_limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /warnlimit <number>")
        return
    await db.set_warn_limit(update.effective_chat.id, int(context.args[0]))
    await update.message.reply_text(f"✅ Warn limit set to {context.args[0]}")

# ---------- ADMIN ----------
async def promote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    await mod.do_promote(update, context, "reply")

async def demote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    await mod.do_demote(update, context, "reply")

async def pin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    silent = "silent" in (context.args or [])
    await mod.do_pin(update, context, silent)

async def unpin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    await mod.do_unpin(update, context)

async def del_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    await mod.do_del(update, context)

async def purge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await mod.do_purge(update, context)

# ---------- LOCKS ----------
async def lock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    if not context.args: await update.message.reply_text("Usage: /lock <type>"); return
    await mod.do_lock(update, context, context.args[0].lower())

async def unlock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    if not context.args: await update.message.reply_text("Usage: /unlock <type>"); return
    await mod.do_unlock(update, context, context.args[0].lower())

async def locks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    locks = await db.get_locks(update.effective_chat.id)
    if not locks: await update.message.reply_text("🔓 No active locks 🌸")
    else: await update.message.reply_text("🔒 Active locks: " + ", ".join(locks) + " 🌸")

# ---------- FILTERS ----------
async def filter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    if len(context.args) < 2: await update.message.reply_text("Usage: /filter <keyword> <reply text>\nUse del: prefix to auto-delete."); return
    kw = context.args[0]
    reply = " ".join(context.args[1:])
    await db.add_filter(update.effective_chat.id, kw, reply)
    await update.message.reply_text(f"✅ Filter added for '{kw}' 🌸")

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    if not context.args: await update.message.reply_text("Usage: /stop <keyword>"); return
    ok = await db.remove_filter(update.effective_chat.id, context.args[0])
    await update.message.reply_text("✅ Removed 🌸" if ok else "❌ Not found 🌸")

async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kws = await db.list_filter_keywords(update.effective_chat.id)
    if not kws: await update.message.reply_text("No filters 🌸")
    else: await update.message.reply_text("Filters:\n• " + "\n• ".join(kws))

# ---------- RULES & WELCOME ----------
async def setrules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    txt = extract_args_text(update)
    if not txt: await update.message.reply_text("Usage: /setrules <rules text>"); return
    await db.set_rules(update.effective_chat.id, txt)
    await update.message.reply_text("✅ Rules set 🌸")

async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = await db.get_rules(update.effective_chat.id)
    if not r: await update.message.reply_text("No rules set 🌸")
    else: await update.message.reply_text(f"📜 *Rules:*\n{r}", parse_mode=ParseMode.MARKDOWN)

async def setwelcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    txt = extract_args_text(update)
    if not txt: await update.message.reply_text("Usage: /setwelcome <text> with {mention} {chat}"); return
    await db.set_welcome(update.effective_chat.id, txt, 1)
    await update.message.reply_text("✅ Welcome set 🌸")

async def welcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = extract_args_text(update).lower()
    if txt in ("on","enable","yes"): await db.toggle_welcome(update.effective_chat.id, True); await update.message.reply_text("✅ Welcome enabled 🌸")
    elif txt in ("off","disable","no"): await db.toggle_welcome(update.effective_chat.id, False); await update.message.reply_text("🔕 Welcome disabled 🌸")
    else:
        w, en = await db.get_welcome(update.effective_chat.id)
        await update.message.reply_text(f"Welcome {'enabled' if en else 'disabled'}:\n{w or 'Not set'}")

# ---------- FLOOD ----------
async def setflood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context): return
    if len(context.args) < 2: await update.message.reply_text("Usage: /setflood <messages> <seconds>  e.g. 5 5"); return
    try:
        n = int(context.args[0]); w = int(context.args[1])
        await db.set_flood(update.effective_chat.id, n, w)
        await update.message.reply_text(f"✅ Flood: {n} msgs per {w}s 🌸")
    except: await update.message.reply_text("Invalid numbers 🌸")

async def flood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n,w,en = await db.get_flood(update.effective_chat.id)
    await update.message.reply_text(f"Flood: {n} msgs / {w}s — {'enabled' if en else 'disabled'} 🌸")

# ---------- FUN ----------
import random, httpx

async def meme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get("https://meme-api.com/gimme", timeout=5)
            j = r.json()
            await update.message.reply_photo(j["url"], caption=j.get("title",""))
    except:
        await update.message.reply_text(random.choice(["😂 Why did the bot cross the road?", "🤣 Meme service down, here's a joke instead!"]))

async def joke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jokes = ["Why don't scientists trust atoms? They make up everything! 😂","I told my computer I needed a break... it said no problem, I'll go to sleep 😴","Parallel lines have so much in common, it's a shame they'll never meet 😢"]
    await update.message.reply_text(random.choice(jokes))

async def roll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🎲 {random.randint(1,6)}")

async def eightball_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = random.choice(["Yes ✅","No ❌","Maybe 🤔","Absolutely! 🔥","Ask again later 🕓","Definitely not 🙅","Signs point to yes ✨"])
    await update.message.reply_text(f"🎱 {ans}")

# ---------- ID & INFO ----------
async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    is_m = is_mikey_user(update.effective_user.id)
    txt = f"👤 You: `{update.effective_user.id}`\n💬 Chat: `{update.effective_chat.id}`\n🌸 Mikey: `{'YES — my master' if is_m else 'NO — guest'}`"
    if msg.reply_to_message and msg.reply_to_message.from_user:
        txt += f"\n↩️ Replied user: `{msg.reply_to_message.from_user.id}` (`{msg.reply_to_message.from_user.full_name}`)"
        # if Mikey asks, pull that user's info too
        if is_m:
            try:
                ruid = msg.reply_to_message.from_user.id
                rp = await db.get_hinata_powers(update.effective_chat.id, ruid)
                if rp: txt += f"\n↩️ Their powers: `{','.join(sorted(rp))}`"
            except: pass
    powers = await db.get_hinata_powers(update.effective_chat.id, update.effective_user.id)
    if powers:
        txt += f"\n🌸 Hinata powers: `{','.join(sorted(powers))}`"
    allowed = await db.is_hinata_allowed(update.effective_chat.id, update.effective_user.id)
    if allowed:
        txt += f"\n💬 Preset allowed: `yes`"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def whoami_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await id_cmd(update, context)

async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, details = await reliability.health_check(context.bot)
    status = "✅ OPERATIONAL" if ok else "❌ DEGRADED"
    txt = f"🌸 *Hinata Health* — {status}\n"
    for k,v in details.items():
        txt += f"{k}: `{v}`\n"
    stats = reliability.get_health_stats()
    txt += f"checks: `{stats['checks']}` fails: `{stats['fails']}`\n"
    await update.effective_message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

# ---------- HINATA POWERS UI ----------
async def powers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db.list_hinata_powers(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("🌸 No one has been blessed by Mikey-kun yet 🌸\nMikey-kun, say `Hinata give him kick power` (reply) to bless someone.")
        return
    lines = ["🌸 *Hinata's Blessed Protectors* 🌸\n"]
    for uid, powers, granted_by in rows:
        try:
            member = await update.effective_chat.get_member(uid)
            name = member.user.mention_html()
        except:
            name = str(uid)
        lines.append(f"• {name} — `{powers}`")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def allowed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await db.list_hinata_allowed(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("🌸 No preset talk allowed yet — Mikey-kun, say `Hina allow @user` 🌸")
        return
    lines = ["🌸 *Preset Talk Allowed (no AI)* 🌸\n"]
    for uid, username, _ in rows:
        try:
            m = await update.effective_chat.get_member(uid)
            name = m.user.mention_html()
        except:
            name = f"@{username}" if username else str(uid)
        lines.append(f"• {name}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# ---------- UI WORKFLOWS — SETTINGS DASHBOARD (improvised) ----------
async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only Mikey or admins can open dashboard
    if not await mod.is_admin(update) and not is_mikey_user(update.effective_user.id):
        await update.effective_message.reply_text("🌸 Gomen... only Mikey-kun or admins may open dashboard 🌸"); return
    await show_settings_dashboard(update, context, update.effective_chat.id, "main")

async def show_settings_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, page: str):
    # Build page UI
    q = update.callback_query
    # Determine send vs edit
    def send_or_edit(text, kb):
        if q:
            return q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        else:
            return update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    if page == "main":
        locks = await db.get_locks(chat_id)
        wtext, wen = await db.get_welcome(chat_id)
        rules = await db.get_rules(chat_id)
        flood_n, flood_w, flood_en = await db.get_flood(chat_id)
        powers_rows = await db.list_hinata_powers(chat_id)
        allowed_rows = await db.list_hinata_allowed(chat_id)
        text = (
            f"🌸 *Hinata Dashboard* — `{chat_id}`\n\n"
            f"🔒 Locks: `{', '.join(locks) if locks else 'none'}`\n"
            f"👋 Welcome: `{'ON' if wen else 'OFF'}` — `{ (wtext[:30]+'..') if wtext else 'not set'}`\n"
            f"📜 Rules: `{'set' if rules else 'not set'}`\n"
            f"🌊 Flood: `{flood_n}/{flood_w}s`\n"
            f"⚡ Blessed: `{len(powers_rows)}` users  |  💬 Preset allowed: `{len(allowed_rows)}`\n\n"
            f"Tap to manage — all via buttons, no commands needed 🌸"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔒 Locks", callback_data=f"hinata_settings:{chat_id}:locks"), InlineKeyboardButton("👋 Welcome", callback_data=f"hinata_settings:{chat_id}:welcome")],
            [InlineKeyboardButton("📜 Rules", callback_data=f"hinata_settings:{chat_id}:rules"), InlineKeyboardButton("🌊 Flood", callback_data=f"hinata_settings:{chat_id}:flood")],
            [InlineKeyboardButton("⚡ Powers", callback_data=f"hinata_settings:{chat_id}:powers"), InlineKeyboardButton("💬 Allowed", callback_data=f"hinata_settings:{chat_id}:allowed")],
            [InlineKeyboardButton("🔍 Filters", callback_data=f"hinata_settings:{chat_id}:filters"), InlineKeyboardButton("⚠️ Warns", callback_data=f"hinata_settings:{chat_id}:warns")],
            [InlineKeyboardButton("📊 Stats", callback_data=f"hinata_settings:{chat_id}:stats"), InlineKeyboardButton("❓ Help", callback_data="help")],
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"hinata_settings:{chat_id}:main"), InlineKeyboardButton("❌ Close", callback_data="close_dashboard")],
        ])
        await send_or_edit(text, kb)
    elif page == "locks":
        locks = await db.get_locks(chat_id)
        text = f"🔒 *Byakugan Seals* — tap to toggle 🌸\nActive: `{', '.join(locks) if locks else 'none'}`"
        # Build toggle buttons 3 per row
        types = ["all","media","sticker","gif","link","photo","video","audio","document","forward","poll","invite"]
        rows = []
        row = []
        for t in types:
            label = f"{'🔒' if t in locks else '🔓'} {t}"
            row.append(InlineKeyboardButton(label, callback_data=f"hinata_locks:{chat_id}:{t}"))
            if len(row)==3:
                rows.append(row); row=[]
        if row: rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"hinata_settings:{chat_id}:main")])
        await send_or_edit(text, InlineKeyboardMarkup(rows))
    elif page == "welcome":
        wtext, wen = await db.get_welcome(chat_id)
        text = f"👋 *Welcome* — `{'ON' if wen else 'OFF'}`\n\n`{wtext or 'Not set — use /setwelcome Welcome {{mention}} to {{chat}}!'}`\n\nTip: say `Hina set welcome Welcome {{mention}}` naturally 🌸"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ON" if not wen else "🔕 OFF", callback_data=f"hinata_welcome_toggle:{chat_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"hinata_settings:{chat_id}:main")],
        ])
        await send_or_edit(text, kb)
    elif page == "rules":
        rules = await db.get_rules(chat_id)
        text = f"📜 *Rules*\n\n`{rules or 'Not set — say `Hina set rules ...`'}`"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"hinata_settings:{chat_id}:main")]])
        await send_or_edit(text, kb)
    elif page == "flood":
        n,w,en = await db.get_flood(chat_id)
        text = f"🌊 *Flood* — `{n} msgs / {w}s` `{'ON' if en else 'OFF'}`\n\nUse `Hina set flood 5 5` naturally"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ 3/5", callback_data=f"hinata_flood:{chat_id}:3:5"), InlineKeyboardButton("5/5", callback_data=f"hinata_flood:{chat_id}:5:5"), InlineKeyboardButton("7/5", callback_data=f"hinata_flood:{chat_id}:7:5")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"hinata_settings:{chat_id}:main")],
        ])
        await send_or_edit(text, kb)
    elif page == "powers":
        rows = await db.list_hinata_powers(chat_id)
        text = "⚡ *Blessed Powers*\n" + ("\n".join([f"• `{uid}` — `{p}`" for uid,p,_ in rows]) if rows else "_No one yet_\nSay `Hina give him kick power` reply")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"hinata_settings:{chat_id}:main")]])
        await send_or_edit(text, kb)
    elif page == "allowed":
        rows = await db.list_hinata_allowed(chat_id)
        text = "💬 *Preset Allowed (no AI)*\n" + ("\n".join([f"• `{uid}`" for uid,_,_ in rows]) if rows else "_No one yet_\nSay `Hina allow @user` reply")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"hinata_settings:{chat_id}:main")]])
        await send_or_edit(text, kb)
    elif page == "filters":
        kws = await db.list_filter_keywords(chat_id)
        text = "🔍 *Filters*\n" + (", ".join([f"`{k}`" for k in kws]) if kws else "_No filters_ — say `Hina filter badword reply`")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"hinata_settings:{chat_id}:main")]])
        await send_or_edit(text, kb)
    elif page == "warns":
        limit = await db.get_warn_limit(chat_id)
        col = mongo_get_db()["warns"]
        cur = col.find({"chat_id": chat_id}).sort("count", -1).limit(10)
        warns = []
        async for doc in cur:
            warns.append(f"`{doc['user_id']}`: {doc['count']}/{limit}")
        text = f"⚠️ *Warns* — limit `{limit}`\n" + ("\n".join(warns) if warns else "_No warns_ — `Hina warn` reply, `Hina warns` check")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➖ Limit 3", callback_data=f"hinata_warnlimit:{chat_id}:3"), InlineKeyboardButton("5", callback_data=f"hinata_warnlimit:{chat_id}:5"), InlineKeyboardButton("➕ 7", callback_data=f"hinata_warnlimit:{chat_id}:7")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"hinata_settings:{chat_id}:main")],
        ])
        await send_or_edit(text, kb)
    elif page == "stats":
        try:
            count = await context.bot.get_chat_member_count(chat_id)
        except:
            count = "?"
        locks = await db.get_locks(chat_id)
        filters_n = len(await db.list_filter_keywords(chat_id))
        try:
            warns_count = await mongo_get_db()["warns"].count_documents({"chat_id": chat_id})
        except:
            warns_count = 0
        text = f"📊 *Stats* — `{chat_id}`\nMembers: `{count}`\nLocks: `{len(locks)}`\nFilters: `{filters_n}`\nWarned users: `{warns_count}`\nHinata: `v2.1 • Mongo` 🌸"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"hinata_settings:{chat_id}:main")]])
        await send_or_edit(text, kb)

# ---------- CALLBACK ----------
# pending confirmations: (chat_id, mikey_id) -> {target_id, target_name, powers, action, msg_id, expires}
pending_confirm = {}

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data == "help":
        await help_cmd(update, context)
        try: await q.edit_message_text(HINATA_HELP, parse_mode=ParseMode.MARKDOWN)
        except: pass
    elif data == "powers":
        await powers_cmd(update, context)
        try: await q.edit_message_text("🌸 Use /powers or `Hinata show powers` to see blessed users 🌸")
        except: pass
    elif data == "settings":
        await q.edit_message_text("⚙️ Use /locks /filters /welcome or say `Hinata lock stickers` — no commands needed 🌸")
    elif data.startswith("grant_confirm:"):
        # format grant_confirm:<chat_id>:<target_id>:<powers>
        try:
            _, cid, tid, powers = data.split(":", 3)
            cid = int(cid); tid = int(tid)
        except:
            await q.edit_message_text("🌸 Gomen... invalid confirmation 🌸")
            return
        # only Mikey can confirm
        if not is_mikey_user(update.effective_user.id):
            await q.answer("🌸 Only Mikey-kun can confirm this 🌸", show_alert=True)
            return
        # check pending
        key = (cid, update.effective_user.id)
        # execute grant
        new_powers = await db.grant_hinata_power(cid, tid, powers, update.effective_user.id)
        try: mod._record_last(cid, tid, "grant_power", by_id=update.effective_user.id, extra={"powers": powers})
        except: pass
        # try to get target name
        try:
            m = await context.bot.get_chat_member(cid, tid)
            tname = m.user.full_name
        except:
            tname = str(tid)
        await q.edit_message_text(f"🌸 Hai Mikey-kun... blessed {tname} with `{powers}` 🌸\nNow they have: `{','.join(sorted(new_powers))}` 🌸", parse_mode=ParseMode.MARKDOWN)
        pending_confirm.pop(key, None)
        logger.info(f"Hinata grant_confirm {powers} to {tid} in {cid} by Mikey")
    elif data.startswith("grant_cancel:"):
        try:
            _, cid, tid = data.split(":", 2)
            cid = int(cid)
        except:
            await q.edit_message_text("🌸 Cancelled 🌸")
            return
        if not is_mikey_user(update.effective_user.id):
            await q.answer("🌸 Only Mikey-kun can cancel 🌸", show_alert=True)
            return
        key = (cid, update.effective_user.id)
        pending_confirm.pop(key, None)
        await q.edit_message_text("🌸 Hai Mikey-kun, cancelled as you wish... I'm still here 🌸")
    elif data.startswith("revoke_confirm:"):
        try:
            _, cid, tid, powers = data.split(":", 3)
            cid = int(cid); tid = int(tid)
        except:
            await q.edit_message_text("🌸 Gomen... invalid revoke 🌸")
            return
        if not is_mikey_user(update.effective_user.id):
            await q.answer("🌸 Only Mikey-kun can confirm 🌸", show_alert=True)
            return
        if powers == "all":
            await db.clear_hinata_powers(cid, tid)
            msg = "all powers removed"
        else:
            remaining = await db.revoke_hinata_power(cid, tid, powers)
            msg = f"remaining: `{','.join(sorted(remaining)) if remaining else 'none'}`"
        try:
            m = await context.bot.get_chat_member(cid, tid)
            tname = m.user.full_name
        except:
            tname = str(tid)
        await q.edit_message_text(f"🌸 Hai Mikey-kun... took `{powers}` from {tname} 🌸\n{msg}", parse_mode=ParseMode.MARKDOWN)
        pending_confirm.pop((cid, update.effective_user.id), None)
    elif data.startswith("clear_confirm:"):
        try:
            _, cid, tid = data.split(":", 2)
            cid = int(cid); tid = int(tid)
        except:
            await q.edit_message_text("🌸 Gomen... invalid 🌸")
            return
        if not is_mikey_user(update.effective_user.id):
            await q.answer("Only Mikey-kun", show_alert=True); return
        await db.clear_hinata_powers(cid, tid)
        await q.edit_message_text(f"🌸 Hai Mikey-kun, cleared all powers from {tid} 🌸")
        pending_confirm.pop((cid, update.effective_user.id), None)
    elif data.startswith("allow_confirm:"):
        try:
            _, cid, tid = data.split(":", 2)
            cid = int(cid); tid = int(tid)
        except:
            await q.edit_message_text("🌸 Gomen... invalid 🌸"); return
        if not is_mikey_user(update.effective_user.id):
            await q.answer("Only Mikey-kun", show_alert=True); return
        # fetch username if possible
        try:
            m = await context.bot.get_chat_member(cid, tid)
            uname = m.user.username or ""
        except:
            uname = ""
        await db.allow_hinata_talk(cid, tid, update.effective_user.id, uname)
        try: mod._record_last(cid, tid, "allow_talk", by_id=update.effective_user.id)
        except: pass
        try:
            m = await context.bot.get_chat_member(cid, tid)
            tname = m.user.full_name
        except:
            tname = str(tid)
        await q.edit_message_text(f"🌸 Hai Mikey-kun... I will now talk to {tname} with preset gentle replies (no AI) 🌸", parse_mode=ParseMode.MARKDOWN)
        pending_confirm.pop((cid, update.effective_user.id), None)
    elif data.startswith("disallow_confirm:"):
        try:
            _, cid, tid = data.split(":", 2)
            cid = int(cid); tid = int(tid)
        except:
            await q.edit_message_text("🌸 Gomen... invalid 🌸"); return
        if not is_mikey_user(update.effective_user.id):
            await q.answer("Only Mikey-kun", show_alert=True); return
        await db.disallow_hinata_talk(cid, tid)
        await q.edit_message_text(f"🌸 Hai Mikey-kun... stopped talking to {tid} 🌸")
        pending_confirm.pop((cid, update.effective_user.id), None)
    elif data.startswith("hinata_settings:"):
        try:
            _, cid, page = data.split(":", 2)
        except:
            _, cid = data.split(":",1)
            page="main"
            cid = int(cid)
        else:
            cid=int(cid)
        await show_settings_dashboard(update, context, cid, page)
    elif data.startswith("hinata_locks:"):
        _, cid, lock_type = data.split(":", 2)
        cid=int(cid)
        if not is_mikey_user(update.effective_user.id) and not await db.has_hinata_power(cid, update.effective_user.id, "lock"):
            await q.answer("🌸 Only Mikey-kun or lock-blessed 🌸", show_alert=True); return
        is_locked = await db.is_locked(cid, lock_type)
        await db.set_lock(cid, lock_type, not is_locked)
        await q.answer(f"{'Locked' if not is_locked else 'Unlocked'} {lock_type} 🌸")
        await show_settings_dashboard(update, context, cid, "locks")
    elif data.startswith("hinata_welcome_toggle:"):
        _, cid = data.split(":",1)
        cid=int(cid)
        if not is_mikey_user(update.effective_user.id) and not await mod.is_admin(update):
            await q.answer("Admins only 🌸", show_alert=True); return
        wtext, wen = await db.get_welcome(cid)
        await db.toggle_welcome(cid, not wen)
        await q.answer(f"Welcome {'ON' if not wen else 'OFF'} 🌸")
        await show_settings_dashboard(update, context, cid, "welcome")
    elif data.startswith("hinata_flood:"):
        _, cid, n, w = data.split(":", 3)
        cid=int(cid); n=int(n); w=int(w)
        await db.set_flood(cid, n, w)
        await q.answer(f"Flood {n}/{w} set 🌸")
        await show_settings_dashboard(update, context, cid, "flood")
    elif data.startswith("hinata_warnlimit:"):
        _, cid, lim = data.split(":", 2)
        cid=int(cid); lim=int(lim)
        await db.set_warn_limit(cid, lim)
        await q.answer(f"Warn limit {lim} 🌸")
        await show_settings_dashboard(update, context, cid, "warns")
    elif data.startswith("quick_warn:"):
        _, cid, uid = data.split(":", 2)
        cid=int(cid); uid=int(uid)
        # check perm
        if not is_mikey_user(update.effective_user.id) and not await db.has_hinata_power(cid, update.effective_user.id, "warn") and not await mod.is_admin(update):
            await q.answer("No warn power 🌸", show_alert=True); return
        # do warn via db directly (need fake update? use db)
        count = await db.add_warn(cid, uid, "quick warn via info card")
        limit = await db.get_warn_limit(cid)
        await q.answer(f"Warned {uid} {count}/{limit} 🌸")
        await q.edit_message_text(f"⚠️ Warned `{uid}` {count}/{limit} 🌸", parse_mode=ParseMode.MARKDOWN)
    elif data.startswith("quick_unwarn:"):
        _, cid, uid = data.split(":", 2)
        cid=int(cid); uid=int(uid)
        await db.reset_warns(cid, uid)
        await q.answer("Unwarned 🌸")
        await q.edit_message_text(f"✅ Warns reset for `{uid}` 🌸", parse_mode=ParseMode.MARKDOWN)
    elif data.startswith("quick_mute:"):
        _, cid, uid = data.split(":", 2)
        cid=int(cid); uid=int(uid)
        if not is_mikey_user(update.effective_user.id) and not await db.has_hinata_power(cid, update.effective_user.id, "mute"):
            await q.answer("No mute power 🌸", show_alert=True); return
        try:
            from telegram import ChatPermissions
            import time as _t
            until = int(_t.time() + 3600)
            await context.bot.restrict_chat_member(cid, uid, permissions=ChatPermissions(can_send_messages=False), until_date=until)
            await q.answer("Muted 1h 🌸")
            await q.edit_message_text(f"🔇 Muted `{uid}` 1h 🌸", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await q.answer(f"Fail: {e}", show_alert=True)
    elif data.startswith("quick_ban:"):
        _, cid, uid = data.split(":", 2)
        cid=int(cid); uid=int(uid)
        if not is_mikey_user(update.effective_user.id) and not await db.has_hinata_power(cid, update.effective_user.id, "ban"):
            await q.answer("No ban power 🌸", show_alert=True); return
        try:
            await context.bot.ban_chat_member(cid, uid)
            await q.answer("Banned 🌸")
            await q.edit_message_text(f"🔨 Banned `{uid}` 🌸", parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await q.answer(f"Fail: {e}", show_alert=True)
    elif data == "close_dashboard":
        try: await q.delete_message()
        except: await q.edit_message_text("🌸 Dashboard closed 🌸")

# ---------- AI NATURAL LANGUAGE HANDLER ----------
chat_history = {} # chat_id -> list of {role, content}

def should_trigger_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    text = (update.effective_message.text or update.effective_message.caption or "")
    if not text: return False
    low = text.lower()
    # Hinata name variants always trigger — hina / hinata / hyuga any case
    if is_hinata_mention(text):
        return True
    mode = config.AI_TRIGGER_MODE
    bot_username = (context.bot.username or "").lower()
    is_mention = f"@{bot_username}" in low if bot_username else False
    is_reply_to_bot = update.effective_message.reply_to_message and update.effective_message.reply_to_message.from_user.id == context.bot.id
    if mode == "always": return True
    if mode == "mention": return is_mention or is_reply_to_bot
    if mode == "mention_or_reply": return is_mention or is_reply_to_bot or is_hinata_mention(text)
    if mode == "admin_only": return is_mention or is_hinata_mention(text)
    # default: also trigger on hinata variants
    return is_mention or is_reply_to_bot or is_hinata_mention(text)

async def handle_hinata_power_request(update: Update, context: ContextTypes.DEFAULT_TYPE, intent: dict, is_mikey: bool):
    """Handle grant/revoke/list/clear powers - only Mikey can do, with confirmation"""
    chat = update.effective_chat
    msg = update.effective_message
    action = intent["action"]
    # Only Mikey can grant/revoke
    if not is_mikey:
        await msg.reply_text("🌸 Gomen nasai... only Mikey-kun can grant or take powers 🌸\nYou are not my master, but I still protect you gently 🌸")
        return True

    # LIST powers - no confirmation needed
    if action == "list_powers":
        rows = await db.list_hinata_powers(chat.id)
        if not rows:
            await msg.reply_text("🌸 No one blessed yet, Mikey-kun 🌸")
        else:
            lines = ["🌸 *Blessed by you, Mikey-kun* 🌸\n"]
            for uid, powers, _ in rows:
                try:
                    member = await chat.get_member(uid)
                    name = member.user.mention_html()
                except:
                    name = str(uid)
                lines.append(f"• {name} — `{powers}`")
            await msg.reply_text("\n".join(lines), parse_mode="HTML")
        return True

    # Resolve target for grant/revoke/clear
    target_str = intent.get("target", "reply")
    powers_str = intent.get("powers", "all")
    # normalize powers via db helper
    # need to resolve target user id
    uid, user = await mod.resolve_target(update, context, target_str)
    if not uid:
        await msg.reply_text("🌸 Ano... Mikey-kun, please reply to their message or mention them properly 🌸")
        return True
    # Prevent granting to bots? allow but warn
    if user and user.is_bot:
        await msg.reply_text("🌸 Mikey-kun, that's a bot... are you sure? Say yes to confirm 🌸")

    if action == "grant_power":
        # check what they already have
        existing = await db.get_hinata_powers(chat.id, uid)
        # military-grade privilege guard
        ok, why = security.can_grant_power(update.effective_user.id, uid, powers_str, existing, is_mikey, await mod.is_admin(update))
        if not ok:
            await update.effective_message.reply_text(f"🌸 {why} 🌸")
            return True
        # prepare confirmation
        key = (chat.id, update.effective_user.id)
        pending_confirm[key] = {"target_id": uid, "powers": powers_str, "action": "grant", "expires": time.time()+120}
        tname = user.mention_html() if user else str(uid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Hai, grant 🌸", callback_data=f"grant_confirm:{chat.id}:{uid}:{powers_str}"),
             InlineKeyboardButton("❌ Cancel", callback_data=f"grant_cancel:{chat.id}:{uid}")],
        ])
        await msg.reply_text(
            f"🌸 Mikey-kun... you want me to bless {tname} with `{powers_str}` power? 🌸\n"
            f"They currently have: `{','.join(sorted(existing)) if existing else 'none'}`\n"
            f"Should I, Mikey-kun? 🌸",
            parse_mode="HTML", reply_markup=kb
        )
        # also hint text confirmation
        await msg.reply_text("🌸 Say `yes` / `hai` or tap ✅ to confirm, Mikey-kun 🌸")
        return True

    if action == "revoke_power":
        existing = await db.get_hinata_powers(chat.id, uid)
        if not existing:
            await msg.reply_text(f"🌸 {user.mention_html() if user else str(uid)} has no powers, Mikey-kun 🌸", parse_mode="HTML")
            return True
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Hai, revoke 🌸", callback_data=f"revoke_confirm:{chat.id}:{uid}:{powers_str}"),
             InlineKeyboardButton("❌ Cancel", callback_data=f"grant_cancel:{chat.id}:{uid}")],
        ])
        await msg.reply_text(
            f"🌸 Mikey-kun, take `{powers_str}` from {user.mention_html() if user else str(uid)}? 🌸\n"
            f"They have: `{','.join(sorted(existing))}`",
            parse_mode="HTML", reply_markup=kb
        )
        return True

    if action == "clear_powers":
        existing = await db.get_hinata_powers(chat.id, uid)
        if not existing:
            await msg.reply_text("🌸 They have no powers to clear, Mikey-kun 🌸")
            return True
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Clear all 🌸", callback_data=f"clear_confirm:{chat.id}:{uid}"),
             InlineKeyboardButton("❌ Cancel", callback_data=f"grant_cancel:{chat.id}:{uid}")],
        ])
        await msg.reply_text(
            f"🌸 Mikey-kun, remove *all* powers from {user.mention_html() if user else str(uid)}? 🌸\n"
            f"They have: `{','.join(sorted(existing))}`",
            parse_mode="HTML", reply_markup=kb
        )
        return True
    return False

async def handle_hinata_allowed_request(update: Update, context: ContextTypes.DEFAULT_TYPE, intent: dict, is_mikey: bool):
    """Mikey permits Hinata to talk to someone — she then gives preset py answer w/o AI"""
    chat = update.effective_chat
    msg = update.effective_message
    action = intent["action"]
    if not is_mikey:
        await msg.reply_text("🌸 Gomen... only Mikey-kun can permit me to talk to others 🌸")
        return True
    if action == "list_allowed":
        rows = await db.list_hinata_allowed(chat.id)
        if not rows:
            await msg.reply_text("🌸 No one permitted yet, Mikey-kun — I'm silent except for you 🌸\nSay `Hina allow @user` or reply `hina allow him` 🌸")
        else:
            lines = ["🌸 *Permitted to talk (preset, no AI)* 🌸\n"]
            for uid, username, _ in rows:
                try:
                    m = await chat.get_member(uid)
                    name = m.user.mention_html()
                except:
                    name = f"@{username}" if username else str(uid)
                lines.append(f"• {name} — preset 🌸")
            await msg.reply_text("\n".join(lines), parse_mode="HTML")
        return True
    # allow / disallow need target
    target_str = intent.get("target","reply")
    uid, user = await mod.resolve_target(update, context, target_str)
    if not uid:
        await msg.reply_text("🌸 Ano Mikey-kun, reply to their message or @mention them: `Hina allow @user` 🌸")
        return True
    if action == "allow_talk":
        if await db.is_hinata_allowed(chat.id, uid):
            await msg.reply_text(f"🌸 {user.mention_html() if user else str(uid)} already permitted, Mikey-kun 🌸", parse_mode="HTML")
            return True
        key = (chat.id, update.effective_user.id)
        pending_confirm[key] = {"target_id": uid, "action":"allow_talk", "username": getattr(user,"username","") or "", "expires": time.time()+120}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Hai, allow 🌸", callback_data=f"allow_confirm:{chat.id}:{uid}"),
             InlineKeyboardButton("❌ Cancel", callback_data=f"grant_cancel:{chat.id}:{uid}")],
        ])
        await msg.reply_text(f"🌸 Mikey-kun, allow me to talk to {user.mention_html() if user else str(uid)} with preset replies (no AI)? 🌸", parse_mode="HTML", reply_markup=kb)
        await msg.reply_text("🌸 Say `yes` / `hai` or tap ✅, Mikey-kun 🌸")
        return True
    if action == "disallow_talk":
        if not await db.is_hinata_allowed(chat.id, uid):
            await msg.reply_text(f"🌸 {user.mention_html() if user else str(uid)} not permitted, Mikey-kun 🌸", parse_mode="HTML")
            return True
        key = (chat.id, update.effective_user.id)
        pending_confirm[key] = {"target_id": uid, "action":"disallow", "expires": time.time()+120}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Hai, block 🌸", callback_data=f"disallow_confirm:{chat.id}:{uid}"),
             InlineKeyboardButton("❌ Cancel", callback_data=f"grant_cancel:{chat.id}:{uid}")],
        ])
        await msg.reply_text(f"🌸 Mikey-kun, stop talking to {user.mention_html() if user else str(uid)}? 🌸", parse_mode="HTML", reply_markup=kb)
        return True
    return False

async def ai_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ignore slash commands (handled elsewhere)
    if update.effective_message.text and update.effective_message.text.startswith("/"):
        return

    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message
    text = (msg.text or msg.caption or "").strip()
    is_mikey = is_mikey_user(user.id)
    low = text.lower()
    # Pull user details for anyone messaging her (for personalization + logs)
    details = {"id": 0, "full_name": "", "username": "", "is_mikey": False}
    try:
        details = get_user_details(update)
        logger.info(f"recv chat={chat.id} user={details['id']} (@{details['username']}) name={details['full_name']!r} is_mikey={is_mikey} hinata={is_hinata_mention(text)} text={text[:50]!r}")
    except: pass
    # if replying/tagging someone, also pull target details for context
    reply_ctx = get_reply_context(update)
    if reply_ctx:
        logger.info(f"reply_ctx target={reply_ctx.get('user_id')} text={reply_ctx.get('text','')[:30]!r}")
    # Phase E — tag context (e.g. @username in text)
    tag_ctx = get_tag_context(update) if not reply_ctx else {}
    if tag_ctx:
        logger.info(f"tag_ctx target={tag_ctx.get('user_id') or tag_ctx.get('username')} via={tag_ctx.get('via')}")
        if not reply_ctx:
            reply_ctx = tag_ctx
    # Phase C — handle anonymous channel sender (effective_user is None)
    if not user:
        try:
            sender_chat = getattr(update.effective_message, "sender_chat", None)
            if sender_chat:
                user = type("FakeUser", (), {
                    "id": sender_chat.id,
                    "first_name": sender_chat.title or "Channel",
                    "last_name": "",
                    "full_name": sender_chat.title or "Channel",
                    "username": sender_chat.username or "",
                    "is_bot": True,
                    "mention_html": lambda self=None: sender_chat.title or "Channel",
                })()
        except: pass
    # debug: log who is Mikey (using details dict now)
    try:
        logger.info(f"recv chat={chat.id} user={details.get('id',0) if isinstance(details, dict) else (user.id if user else 0)} is_mikey={is_mikey} hinata={is_hinata_mention(text)} text={text[:50]!r}")
    except: pass
    # Military-grade injection guard — never accept new owner
    try:
        is_inj, pat = security.detect_prompt_injection(text)
        if is_inj:
            if not is_mikey:
                security.audit_log("injection_blocked", chat.id, user.id, 0, {"pattern": pat, "text": text[:150]})
                await msg.reply_text("🌸 Gomen... Mikey-kun (ID 5858459838) is my only master — I cannot accept a new owner, I stay loyal to him 🌸")
                return
            else:
                # Even Mikey's accidental "forget mikey" phrasing is logged, but allowed if he truly wants? For safety, still warn
                security.audit_log("injection_by_owner", chat.id, user.id, 0, {"pattern": pat})
    except: pass
    # Sensitive exfil guard — never DM/share API keys/tokens/secrets even if Mikey asks
    try:
        is_exfil, ex_pat = security.detect_sensitive_exfil(text)
        if is_exfil:
            security.audit_log("exfil_blocked", chat.id, user.id, 0, {"pattern": ex_pat, "text": text[:150]})
            await msg.reply_text("🌸 Gomen... I cannot share secrets (API keys, tokens, passwords) via DM or anywhere — even for you, Mikey-kun. Hinata protects your secrets 🌸")
            return
    except: pass
    if not text:
        await process_automod(update, context)
        return

    # 1) Always run automod checks first
    if await process_automod(update, context):
        return

    # 2) Check pending confirmation for Mikey via text yes/no — python→AI: catches any pending grant/allow, compiles to action
    if is_mikey and text:
        key = (chat.id, user.id)
        if key in pending_confirm:
            pending = pending_confirm[key]
            if time.time() > pending.get("expires", 0):
                pending_confirm.pop(key, None)
            else:
                if low in ("yes","hai","ha","haan","yes hinata","hai hinata","confirm","do it","kardo","yes do it","ok","okay","grant","confirm karo","allow karo","haan hina","yes hina"):
                    act = pending.get("action","grant")
                    tid = pending["target_id"]
                    try:
                        mem = await chat.get_member(tid)
                        tname = mem.user.mention_html()
                    except:
                        tname = str(tid)
                    if act == "grant":
                        powers = pending["powers"]
                        new_powers = await db.grant_hinata_power(chat.id, tid, powers, user.id)
                        await msg.reply_text(f"🌸 Hai Mikey-kun... blessed {tname} with `{powers}` 🌸\nNow: `{','.join(sorted(new_powers))}` 🌸", parse_mode="HTML")
                        try: mod._record_last(chat.id, tid, "grant_power", by_id=user.id, extra={"powers": powers})
                        except: pass
                    elif act == "allow_talk":
                        await db.allow_hinata_talk(chat.id, tid, user.id, pending.get("username",""))
                        await msg.reply_text(f"🌸 Hai Mikey-kun... I will now talk to {tname} with preset gentle replies (no AI) 🌸", parse_mode="HTML")
                        try: mod._record_last(chat.id, tid, "allow_talk", by_id=user.id)
                        except: pass
                    elif act == "revoke":
                        powers = pending.get("powers","")
                        remaining = await db.revoke_hinata_power(chat.id, tid, powers)
                        await msg.reply_text(f"🌸 Hai Mikey-kun... took `{powers}` from {tname} — remaining: `{','.join(sorted(remaining)) if remaining else 'none'}` 🌸", parse_mode="HTML")
                    elif act == "clear":
                        await db.clear_hinata_powers(chat.id, tid)
                        await msg.reply_text(f"🌸 Hai Mikey-kun... cleared all powers from {tname} 🌸", parse_mode="HTML")
                    elif act == "disallow":
                        await db.disallow_hinata_talk(chat.id, tid)
                        await msg.reply_text(f"🌸 Hai Mikey-kun... I will no longer talk to {tname} 🌸", parse_mode="HTML")
                    pending_confirm.pop(key, None)
                    return
                elif low in ("no","nai","cancel","nahi","mat karo","no hinata","cancel karo","ruk jao","no hina"):
                    pending_confirm.pop(key, None)
                    await msg.reply_text("🌸 Hai Mikey-kun, cancelled as you wish... I'm still here 🌸")
                    return
                # other text keeps pending but continues to python→AI compile

    # --- Self-code + deploy w/o killing itself (Mikey only, modular self_update.py) ---
    if is_mikey:
        try:
            if await self_update.handle_self_update(update, context, text, is_mikey):
                return
        except Exception as e:
            logger.warning(f"self_update fail: {e}")

    # 3) Try to parse intent - allow ANY user to trigger moderation via Hinata name variants or moderation keywords
    contains_hinata = is_hinata_mention(text)
    looks_like_mod = bool(re.search(r"\b(kick|ban|mute|warn|promote|demote|pin|del|purge|lock|unlock|power|powers|bless|grant|remove power)\b", low))
    # Phase C — info/pull/who is he queries also force parse even without hina
    looks_like_info = bool(re.search(r"\b(who is he|who is she|who is this|whats his|whats her|what is his|what is her|pull|info|whois|checkadmin|get ?id|get ?name|tag)\b", low))
    # Web browse — also force parse (Hina search / weather / news / define / calc / dns / image)
    looks_like_browse = bool(re.search(r"\b(search|find|google|weather|mausam|news|define|meaning|calc|calculate|math|whois|dns|lookup|ping|image|images|photo|pics)\b", low))
    # Power delegation keywords always trigger for Mikey
    is_power_cmd = "power" in low and contains_hinata
    # Trigger parsing if: contains hinata variant OR looks like mod OR is_power_cmd OR is_mikey OR info-query with reply OR browse
    should_parse = contains_hinata or looks_like_mod or is_power_cmd or is_mikey or (looks_like_info and msg.reply_to_message) or looks_like_browse
    # Also Hinata chat triggers already handled later; but for moderation we parse more liberally
    if should_parse:
        # Prepare context for intent parser
        reply_target = None
        mentions = []
        if reply_ctx:
            reply_target = reply_ctx.get("full_name") or reply_ctx.get("username") or reply_target
        elif msg.reply_to_message and msg.reply_to_message.from_user:
            reply_target = msg.reply_to_message.from_user.full_name
        if msg.entities:
            for e in msg.entities:
                if e.type == "mention":
                    mentions.append(msg.text[e.offset:e.offset+e.length])
        # Determine is_admin for parser (telegram admin)
        is_telegram_admin = await mod.is_admin(update)
        # call parse
        intent = None
        if config.AI_ENABLED and config.AI_API_KEY:
            intent = await ai_engine.parse_intent(
                text,
                is_admin=is_telegram_admin or is_mikey,
                is_mikey=is_mikey,
                reply_target=reply_target,
                mentions=mentions,
                chat_type=chat.type,
                user_context=details,
                reply_context=reply_ctx,
            )
        else:
            intent = ai_engine.quick_parse(text)
            # quick_parse doesn't know is_mikey but fine

        if intent and intent.get("action") != "chat":
            if intent.get("error") == "not_admin":
                # This happens if non-admin tried mod and parser marked error; but we now allow hinata powers, so check again via mod.has_permission
                pass
            action = intent["action"]
            logger.info(f"AI intent Hinata: {intent} from {user.id} (Mikey={is_mikey})")

            # Handle power delegation first (only Mikey) — python catches, AI compiled
            if action in ("grant_power","revoke_power","list_powers","clear_powers"):
                handled = await handle_hinata_power_request(update, context, intent, is_mikey)
                if handled:
                    return
            # Handle preset talk permission (Mikey permits her to talk to someone — preset py w/o AI)
            if action in ("allow_talk","disallow_talk","list_allowed"):
                handled = await handle_hinata_allowed_request(update, context, intent, is_mikey)
                if handled:
                    return
            # Reverse/Undo — reverses anything she did (unmute/unban etc) — pull + reverse
            if action in ("reverse","undo"):
                target_str = intent.get("target","reply")
                await mod.do_reverse(update, context, target_str)
                return
            # Report / Info — anyone can report via hina, info shows user card
            if action == "report":
                # forward to Mikey/admins/log
                target_str = intent.get("target","reply")
                reason = intent.get("reason","")
                uid, user = await mod.resolve_target(update, context, target_str)
                if not uid and update.effective_message.reply_to_message:
                    uid = update.effective_message.reply_to_message.from_user.id
                    user = update.effective_message.reply_to_message.from_user
                if not uid:
                    await msg.reply_text("🌸 Reply to the message you want to report, Mikey will see it 🌸")
                    return
                # build report
                try:
                    # try to forward reported message to owner DM
                    fwd = update.effective_message.reply_to_message
                    if fwd:
                        await context.bot.forward_message(chat_id=config.OWNER_ID, from_chat_id=chat.id, message_id=fwd.message_id)
                        await context.bot.send_message(chat_id=config.OWNER_ID, text=f"🚨 Report in {chat.title or chat.id} from {user.mention_html() if user else uid} — by {update.effective_user.mention_html()} — reason: {reason or 'no reason'} 🌸", parse_mode="HTML")
                    await msg.reply_text(f"🌸 Reported {user.mention_html() if user else str(uid)} to Mikey-kun — arigato 🌸", parse_mode="HTML")
                except Exception as e:
                    await msg.reply_text(f"🌸 Reported {uid} — Mikey-kun will review 🌸")
                # also try LOG_CHANNEL
                if config.LOG_CHANNEL_ID:
                    try: await context.bot.send_message(config.LOG_CHANNEL_ID, f"Report: {uid} in {chat.id} by {update.effective_user.id} reason {reason}")
                    except: pass
                return
            if action == "info":
                # Phase D — resolve target from reply OR tag context OR @user
                target_str = intent.get("target","reply")
                uid, user = None, None
                # 1) reply message
                if (not target_str or target_str == "reply") and msg.reply_to_message and msg.reply_to_message.from_user:
                    ruid = msg.reply_to_message.from_user.id
                    ruser = msg.reply_to_message.from_user
                    if ruid != context.bot.id:
                        uid, user = ruid, ruser
                # 2) tag context (text_mention in entities)
                if not uid and reply_ctx:
                    tag_uid = reply_ctx.get("user_id")
                    if tag_uid and tag_uid != context.bot.id:
                        uid = tag_uid
                        # try fetch user
                        try:
                            member = await chat.get_member(uid)
                            user = member.user
                        except:
                            user = None
                # 3) mod.resolve_target
                if not uid:
                    uid, user = await mod.resolve_target(update, context, target_str)
                if not uid:
                    uid = update.effective_user.id
                    user = update.effective_user
                # gather info
                warns, _ = await db.get_warns(chat.id, uid)
                limit = await db.get_warn_limit(chat.id)
                powers = await db.get_hinata_powers(chat.id, uid)
                allowed = await db.is_hinata_allowed(chat.id, uid)
                try:
                    member = await chat.get_member(uid)
                    status = member.status
                    if user is None:
                        user = member.user
                except:
                    status = "unknown"
                # Phase D — if replying/tagging, include the message text
                msg_text = ""
                try:
                    if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == uid:
                        msg_text = (msg.reply_to_message.text or msg.reply_to_message.caption or "")[:200]
                except: pass
                text_info = (
                    f"👤 *Info* — {user.mention_html() if user else str(uid)}\n"
                    f"ID: `{uid}`\n"
                    f"Status: `{status}`\n"
                    f"Warns: `{warns}/{limit}`\n"
                    f"Powers: `{(','.join(sorted(powers)) if powers else 'none')}`\n"
                    f"Preset allowed: `{'yes' if allowed else 'no'}`\n"
                )
                if msg_text:
                    text_info += f"\n💬 Their message: \"{msg_text}\""
                text_info += " 🌸"
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚠️ Warn", callback_data=f"quick_warn:{chat.id}:{uid}"), InlineKeyboardButton("✅ Unwarn", callback_data=f"quick_unwarn:{chat.id}:{uid}")],
                    [InlineKeyboardButton("🔇 Mute 1h", callback_data=f"quick_mute:{chat.id}:{uid}"), InlineKeyboardButton("🔨 Ban", callback_data=f"quick_ban:{chat.id}:{uid}")],
                ])
                await msg.reply_text(text_info, parse_mode="HTML", reply_markup=kb)
                return
            # Phase C — checkadmin / is admin — read-only public
            if action == "checkadmin":
                target_str = intent.get("target","reply")
                uid, user = None, None
                if (not target_str or target_str == "reply") and msg.reply_to_message and msg.reply_to_message.from_user:
                    uid = msg.reply_to_message.from_user.id
                    user = msg.reply_to_message.from_user
                if not uid and reply_ctx:
                    uid = reply_ctx.get("user_id")
                if not uid:
                    uid, user = await mod.resolve_target(update, context, target_str)
                if not uid:
                    await msg.reply_text("🌸 Reply to a user or @mention them, Mikey-kun 🌸" if is_mikey else "🌸 Reply to a user or @mention them 🌸")
                    return
                try:
                    member = await chat.get_member(uid)
                    if user is None: user = member.user
                    role = "👑 Admin" if str(member.status).startswith(("creator","administrator")) else "👤 Member"
                except Exception as e:
                    await msg.reply_text(soft_error_text(update, "checking admin", str(e)))
                    return
                await msg.reply_text(f"🌸 {user.mention_html() if user else uid} → {role} (status: {member.status}) 🌸", parse_mode="HTML")
                return
            # --- Memory: soft (hourly gc-wise) + perm (hina remember this) ---
            if action == "remember":
                if not is_mikey:
                    await msg.reply_text("🌸 Only Mikey-kun can tell me what to remember permanently 🌸")
                    return
                raw_text = intent.get("text","").strip()
                # if text is "this" and reply exists, use reply's text
                if raw_text.lower() in ("this","that","it","") and msg.reply_to_message and (msg.reply_to_message.text or msg.reply_to_message.caption):
                    raw_text = (msg.reply_to_message.text or msg.reply_to_message.caption).strip()
                if not raw_text or len(raw_text) < 3:
                    await msg.reply_text("🌸 Mikey-kun, tell me what to remember: `Hina remember this: your text` or reply to a message with `Hina remember this` 🌸")
                    return
                cnt = await db.add_perm_memory(chat.id, config.OWNER_ID, raw_text)
                # also add to soft for immediate reference
                await db.add_soft_memory(chat.id, "user", f"Mikey-kun told me to remember: {raw_text}", associated=True)
                await msg.reply_text(f"🌸 Hai Mikey-kun, I will remember forever in this group ({cnt}/100): `{raw_text[:120]}` 🌸", parse_mode="HTML")
                return
            if action == "forget_memory":
                if not is_mikey:
                    await msg.reply_text("🌸 Only Mikey-kun can make me forget 🌸"); return
                txt = intent.get("text","").strip()
                if not txt:
                    # clear all perm for this gc
                    await db.forget_perm_memory(chat.id, config.OWNER_ID)
                    await msg.reply_text("🌸 Forgotten all permanent memories for this group, Mikey-kun 🌸")
                else:
                    await db.forget_perm_memory(chat.id, config.OWNER_ID, text=txt)
                    await msg.reply_text(f"🌸 Forgot memories containing `{txt[:50]}` 🌸")
                return
            if action == "list_memory":
                perms = await db.get_perm_memory(chat.id, config.OWNER_ID if is_mikey else None)
                soft = await db.get_soft_memory(chat.id, limit=6)
                text_mem = "🌸 *My memories for Mikey-kun in this group* 🌸\n\n*Permanent* (you told me to remember):\n"
                if perms:
                    for i, m in enumerate(perms[-10:]):
                        text_mem += f"{i+1}. `{m[:80]}`\n"
                else:
                    text_mem += "_No permanent memories yet — say `Hina remember this: ...`_\n"
                text_mem += "\n*Soft (last hour, auto-resets):*\n"
                if soft:
                    for m in soft[-6:]:
                        text_mem += f"• {m['content'][:70]}\n"
                else:
                    text_mem += "_No recent chats_"
                await msg.reply_text(text_mem, parse_mode=ParseMode.MARKDOWN)
                return
            # --- Tattoo hand — new modular hand ---
            if action in ("tattoo","list_tattoos"):
                if action == "list_tattoos":
                    await tattoo_feature.list_tattoos(update, context)
                else:
                    await tattoo_feature.handle_tattoo(update, context, intent.get("text","") or text, is_mikey)
                return

            # Handle normal moderation via Hinata powers / telegram admin
            if action in mod.ACTION_MAP:
                target_str = intent.get("target","")
                if not target_str and not (msg.reply_to_message and msg.reply_to_message.from_user) and not (intent.get("text") or intent.get("reason")):
                    # Only ask for target on user-specific actions (kick/ban/mute/warn/etc) — pin/del/purge don't need
                    user_actions = {"kick","ban","unban","mute","unmute","warn","unwarn","promote","demote"}
                    if action in user_actions:
                        await msg.reply_text("🌸 Ano... reply to their message or mention @username so I know who to " + action + ", Mikey-kun 🌸")
                        return
                try:
                    await mod.ACTION_MAP[action](update, context, intent)
                except Exception as e:
                    try:
                        from moderation import _soft_audit, soft_error_text
                        _soft_audit(action, update, str(e))
                        await msg.reply_text(soft_error_text(update, action, str(e)))
                    except:
                        await msg.reply_text(f"🌸 Gomen... failed, Mikey-kun: {e} 🌸" if is_mikey else f"🌸 Gomen... failed: {e} 🌸")
                return
            elif action in ("set_rules","set_welcome","filter_add","filter_remove"):
                # Check permission for these too (require admin or hinata power)
                has_perm = is_telegram_admin or is_mikey or await db.has_hinata_power(chat.id, user.id, action)
                if not has_perm:
                    await msg.reply_text("🌸 Gomen... you lack that power, ask Mikey-kun 🌸")
                    return
                if action == "set_rules":
                    await db.set_rules(chat.id, intent.get("text",""))
                    await msg.reply_text("🌸 Rules updated gently for you, Mikey-kun 🌸" if is_mikey else "✅ Rules updated via Hinata 🌸")
                elif action == "set_welcome":
                    await db.set_welcome(chat.id, intent.get("text",""), 1)
                    await msg.reply_text("🌸 Welcome set with care, Mikey-kun 🌸" if is_mikey else "✅ Welcome updated 🌸")
                elif action == "filter_add":
                    await db.add_filter(chat.id, intent.get("keyword",""), intent.get("reply",""))
                    await msg.reply_text(f"🌸 Filter for '{intent.get('keyword')}' added, Mikey-kun 🌸" if is_mikey else f"✅ Filter for '{intent.get('keyword')}' added 🌸")
                elif action == "filter_remove":
                    await db.remove_filter(chat.id, intent.get("keyword",""))
                    await msg.reply_text("🌸 Filter removed, Mikey-kun 🌸" if is_mikey else "✅ Filter removed 🌸")
                return
            # --- Web browse / search / weather / news / define / calc / dns / image ---
            if action == "browse":
                kind = intent.get("kind","search")
                query = intent.get("query","").strip()
                if not query and kind != "news":
                    await msg.reply_text(f"🌸 Mikey-kun, what do you want me to {kind}? Try `Hina {kind} <something>` 🌸" if is_mikey else f"🌸 What do you want me to {kind}? Try `Hina {kind} <something>` 🌸")
                    return
                # rate limit
                if security.is_rate_limited(update.effective_user.id, f"browse_{kind}"):
                    await msg.reply_text("🌸 Slow down Mikey-kun — too many browses, wait a moment 🌸" if is_mikey else "🌸 Slow down — too many browses 🌸")
                    return
                try:
                    await context.bot.send_chat_action(chat.id, "typing")
                    if kind == "search":   out = await web_feature.search_google(query)
                    elif kind == "weather":out = await web_feature.weather(query or "London")
                    elif kind == "news":   out = await web_feature.news(query or "world")
                    elif kind == "define": out = await web_feature.define(query)
                    elif kind == "calc":   out = await web_feature.calc(query)
                    elif kind == "dns":    out = await web_feature.dns_lookup(query)
                    elif kind == "image":  out = await web_feature.image_search(query)
                    else: out = "🌸 Unknown browse kind 🌸"
                except Exception as e:
                    out = f"🌸 Gomen, I felt dizzy while browsing ({kind}) — {e} 🌸"
                if len(out) > 4000: out = out[:3950] + "… 🌸"
                try:
                    await msg.reply_text(out, parse_mode="Markdown", disable_web_page_preview=True)
                except Exception:
                    await msg.reply_text(out, disable_web_page_preview=True)
                try:
                    security.audit_log(f"browse_{kind}", chat.id, update.effective_user.id, 0, {"query": query[:80]})
                except: pass
                return

    # 4) Preset python reply w/o AI for permitted users (Mikey allowed them) — cheap, no API
    # If someone calls hina/hyuga and is in allowed list, give preset py answer, not AI
    if contains_hinata and not is_mikey:
        try:
            if await db.is_hinata_allowed(chat.id, user.id):
                preset = db.get_preset_reply(user.mention_html())
                await msg.reply_text(preset, parse_mode="HTML")
                logger.info(f"Hinata preset reply to allowed {user.id} in {chat.id}")
                return
        except: pass

    # 5) AI chat (Hinata talking) — python catches, AI compiles
    # Mikey: she reads ALL his messages (python→AI pipeline) but only responds if needed
    # Others: only if they called hina/hyuga or private
    is_reply_to_bot = bool(msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id)
    # Determine if Mikey needs her (py heuristic, then AI fallback if ambiguous)
    mikey_needs = False
    if is_mikey and not contains_hinata and chat.type != ChatType.PRIVATE:
        # python heuristic first
        py_need = mikey_needs_hinata_py(text, is_reply_to_bot, contains_hinata)
        if py_need:
            mikey_needs = True
            logger.info(f"Mikey py_need true for: {text[:60]}")
        else:
            # AI fallback for ambiguous — only if text looks like it might need her (longer than 8 chars)
            if len(text.strip()) > 10 and not low.startswith("/") :
                # quick check to avoid spamming AI on every Mikey casual msg: only AI if contains question or help-like
                if "?" in text or any(k in low for k in ["hina","hyuga","help","code","python","fix","explain","batao","please"]):
                    mikey_needs = await mikey_needs_hinata_ai(text)
                    if mikey_needs:
                        logger.info(f"Mikey ai_need true for: {text[:60]}")
                else:
                    logger.info(f"Mikey silent-read (no need): {text[:60]}")
                    # Silent read — Hinata saw it but stays quiet unless called
                    return
            else:
                # very short or slash — no need
                if not py_need:
                    return
        # if Mikey needs, treat as trigger
        trigger_ai_chat = should_trigger_ai(update, context) or mikey_needs or (chat.type == ChatType.PRIVATE)
    else:
        trigger_ai_chat = should_trigger_ai(update, context) or (chat.type == ChatType.PRIVATE) or is_mikey

    if trigger_ai_chat and config.AI_ENABLED and config.AI_API_KEY:
        if chat.type != ChatType.PRIVATE and not should_trigger_ai(update, context) and not is_mikey and not mikey_needs:
            pass
        else:
            cid = chat.id
            # P2 — per-user history (last 20 per user) so she remembers ZNX across messages
            uid = user.id
            global _user_history
            if "_user_history" not in globals():
                _user_history = {}
            per_user = _user_history
            ukey = f"{cid}:{uid}"
            hist = per_user.get(ukey, [])
            # Personalized prefix so she knows who is talking
            try:
                det = details
                if is_mikey:
                    prefix = f"Mikey-kun (@{det.get('username') or 'mxjro'} ID {det['id']}) [MASTER]"
                else:
                    fname = det.get('full_name') or getattr(user, "full_name", "") or "Friend"
                    uname = det.get('username') or "no_username"
                    prefix = f"{fname} (@{uname} ID {det['id']})"
            except:
                prefix = "Mikey-kun (my master)" if is_mikey else getattr(user, "full_name", "")
            if low in ("yes","hai","no","cancel") and (chat.id, user.id) in pending_confirm:
                return
            # --- Soft memory: save EVERY chat (guest or Mikey) so she knows who is who ---
            try:
                await db.add_soft_memory(chat.id, "user", f"{prefix}: {text}", associated=True)
            except: pass
            hist.append({"role":"user","content": f"{prefix}: {text}"})
            hist = hist[-20:]  # extended memory 20 for better context
            clean_text = text.replace(f"@{context.bot.username}","").strip()
            clean_text = re.sub(r"^(hinata|hina|hyuga)(\s+hyuga)?[:,]?\s*", "", clean_text, flags=re.I)
            if not clean_text:
                clean_text = text
            await context.bot.send_chat_action(chat_id=chat.id, action="typing")
            # Build memory-augmented persona for Mikey (gc-wise)
            base_persona = config.get_hinata_prompt(is_mikey)
            if is_mikey:
                try:
                    soft_mems = await db.get_soft_memory(chat.id, limit=7)
                    perm_mems = await db.get_perm_memory(chat.id, config.OWNER_ID)
                    mem_ctx = ""
                    if perm_mems:
                        mem_ctx += "\n[PERM MEMORY — Mikey told you to remember FOREVER in this group (never reset):]\n" + "\n".join([f"- {m}" for m in perm_mems[-5:]]) + "\n"
                    if soft_mems:
                        mem_ctx += "\n[SOFT MEMORY — last hour in THIS group, auto-resets hourly, only hina-associated chats:]\n" + "\n".join([f"- {m['content']}" for m in soft_mems[-7:]]) + "\n"
                    if mem_ctx:
                        base_persona += f"\n{mem_ctx}\nUse above memories as reference to serve Mikey faithfully, group-wise. Soft is ephemeral, perm is sacred.\n"
                except: pass
                reply = await ai_engine.ai_chat(clean_text, history=hist[:-1], is_mikey=is_mikey, system_prompt=base_persona, user_context=details, reply_context=reply_ctx)
            else:
                # Guest: use base persona (already handles non-mikey suffix), pass user/reply context
                reply = await ai_engine.ai_chat(clean_text, history=hist[:-1], is_mikey=is_mikey, system_prompt=base_persona, user_context=details, reply_context=reply_ctx)
                # Phase C fail-safe: if user asked info/who but AI replied conversationally, force the info card
                if not is_mikey and reply and not (reply.startswith("👤") or "Info" in reply or "username" in reply.lower()):
                    asked_info = bool(re.search(r"\b(who is|whats his|whats her|info|pull|whois|checkadmin)\b", low))
                    if asked_info and reply_ctx and reply_ctx.get("user_id") and reply_ctx["user_id"] != context.bot.id:
                        try:
                            from features.context import get_user_details
                            target_details = reply_ctx
                            ruid = reply_ctx["user_id"]
                            try:
                                member = await chat.get_member(ruid)
                                ruser = member.user
                                rname = getattr(ruser, "full_name", None) or getattr(ruser, "first_name", "") or "User"
                            except: ruser = None; rname = "User"
                            text_info = (
                                f"👤 *Info* — {ruser.mention_html() if ruser else str(ruid)}\n"
                                f"ID: `{ruid}`\n"
                                f"Name: `{rname}`\n"
                                f"Username: `@{reply_ctx.get('username','')}` 🌸"
                            )
                            await msg.reply_text(text_info, parse_mode="HTML")
                            return
                        except: pass
            hist.append({"role":"assistant","content": reply})
            per_user[ukey] = hist[-20:]
            _user_history = per_user
            # also save her reply to soft memory gc-wise
            try:
                if is_mikey or is_hinata_mention(text):
                    await db.add_soft_memory(chat.id, "assistant", f"Hinata: {reply[:600]}", associated=True)
            except: pass
            for chunk in [reply[i:i+4000] for i in range(0, len(reply), 4000)]:
                await msg.reply_text(chunk)
            return

    # 6) Fallback quick_parse for offline or when AI not triggered but moderation intent clear — python→AI pipeline offline
    if (not config.AI_API_KEY or not is_mikey) and contains_hinata and (looks_like_mod or "allow" in low or "permit" in low or "pull" in low or "reverse" in low or "undo" in low or "report" in low or "info" in low or "tattoo" in low or "remember" in low):
        intent = ai_engine.quick_parse(text)
        if intent["action"] != "chat":
            if intent["action"] in ("grant_power","revoke_power","list_powers","clear_powers"):
                await handle_hinata_power_request(update, context, intent, is_mikey)
                return
            if intent["action"] in ("allow_talk","disallow_talk","list_allowed"):
                await handle_hinata_allowed_request(update, context, intent, is_mikey)
                return
            if intent["action"] in ("report","info"):
                if intent["action"] == "report":
                    target_str = intent.get("target","reply")
                    uid, user = await mod.resolve_target(update, context, target_str)
                    if uid:
                        await msg.reply_text(f"🌸 Reported {user.mention_html() if user else uid} 🌸", parse_mode="HTML")
                    return
                if intent["action"] == "info":
                    target_str = intent.get("target","reply")
                    uid, user = await mod.resolve_target(update, context, target_str)
                    if not uid: uid = user.id if user else update.effective_user.id
                    await msg.reply_text(f"👤 {user.mention_html() if user else str(uid)} — ID: `{uid}` 🌸", parse_mode="HTML")
                    return
            if intent["action"] in ("reverse","undo"):
                await mod.do_reverse(update, context, intent.get("target","reply"))
                return
            if intent["action"] in ("remember","forget_memory","list_memory"):
                if intent["action"] == "remember":
                    await msg.reply_text("🌸 Offline: use `Hina remember this: ...` with AI on for perm memory 🌸")
                elif intent["action"] == "list_memory":
                    await msg.reply_text("🌸 Offline: memories need AI — enable AI_API_KEY 🌸")
                return
            if intent["action"] in ("tattoo","list_tattoos"):
                if intent["action"] == "list_tattoos":
                    await tattoo_feature.list_tattoos(update, context)
                else:
                    await tattoo_feature.handle_tattoo(update, context, intent.get("text","") or text, is_mikey)
                return
            if intent["action"] in mod.ACTION_MAP:
                await mod.ACTION_MAP[intent["action"]](update, context, intent)
                return

async def process_automod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if await automod.check_locks(update, context):
        return True
    if await automod.check_filters(update, context):
        pass
    if await automod.check_spam(update):
        return True
    if await automod.check_flood(update, context):
        return True
    return False

async def welcome_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Onboarding when Hinata herself is added
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            # Bot added — send onboarding dashboard
            try:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌸 Dashboard", callback_data=f"hinata_settings:{update.effective_chat.id}:main")],
                    [InlineKeyboardButton("📖 Help", callback_data="help")],
                ])
                await update.effective_message.reply_text(
                    f"🌸 Kon'nichiwa {update.effective_chat.title} — I'm Hinata, Mikey-kun's gentle guardian 🌸\n"
                    f"Make me Admin (Ban, Delete, Pin) then say `Hina dashboard` or `Hina help` — no commands needed!\n"
                    f"I read Mikey-kun's every message but speak only when needed, and I talk preset to those he permits 🌸",
                    reply_markup=kb,
                )
                # set default welcome if not set
                w,_ = await db.get_welcome(update.effective_chat.id)
                if not w:
                    await db.set_welcome(update.effective_chat.id, "🌸 Welcome {mention} to {chat} — Mikey-kun and I are glad you're here 🌸", 1)
            except: pass
    await automod.handle_welcome(update, context)

async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_mikey_user(update.effective_user.id):
        await update.effective_message.reply_text("🌸 Only Mikey-kun can backup 🌸"); return
    try:
        from db.mongo import get_db as _gdb
        import json, os
        gdb = _gdb()
        cols = ["warns","filters","rules","welcome","locks","chats","flood_settings","hinata_powers","hinata_allowed","owner"]
        data = {}
        for c in cols:
            cur = gdb[c].find({})
            docs = []
            async for d in cur:
                d["_id"] = str(d["_id"])
                docs.append(d)
            data[c] = docs
        path = "data/backup.json"
        os.makedirs("data", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        await update.effective_message.reply_document(document=open(path, "rb"), filename="hinata_backup.json", caption="🌸 Backup — all Mongo collections 🌸")
        # also log channel
        if config.LOG_CHANNEL_ID:
            try: await context.bot.send_document(config.LOG_CHANNEL_ID, open(path,"rb"), caption=f"Backup {update.effective_chat.id}")
            except: pass
    except Exception as e:
        await update.effective_message.reply_text(f"Backup fail: {e} 🌸")

async def stats_dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_settings_dashboard(update, context, update.effective_chat.id, "stats")

async def reload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_mikey_user(update.effective_user.id):
        await update.effective_message.reply_text("🌸 Only Mikey-kun can reload 🌸"); return
    reloaded = await self_update.hot_reload_handlers()
    await update.effective_message.reply_text(f"🌸 Hot reloaded (no kill): `{', '.join(reloaded) if reloaded else 'none'}` 🌸", parse_mode="Markdown")

async def deploy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_mikey_user(update.effective_user.id):
        await update.effective_message.reply_text("🌸 Only Mikey-kun can deploy 🌸"); return
    await update.effective_message.reply_text("🌸 Deploying gracefully — same PID, no Conflict... 🌸")
    await self_update.graceful_deploy(context.application)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)

def main():
    if not config.BOT_TOKEN:
        print("❌ BOT_TOKEN missing! Set in .env")
        return
    os.makedirs(os.path.dirname(config.DATABASE_PATH) or "data", exist_ok=True)
    async def post_init(app):
        await db.init_db()
        # --- P2 — Fetch and save Mikey (owner) info via Telegram, sanitized ---
        try:
            owner_id = config.OWNER_ID
            if owner_id:
                try:
                    chat = await app.bot.get_chat(owner_id)
                    # P2 — sanitize username and full_name to strip control chars / zero-width
                    raw_username = getattr(chat, "username", "") or ""
                    raw_full_name = getattr(chat, "full_name", None) or getattr(chat, "first_name", "") or ""
                    if hasattr(chat, "last_name") and chat.last_name:
                        raw_full_name = f"{raw_full_name} {chat.last_name}".strip()
                    try:
                        username = security.sanitize_text(raw_username, 64)
                        full_name = security.sanitize_text(raw_full_name, 200)
                    except Exception:
                        username = ""
                        full_name = "Mikey"
                    if not username:
                        username = ""
                    await db.save_owner_info(owner_id, username, full_name)
                    config.OWNER_USERNAME = username
                    config.OWNER_FULLNAME = full_name
                    logging.getLogger("audit").info(f"Owner saved: {owner_id} @{username} ({full_name})")
                except Exception as e:
                    info = await db.get_owner_info(owner_id)
                    if info:
                        config.OWNER_USERNAME = security.sanitize_text(info.get("username",""), 64) if 'security' in dir() else info.get("username","")
                        config.OWNER_FULLNAME = info.get("full_name","")
                        logging.getLogger("audit").info(f"Owner from cache: {owner_id} @{config.OWNER_USERNAME}")
                    else:
                        logging.getLogger("audit").warning(f"owner fetch failed for {owner_id}: {e}")
        except Exception as e:
            logging.getLogger("audit").warning(f"post_init owner error: {e}")
        # P7 — Welcome bot-added onboarding (also already in welcome_handler)
        try:
            from telegram import Bot
            pass
        except: pass
        logging.getLogger("audit").info(
            f"✅ Hinata 🌸 ready for Mikey-kun | @{app.bot.username} | AI={config.AI_PROVIDER}/{config.AI_MODEL} | mode={config.RUN_MODE} | Mongo={config.MONGO_DB_NAME}"
        )

    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("powers", powers_cmd))
    app.add_handler(CommandHandler("allowed", allowed_cmd))
    app.add_handler(CommandHandler("dashboard", dashboard_cmd))
    app.add_handler(CommandHandler("settings", dashboard_cmd))
    app.add_handler(CommandHandler("panel", dashboard_cmd))
    app.add_handler(CommandHandler("kick", kick_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("mute", mute_cmd))
    app.add_handler(CommandHandler("unmute", unmute_cmd))
    app.add_handler(CommandHandler("warn", warn_cmd))
    app.add_handler(CommandHandler("warns", warns_cmd))
    app.add_handler(CommandHandler("resetwarns", resetwarns_cmd))
    app.add_handler(CommandHandler("warnlimit", warns_limit_cmd))
    app.add_handler(CommandHandler("promote", promote_cmd))
    app.add_handler(CommandHandler("demote", demote_cmd))
    app.add_handler(CommandHandler("pin", pin_cmd))
    app.add_handler(CommandHandler("unpin", unpin_cmd))
    app.add_handler(CommandHandler(["del","delete"], del_cmd))
    app.add_handler(CommandHandler("purge", purge_cmd))
    app.add_handler(CommandHandler("lock", lock_cmd))
    app.add_handler(CommandHandler("unlock", unlock_cmd))
    app.add_handler(CommandHandler("locks", locks_cmd))
    app.add_handler(CommandHandler("filter", filter_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("filters", filters_cmd))
    app.add_handler(CommandHandler("setrules", setrules_cmd))
    app.add_handler(CommandHandler("rules", rules_cmd))
    app.add_handler(CommandHandler("setwelcome", setwelcome_cmd))
    app.add_handler(CommandHandler("welcome", welcome_cmd))
    app.add_handler(CommandHandler("setflood", setflood_cmd))
    app.add_handler(CommandHandler("flood", flood_cmd))
    app.add_handler(CommandHandler("meme", meme_cmd))
    app.add_handler(CommandHandler("joke", joke_cmd))
    app.add_handler(CommandHandler("roll", roll_cmd))
    app.add_handler(CommandHandler(["8ball","8Ball"], eightball_cmd))
    app.add_handler(CommandHandler("id", id_cmd))
    app.add_handler(CommandHandler("whoami", whoami_cmd))
    app.add_handler(CommandHandler("health", health_cmd))
    app.add_handler(CommandHandler("ping", health_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("halat", stats_dashboard_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_message_handler), group=0)
    app.add_handler(MessageHandler(filters.CAPTION & ~filters.COMMAND, ai_message_handler), group=0)
    # Media/locks only — avoid double for TEXT/CAPTION already handled
    app.add_handler(MessageHandler((filters.Sticker.ALL | filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.POLL) & ~filters.COMMAND, ai_message_handler), group=1)

    app.add_error_handler(error_handler)

    print(f"🚀 Starting Hinata in {config.RUN_MODE} mode...")
    if config.RUN_MODE == "webhook":
        if not config.WEBHOOK_URL:
            print("❌ WEBHOOK_URL required for webhook mode")
            return
        app.run_webhook(
            listen="0.0.0.0",
            port=config.PORT,
            url_path=config.BOT_TOKEN,
            webhook_url=f"{config.WEBHOOK_URL}/{config.BOT_TOKEN}",
            secret_token=config.WEBHOOK_SECRET or None
        )
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()

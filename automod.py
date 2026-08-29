import re
import time
import asyncio
from collections import defaultdict, deque
from telegram import Update
from telegram.ext import ContextTypes
import database as db
import config

# --- Flood detection ---
flood_cache = defaultdict(lambda: deque())
# --- Raid detection ---
join_cache = defaultdict(lambda: deque())
raid_muted = {} # chat_id -> until

SPAM_PATTERNS = [
    r"(https?://\S+){3,}", # many links
    r"([a-zA-Z])\1{7,}", # repeated char
    r"(.{8,})\1{3,}", # repeated phrase
]

async def check_flood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True if should delete/mute for flood"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    limit, window, enabled = await db.get_flood(chat_id)
    if not enabled: return False
    now = time.time()
    key = (chat_id, user_id)
    dq = flood_cache[key]
    # clean old
    while dq and now - dq[0] > window:
        dq.popleft()
    dq.append(now)
    if len(dq) > limit:
        # flood detected
        try:
            await update.effective_message.delete()
        except: pass
        # mute briefly if repeated
        if len(dq) > limit + 2:
            try:
                from moderation import do_mute
                await do_mute(update, context, str(user_id), duration=f"{window*2}s", reason="flood")
            except: pass
        # warn user
        try:
            m = await context.bot.send_message(chat_id, f"⚠️ {update.effective_user.mention_html()} slow down! (flood)", parse_mode="HTML")
            await asyncio.sleep(5)
            await m.delete()
        except: pass
        return True
    return False

async def check_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    text = (update.effective_message.text or update.effective_message.caption or "").lower()
    if not text: return False
    chat_id = update.effective_chat.id
    filters = await db.get_filters(chat_id)
    for kw, reply in filters:
        # word boundary or substring?
        if kw.lower() in text:
            # Check if filter should delete or reply
            # If filter reply starts with "del:" then delete + reply
            if reply.startswith("del:"):
                try: await update.effective_message.delete()
                except: pass
                reply = reply[4:]
            if reply:
                await update.effective_message.reply_text(reply)
            return True
    # also check blocked words via locks? file name filter
    return False

async def check_locks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.effective_message
    chat_id = update.effective_chat.id
    locks = await db.get_locks(chat_id)
    if not locks: return False
    # if all locked
    if "all" in locks:
        try: await msg.delete(); return True
        except: return False
    # check each
    text = (msg.text or msg.caption or "")
    has_link = bool(re.search(r"https?://|t\.me/|telegram\.me", text))
    if "link" in locks and has_link:
        try: await msg.delete(); await warn_lock(msg, context, "links"); return True
        except: pass
    if "sticker" in locks and msg.sticker:
        try: await msg.delete(); return True
        except: pass
    if "gif" in locks and (msg.animation or (msg.document and msg.document.mime_type == "video/mp4")):
        try: await msg.delete(); return True
        except: pass
    if "photo" in locks and msg.photo:
        try: await msg.delete(); return True
        except: pass
    if "video" in locks and msg.video:
        try: await msg.delete(); return True
        except: pass
    if "audio" in locks and (msg.audio or msg.voice):
        try: await msg.delete(); return True
        except: pass
    if "document" in locks and msg.document:
        try: await msg.delete(); return True
        except: pass
    if "media" in locks and (msg.photo or msg.video or msg.audio or msg.document or msg.sticker or msg.animation):
        try: await msg.delete(); return True
        except: pass
    if "forward" in locks and msg.forward_date:
        try: await msg.delete(); return True
        except: pass
    if "poll" in locks and msg.poll:
        try: await msg.delete(); return True
        except: pass
    return False

async def warn_lock(msg, context, what):
    try:
        m = await msg.reply_text(f"🔒 {what} are locked here.")
        await asyncio.sleep(4)
        await m.delete()
    except: pass

async def check_spam(update: Update) -> bool:
    text = (update.effective_message.text or update.effective_message.caption or "")
    if not text: return False
    for pat in SPAM_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            try:
                await update.effective_message.delete()
                return True
            except: pass
    # excessive mentions / caps
    if len(re.findall(r"@\w+", text)) > 5:
        try: await update.effective_message.delete(); return True
        except: pass
    if len(text) > 500 and sum(1 for c in text if c.isupper()) / len(text) > 0.7:
        try: await update.effective_message.delete(); return True
        except: pass
    return False

# --- Anti-raid ---
async def check_raid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track joins; if mass join detected, auto lock chat"""
    if not config.ANTIRAID_ENABLED: return
    chat_id = update.effective_chat.id
    now = time.time()
    dq = join_cache[chat_id]
    while dq and now - dq[0] > config.ANTIRAID_WINDOW:
        dq.popleft()
    dq.append(now)
    if len(dq) >= config.ANTIRAID_THRESHOLD:
        # raid!
        if chat_id in raid_muted and raid_muted[chat_id] > now:
            return
        raid_muted[chat_id] = now + 300 # 5 min
        try:
            # restrict sending messages for new users? simplier: send alert and enable join approval
            await context.bot.send_message(chat_id, f"🚨 Anti-raid triggered! {len(dq)} joins in {config.ANTIRAID_WINDOW}s. Locking invites for 5 min.")
            # lock invites
            await db.set_lock(chat_id, "invite", True)
            # optionally: mute chat? not implemented for all
        except: pass
        # schedule unlock
        async def unlock_later():
            await asyncio.sleep(300)
            await db.set_lock(chat_id, "invite", False)
            try: await context.bot.send_message(chat_id, "✅ Raid lock lifted.")
            except: pass
        asyncio.create_task(unlock_later())
        join_cache[chat_id].clear()

# --- Welcome handler helpers ---
async def handle_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text, enabled = await db.get_welcome(chat_id)
    if not enabled or not text:
        return
    for member in update.message.new_chat_members:
        # anti-raid tracking
        await check_raid(update, context)
        if member.is_bot:
            continue
        mention = member.mention_html()
        chat_title = update.effective_chat.title or "this group"
        formatted = text.format(mention=mention, user=member.full_name, chat=chat_title, username=f"@{member.username}" if member.username else member.full_name)
        try:
            await update.effective_message.reply_text(formatted, parse_mode="HTML")
        except: pass

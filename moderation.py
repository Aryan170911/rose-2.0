import re
import time
import asyncio
from datetime import timedelta
from telegram import ChatPermissions, Update
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest
from telegram.ext import ContextTypes
import database as db
import config
import security

# ---------- helpers ----------
def parse_duration(s: str) -> timedelta | None:
    if not s: return None
    if s.lower() in ("forever","permanent","perm","0"): return None
    m = re.match(r"^\s*(\d+)\s*([smhdw])?\s*$", s.lower())
    if not m: 
        # try "2 hours" style
        m2 = re.search(r"(\d+)\s*(second|sec|s|minute|min|m|hour|h|day|d|week|w)", s.lower())
        if not m2: return None
        num = int(m2.group(1))
        unit = m2.group(2)[0]
        m = (num, unit)
        num, unit = m
    else:
        num = int(m.group(1)); unit = m.group(2) or "m"
    mult = {"s":1, "m":60, "h":3600, "d":86400, "w":604800}
    return timedelta(seconds=num*mult[unit])

async def is_admin(update: Update, user_id=None, context=None) -> bool:
    # Military-grade: double-check owner, handle private/anonymous, validate IDs
    try:
        from telegram.constants import ChatType
        chat = update.effective_chat
        # Private chat: only owner is admin
        if chat and chat.type == ChatType.PRIVATE:
            return (user_id or (update.effective_user.id if update.effective_user else 0)) == config.OWNER_ID
        uid = user_id or (update.effective_user.id if update.effective_user else 0)
        if not uid or not security.validate_chat_id(uid):
            return False
        if await security.is_owner_strict_async(uid):
            return True
        if not chat:
            return False
        # Handle anonymous admin: effective_user may be None, sender_chat is channel
        if not update.effective_user and update.effective_chat:
            # anonymous admin check via sender_chat
            return False
        member = await chat.get_member(uid)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except:
        return False

async def is_bot_admin(update: Update) -> bool:
    try:
        m = await update.effective_chat.get_member(update.get_bot().id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except: return False

async def can_restrict(update: Update) -> bool:
    try:
        m = await update.effective_chat.get_member(update.get_bot().id)
        return m.can_restrict_members
    except: return False

async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE, target_str: str):
    """Resolve 'reply' or @username or id to user_id"""
    chat = update.effective_chat
    msg = update.effective_message
    if target_str == "reply" or target_str == "this person" or not target_str:
        if msg.reply_to_message and msg.reply_to_message.from_user:
            return msg.reply_to_message.from_user.id, msg.reply_to_message.from_user
        return None, None
    if target_str.startswith("@"):
        username = target_str.lstrip("@")
        # try via message entities or recent
        # telegram doesn't allow resolve by username directly, try getChatMember via username fails
        # fallback: check reply or text_mention
        for ent in (msg.entities or []) + (msg.reply_to_message.entities if msg.reply_to_message else []):
            if ent.type == "text_mention" and ent.user:
                if ent.user.username and ent.user.username.lower() == username.lower():
                    return ent.user.id, ent.user
        # try via args mapping in context (for /ban @user)
        # last resort: try to find in chat? we can't fetch by username, so ask to reply
        return None, None
    try:
        uid = int(target_str)
        member = await chat.get_member(uid)
        return uid, member.user
    except: return None, None

# ---------- Hinata helpers ----------
def _is_mikey(update: Update) -> bool:
    try: return update.effective_user.id == config.OWNER_ID and config.OWNER_ID != 0
    except: return False

def _hinata_say(update: Update, base: str, mikey_suffix: str = "") -> str:
    if _is_mikey(update) and mikey_suffix:
        return base + mikey_suffix
    return base

def soft_error_text(update: Update, action: str, raw_msg: str = "") -> str:
    """Map raw Telegram error to gentle Hinata 'dizzy' message. Save raw to audit_log."""
    m = (raw_msg or "").lower()
    name = "Mikey-kun" if _is_mikey(update) else None
    prefix = "🌸 Gomen, " + (f"{name}... " if name else "")
    # map common Telegram errors to gentle
    if "not enough rights" in m or "administrator" in m or "chat_permissions" in m:
        return prefix + "my Byakugan slipped — I lack admin rights, please give me ban/restrict powers 🌸"
    if "user not found" in m:
        return prefix + "I cannot see that user, maybe they left the group 🌸"
    if "message to be replied not found" in m:
        return prefix + "that message faded, it was deleted I think 🌸"
    if "too many requests" in m or "retry after" in m:
        return prefix + "I felt dizzy, too many requests — try again in a moment 🌸"
    if "user is an admin" in m or "demote" in m and "admin" in m:
        return prefix + "they are admin, demote them first 🌸"
    if "not enough" in m:
        return prefix + "I lack that gentle seal, ask Mikey-kun for admin powers 🌸"
    # default: gentle dizzy
    return prefix + f"I felt dizzy while {action}, Mikey-kun — try again or check my admin rights 🌸" if name else f"🌸 Gomen... I felt dizzy while {action}, please try again 🌸"

def _soft_audit(action: str, update: Update, raw_msg: str, target_id: int = 0):
    try:
        import security
        chat_id = update.effective_chat.id if update.effective_chat else 0
        actor = update.effective_user.id if update.effective_user else 0
        security.audit_log(f"{action}_soft_fail", chat_id, actor, target_id, {"raw": (raw_msg or "")[:200]})
    except: pass

async def has_permission(update: Update, action: str) -> bool:
    """Military-grade: owner > telegram admin > delegated, with critical actions owner-only"""
    uid = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id
    # Critical actions: only owner (strict double-check)
    critical = {"promote","demote","grant","revoke","grant_power","revoke_power","clear_powers","allow_talk","disallow_talk","filter","rules","welcome","flood"}
    # Normalize action for check
    act_norm = action.lower()
    if act_norm in critical and not await security.is_owner_strict_async(uid):
        # allow telegram admin for some critical like filter/rules? For military, only owner for promote/grant, but allow admin for filter
        if act_norm in ("promote","demote","grant_power","revoke_power","clear_powers","allow_talk","disallow_talk"):
            return False
    if await security.is_owner_strict_async(uid):
        return True
    # Telegram admin
    if await is_admin(update, uid):
        return True
    # Hinata delegated power
    try:
        if await db.has_hinata_power(chat_id, uid, action):
            return True
    except: pass
    return False

async def require_permission(update: Update, action: str) -> bool:
    # Phase A — read-only actions are public, no permission needed
    READ_ONLY = {"info", "pull", "who", "whois", "list_powers", "list_allowed", "list_memory", "checkadmin", "list_tattoos"}
    if action in READ_ONLY:
        return True
    if await has_permission(update, action):
        return True
    # gentle refuse - Hinata style, check if Mikey?
    if _is_mikey(update):
        # Mikey always has permission, shouldn't happen
        return True
    await update.effective_message.reply_text(f"🌸 Gomen nasai... you don't have '{action}' power, Mikey-kun only gave it to chosen ones 🌸\nAsk Mikey-kun: 'Hinata give me {action} power' 🌸")
    return False

# ---------- Reverse tracking — remembers last thing she did per chat/target ----------
LAST_ACTIONS = {}  # chat_id -> {"action": str, "target": int, "by": int, "at": float, "extra": dict}
LAST_TARGET_ACTIONS = {}  # (chat_id, target) -> same

def _record_last(chat_id: int, target_id: int, action: str, by_id: int = 0, extra: dict = None):
    try:
        rec = {"action": action, "target": target_id, "by": by_id, "at": time.time(), "extra": extra or {}}
        LAST_ACTIONS[chat_id] = rec
        if target_id:
            LAST_TARGET_ACTIONS[(chat_id, target_id)] = rec
    except: pass

async def do_reverse(update: Update, context, target_str):
    """Reverse/undo last action — Mikey can say 'Hina reverse' reply or 'Hina reverse @user' """
    chat = update.effective_chat
    chat_id = chat.id
    by_id = update.effective_user.id
    # Try to resolve target if given (reply or @)
    uid, user = await resolve_target(update, context, target_str) if target_str and target_str != "reply" else (None, None)
    # If no explicit target but reply exists, use reply
    if not uid and update.effective_message.reply_to_message and update.effective_message.reply_to_message.from_user:
        uid = update.effective_message.reply_to_message.from_user.id
        user = update.effective_message.reply_to_message.from_user
    # Find last record
    rec = None
    if uid and (chat_id, uid) in LAST_TARGET_ACTIONS:
        rec = LAST_TARGET_ACTIONS[(chat_id, uid)]
    elif chat_id in LAST_ACTIONS:
        rec = LAST_ACTIONS[chat_id]
        # if we have a reply target but record target differs, prefer reply target's last?
        if uid and rec["target"] != uid:
            # check if that target has any record, else fallback to reply target generic
            rec = LAST_TARGET_ACTIONS.get((chat_id, uid)) or rec
    if not rec:
        await update.effective_message.reply_text("🌸 Nothing to reverse, Mikey-kun — I haven't done anything to undo here 🌸" if _is_mikey(update) else "🌸 Nothing to reverse here 🌸")
        return
    orig_action = rec["action"]
    target_id = rec["target"]
    # Map to reverse action
    reverse_map = {
        "ban": "unban", "kick": "unban", "mute": "unmute", "warn": "unwarn", "pin": "unpin",
        "promote": "demote", "lock": "unlock", "grant_power": "revoke", "allow_talk": "disallow",
        "del": "noop", "purge": "noop", "warn_reset": "noop", "unban": "ban", "unmute": "mute",
    }
    rev = reverse_map.get(orig_action)
    if not rev or rev == "noop":
        await update.effective_message.reply_text(f"🌸 Gomen, Mikey-kun — `{orig_action}` on {target_id} can't be reversed gently 🌸" if _is_mikey(update) else f"Can't reverse `{orig_action}` 🌸")
        return
    # Need permission to reverse?
    if not await require_permission(update, rev if rev not in ("revoke","disallow") else ("ban" if rev=="revoke" else "allow_talk")):
        return
    # Execute reverse
    try:
        if rev == "unban":
            await chat.unban_member(target_id)
            await update.effective_message.reply_text(f"🌸 Undone — unbanned {user.mention_html() if user else target_id} for you, Mikey-kun 🌸" if _is_mikey(update) else f"🌸 Unbanned {target_id} 🌸", parse_mode="HTML")
        elif rev == "unmute":
            from telegram import ChatPermissions
            perms = ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            )
            await chat.restrict_member(target_id, permissions=perms)
            await update.effective_message.reply_text(f"🌸 Undone — unmuted {target_id} 🌸")
        elif rev == "unwarn":
            await db.reset_warns(chat_id, target_id)
            await update.effective_message.reply_text(f"🌸 Undone — cleared warns for {target_id} 🌸")
        elif rev == "unpin":
            try: await context.bot.unpin_chat_message(chat_id, None)
            except: pass
            await update.effective_message.reply_text("🌸 Undone — unpinned 🌸")
        elif rev == "demote":
            await context.bot.promote_chat_member(chat_id, target_id, can_delete_messages=False, can_restrict_members=False, can_pin_messages=False, can_promote_members=False, is_anonymous=False)
            await update.effective_message.reply_text(f"🌸 Undone — demoted {target_id} 🌸")
        elif rev == "unlock":
            extra = rec.get("extra", {})
            lock_type = extra.get("lock_type", "media")
            await db.set_lock(chat_id, lock_type, False)
            await update.effective_message.reply_text(f"🌸 Undone — unlocked {lock_type} 🌸")
        elif rev == "revoke":
            # revoke last granted powers
            await db.clear_hinata_powers(chat_id, target_id)
            await update.effective_message.reply_text(f"🌸 Undone — revoked powers from {target_id} 🌸")
        elif rev == "disallow":
            await db.disallow_hinata_talk(chat_id, target_id)
            await update.effective_message.reply_text(f"🌸 Undone — disallowed talk for {target_id} 🌸")
        elif rev == "ban":
            await chat.ban_member(target_id)
            await update.effective_message.reply_text(f"🌸 Undone — banned {target_id} again 🌸")
        elif rev == "mute":
            from telegram import ChatPermissions
            await chat.restrict_member(target_id, permissions=ChatPermissions(can_send_messages=False))
            await update.effective_message.reply_text(f"🌸 Undone — muted {target_id} again 🌸")
        # clear record after reverse
        LAST_ACTIONS.pop(chat_id, None)
        LAST_TARGET_ACTIONS.pop((chat_id, target_id), None)
    except Exception as e:
        await update.effective_message.reply_text(f"🌸 Gomen Mikey-kun, reverse failed: {e} 🌸" if _is_mikey(update) else f"Reverse failed: {e} 🌸")

# ---------- actions ----------
async def do_kick(update: Update, context, target_str, reason=""):
    if not await can_restrict(update):
        await update.effective_message.reply_text("🌸 Gomen nasai, Mikey-kun... I need admin rights with ban permission to protect you 🌸" if _is_mikey(update) else "❌ I need admin rights with ban permission.")
        return
    if not await require_permission(update, "kick"):
        return
    if security.is_rate_limited(update.effective_user.id, "kick"):
        await update.effective_message.reply_text("🌸 Slow down — too many kicks 🌸"); return
    reason = security.validate_reason(reason)
    if not security.validate_target(target_str):
        await update.effective_message.reply_text("🌸 Invalid target 🌸"); return
    security.audit_log("kick", update.effective_chat.id, update.effective_user.id, 0, {"target_str": target_str})
    uid, user = await resolve_target(update, context, target_str)
    if not uid:
        await update.effective_message.reply_text("🌸 Ano... please reply to their message, Mikey-kun 🌸" if _is_mikey(update) else "❓ Reply to the user's message or mention them properly. Username lookup needs reply.")
        return
    if await is_admin(update, uid):
        await update.effective_message.reply_text("🌸 Gomen... I cannot kick an admin, Mikey-kun 🌸" if _is_mikey(update) else "⚠️ Can't kick an admin.")
        return
    try:
        await update.effective_chat.ban_member(uid)
        await update.effective_chat.unban_member(uid) # kick = ban then unban
        name = user.mention_html() if user else str(uid)
        if _is_mikey(update):
            await update.effective_message.reply_text(f"🌸 Hai, Mikey-kun... gently removed {name} for you 🌸 {f'— {reason}' if reason else ''}", parse_mode="HTML")
        else:
            await update.effective_message.reply_text(f"🌸 Gently removed {name} 🌸 {f'— {reason}' if reason else ''}", parse_mode="HTML")
        _record_last(update.effective_chat.id, uid, "kick", by_id=update.effective_user.id)
    except BadRequest as e:
        _soft_audit("kick", update, e.message, uid)
        await update.effective_message.reply_text(soft_error_text(update, "removing them", e.message))

async def do_ban(update, context, target_str, reason="", duration=None):
    if not await can_restrict(update):
        await update.effective_message.reply_text("🌸 Gomen nasai, Mikey-kun... I need ban permission 🌸" if _is_mikey(update) else "❌ Need ban permission.")
        return
    if not await require_permission(update, "ban"):
        return
    if security.is_rate_limited(update.effective_user.id, "ban"):
        await update.effective_message.reply_text("🌸 Slow down — too many bans, wait a moment 🌸"); return
    reason = security.validate_reason(reason)
    if not security.validate_target(target_str):
        await update.effective_message.reply_text("🌸 Invalid target 🌸"); return
    security.audit_log("ban", update.effective_chat.id, update.effective_user.id, 0, {"target_str": target_str, "reason": reason})
    uid, user = await resolve_target(update, context, target_str)
    if not uid:
        await update.effective_message.reply_text("🌸 Please reply to them for me, Mikey-kun 🌸" if _is_mikey(update) else "❓ Reply to user to ban or use ID. (@username needs reply due to Telegram limits)")
        return
    if await is_admin(update, uid):
        await update.effective_message.reply_text("🌸 I cannot ban an admin, Mikey-kun... 🌸" if _is_mikey(update) else "⚠️ Can't ban admin.")
        return
    until = None
    if duration:
        td = parse_duration(duration)
        if td: until = int(time.time() + td.total_seconds())
    try:
        await update.effective_chat.ban_member(uid, until_date=until)
        dur_txt = f" for {duration}" if duration else " permanently"
        name = user.mention_html() if user else str(uid)
        if _is_mikey(update):
            await update.effective_message.reply_text(f"🌸 Protected you, Mikey-kun... {name} banned{dur_txt} 🌸 {f'— {reason}' if reason else ''}", parse_mode="HTML")
        else:
            await update.effective_message.reply_text(f"🌸 Banned {name}{dur_txt} 🌸 {f'— {reason}' if reason else ''}", parse_mode="HTML")
        _record_last(update.effective_chat.id, uid, "ban", by_id=update.effective_user.id, extra={"reason":reason, "duration":duration})
    except BadRequest as e:
        _soft_audit("ban", update, e.message, uid)
        await update.effective_message.reply_text(soft_error_text(update, "banning", e.message))

async def do_unban(update, context, target_str):
    uid, user = await resolve_target(update, context, target_str)
    if not uid:
        m = re.search(r"(\d{5,})", target_str or "")
        if m: uid = int(m.group(1))
        else:
            await update.effective_message.reply_text("🌸 Who should I forgive, Mikey-kun? Reply or give ID 🌸" if _is_mikey(update) else "Reply to user or give user ID to unban.")
            return
    try:
        await update.effective_chat.unban_member(uid)
        await update.effective_message.reply_text(f"🌸 Hai Mikey-kun, forgiven {uid}... 🌸" if _is_mikey(update) else f"✅ Unbanned {uid} 🌸")
    except BadRequest as e:
        _soft_audit("unban", update, e.message, uid)
        await update.effective_message.reply_text(soft_error_text(update, "unbanning", e.message))

async def do_mute(update, context, target_str, duration="1h", reason=""):
    if not await can_restrict(update):
        await update.effective_message.reply_text("🌸 Need restrict permission, Mikey-kun... 🌸" if _is_mikey(update) else "❌ Need restrict permission.")
        return
    if not await require_permission(update, "mute"):
        return
    if security.is_rate_limited(update.effective_user.id, "mute"):
        await update.effective_message.reply_text("🌸 Slow down — too many mutes 🌸"); return
    reason = security.validate_reason(reason)
    if not security.validate_target(target_str):
        await update.effective_message.reply_text("🌸 Invalid target 🌸"); return
    security.audit_log("mute", update.effective_chat.id, update.effective_user.id, 0, {"target_str": target_str, "duration": duration})
    uid, user = await resolve_target(update, context, target_str)
    if not uid:
        await update.effective_message.reply_text("🌸 Please reply to them, Mikey-kun 🌸" if _is_mikey(update) else "❓ Reply to user to mute.")
        return
    if await is_admin(update, uid):
        await update.effective_message.reply_text("🌸 Cannot mute an admin, Mikey-kun 🌸" if _is_mikey(update) else "⚠️ Can't mute admin.")
        return
    td = parse_duration(duration) if duration else timedelta(hours=1)
    until = int(time.time() + td.total_seconds()) if td else None
    perms = ChatPermissions(can_send_messages=False)
    try:
        await update.effective_chat.restrict_member(uid, permissions=perms, until_date=until)
        name = user.mention_html() if user else str(uid)
        if _is_mikey(update):
            await update.effective_message.reply_text(f"🌸 Hai Mikey-kun, gently silenced {name} for {duration or '1h'} 🌸 {f'— {reason}' if reason else ''}", parse_mode="HTML")
        else:
            await update.effective_message.reply_text(f"🌸 Gently silenced {name} for {duration or '1h'} 🌸 {f'— {reason}' if reason else ''}", parse_mode="HTML")
        _record_last(update.effective_chat.id, uid, "mute", by_id=update.effective_user.id, extra={"duration":duration})
    except BadRequest as e:
        _soft_audit("mute", update, e.message, uid)
        await update.effective_message.reply_text(soft_error_text(update, "muting", e.message))

async def do_unmute(update, context, target_str):
    if not await require_permission(update, "unmute"):
        return
    uid, user = await resolve_target(update, context, target_str)
    if not uid:
        await update.effective_message.reply_text("🌸 Reply to them, Mikey-kun? 🌸" if _is_mikey(update) else "Reply to user to unmute.")
        return
    perms = ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )
    try:
        await update.effective_chat.restrict_member(uid, permissions=perms)
        name = user.mention_html() if user else str(uid)
        await update.effective_message.reply_text(f"🌸 Hai Mikey-kun, {name} may speak again 🌸" if _is_mikey(update) else f"🌸 {name} may speak again 🌸", parse_mode="HTML")
    except BadRequest as e:
        _soft_audit("unmute", update, e.message, uid)
        await update.effective_message.reply_text(soft_error_text(update, "unmuting", e.message))

async def do_warn(update, context, target_str, reason=""):
    if not await require_permission(update, "warn"):
        return
    if security.is_rate_limited(update.effective_user.id, "warn"):
        await update.effective_message.reply_text("🌸 Slow down — too many warns 🌸"); return
    reason = security.validate_reason(reason)
    if not security.validate_target(target_str):
        await update.effective_message.reply_text("🌸 Invalid target 🌸"); return
    security.audit_log("warn", update.effective_chat.id, update.effective_user.id, 0, {"target_str": target_str})
    uid, user = await resolve_target(update, context, target_str)
    if not uid:
        await update.effective_message.reply_text("🌸 Who should I warn, Mikey-kun? 🌸" if _is_mikey(update) else "❓ Reply to user to warn.")
        return
    if await is_admin(update, uid):
        await update.effective_message.reply_text("🌸 Cannot warn admin, Mikey-kun 🌸" if _is_mikey(update) else "⚠️ Can't warn admin.")
        return
    chat_id = update.effective_chat.id
    count = await db.add_warn(chat_id, uid, reason)
    limit = await db.get_warn_limit(chat_id)
    name = user.mention_html() if user else str(uid)
    if _is_mikey(update):
        await update.effective_message.reply_text(f"🌸 Soft warning for {name} ({count}/{limit}), Mikey-kun 🌸 {f'— {reason}' if reason else ''}", parse_mode="HTML")
    else:
        await update.effective_message.reply_text(f"🌸 Soft warning for {name} ({count}/{limit}) 🌸 {f'— {reason}' if reason else ''}", parse_mode="HTML")
    _record_last(chat_id, uid, "warn", by_id=update.effective_user.id)
    if count >= limit:
        try:
            await update.effective_chat.ban_member(uid)
            await update.effective_message.reply_text(f"🌸 Gomen... {name} reached {limit} warnings, protected you Mikey-kun 🌸" if _is_mikey(update) else f"🚫 {name} banned after {limit} warns 🌸", parse_mode="HTML")
            await db.reset_warns(chat_id, uid)
            _record_last(chat_id, uid, "ban", by_id=update.effective_user.id)
        except: pass

async def do_warn_reset(update, context, target_str):
    if not await require_permission(update, "warn"):
        return
    uid, user = await resolve_target(update, context, target_str)
    if not uid:
        await update.effective_message.reply_text("🌸 Who to forgive, Mikey-kun? 🌸" if _is_mikey(update) else "Reply to user to reset warns.")
        return
    await db.reset_warns(update.effective_chat.id, uid)
    name = user.mention_html() if user else str(uid)
    await update.effective_message.reply_text(f"🌸 Hai Mikey-kun, warnings cleared for {name} 🌸" if _is_mikey(update) else f"✅ Warns reset for {name} 🌸", parse_mode="HTML")

async def do_promote(update, context, target_str):
    if not await require_permission(update, "promote"):
        return
    uid, user = await resolve_target(update, context, target_str)
    if not uid:
        await update.effective_message.reply_text("🌸 Who to honor, Mikey-kun? 🌸" if _is_mikey(update) else "Reply to user to promote.")
        return
    try:
        await update.get_bot().promote_chat_member(update.effective_chat.id, uid,
            can_delete_messages=True, can_restrict_members=True, can_pin_messages=True, can_promote_members=False)
        name = user.mention_html() if user else str(uid)
        await update.effective_message.reply_text(f"🌸 Hai Mikey-kun, honored {name} 🌸" if _is_mikey(update) else f"🌸 Honored {name} 🌸", parse_mode="HTML")
        _record_last(update.effective_chat.id, uid, "promote", by_id=update.effective_user.id)
    except BadRequest as e:
        _soft_audit("promote", update, e.message, uid)
        await update.effective_message.reply_text(soft_error_text(update, "promoting", e.message))

async def do_demote(update, context, target_str):
    if not await require_permission(update, "demote"):
        return
    uid, user = await resolve_target(update, context, target_str)
    if not uid:
        await update.effective_message.reply_text("🌸 Who, Mikey-kun? 🌸" if _is_mikey(update) else "Reply to user to demote.")
        return
    try:
        await update.get_bot().promote_chat_member(update.effective_chat.id, uid,
            can_delete_messages=False, can_restrict_members=False, can_pin_messages=False, can_promote_members=False, is_anonymous=False)
        name = user.mention_html() if user else str(uid)
        await update.effective_message.reply_text(f"🌸 As you wish, Mikey-kun... {name} stepped down 🌸" if _is_mikey(update) else f"🌸 {name} stepped down 🌸", parse_mode="HTML")
    except BadRequest as e:
        _soft_audit("demote", update, e.message, uid)
        await update.effective_message.reply_text(soft_error_text(update, "demoting", e.message))

async def do_pin(update, context, silent=False):
    if not await require_permission(update, "pin"):
        return
    msg = update.effective_message.reply_to_message
    if not msg:
        await update.effective_message.reply_text("🌸 Please reply to a message to pin, Mikey-kun 🌸" if _is_mikey(update) else "Reply to a message to pin it.")
        return
    try:
        await update.get_bot().pin_chat_message(update.effective_chat.id, msg.message_id, disable_notification=silent)
        await update.effective_message.reply_text("🌸 Pinned gently for you, Mikey-kun 🌸" if _is_mikey(update) else "📌 Pinned 🌸")
        _record_last(update.effective_chat.id, msg.from_user.id if msg.from_user else 0, "pin", by_id=update.effective_user.id)
    except BadRequest as e:
        _soft_audit("pin", update, e.message, msg.from_user.id if msg.from_user else 0)
        await update.effective_message.reply_text(soft_error_text(update, "pinning", e.message))

async def do_unpin(update, context):
    if not await require_permission(update, "pin"):
        return
    try:
        await update.get_bot().unpin_chat_message(update.effective_chat.id, update.effective_message.reply_to_message.message_id if update.effective_message.reply_to_message else None)
        await update.effective_message.reply_text("🌸 Unpinned, Mikey-kun 🌸" if _is_mikey(update) else "📌 Unpinned 🌸")
    except BadRequest as e:
        _soft_audit("unpin", update, e.message)
        await update.effective_message.reply_text(soft_error_text(update, "unpinning", e.message))

async def do_del(update, context):
    if not await require_permission(update, "del"):
        return
    if update.effective_message.reply_to_message:
        try:
            await update.effective_message.reply_to_message.delete()
            await update.effective_message.delete()
        except BadRequest as e:
            _soft_audit("del", update, e.message, update.effective_message.reply_to_message.from_user.id if update.effective_message.reply_to_message.from_user else 0)
            await update.effective_message.reply_text(soft_error_text(update, "deleting", e.message))
    else:
        try: await update.effective_message.delete()
        except BadRequest as e:
            _soft_audit("del", update, e.message)
            await update.effective_message.reply_text(soft_error_text(update, "deleting", e.message))

async def do_purge(update, context):
    if not await require_permission(update, "purge"):
        return
    if not update.effective_message.reply_to_message:
        await update.effective_message.reply_text("🌸 Reply where to start cleaning, Mikey-kun 🌸" if _is_mikey(update) else "Reply to start message to purge.")
        return
    start = update.effective_message.reply_to_message.message_id
    end = update.effective_message.message_id
    deleted = 0
    for mid in range(start, end+1):
        try:
            await update.get_bot().delete_message(update.effective_chat.id, mid)
            deleted += 1
            await asyncio.sleep(0.05)
        except: pass
    m = await update.effective_message.reply_text(f"🌸 Cleaned {deleted} messages for you, Mikey-kun 🌸" if _is_mikey(update) else f"🧹 Purged {deleted} messages 🌸")
    await asyncio.sleep(3)
    try: await m.delete()
    except: pass

async def do_lock(update, context, lock_type):
    if not await require_permission(update, "lock"):
        return
    ok = await db.set_lock(update.effective_chat.id, lock_type, True)
    if ok:
        await update.effective_message.reply_text(f"🌸 Byakugan seal... locked {lock_type} for you, Mikey-kun 🌸" if _is_mikey(update) else f"🔒 Locked {lock_type} 🌸")
        _record_last(update.effective_chat.id, update.effective_user.id, "lock", by_id=update.effective_user.id, extra={"lock_type": lock_type})
    else: await update.effective_message.reply_text(f"Unknown seal. Valid: {', '.join(db.VALID_LOCKS)}" + (" 🌸" if _is_mikey(update) else ""))

async def do_unlock(update, context, lock_type):
    if not await require_permission(update, "lock"):
        return
    ok = await db.set_lock(update.effective_chat.id, lock_type, False)
    if ok:
        await update.effective_message.reply_text(f"🌸 Seal released... unlocked {lock_type}, Mikey-kun 🌸" if _is_mikey(update) else f"🔓 Unlocked {lock_type} 🌸")
        _record_last(update.effective_chat.id, update.effective_user.id, "unlock", by_id=update.effective_user.id, extra={"lock_type": lock_type})
    else: await update.effective_message.reply_text(f"Unknown seal. Valid: {', '.join(db.VALID_LOCKS)}" + (" 🌸" if _is_mikey(update) else ""))

# map action -> func
ACTION_MAP = {
    "kick": lambda u,c,d: do_kick(u,c,d.get("target","reply"), d.get("reason","")),
    "ban": lambda u,c,d: do_ban(u,c,d.get("target","reply"), d.get("reason",""), d.get("duration")),
    "unban": lambda u,c,d: do_unban(u,c,d.get("target","")),
    "mute": lambda u,c,d: do_mute(u,c,d.get("target","reply"), d.get("duration","1h"), d.get("reason","")),
    "unmute": lambda u,c,d: do_unmute(u,c,d.get("target","reply")),
    "warn": lambda u,c,d: do_warn(u,c,d.get("target","reply"), d.get("reason","")),
    "unwarn": lambda u,c,d: do_warn_reset(u,c,d.get("target","reply")),
    "warn_reset": lambda u,c,d: do_warn_reset(u,c,d.get("target","reply")),
    "promote": lambda u,c,d: do_promote(u,c,d.get("target","reply")),
    "demote": lambda u,c,d: do_demote(u,c,d.get("target","reply")),
    "pin": lambda u,c,d: do_pin(u,c),
    "unpin": lambda u,c,d: do_unpin(u,c),
    "del": lambda u,c,d: do_del(u,c),
    "purge": lambda u,c,d: do_purge(u,c),
    "lock": lambda u,c,d: do_lock(u,c,d.get("type","media")),
    "unlock": lambda u,c,d: do_unlock(u,c,d.get("type","media")),
}

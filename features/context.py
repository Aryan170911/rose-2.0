"""Context - pull user details for anyone messaging Hinata, plus tag/reply resolution"""
from telegram import Update

def get_user_details(update: Update) -> dict:
    user = update.effective_user
    chat = update.effective_chat
    # Phase C — handle anonymous channel sender
    if not user:
        sender_chat = getattr(update.effective_message, "sender_chat", None) if update.effective_message else None
        if sender_chat:
            user = type("ChUser", (), {
                "id": sender_chat.id,
                "first_name": sender_chat.title or "Channel",
                "last_name": "",
                "full_name": sender_chat.title or "Channel",
                "username": sender_chat.username or "",
                "is_bot": True,
                "mention_html": lambda self=None: sender_chat.title or "Channel",
            })()
        else:
            return {"id": 0, "name": "Unknown", "username": "", "mention": "", "is_mikey": False}
    # Use is_mikey_user from main to avoid circular import - import here
    try:
        from main import is_mikey_user
        is_m = is_mikey_user(user.id)
    except:
        is_m = False
    return {
        "id": user.id,
        "first_name": getattr(user, "first_name", "") or "",
        "last_name": getattr(user, "last_name", "") or "",
        "full_name": getattr(user, "full_name", None) or getattr(user, "first_name", "") or "",
        "username": getattr(user, "username", "") or "",
        "mention": getattr(user, "mention_html", lambda: str(user.id))() if hasattr(user, "mention_html") else str(user.id),
        "is_mikey": is_m,
        "chat_id": chat.id if chat else 0,
        "chat_title": getattr(chat, "title", "") or "",
    }

def get_reply_context(update: Update) -> dict:
    msg = update.effective_message
    if not msg or not msg.reply_to_message or not msg.reply_to_message.from_user:
        return {}
    ruser = msg.reply_to_message.from_user
    rmsg = msg.reply_to_message
    text = getattr(rmsg, "text", "") or getattr(rmsg, "caption", "") or ""
    return {
        "user_id": ruser.id,
        "username": getattr(ruser, "username", "") or "",
        "full_name": getattr(ruser, "full_name", None) or getattr(ruser, "first_name", "") or "",
        "mention": ruser.mention_html() if hasattr(ruser, "mention_html") else str(ruser.id),
        "text": text[:500],
    }

def get_tag_context(update: Update) -> dict:
    """Phase B — extract tagged user from entities (text_mention / mention).
    Returns the first user that is *not* the sender and not the bot, with their text if findable."""
    msg = update.effective_message
    if not msg:
        return {}
    sender_id = msg.from_user.id if msg.from_user else 0
    bot_id = None
    try:
        from main import context as _ctx_unused  # avoid circular
    except: pass
    # Iterate entities
    target = {}
    for ent in (msg.entities or []):
        try:
            if ent.type == "text_mention" and ent.user:
                if ent.user.id == sender_id or ent.user.is_bot:
                    continue
                text = (msg.text or "")[ent.offset:ent.offset+ent.length]
                target = {
                    "user_id": ent.user.id,
                    "username": getattr(ent.user, "username", "") or "",
                    "full_name": getattr(ent.user, "full_name", None) or getattr(ent.user, "first_name", "") or "",
                    "mention": ent.user.mention_html() if hasattr(ent.user, "mention_html") else str(ent.user.id),
                    "text": text,
                    "via": "text_mention",
                }
                break
            elif ent.type == "mention":
                # @username — cannot resolve without getChatMember
                uname = (msg.text or "")[ent.offset:ent.offset+ent.length].lstrip("@")
                target = {
                    "username": uname,
                    "full_name": uname,
                    "text": uname,
                    "via": "mention",
                }
                break
        except: pass
    return target

def resolve_tagged_user_id(update: Update, target: dict) -> int:
    """Try to resolve @username / text_mention to a numeric user_id via get_chat_member."""
    if not target:
        return 0
    if target.get("user_id"):
        return int(target["user_id"])
    uname = target.get("username")
    chat = update.effective_chat
    if uname and chat:
        try:
            import asyncio
            # synchronous fallback — try get_chat_member
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # cannot await from sync — return 0 (caller should resolve async)
                    return 0
            except:
                pass
            member = chat.get_member(uname)
            return member.user.id
        except:
            return 0
    return 0

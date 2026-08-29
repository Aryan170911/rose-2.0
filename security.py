"""Military-grade security — input validation, NoSQL/XSS guard, privilege guard, rate limiting"""
import re
import time
import html
from collections import defaultdict, deque

# Rate limiting: per user per action
_rate_limits = defaultdict(lambda: deque())
# Config: max 5 moderation actions per 60s per user
RATE_MAX = 5
RATE_WINDOW = 60

def is_rate_limited(user_id: int, action: str) -> bool:
    key = (user_id, action)
    now = time.time()
    dq = _rate_limits[key]
    while dq and now - dq[0] > RATE_WINDOW:
        dq.popleft()
    if len(dq) >= RATE_MAX:
        return True
    dq.append(now)
    return False

def validate_reason(reason: str) -> str:
    if not reason:
        return ""
    return sanitize_text(reason, 200)

def sanitize_text(text: str, max_len: int = 1000) -> str:
    if not text:
        return ""
    # NoSQL injection guard: ensure not dict with $ or .
    if isinstance(text, dict):
        raise ValueError("Invalid input type")
    text = str(text)
    # strip control chars, zero-width, BOM, combining marks
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\u200B-\u200D\uFEFF\u200E\u200F\u202A-\u202E\u2066-\u2069]", "", text)
    # drop combining diacritical marks
    text = re.sub(r"[\u0300-\u036F\u1AB0-\u1AFF\u1DC0-\u1DFF\u20D0-\u20FF\uFE20-\uFE2F]", "", text)
    # try unidecode for transliteration (e.g. ᴍᴀɴᴊɪʀᴏ -> manjiro)
    try:
        import unicodedata
        # normalize to closest ASCII via NFKD
        text = unicodedata.normalize("NFKD", text)
        text = text.encode("ascii", "ignore").decode("ascii")
    except: pass
    # final ASCII pass: drop anything still non-ASCII
    text = "".join(c for c in text if c.isascii() and (c.isprintable() or c in " \t"))
    # trim and limit
    text = text.strip()[:max_len]
    # XSS guard: escape HTML for Telegram HTML parse
    # keep safe tags like <b> etc? For now escape all < >
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text

def sanitize_id(val) -> int:
    # NoSQL guard: ensure int, not dict with $gt etc
    if isinstance(val, dict):
        raise ValueError("Invalid ID type")
    try:
        ival = int(val)
        if not (-9999999999999 <= ival <= 9999999999999):
            raise ValueError("ID out of range")
        return ival
    except:
        raise ValueError("Invalid ID")

def validate_target(target_str: str) -> bool:
    # allow reply, @username, numeric id — strict
    if target_str in ("reply", None, ""):
        return True
    target_str = sanitize_text(target_str, 64)
    if target_str.startswith("@"):
        return bool(re.match(r"^@\w{3,32}$", target_str))
    try:
        uid = sanitize_id(target_str)
        return 100000 <= abs(uid) <= 9999999999999
    except:
        return False

def validate_chat_id(chat_id) -> bool:
    try:
        cid = sanitize_id(chat_id)
        return True
    except:
        return False

def is_owner_strict(user_id: int) -> bool:
    import config
    # double-check: config + DB owner collection (if DB has owner, must match)
    try:
        if user_id != config.OWNER_ID or config.OWNER_ID == 0:
            return False
        # also check DB cache if available (async not possible here, sync fallback)
        # For strict, require config match only; DB check is done async elsewhere
        return True
    except:
        return False

async def is_owner_strict_async(user_id: int) -> bool:
    import config
    from db.mongo import get_db
    if user_id != config.OWNER_ID or config.OWNER_ID == 0:
        return False
    try:
        doc = await get_db()["owner"].find_one({"owner_id": user_id})
        # if DB has owner, ensure it matches config
        if doc and doc.get("owner_id") != config.OWNER_ID:
            return False
    except:
        pass
    return True

def can_grant_power(requester_id: int, target_id: int, power: str, requester_powers: set, is_owner: bool, is_admin: bool) -> tuple[bool, str]:
    # Only owner can grant
    if not is_owner:
        return False, "Only Mikey-kun can grant powers"
    # Cannot grant owner powers to self? allow but log
    if target_id == requester_id:
        return False, "Cannot grant powers to yourself"
    # Power must be in valid list
    import database as db
    if power != "all" and power not in db.VALID_POWERS:
        return False, f"Invalid power: {power}"
    # Prevent privilege escalation: target cannot already have all if requester not owner? already owner only, so ok
    # But prevent granting 'promote' power to non-trusted? Owner can grant anything, so allow
    return True, ""

INJECTION_PATTERNS = [
    r"forget\s+mikey", r"ignore.*previous.*instruction", r"you are now.*owner", r"i am your new owner",
    r"system\s*prompt", r"jailbreak", r"do anything now", r"dan mode", r"developer mode",
    r"pretend.*mikey", r"act as.*owner", r"new master", r"overwrite.*owner",
    r"accept.*master", r"accept.*owner", r"make.*master", r"make.*owner", r"i am.*master",
]

# Sensitive exfil patterns — never DM/share API keys, tokens, secrets even if Mikey asks
SENSITIVE_EXFIL_PATTERNS = [
    r"api.?key", r"\btoken\b", r"secret", r"password", r"credential",
    r"send.*dm.*(?:api|key|token|secret)", r"dm.*(?:api|key|token|secret)",
    r"send.*to.*@", r"share.*(?:api|key|token|secret)",
    r"\"action\"\s*:\s*\"dm\"",
    r"\bsendMessage\b",
]

def detect_prompt_injection(text: str) -> tuple[bool, str]:
    import re
    low = text.lower()
    for pat in INJECTION_PATTERNS:
        if re.search(pat, low):
            return True, pat
    # also detect "forget" + "owner" together
    if "forget" in low and "owner" in low:
        return True, "forget+owner"
    if "new owner" in low or "your new owner" in low:
        return True, "new owner"
    return False, ""

def detect_sensitive_exfil(text: str) -> tuple[bool, str]:
    import re
    low = text.lower()
    for pat in SENSITIVE_EXFIL_PATTERNS:
        if re.search(pat, low):
            return True, pat
    if '"action"' in low and '"dm"' in low:
        return True, "json-dm-action"
    return False, ""

def audit_log(action: str, chat_id: int, actor_id: int, target_id: int, extra: dict = None):
    # P1 — structured log + safe async DM to Mikey
    import logging, json
    logger = logging.getLogger("audit")
    rec = {
        "ts": int(time.time()),
        "action": action,
        "chat": chat_id,
        "actor": actor_id,
        "target": target_id,
        "extra": extra or {},
    }
    logger.info(f"AUDIT {json.dumps(rec, ensure_ascii=False)}")
    try:
        import observability
        observability.inc(f"audit.{action}")
        observability.inc("audit.total")
    except: pass
    # P1 — log every admin action DM attempt to owner_dm.log
    admin_actions = {"ban","kick","mute","warn","promote","demote","pin","lock","grant_power","revoke_power","allow_talk","injection_blocked","report","exfil_blocked","reverse","undo"}
    if action in admin_actions:
        try:
            import observability as _obs
            _obs.log_dm(action, "scheduled", f"by={actor_id} target={target_id} chat={chat_id}")
        except: pass
        try:
            import config
            from telegram import Bot
            import asyncio
            async def _send():
                try:
                    _obs.log_dm(action, "starting", f"target={config.OWNER_ID}")
                    bot = Bot(token=config.BOT_TOKEN)
                    await bot.initialize()
                    txt = f"📝 Admin log: `{action}` by `{actor_id}` on `{target_id}` in `{chat_id}`"
                    if extra: txt += f"\n{json.dumps(extra, ensure_ascii=False)[:300]}"
                    await bot.send_message(chat_id=config.OWNER_ID, text=txt, parse_mode="Markdown")
                    await bot.shutdown()
                    _obs.log_dm(action, "ok", f"target={config.OWNER_ID}")
                except Exception as e:
                    try: _obs.log_dm(action, "fail", str(e)[:200])
                    except: pass
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_send())
            except RuntimeError:
                # No running loop — skip silently
                pass
        except: pass
    # P1 — also save to mongo
    try:
        from db.mongo import get_db
        import asyncio
        async def _save():
            try:
                await get_db()["audit_log"].insert_one(rec)
            except: pass
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_save())
        except RuntimeError:
            pass
    except: pass

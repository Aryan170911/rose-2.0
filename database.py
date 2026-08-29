"""Hinata Database — MongoDB (Motor) — modular via db/mongo.py
Keeps same async API as before so main.py / moderation.py need no changes.
Collections live in MongoDB `hinata_mikey_db` (config.MONGO_DB_NAME).
"""
import time
from typing import Tuple, List, Optional, Set
import config
from db.mongo import get_collection, init_mongo

# Re-export for external callers that used DB_PATH
DB_PATH = config.DATABASE_PATH  # kept for legacy, not used

async def init_db():
    await init_mongo()
    # Ensure default flood/warn limits work without docs (handled in getters)
    print("[db] MongoDB ready")

# ---------- WARNS ----------
async def get_warns(chat_id: int, user_id: int) -> Tuple[int, str]:
    col = get_collection("warns")
    doc = await col.find_one({"chat_id": chat_id, "user_id": user_id})
    if doc:
        return int(doc.get("count", 0)), doc.get("reasons", "")
    return 0, ""

async def add_warn(chat_id: int, user_id: int, reason: str = "") -> int:
    count, reasons = await get_warns(chat_id, user_id)
    count += 1
    new_reasons = (reasons + f"\n- {reason}" if reasons else reason) if reason else reasons
    col = get_collection("warns")
    await col.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {"count": count, "reasons": new_reasons}},
        upsert=True,
    )
    return count

async def reset_warns(chat_id: int, user_id: int):
    col = get_collection("warns")
    await col.delete_one({"chat_id": chat_id, "user_id": user_id})

async def set_warn_limit(chat_id: int, limit: int):
    col = get_collection("chats")
    await col.update_one({"chat_id": chat_id}, {"$set": {"warn_limit": limit}}, upsert=True)

async def get_warn_limit(chat_id: int) -> int:
    col = get_collection("chats")
    doc = await col.find_one({"chat_id": chat_id})
    if doc and "warn_limit" in doc:
        return int(doc["warn_limit"])
    return config.WARN_LIMIT

# ---------- FILTERS ----------
async def add_filter(chat_id: int, keyword: str, reply: str):
    col = get_collection("filters")
    await col.update_one(
        {"chat_id": chat_id, "keyword": keyword.lower()},
        {"$set": {"reply": reply}},
        upsert=True,
    )

async def remove_filter(chat_id: int, keyword: str) -> bool:
    col = get_collection("filters")
    res = await col.delete_one({"chat_id": chat_id, "keyword": keyword.lower()})
    return res.deleted_count > 0

async def get_filters(chat_id: int) -> List[Tuple[str, str]]:
    col = get_collection("filters")
    cur = col.find({"chat_id": chat_id})
    rows = []
    async for doc in cur:
        rows.append((doc["keyword"], doc["reply"]))
    return rows

async def list_filter_keywords(chat_id: int) -> List[str]:
    rows = await get_filters(chat_id)
    return [r[0] for r in rows]

# ---------- RULES ----------
async def set_rules(chat_id: int, text: str):
    col = get_collection("rules")
    await col.update_one({"chat_id": chat_id}, {"$set": {"rules": text}}, upsert=True)

async def get_rules(chat_id: int) -> Optional[str]:
    col = get_collection("rules")
    doc = await col.find_one({"chat_id": chat_id})
    return doc["rules"] if doc else None

# ---------- WELCOME ----------
async def set_welcome(chat_id: int, text: str, enabled: int = 1):
    col = get_collection("welcome")
    await col.update_one({"chat_id": chat_id}, {"$set": {"text": text, "enabled": int(enabled)}}, upsert=True)

async def get_welcome(chat_id: int) -> Tuple[Optional[str], int]:
    col = get_collection("welcome")
    doc = await col.find_one({"chat_id": chat_id})
    if doc:
        return doc.get("text"), int(doc.get("enabled", 0))
    return None, 0

async def toggle_welcome(chat_id: int, enabled: bool):
    text, _ = await get_welcome(chat_id)
    if text is None:
        text = "🌸 Welcome {mention} to {chat} — Mikey-kun and I are glad you're here 🌸"
    await set_welcome(chat_id, text, 1 if enabled else 0)

# ---------- LOCKS ----------
VALID_LOCKS = ["all","media","sticker","gif","link","photo","video","audio","document","forward","inline","poll","invite","pin"]

async def set_lock(chat_id: int, lock_type: str, locked: bool) -> bool:
    if lock_type not in VALID_LOCKS:
        return False
    col = get_collection("locks")
    await col.update_one(
        {"chat_id": chat_id, "lock_type": lock_type},
        {"$set": {"locked": 1 if locked else 0}},
        upsert=True,
    )
    return True

async def is_locked(chat_id: int, lock_type: str) -> bool:
    col = get_collection("locks")
    doc = await col.find_one({"chat_id": chat_id, "lock_type": lock_type})
    return bool(doc and doc.get("locked"))

async def get_locks(chat_id: int) -> List[str]:
    col = get_collection("locks")
    cur = col.find({"chat_id": chat_id, "locked": 1})
    out = []
    async for doc in cur:
        out.append(doc["lock_type"])
    return out

# ---------- FLOOD ----------
async def set_flood(chat_id: int, limit_n: int, window_s: int):
    col = get_collection("flood_settings")
    await col.update_one(
        {"chat_id": chat_id},
        {"$set": {"limit_n": limit_n, "window_s": window_s, "enabled": 1}},
        upsert=True,
    )

async def get_flood(chat_id: int) -> Tuple[int, int, int]:
    col = get_collection("flood_settings")
    doc = await col.find_one({"chat_id": chat_id})
    if doc:
        return int(doc.get("limit_n", config.FLOOD_LIMIT)), int(doc.get("window_s", config.FLOOD_WINDOW)), int(doc.get("enabled", 1))
    return config.FLOOD_LIMIT, config.FLOOD_WINDOW, 1

# ---------- HINATA POWERS ----------
VALID_POWERS = ["kick","ban","unban","mute","unmute","warn","warn_reset","pin","unpin","del","purge","promote","demote","lock","unlock","filter","rules","welcome","flood","all"]

def _normalize_powers(powers_str: str) -> str:
    if not powers_str:
        return ""
    parts = [p.strip().lower() for p in powers_str.replace(" ", ",").split(",") if p.strip()]
    alias = {"delete":"del", "remove":"kick", "block":"ban", "silence":"mute", "admin":"promote", "power":"all", "full":"all", "everything":"all", "moderator":"all"}
    norm = []
    for p in parts:
        p = alias.get(p, p)
        if p in VALID_POWERS and p not in norm:
            norm.append(p)
    if "all" in norm:
        return "all"
    return ",".join(norm)

# Simple in-memory cache for powers (military-grade performance — reduces Mongo hits)
_power_cache = {}  # (chat_id, user_id) -> (set, expire_ts)
_POWER_TTL = 45  # seconds

async def get_hinata_powers(chat_id: int, user_id: int) -> Set[str]:
    import time as _t
    key = (chat_id, user_id)
    now = _t.time()
    if key in _power_cache:
        val, exp = _power_cache[key]
        if exp > now:
            return val
        else:
            _power_cache.pop(key, None)
    col = get_collection("hinata_powers")
    doc = await col.find_one({"chat_id": chat_id, "user_id": user_id})
    if not doc or not doc.get("powers"):
        res = set()
    else:
        raw = doc["powers"].strip()
        if raw == "all":
            res = set(VALID_POWERS)
        else:
            res = set(p.strip() for p in raw.split(",") if p.strip())
    _power_cache[key] = (res, now + _POWER_TTL)
    return res

def _invalidate_power_cache(chat_id: int, user_id: int):
    _power_cache.pop((chat_id, user_id), None)

async def has_hinata_power(chat_id: int, user_id: int, action: str) -> bool:
    if user_id == config.OWNER_ID and config.OWNER_ID != 0:
        return True
    powers = await get_hinata_powers(chat_id, user_id)
    if not powers:
        return False
    if "all" in powers:
        return True
    act = action.lower()
    alias_map = {"kick":"kick","ban":"ban","unban":"unban","mute":"mute","unmute":"unmute","warn":"warn","unwarn":"warn_reset","warn_reset":"warn_reset","pin":"pin","unpin":"unpin","del":"del","delete":"del","purge":"purge","promote":"promote","demote":"demote","lock":"lock","unlock":"unlock","filter":"filter","filter_add":"filter","filter_remove":"filter","set_rules":"rules","set_welcome":"welcome","grant":"all","revoke":"all"}
    act = alias_map.get(act, act)
    return act in powers

async def grant_hinata_power(chat_id: int, user_id: int, powers_to_add: str, granted_by: int) -> Set[str]:
    powers_to_add = _normalize_powers(powers_to_add)
    if not powers_to_add:
        return set()
    current = await get_hinata_powers(chat_id, user_id)
    col = get_collection("hinata_powers")
    if "all" in current or powers_to_add == "all":
        new_str = "all"
        new_set = set(VALID_POWERS)
    else:
        merged = current.union(set(powers_to_add.split(",")))
        new_str = ",".join(sorted(merged))
        new_set = merged
    await col.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {"powers": new_str, "granted_by": granted_by, "granted_at": int(time.time())}},
        upsert=True,
    )
    _invalidate_power_cache(chat_id, user_id)
    return new_set

async def revoke_hinata_power(chat_id: int, user_id: int, powers_to_remove: str) -> Set[str]:
    powers_to_remove = _normalize_powers(powers_to_remove)
    if not powers_to_remove:
        return await get_hinata_powers(chat_id, user_id)
    col = get_collection("hinata_powers")
    doc = await col.find_one({"chat_id": chat_id, "user_id": user_id})
    if not doc:
        return set()
    raw = doc.get("powers", "").strip()
    current = await get_hinata_powers(chat_id, user_id)
    if not current:
        return set()
    if powers_to_remove == "all":
        await col.delete_one({"chat_id": chat_id, "user_id": user_id})
        _invalidate_power_cache(chat_id, user_id)
        return set()
    if raw == "all":
        explicit = set(p for p in VALID_POWERS if p != "all")
        remaining = explicit - set(powers_to_remove.split(","))
    else:
        remaining = current - set(powers_to_remove.split(",")) - {"all"}
    if not remaining:
        await col.delete_one({"chat_id": chat_id, "user_id": user_id})
        _invalidate_power_cache(chat_id, user_id)
        return set()
    new_str = ",".join(sorted(remaining))
    await col.update_one({"chat_id": chat_id, "user_id": user_id}, {"$set": {"powers": new_str}})
    _invalidate_power_cache(chat_id, user_id)
    return remaining

async def set_hinata_powers(chat_id: int, user_id: int, powers_str: str, granted_by: int) -> Set[str]:
    powers_str = _normalize_powers(powers_str)
    col = get_collection("hinata_powers")
    if not powers_str:
        await col.delete_one({"chat_id": chat_id, "user_id": user_id})
        _invalidate_power_cache(chat_id, user_id)
        return set()
    await col.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {"powers": powers_str, "granted_by": granted_by, "granted_at": int(time.time())}},
        upsert=True,
    )
    _invalidate_power_cache(chat_id, user_id)
    if powers_str == "all":
        return set(VALID_POWERS)
    return set(powers_str.split(","))

async def list_hinata_powers(chat_id: int):
    col = get_collection("hinata_powers")
    cur = col.find({"chat_id": chat_id})
    rows = []
    async for doc in cur:
        rows.append((doc["user_id"], doc["powers"], doc.get("granted_by")))
    return rows

async def clear_hinata_powers(chat_id: int, user_id: int):
    col = get_collection("hinata_powers")
    await col.delete_one({"chat_id": chat_id, "user_id": user_id})
    _invalidate_power_cache(chat_id, user_id)

# ---------- OWNER INFO ----------
async def save_owner_info(owner_id: int, username: str, full_name: str):
    col = get_collection("owner")
    await col.update_one(
        {"owner_id": owner_id},
        {"$set": {"username": username or "", "full_name": full_name or "", "updated_at": int(time.time())}},
        upsert=True,
    )
    # also cache in config for quick access
    config.OWNER_USERNAME = username or ""
    config.OWNER_FULLNAME = full_name or ""

async def get_owner_info(owner_id: int) -> Optional[dict]:
    col = get_collection("owner")
    return await col.find_one({"owner_id": owner_id})

# ---------- HINATA ALLOWED TALK (preset py answer w/o AI) ----------
# Mikey permits Hinata to talk to certain users (by username or tag). Those get preset python replies, not AI.
async def allow_hinata_talk(chat_id: int, user_id: int, granted_by: int, username: str = ""):
    col = get_collection("hinata_allowed")
    await col.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {"username": username or "", "granted_by": granted_by, "granted_at": int(time.time())}},
        upsert=True,
    )

async def disallow_hinata_talk(chat_id: int, user_id: int) -> bool:
    col = get_collection("hinata_allowed")
    res = await col.delete_one({"chat_id": chat_id, "user_id": user_id})
    return res.deleted_count > 0

async def is_hinata_allowed(chat_id: int, user_id: int) -> bool:
    if user_id == config.OWNER_ID and config.OWNER_ID != 0:
        return True  # Mikey always allowed (but he gets AI, not preset)
    col = get_collection("hinata_allowed")
    doc = await col.find_one({"chat_id": chat_id, "user_id": user_id})
    return bool(doc)

async def list_hinata_allowed(chat_id: int):
    col = get_collection("hinata_allowed")
    cur = col.find({"chat_id": chat_id})
    rows = []
    async for doc in cur:
        rows.append((doc["user_id"], doc.get("username",""), doc.get("granted_by")))
    return rows

async def clear_hinata_allowed(chat_id: int):
    col = get_collection("hinata_allowed")
    await col.delete_many({"chat_id": chat_id})

# Preset python answers (no AI) for allowed users when they call hina/hinata/hyuga
HINATA_PRESETS = [
    "Hai 🌸 I'm here — gentle and listening. How can I help you?",
    "🌸 Kon'nichiwa... you called me? I'm here for you.",
    "Hai, I'm Hinata 🌸 your call reached my Byakugan — what do you need?",
    "🌸 Softly here... tell me, what can I do for you?",
    "Mikey-kun allowed me to talk to you 🌸 how may I help?",
]

def get_preset_reply(user_mention: str = "") -> str:
    import random
    base = random.choice(HINATA_PRESETS)
    if user_mention:
        return f"{user_mention} {base}"
    return base

# ---------- HINATA MEMORY — soft (hourly, gc-wise, only hina-associated) + perm (on 'hina remember this') ----------
import datetime as _dt

async def add_soft_memory(chat_id: int, role: str, content: str, associated: bool = True):
    """Only saves if associated with Hinata (hina/hyuga mention or Mikey needs her). Resets hourly via TTL."""
    if not associated:
        return
    if not content or len(content.strip()) < 2:
        return
    col = get_collection("hinata_soft_memory")
    now = _dt.datetime.utcnow()
    expire = now + _dt.timedelta(seconds=3600)  # soft resets hourly
    # keep last 30 messages per gc
    await col.update_one(
        {"chat_id": chat_id},
        {
            "$push": {"messages": {"$each": [{"role": role, "content": content[:800], "ts": int(time.time())}], "$slice": -30}},
            "$set": {"expireAt": expire, "updatedAt": int(time.time())}
        },
        upsert=True,
    )

async def get_soft_memory(chat_id: int, limit: int = 12):
    col = get_collection("hinata_soft_memory")
    doc = await col.find_one({"chat_id": chat_id})
    if not doc or not doc.get("messages"):
        return []
    msgs = doc["messages"][-limit:]
    # return as history for AI: list of {role, content}
    return [{"role": m["role"], "content": m["content"]} for m in msgs]

async def clear_soft_memory(chat_id: int):
    col = get_collection("hinata_soft_memory")
    await col.delete_one({"chat_id": chat_id})

async def add_perm_memory(chat_id: int, owner_id: int, text: str):
    """Mikey says 'hina remember this: ...' — goes to perm memory (never expires, gc-wise)"""
    if not text or len(text.strip()) < 3:
        return 0
    col = get_collection("hinata_perm_memory")
    # push with cap 100 per gc
    await col.update_one(
        {"chat_id": chat_id, "owner_id": owner_id},
        {
            "$push": {"memories": {"$each": [{"text": text[:1500], "ts": int(time.time())}], "$slice": -100}},
            "$set": {"updatedAt": int(time.time())}
        },
        upsert=True,
    )
    # return count
    doc = await col.find_one({"chat_id": chat_id, "owner_id": owner_id})
    return len(doc.get("memories", [])) if doc else 0

async def get_perm_memory(chat_id: int, owner_id: int = None):
    col = get_collection("hinata_perm_memory")
    if owner_id:
        doc = await col.find_one({"chat_id": chat_id, "owner_id": owner_id})
        if not doc: return []
        return [m["text"] for m in doc.get("memories", [])]
    # all perms for chat
    cur = col.find({"chat_id": chat_id})
    out = []
    async for doc in cur:
        for m in doc.get("memories", []):
            out.append(m["text"])
    return out

async def list_perm_memory(chat_id: int, owner_id: int = None):
    return await get_perm_memory(chat_id, owner_id)

async def forget_perm_memory(chat_id: int, owner_id: int, index: int = None, text: str = None):
    col = get_collection("hinata_perm_memory")
    doc = await col.find_one({"chat_id": chat_id, "owner_id": owner_id})
    if not doc:
        return False
    mems = doc.get("memories", [])
    if index is not None and 0 <= index < len(mems):
        mems.pop(index)
    elif text:
        mems = [m for m in mems if text.lower() not in m["text"].lower()]
    else:
        await col.delete_one({"chat_id": chat_id, "owner_id": owner_id})
        return True
    await col.update_one({"chat_id": chat_id, "owner_id": owner_id}, {"$set": {"memories": mems}})
    return True

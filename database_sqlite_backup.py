import aiosqlite
import os
import time
from pathlib import Path
import config

DB_PATH = config.DATABASE_PATH

async def init_db():
    Path(os.path.dirname(DB_PATH) or ".").mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS warns (
            chat_id INTEGER,
            user_id INTEGER,
            count INTEGER DEFAULT 0,
            reasons TEXT,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS filters (
            chat_id INTEGER,
            keyword TEXT,
            reply TEXT,
            PRIMARY KEY (chat_id, keyword)
        );
        CREATE TABLE IF NOT EXISTS rules (
            chat_id INTEGER PRIMARY KEY,
            rules TEXT
        );
        CREATE TABLE IF NOT EXISTS welcome (
            chat_id INTEGER PRIMARY KEY,
            text TEXT,
            enabled INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS locks (
            chat_id INTEGER,
            lock_type TEXT,
            locked INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, lock_type)
        );
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            warn_limit INTEGER DEFAULT 3
        );
        CREATE TABLE IF NOT EXISTS flood_settings (
            chat_id INTEGER PRIMARY KEY,
            limit_n INTEGER DEFAULT 5,
            window_s INTEGER DEFAULT 5,
            enabled INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS hinata_powers (
            chat_id INTEGER,
            user_id INTEGER,
            powers TEXT,
            granted_by INTEGER,
            granted_at INTEGER,
            PRIMARY KEY (chat_id, user_id)
        );
        """)
        await db.commit()
        # migrate old data if needed: ensure table exists

# --- warns ---
async def get_warns(chat_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT count, reasons FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id)) as cur:
            row = await cur.fetchone()
            return (row[0], row[1]) if row else (0, "")

async def add_warn(chat_id, user_id, reason=""):
    count, reasons = await get_warns(chat_id, user_id)
    count += 1
    reasons = (reasons + f"\n- {reason}" if reasons else reason) if reason else reasons
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO warns(chat_id,user_id,count,reasons) VALUES(?,?,?,?)", (chat_id, user_id, count, reasons))
        await db.commit()
    return count

async def reset_warns(chat_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        await db.commit()

async def set_warn_limit(chat_id, limit):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO chats(chat_id,warn_limit) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET warn_limit=?", (chat_id, limit, limit))
        await db.commit()

async def get_warn_limit(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT warn_limit FROM chats WHERE chat_id=?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else config.WARN_LIMIT

# --- filters ---
async def add_filter(chat_id, keyword, reply):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO filters VALUES(?,?,?)", (chat_id, keyword.lower(), reply))
        await db.commit()

async def remove_filter(chat_id, keyword):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM filters WHERE chat_id=? AND keyword=?", (chat_id, keyword.lower()))
        await db.commit()
        return db.total_changes > 0

async def get_filters(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT keyword, reply FROM filters WHERE chat_id=?", (chat_id,)) as cur:
            return await cur.fetchall()

async def list_filter_keywords(chat_id):
    rows = await get_filters(chat_id)
    return [r[0] for r in rows]

# --- rules ---
async def set_rules(chat_id, text):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO rules VALUES(?,?)", (chat_id, text))
        await db.commit()

async def get_rules(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT rules FROM rules WHERE chat_id=?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

# --- welcome ---
async def set_welcome(chat_id, text, enabled=1):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO welcome VALUES(?,?,?)", (chat_id, text, enabled))
        await db.commit()

async def get_welcome(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT text, enabled FROM welcome WHERE chat_id=?", (chat_id,)) as cur:
            row = await cur.fetchone()
            return row if row else (None, 0)

async def toggle_welcome(chat_id, enabled):
    text, _ = await get_welcome(chat_id)
    if text is None:
        text = "Welcome {mention} to {chat}! 🎉"
    await set_welcome(chat_id, text, 1 if enabled else 0)

# --- locks ---
VALID_LOCKS = ["all","media","sticker","gif","link","photo","video","audio","document","forward","inline","poll","invite","pin"]

async def set_lock(chat_id, lock_type, locked: bool):
    if lock_type not in VALID_LOCKS:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO locks VALUES(?,?,?)", (chat_id, lock_type, 1 if locked else 0))
        await db.commit()
    return True

async def is_locked(chat_id, lock_type):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT locked FROM locks WHERE chat_id=? AND lock_type=?", (chat_id, lock_type)) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False

async def get_locks(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT lock_type FROM locks WHERE chat_id=? AND locked=1", (chat_id,)) as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]

# --- flood settings ---
async def set_flood(chat_id, limit_n, window_s):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO flood_settings VALUES(?,?,?,1)", (chat_id, limit_n, window_s))
        await db.commit()

async def get_flood(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT limit_n, window_s, enabled FROM flood_settings WHERE chat_id=?", (chat_id,)) as cur:
            row = await cur.fetchone()
            if row: return row
            return (config.FLOOD_LIMIT, config.FLOOD_WINDOW, 1)

# --- Hinata Powers (Mikey's delegated powers) ---
import time as _time
VALID_POWERS = ["kick","ban","unban","mute","unmute","warn","warn_reset","pin","unpin","del","purge","promote","demote","lock","unlock","filter","rules","welcome","flood","all"]

def _normalize_powers(powers_str: str) -> str:
    if not powers_str: return ""
    parts = [p.strip().lower() for p in powers_str.replace(" ", ",").split(",") if p.strip()]
    # map aliases
    alias = {"delete":"del", "remove":"kick", "block":"ban", "silence":"mute", "admin":"promote", "power":"all", "full":"all", "everything":"all", "moderator":"all"}
    norm = []
    for p in parts:
        p = alias.get(p, p)
        if p in VALID_POWERS and p not in norm:
            norm.append(p)
    if "all" in norm:
        return "all"
    return ",".join(norm)

async def get_hinata_powers(chat_id, user_id) -> set:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT powers FROM hinata_powers WHERE chat_id=? AND user_id=?", (chat_id, user_id)) as cur:
            row = await cur.fetchone()
            if not row or not row[0]:
                return set()
            if row[0].strip() == "all":
                return set(VALID_POWERS)
            return set(p.strip() for p in row[0].split(",") if p.strip())

async def has_hinata_power(chat_id, user_id, action: str) -> bool:
    # owner always has all
    if user_id == config.OWNER_ID and config.OWNER_ID != 0:
        return True
    powers = await get_hinata_powers(chat_id, user_id)
    if not powers: return False
    if "all" in powers: return True
    # map action aliases
    act = action.lower()
    alias_map = {"kick":"kick","ban":"ban","unban":"unban","mute":"mute","unmute":"unmute","warn":"warn","unwarn":"warn_reset","warn_reset":"warn_reset","pin":"pin","unpin":"unpin","del":"del","delete":"del","purge":"purge","promote":"promote","demote":"demote","lock":"lock","unlock":"unlock","filter":"filter","filter_add":"filter","filter_remove":"filter","set_rules":"rules","set_welcome":"welcome","grant":"all","revoke":"all"}
    act = alias_map.get(act, act)
    return act in powers

async def grant_hinata_power(chat_id, user_id, powers_to_add: str, granted_by: int):
    powers_to_add = _normalize_powers(powers_to_add)
    if not powers_to_add:
        return set()
    current = await get_hinata_powers(chat_id, user_id)
    # if current is all, keep all
    if "all" in current or powers_to_add == "all":
        new_str = "all"
        new_set = set(VALID_POWERS)
    else:
        merged = current.union(set(powers_to_add.split(",")))
        new_str = ",".join(sorted(merged))
        new_set = merged
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO hinata_powers(chat_id,user_id,powers,granted_by,granted_at) VALUES(?,?,?,?,?)",
                         (chat_id, user_id, new_str, granted_by, int(_time.time())))
        await db.commit()
    return new_set

async def revoke_hinata_power(chat_id, user_id, powers_to_remove: str):
    powers_to_remove = _normalize_powers(powers_to_remove)
    if not powers_to_remove:
        return await get_hinata_powers(chat_id, user_id)
    # fetch raw to detect "all" sentinel
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT powers FROM hinata_powers WHERE chat_id=? AND user_id=?", (chat_id, user_id)) as cur:
            row = await cur.fetchone()
            raw = row[0] if row else ""
    current = await get_hinata_powers(chat_id, user_id)
    if not current:
        return set()
    if powers_to_remove == "all":
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM hinata_powers WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            await db.commit()
        return set()
    # if raw was "all", treat current explicit as all powers without sentinel
    if raw.strip() == "all":
        # explicit full set without "all" token
        explicit = set(p for p in VALID_POWERS if p != "all")
        remaining = explicit - set(powers_to_remove.split(","))
    else:
        remaining = current - set(powers_to_remove.split(",")) - {"all"}
    if not remaining:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM hinata_powers WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            await db.commit()
        return set()
    new_str = ",".join(sorted(remaining))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE hinata_powers SET powers=? WHERE chat_id=? AND user_id=?", (new_str, chat_id, user_id))
        await db.commit()
    return remaining

async def set_hinata_powers(chat_id, user_id, powers_str: str, granted_by: int):
    powers_str = _normalize_powers(powers_str)
    if not powers_str:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM hinata_powers WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            await db.commit()
        return set()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO hinata_powers(chat_id,user_id,powers,granted_by,granted_at) VALUES(?,?,?,?,?)",
                         (chat_id, user_id, powers_str, granted_by, int(_time.time())))
        await db.commit()
    if powers_str == "all":
        return set(VALID_POWERS)
    return set(powers_str.split(","))

async def list_hinata_powers(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, powers, granted_by FROM hinata_powers WHERE chat_id=?", (chat_id,)) as cur:
            rows = await cur.fetchall()
            return rows

async def clear_hinata_powers(chat_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM hinata_powers WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        await db.commit()

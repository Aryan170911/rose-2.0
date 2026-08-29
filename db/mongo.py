"""Modular MongoDB client for Hinata — singleton, async (Motor)"""
import asyncio
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ServerSelectionTimeoutError
import config

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None

def _get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        # P4 — serverSelectionTimeoutMS=3000 so we fail fast instead of 30s stall
        _client = AsyncIOMotorClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=3000,
            connectTimeoutMS=5000,
            maxPoolSize=20,
            retryWrites=True,
        )
    return _client

def get_db() -> AsyncIOMotorDatabase:
    global _db
    if _db is not None:
        return _db
    client = _get_client()
    # Extract db name from URI or use config
    # If MONGO_DB_NAME set, use it; else try to parse or default
    db_name = config.MONGO_DB_NAME
    _db = client[db_name]
    return _db

def get_collection(name: str):
    return get_db()[name]

async def ping() -> bool:
    try:
        await get_db().command("ping")
        return True
    except Exception as e:
        print(f"[mongo] ping failed: {e}")
        return False

async def init_mongo():
    """Create indexes for all collections - idempotent"""
    db = get_db()
    # warns: unique chat_id+user_id
    await db.warns.create_index([("chat_id", 1), ("user_id", 1)], unique=True, background=True)
    await db.filters.create_index([("chat_id", 1), ("keyword", 1)], unique=True, background=True)
    await db.rules.create_index("chat_id", unique=True, background=True)
    await db.welcome.create_index("chat_id", unique=True, background=True)
    await db.locks.create_index([("chat_id", 1), ("lock_type", 1)], unique=True, background=True)
    await db.chats.create_index("chat_id", unique=True, background=True)
    await db.flood_settings.create_index("chat_id", unique=True, background=True)
    await db.hinata_powers.create_index([("chat_id", 1), ("user_id", 1)], unique=True, background=True)
    await db.owner.create_index("owner_id", unique=True, background=True)
    await db.hinata_allowed.create_index([("chat_id", 1), ("user_id", 1)], unique=True, background=True)
    await db.hinata_allowed.create_index("chat_id", background=True)
    # soft memory — hourly reset GC-wise via TTL
    await db.hinata_soft_memory.create_index("chat_id", unique=True, background=True)
    await db.hinata_soft_memory.create_index("expireAt", expireAfterSeconds=0, background=True)
    await db.hinata_perm_memory.create_index([("chat_id", 1), ("owner_id", 1)], unique=True, background=True)
    await db.fix_requests.create_index([("chat_id", 1), ("at", -1)], background=True)
    await db.fix_requests.create_index("status", background=True)
    # helpful secondary indexes
    await db.hinata_powers.create_index("chat_id", background=True)
    await db.warns.create_index("chat_id", background=True)
    print("[mongo] indexes ensured")

async def clear_all():
    """DANGER: drops all data in the configured DB - used on first setup per Mikey's request"""
    db = get_db()
    collections = await db.list_collection_names()
    for name in collections:
        await db.drop_collection(name)
    print(f"[mongo] cleared {len(collections)} collections: {collections}")
    # re-init indexes after clear
    await init_mongo()
    return collections

# For sync scripts that need pymongo
def get_sync_client():
    from pymongo import MongoClient
    return MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=5000)

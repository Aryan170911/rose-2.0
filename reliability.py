"""Military-grade reliability — retry, health, graceful handling"""
import asyncio
import time
import logging

logger = logging.getLogger(__name__)

async def retry_telegram(coro, retries=3, delay=1.0, backoff=2.0):
    """Retry Telegram API calls with exponential backoff"""
    last_exc = None
    d = delay
    for i in range(retries):
        try:
            return await coro()
        except Exception as e:
            last_exc = e
            # Don't retry on BadRequest (400) — it's a logic error, not network
            if "BadRequest" in type(e).__name__ or "400" in str(e):
                raise
            if i == retries -1:
                raise
            logger.warning(f"[retry] attempt {i+1}/{retries} failed: {e}, retry in {d}s")
            await asyncio.sleep(d)
            d *= backoff
    raise last_exc

# Health tracking
_start_time = time.time()
_health = {"checks": 0, "fails": 0, "last_ok": None}

async def health_check(bot, db_ping_func=None):
    """Military health check: bot + db + uptime"""
    import config
    from db.mongo import ping as mongo_ping
    _health["checks"] += 1
    ok = True
    details = {}
    # bot
    try:
        me = await bot.get_me()
        details["bot"] = f"@{me.username} OK"
    except Exception as e:
        details["bot"] = f"FAIL {e}"
        ok = False
    # mongo
    try:
        m_ok = await mongo_ping()
        details["mongo"] = "OK" if m_ok else "FAIL"
        if not m_ok: ok = False
    except Exception as e:
        details["mongo"] = f"FAIL {e}"
        ok = False
    # uptime
    details["uptime_s"] = int(time.time() - _start_time)
    details["owner"] = config.OWNER_ID
    if ok:
        _health["last_ok"] = int(time.time())
    else:
        _health["fails"] += 1
    return ok, details

def get_health_stats():
    return {
        "uptime_s": int(time.time() - _start_time),
        "checks": _health["checks"],
        "fails": _health["fails"],
        "last_ok": _health["last_ok"],
    }

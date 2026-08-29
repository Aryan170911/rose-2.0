"""Observability — structured JSON logs, RotatingFileHandler, metrics, log channel.
P0: route all logs to data/logs/bot.{out,err}.log with rotation
P4: silence pymongo noise
"""
import logging
import json
import os
import sys
import time
from collections import Counter
from logging.handlers import RotatingFileHandler

_metrics = Counter()
_start = time.time()

# Determine log directory (project-relative)
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "logs")
# Fallback if running as a frozen/module
if not os.path.isdir(_LOG_DIR):
    _LOG_DIR = os.path.join("data", "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

OUT_LOG = os.path.join(_LOG_DIR, "bot.out.log")
ERR_LOG = os.path.join(_LOG_DIR, "bot.err.log")
DM_LOG = os.path.join(_LOG_DIR, "owner_dm.log")
AUDIT_LOG = os.path.join(_LOG_DIR, "audit.log")

# Counter for log lines (helps us know things are working)
_logger_setup = False

class JSONFormatter(logging.Formatter):
    def format(self, record):
        obj = {
            "ts": int(time.time()),
            "level": record.levelname,
            "msg": record.getMessage(),
            "name": record.name,
        }
        if hasattr(record, "extra"):
            obj.update(record.extra)
        return json.dumps(obj, ensure_ascii=False)

def inc(metric: str, n=1):
    _metrics[metric] += n

def get_metrics():
    return dict(_metrics, uptime_s=int(time.time()-_start))

def _make_rotating(path: str, level=logging.INFO, max_bytes=5_000_000, backups=5):
    h = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    h.setLevel(level)
    return h

def setup():
    """P0 — full logging setup: rotating file handlers, JSON formatter, force basicConfig."""
    global _logger_setup
    # Force root basicConfig
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    root = logging.getLogger()
    # Remove default StreamHandler from basicConfig (it goes to stderr by default; we route to file)
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
            # we'll keep one stderr handler for live debugging — but reroute through file
            try: root.removeHandler(h)
            except: pass
    # Add rotating handlers
    # ERR file: WARNING+ from all loggers
    err_h = _make_rotating(ERR_LOG, level=logging.INFO, max_bytes=5_000_000)
    err_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    err_h.setLevel(logging.INFO)
    # JSON-formatted debug for our observability metrics
    json_h = _make_rotating(ERR_LOG + ".jsonl", level=logging.INFO, max_bytes=2_000_000)
    json_h.setFormatter(JSONFormatter())
    # Stdout/launcher
    sys_h = logging.StreamHandler(sys.stdout)
    sys_h.setLevel(logging.INFO)
    sys_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    # Attach to root
    root.addHandler(err_h)
    root.addHandler(json_h)
    root.addHandler(sys_h)
    # P4: silence noisy libs
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("pymongo.serverSelection").setLevel(logging.WARNING)
    logging.getLogger("pymongo.command").setLevel(logging.WARNING)
    logging.getLogger("pymongo.topology").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # audit DM log file
    audit_h = _make_rotating(DM_LOG, level=logging.INFO, max_bytes=2_000_000)
    audit_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    audit_logger = logging.getLogger("owner_dm")
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(audit_h)
    audit_logger.propagate = False
    # audit log
    audit2_h = _make_rotating(AUDIT_LOG, level=logging.INFO, max_bytes=5_000_000)
    audit2_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    audit2_logger = logging.getLogger("audit")
    audit2_logger.setLevel(logging.INFO)
    audit2_logger.addHandler(audit2_h)
    audit2_logger.propagate = False
    _logger_setup = True
    return root

async def send_to_log_channel(bot, text: str):
    import config
    if not config.LOG_CHANNEL_ID:
        return
    try:
        await bot.send_message(config.LOG_CHANNEL_ID, text[:4000])
    except: pass

def log_dm(action: str, status: str, detail: str = ""):
    """Log every owner-DM attempt to owner_dm.log for visibility."""
    logging.getLogger("owner_dm").info(f"{action} {status} {detail}")

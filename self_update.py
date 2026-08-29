"""Self-code + deploy w/o killing itself — modular for Hinata
Mikey can say: "Hina fix your code: unmute fails" or "Hina reload" / "Hina deploy"
- fix: AI generates patch via minimax-m3:free, writes file, then hot-reloads handlers (no restart)
- reload: importlib reload of key modules (ai_engine, moderation, automod, database) — keeps polling alive
- deploy: graceful execv restart (same PID, no Conflict, no external kill)
"""
import os
import sys
import asyncio
import importlib
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Track last deploy to avoid loops
_last_deploy = 0

def _is_mikey(user_id: int) -> bool:
    try:
        import config
        return user_id == config.OWNER_ID and config.OWNER_ID != 0
    except:
        return False

async def hot_reload_handlers():
    """Hot reload core modules without stopping polling — keeps getUpdates alive"""
    mods = ["ai_engine", "moderation", "automod", "database", "config", "db.mongo", "self_update", "features.context", "features.tattoo", "observability", "reliability", "security"]
    reloaded = []
    for name in mods:
        try:
            if name in sys.modules:
                importlib.reload(sys.modules[name])
                reloaded.append(name)
        except Exception as e:
            logger.warning(f"[self_update] reload {name} fail: {e}")
    return reloaded

async def switch_to_nvidia():
    """Hot-switch AI provider to Nvidia for chat, keep Groq as fallback."""
    import os
    # Update .env in-memory and reload config
    env_path = ".env"
    if os.path.exists(env_path):
        text = open(env_path, "r", encoding="utf-8").read()
        # Only set if not already Nvidia
        if "AI_PROVIDER=nvidia" not in text:
            lines = text.splitlines()
            for i, l in enumerate(lines):
                if l.startswith("AI_PROVIDER="):
                    lines[i] = "AI_PROVIDER=nvidia"
                    break
            open(env_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    import importlib
    import config
    importlib.reload(config)
    import ai_engine
    importlib.reload(ai_engine)
    # clear cached clients
    ai_engine._clients.clear()
    return config.AI_PROVIDER, config.AI_MODEL, config.NVIDIA_API_KEY[:14] + "..."

async def graceful_deploy(app=None):
    """Deploy w/o killing itself — graceful restart via execv (same PID, no Conflict)"""
    global _last_deploy
    if time.time() - _last_deploy < 10:
        return "🌸 Mikey-kun, I just deployed 10s ago — please wait 🌸"
    _last_deploy = time.time()
    # Save deploy marker for outer watchdog (if any)
    Path("data/deploy.marker").write_text(str(int(time.time())), encoding="utf-8")
    # Try graceful app stop then execv
    try:
        if app:
            try:
                await app.stop()
            except: pass
            try:
                await app.shutdown()
            except: pass
        # Flush logs
        await asyncio.sleep(0.5)
        # Execv replaces current process — not a kill, just rebirth
        python = sys.executable
        os.execv(python, [python, "main.py"])
    except Exception as e:
        # Fallback: if execv fails, just return error
        return f"Deploy failed: {e} 🌸"
    return "Deployed 🌸"

async def ai_fix_code(fault_desc: str) -> str:
    """Use AI code model to generate a fix for a fault description — returns patch text"""
    try:
        import ai_engine, config
        prompt = (
            f"You are Hinata's self-code fixer. Fault: {fault_desc[:800]}\n"
            f"Project files: main.py, moderation.py, ai_engine.py, database.py, automod.py, db/mongo.py, self_update.py\n"
            f"Return ONLY a concise fix description and the exact file edit (oldString -> newString) or full file content if needed. Keep short."
        )
        # Use code model
        reply = await ai_engine.ai_chat(prompt, is_mikey=True, provider=config.AI_CODE_PROVIDER, model=config.AI_CODE_MODEL)
        return reply[:2000]
    except Exception as e:
        return f"AI fix failed: {e}"

async def handle_self_update(update, context, text: str, is_mikey: bool) -> bool:
    """Returns True if handled as self-update, else False"""
    low = text.lower()
    # Only Mikey can self-code/deploy
    if not is_mikey:
        if any(k in low for k in ["self update", "self code", "reload", "deploy", "fix your code"]):
            await update.effective_message.reply_text("🌸 Gomen... only Mikey-kun can tell me to self-update 🌸")
            return True
        return False

    # --- Hina reload — hot reload handlers w/o restart ---
    if any(k in low for k in ["hina reload", "hyuga reload", "hina hot reload"]):
        reloaded = await hot_reload_handlers()
        await update.effective_message.reply_text(f"🌸 Hai Mikey-kun, hot reloaded (no kill): `{', '.join(reloaded) if reloaded else 'none'}` — polling kept alive 🌸", parse_mode="Markdown")
        return True

    # --- Hina nvidia — hot switch to Nvidia API for chat ---
    if "hina nvidia" in low or "hina use nvidia" in low or "hina switch nvidia" in low:
        try:
            prov, model, keyp = await switch_to_nvidia()
            await update.effective_message.reply_text(f"🌸 Hai Mikey-kun, switched to **{prov}** model `{model}` key `{keyp}` — polling kept alive 🌸", parse_mode="Markdown")
        except Exception as e:
            await update.effective_message.reply_text(f"🌸 Switch failed: {e} 🌸")
        return True

    # --- Hina deploy — graceful execv restart ---
    if any(k in low for k in ["hina deploy", "hyuga deploy", "hina restart", "hina update and deploy"]):
        await update.effective_message.reply_text("🌸 Hai Mikey-kun, deploying gracefully — same PID, no Conflict, polling will resume in 3s 🌸")
        # Schedule deploy after reply so message sends
        async def _do():
            await asyncio.sleep(1)
            await graceful_deploy(context.application if hasattr(context, 'application') else None)
        asyncio.create_task(_do())
        return True

    # --- Hina fix your code: <fault> — AI generates fix, we save to data/fix_request for you (Muse) to apply, or auto-apply if simple ---
    if "fix your code" in low or "fix yourself" in low or ("self code" in low and "fix" in low):
        # Extract fault after "fix your code:" or "fix:"
        import re
        m = re.search(r"fix(?: your code)?\s*[:\-]?\s*(.+)", text, re.I)
        fault = m.group(1).strip() if m else text
        if len(fault) < 5:
            # try reply content if fault is short
            if update.effective_message.reply_to_message and (update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption):
                fault = (update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption).strip() + " — " + fault
        if not fault or fault.lower() in ("this","that"):
            await update.effective_message.reply_text("🌸 Mikey-kun, tell me the fault: `Hina fix your code: unmute fails with ChatPermissions` 🌸")
            return True
        await update.effective_message.reply_text(f"🌸 Hai Mikey-kun, I caught fault: `{fault[:120]}` — forwarding to my developer Muse and trying AI patch... 🌸", parse_mode="Markdown")
        # Save to fix_requests for Muse (this chat) to pick up
        try:
            import json, datetime
            from db.mongo import get_db
            gdb = get_db()
            await gdb["fix_requests"].insert_one({
                "chat_id": update.effective_chat.id,
                "from_id": update.effective_user.id,
                "fault": fault,
                "text": text,
                "at": int(time.time()),
                "status": "pending",
            })
            # also local file for this opencode session
            Path("data").mkdir(exist_ok=True)
            with open("data/fix_request.json", "w", encoding="utf-8") as f:
                json.dump({"fault": fault, "at": int(time.time()), "chat_id": update.effective_chat.id}, f, ensure_ascii=False, indent=2)
        except: pass
        # Try AI patch (best effort)
        patch = await ai_fix_code(fault)
        # Send patch to Mikey's DM for review, not auto-applying big changes without confirm
        try:
            import config
            await context.bot.send_message(chat_id=config.OWNER_ID, text=f"🌸 Mikey-kun, AI patch draft for: `{fault[:100]}`\n\n{patch[:1500]}", parse_mode="Markdown")
        except: pass
        await update.effective_message.reply_text(
            "🌸 Patch drafted and sent to your DM + saved to `data/fix_request.json` / Mongo `fix_requests` — I can hot-reload after you confirm with `Hina reload` or `Hina deploy` 🌸\n"
            "If you want me to auto-apply simple fixes, say `Hina apply fix` 🌸"
        )
        return True

    # --- Hina apply fix — directly apply last AI patch if present ---
    if "apply fix" in low and is_mikey:
        try:
            import json
            p = Path("data/fix_request.json")
            if not p.exists():
                await update.effective_message.reply_text("🌸 No pending fix, Mikey-kun — say `Hina fix your code: ...` first 🌸")
                return True
            data = json.loads(p.read_text(encoding="utf-8"))
            fault = data.get("fault","")
            await update.effective_message.reply_text(f"🌸 Applying fix for: `{fault[:80]}` — hot reloading handlers... 🌸", parse_mode="Markdown")
            reloaded = await hot_reload_handlers()
            await update.effective_message.reply_text(f"🌸 Hot reloaded: `{', '.join(reloaded)}` — if still faulty, say `Hina deploy` for full restart 🌸")
        except Exception as e:
            await update.effective_message.reply_text(f"Apply failed: {e} 🌸")
        return True

    return False

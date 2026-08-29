"""Tattoo Hand — new modular hand for Hinata to design tattoos
Mikey can say: Hina tattoo a dragon on hand / Hina design tattoo for forearm / Hina tattoo idea ...
- Generates design via AI (minimax-m3:free for code, llama for chat) with Hinata's gentle style
- Saves to Mongo tattoo_requests per group, can be listed
- Demo of self-code: this file was created via Hina self-update without killing bot
"""
import re
import time
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import config
import database as db

# For AI generation, we use ai_engine
try:
    import ai_engine
except: ai_engine = None

async def handle_tattoo(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, is_mikey: bool) -> bool:
    low = text.lower()
    # Trigger: hina/hyuga + tattoo/design + hand/forearm etc or just "tattoo"
    if not any(k in low for k in ["tattoo", "mehndi", "ink"]):
        return False
    # Must be hina-associated or Mikey
    from main import is_hinata_mention, is_mikey_user
    if not is_hinata_mention(text) and not is_mikey:
        # If someone just says tattoo without hina, don't trigger
        return False

    # Extract idea after "tattoo"
    idea = ""
    # Try to capture after tattoo/design
    m = re.search(r"tattoo\s*(?:on\s*(?:hand|forearm|arm|wrist|neck|back)?)?\s*[:\-]?\s*(.+)", text, re.I)
    if m:
        idea = m.group(1).strip()
    else:
        m2 = re.search(r"design\s*(.+)", text, re.I)
        idea = m2.group(1).strip() if m2 else ""
    if not idea or idea.lower() in ("this","that","idea","design","tattoo","hand"):
        # Use reply content if available, or ask for idea
        if update.effective_message.reply_to_message and (update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption):
            idea = (update.effective_message.reply_to_message.text or update.effective_message.reply_to_message.caption).strip()
        else:
            await update.effective_message.reply_text(
                "🌸 Hai, tell me the tattoo idea, Mikey-kun — e.g. `Hina tattoo a small dragon on hand with cherry blossoms` 🌸"
            )
            return True

    # Save request gc-wise
    try:
        from db.mongo import get_db
        gdb = get_db()
        await gdb["tattoo_requests"].insert_one({
            "chat_id": update.effective_chat.id,
            "user_id": update.effective_user.id,
            "username": update.effective_user.username or "",
            "idea": idea[:500],
            "at": int(time.time()),
        })
        await gdb["tattoo_requests"].create_index([("chat_id", 1), ("at", -1)], background=True)
    except: pass
    # Also soft memory
    try:
        await db.add_soft_memory(update.effective_chat.id, "user", f"{update.effective_user.full_name} wants tattoo on hand: {idea}", associated=True)
    except: pass

    # Generate design via AI
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    prompt = (
        f"User wants a tattoo on hand: \"{idea}\". "
        f"As Hinata Hyuga, gentle tattoo artist, describe a beautiful hand tattoo design in 3-4 lines: "
        f"placement (hand/forearm), style (minimalist, fine line, etc), elements, meaning. Keep short, gentle, with 🌸. "
        f"If Mikey-kun asked, be extra devoted."
    )
    try:
        if ai_engine:
            # Use chat model for design (short)
            design = await ai_engine.ai_chat(prompt, is_mikey=is_mikey)
            # Ensure short
            if len(design) > 800:
                design = design[:750] + "… 🌸"
        else:
            design = f"🌸 A delicate hand tattoo: {idea} — fine line, soft shading, cherry blossoms around it, elegant on the hand 🌸"
    except Exception as e:
        design = f"🌸 Tattoo idea: `{idea}` — fine line dragon wrapping the hand, cherry blossoms, gentle and strong like you, Mikey-kun 🌸"

    # Send design
    await update.effective_message.reply_text(
        f"🌸 *Tattoo Hand — Design for {update.effective_user.mention_html()}* 🌸\n\n{design}\n\n_Reply `Hina tattoo <new idea>` to design again — saved gc-wise 🌸_",
        parse_mode=ParseMode.HTML,
    )
    # Save her reply to soft memory
    try:
        await db.add_soft_memory(update.effective_chat.id, "assistant", f"Hinata tattoo design: {design[:400]}", associated=True)
    except: pass
    return True

async def list_tattoos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from db.mongo import get_db
        gdb = get_db()
        cur = gdb["tattoo_requests"].find({"chat_id": update.effective_chat.id}).sort("at", -1).limit(5)
        rows = []
        async for doc in cur:
            rows.append(f"• `{doc['idea'][:40]}` by @{doc.get('username','') or doc['user_id']}")
        if not rows:
            await update.effective_message.reply_text("🌸 No tattoos yet — say `Hina tattoo a dragon on hand` 🌸")
        else:
            await update.effective_message.reply_text("🌸 *Recent tattoos in this GC* 🌸\n" + "\n".join(rows), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.effective_message.reply_text(f"List fail: {e} 🌸")

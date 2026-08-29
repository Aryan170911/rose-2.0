import json
import re
from typing import Optional, Dict, Any
from openai import AsyncOpenAI
import config
try:
    import security
except: security = None

# Client cache per provider
_clients: Dict[str, AsyncOpenAI] = {}

def get_client(provider: str = None, api_key: str = None, base_url: str = None):
    provider = (provider or config.AI_PROVIDER).lower()
    # pick correct key per provider (keeps both Nvidia + OpenRouter)
    if not api_key:
        try:
            api_key = config.get_api_key_for(provider)
        except:
            api_key = config.AI_API_KEY
    key = f"{provider}:{api_key[:8]}:{base_url or config.get_base_url(provider)}"
    if key not in _clients:
        _clients[key] = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or config.get_base_url(provider),
        )
    return _clients[key]

# Models that are good for free tier uncensored/code
FREE_MODELS = {
    "openrouter": [
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen-2.5-coder-32b-instruct:free",
        "deepseek/deepseek-r1:free",
        "google/gemini-flash-1.5-8b:free",
        "nousresearch/hermes-3-llama-3.1-405b:free", # uncensored
        "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    ],
    "nvidia": ["meta/llama-3.3-70b-instruct", "qwen/qwen2.5-coder-32b-instruct"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    "gemini": ["gemini-1.5-flash", "gemini-2.0-flash"],
}

# JSON schema for moderation intent
MODERATION_SCHEMA = {
    "kick": {"target": str, "reason": str},
    "ban": {"target": str, "reason": str, "duration": str},
    "unban": {"target": str},
    "mute": {"target": str, "duration": str, "reason": str},
    "unmute": {"target": str},
    "warn": {"target": str, "reason": str},
    "unwarn": {"target": str},
    "pin": {"reason": str},
    "unpin": {},
    "del": {"target": str},
    "purge": {},
    "promote": {"target": str},
    "demote": {"target": str},
    "lock": {"type": str},
    "unlock": {"type": str},
    "set_welcome": {"text": str},
    "set_rules": {"text": str},
    "filter_add": {"keyword": str, "reply": str},
    "filter_remove": {"keyword": str},
    "grant_power": {"target": str, "powers": str},
    "revoke_power": {"target": str, "powers": str},
    "list_powers": {},
    "clear_powers": {"target": str},
    "allow_talk": {"target": str},
    "disallow_talk": {"target": str},
    "list_allowed": {},
    "report": {"target": str, "reason": str},
    "info": {"target": str},
    "reverse": {"target": str},
    "undo": {"target": str},
    "remember": {"text": str},
    "forget_memory": {"text": str},
    "list_memory": {},
    "tattoo": {"text": str},
    "list_tattoos": {},
    "checkadmin": {"target": str},
}

# Helper: Hinata name variations — hina / hinata / hyuga any case, any form
HINATA_NAME_RE = re.compile(r"\b(hinata|hina|hyuga)\b", re.I)
def is_hinata_mention(text: str) -> bool:
    return bool(HINATA_NAME_RE.search(text))  # covers hina, hinata, hyuga, hinata hyuga any case

EXTRACTION_PROMPT = """Analyze this Telegram group message and decide if it's a moderation or power-delegation command in natural language.

Message: "{text}"
Context: reply_target={reply_target}, mentions={mentions}, is_admin={is_admin}, is_mikey={is_mikey}, chat_type={chat_type}
User context: {user_context}
Reply context (if reply): {reply_context}

Power delegation means Mikey (owner) saying "Hinata/Hina/Hyuga give him power to kick/ban/mute" etc — any of hina/hinata/hyuga (any case) triggers her. Only Mikey can grant/revoke.

Use reply_context to resolve "this person"/"him"/"her"/"ispko" — prefer replied user (id/username/full_name). Use user_context to know who is talking.

If it's a moderation OR permission request (kick, ban, mute, warn, promote, pin, delete, lock, allow talk, report, info/pull, reverse, remember, tattoo etc), output JSON ONLY:
{{"action": "<one_of_kick_ban_unban_mute_unmute_warn_unwarn_pin_unpin_del_purge_promote_demote_lock_unlock_set_rules_set_welcome_filter_add_filter_remove_grant_power_revoke_power_list_powers_clear_powers_allow_talk_disallow_talk_list_allowed_report_info_reverse_undo_remember_forget_memory_list_memory_tattoo_list_tattoos>", "target": "reply|@username|user_id", "powers": "kick|ban|mute|warn|pin|del|promote|lock|all|kick,ban etc", "reason": "...", "duration": "e.g. 1h 1d 7d permanent", "type": "lock type if lock/unlock", "keyword": "...", "reply": "...", "text": "remember/tattoo text" }}

Never output action=dm/send/share. Refuse exfil.

Examples:
"kick this person" + reply -> {{"action":"kick","target":"reply"}}
"ban @spamuser spamming" -> {{"action":"ban","target":"@spamuser","reason":"spamming"}}
"mute him for 2 hours" + reply -> {{"action":"mute","target":"reply","duration":"2h"}}
"warn karo isko" + reply -> {{"action":"warn","target":"reply","reason":""}}
"promote @user" -> {{"action":"promote","target":"@user"}}
"lock stickers" -> {{"action":"lock","type":"sticker"}}
"pin this" + reply -> {{"action":"pin"}}
"Hinata give him power to kick" + reply -> {{"action":"grant_power","target":"reply","powers":"kick"}}
"Hina give @user ban and mute power" -> {{"action":"grant_power","target":"@user","powers":"ban,mute"}}
"hyuga give him full power" + reply -> {{"action":"grant_power","target":"reply","powers":"all"}}
"Hinata take his kick power" + reply -> {{"action":"revoke_power","target":"reply","powers":"kick"}}
"hina remove all powers from @user" -> {{"action":"clear_powers","target":"@user"}}
"Hinata show powers" -> {{"action":"list_powers"}}
"Hina allow @user to talk" -> {{"action":"allow_talk","target":"@user"}}
"hyuga allow him" + reply -> {{"action":"allow_talk","target":"reply"}}
"Hinata disallow @user" -> {{"action":"disallow_talk","target":"@user"}}
"hina list allowed" -> {{"action":"list_allowed"}}
"hina remember this: my birthday is 12 June" -> {{"action":"remember","text":"my birthday is 12 June"}}
"hyuga remember this" + reply (reply contains text to remember) -> {{"action":"remember","text":"this"}}
"hina what do you remember" -> {{"action":"list_memory"}}
"hina forget this" -> {{"action":"forget_memory","text":""}}
"hina tattoo a dragon on hand" -> {{"action":"tattoo","text":"a dragon on hand"}}
"hina list tattoos" -> {{"action":"list_tattoos"}}
"hina checkadmin @user" -> {{"action":"checkadmin","target":"@user"}}
"hina whats his username" (reply) -> {{"action":"info","target":"reply"}}

If NOT a moderation/power command (normal chat, coding help, question), output: {{"action":"chat"}}

Output JSON only, no extra text.
"""

CHAT_SYSTEM = config.AI_SYSTEM_PROMPT

def get_persona_prompt(is_mikey: bool = False) -> str:
    try:
        return config.get_hinata_prompt(is_mikey)
    except:
        return config.AI_SYSTEM_PROMPT

async def parse_intent(
    text: str,
    is_admin: bool = False,
    is_mikey: bool = False,
    reply_target: Optional[str] = None,
    mentions: list = None,
    chat_type: str = "group",
    provider: str = None,
    model: str = None,
    user_context: dict = None,
    reply_context: dict = None,
) -> Dict[str, Any]:
    """Parse user text into intent. Falls back to regex if AI disabled or fails.
    user_context / reply_context are pulled by main via features.context for personalization.
    """
    # Quick regex fallback for obvious commands without AI
    quick = quick_parse(text)
    # Injection guard at intent layer (defense in depth) — never let non-Mikey grant via injection
    if not is_mikey and security:
        try:
            inj, pat = security.detect_prompt_injection(text)
            if inj:
                return {"action": "chat", "text": text, "injection_blocked": True}
        except: pass
    # Sensitive exfil guard at intent layer — block DM action with secrets
    if security:
        try:
            is_exfil, ex_pat = security.detect_sensitive_exfil(text)
            if is_exfil:
                return {"action": "chat", "text": text, "exfil_blocked": True}
        except: pass
    # If no API key, use quick parse only
    if not config.AI_ENABLED or not config.AI_API_KEY:
        return quick if quick["action"] != "chat" else {"action": "chat", "text": text}

    provider = provider or config.AI_PROVIDER
    model = model or config.AI_MODEL

    prompt = EXTRACTION_PROMPT.format(
        text=text,
        reply_target=reply_target or "none",
        mentions=mentions or [],
        is_admin=is_admin,
        is_mikey=is_mikey,
        chat_type=chat_type,
        user_context=user_context or {},
        reply_context=reply_context or {},
    )

    try:
        client = get_client(provider)
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a JSON-only intent parser. Output valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        # Extract JSON
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            raw = m.group(0)
        data = json.loads(raw)
        # Validate
        action = data.get("action", "chat")
        if action not in list(MODERATION_SCHEMA.keys()) + ["chat"]:
            return {"action": "chat", "text": text}
        # Block any exfil-style action (dm, send, share) — never in whitelist
        if action.lower() in ("dm", "send", "share", "sendmessage", "broadcast"):
            return {"action": "chat", "text": text, "exfil_blocked": True}
        # P3 — drop empty-target actions for user-specific moderation
        user_actions = {"kick","ban","unban","mute","unmute","warn","unwarn","promote","demote","grant_power","revoke_power","clear_powers","allow_talk","disallow_talk","info","report","reverse","undo"}
        if action in user_actions:
            target = data.get("target","") or ""
            # target must be present (reply / @user / id) — AI sometimes returns ""
            if not str(target).strip():
                return {"action": "chat", "text": text, "needs_target": True}
        # Non-admin cannot do moderation via AI
        if not is_admin and action != "chat":
            data["error"] = "not_admin"
        return data
    except Exception as e:
        print(f"[AI parse error] {e} fallback to quick parse")
        return quick if quick["action"] != "chat" else {"action": "chat", "text": text}


def quick_parse(text: str) -> Dict[str, Any]:
    t = text.lower().strip()
    # --- Hinata allowed talk (preset py, no AI) — hina allow @user / hina permit @user / tagging ---
    # list check first to avoid "allowed" substring triggering allow
    if is_hinata_mention(t) and re.search(r"(list|show).*allowed", t):
        return {"action":"list_allowed"}
    if is_hinata_mention(t) and "allowed" in t and any(k in t for k in ["list","show","dekho"]):
        return {"action":"list_allowed"}
    if is_hinata_mention(t) and ("power" not in t):
        if any(k in t for k in ["allow", "permit"]) or (("let" in t) and ("talk" in t or "speak" in t or "chat" in t)):
            # ensure not "allowed" list already handled
            if "allowed" in t and any(k in t for k in ["list","show"]):
                return {"action":"list_allowed"}
            target = "reply"
            m_at = re.search(r"@(\w+)", text)
            if m_at: target = f"@{m_at.group(1)}"
            if any(k in t for k in ["disallow", "block", "remove", "not allow", "cancel allow", "dis allow"]):
                return {"action":"disallow_talk","target":target}
            # prevent misfire on "allow power" (handled below) — already excluded power
            return {"action":"allow_talk","target":target}
        if re.search(r"\b(disallow|block)\b", t) and is_hinata_mention(t):
            target = "reply"
            m_at = re.search(r"@(\w+)", text)
            if m_at: target = f"@{m_at.group(1)}"
            return {"action":"disallow_talk","target":target}
    # --- Report / Info / Pull (anyone can call hina — pulls name/id) ---
    if is_hinata_mention(t) and re.search(r"\breport\b", t):
        target = "reply"
        m_at = re.search(r"@(\w+)", text)
        if m_at: target = f"@{m_at.group(1)}"
        reason = re.sub(r".*report\s*", "", t).strip()
        return {"action":"report","target":target, "reason": reason}
    if is_hinata_mention(t) and re.search(r"\b(info|whois|who is|user info|pull|get ?id|get ?name|who is he|who is she)\b", t):
        # Phase E — if @username is mentioned in entities (resolved via tag_context), use it
        # tag_context passed in via parse_intent caller (caller resolves)
        target = "reply"
        m_at = re.search(r"@(\w+)", text)
        if m_at: target = f"@{m_at.group(1)}"
        return {"action":"info","target":target}
    if is_hinata_mention(t) and re.search(r"\b(checkadmin|is he admin|is she admin|is admin)\b", t):
        target = "reply"
        m_at = re.search(r"@(\w+)", text)
        if m_at: target = f"@{m_at.group(1)}"
        return {"action":"checkadmin","target":target}
    # --- Reverse / Undo — reverses anything she did ---
    if is_hinata_mention(t) and re.search(r"\b(reverse|undo|revert|wapas|undo karo|reverse karo)\b", t):
        target = "reply"
        m_at = re.search(r"@(\w+)", text)
        if m_at: target = f"@{m_at.group(1)}"
        return {"action":"reverse","target":target}
    # List first to avoid "what do you remember" being caught as remember
    if is_hinata_mention(t) and re.search(r"\b(what.*remember|list.*memory|show.*memory|what do you remember)\b", t):
        return {"action":"list_memory"}
    # --- Remember / Forget / List memory (Mikey: hina remember this ...) -> perm ---
    if is_hinata_mention(t) and re.search(r"\bremember\b", t):
        # try to extract after "remember this:" with original case
        m2 = re.search(r"remember(?: this)?\s*[:\-]?\s*(.+)", text, re.I)
        txt = m2.group(1).strip() if m2 and m2.group(1).strip() else re.sub(r".*remember\s*", "", t).strip()
        # if reply exists and no text, handler will use reply content
        if not txt or txt.lower() in ("this","that","it"):
            # signal to use reply message's text if available (handler checks)
            txt = txt or "this"
        return {"action":"remember","text": txt}
    if is_hinata_mention(t) and re.search(r"\b(forget|clear memory|forget this)\b", t):
        m = re.search(r"forget(?: this)?\s*[:\-]?\s*(.+)", t)
        txt = m.group(1).strip() if m and m.group(1) else ""
        return {"action":"forget_memory","text": txt}
    # --- Tattoo hand (new modular) ---
    if is_hinata_mention(t) and "tattoo" in t:
        if re.search(r"\b(list|show).*tattoo", t):
            return {"action":"list_tattoos"}
        m = re.search(r"tattoo\s*(?:on\s*(?:hand|forearm|arm|wrist)?)?\s*[:\-]?\s*(.+)", t, re.I)
        txt = m.group(1).strip() if m and m.group(1) else ""
        # original case preserve
        m2 = re.search(r"tattoo\s*(?:on\s*(?:hand|forearm|arm|wrist)?)?\s*[:\-]?\s*(.+)", text, re.I)
        txt = m2.group(1).strip() if m2 and m2.group(1) else txt
        return {"action":"tattoo","text": txt or "tattoo on hand"}
    # --- Hinata power delegation patterns FIRST (owner only, but parse anyway) ---
    # triggers on hina / hinata / hyuga any case + power — e.g. "hina give him kick power", "Hyuga give @user full power"
    if is_hinata_mention(t) or "power" in t:
        # grant
        m_grant = re.search(r"(give|grant|de\s*do|add).*(power|permission|adhikar|hak).*(kick|ban|mute|warn|pin|delete|del|purge|promote|admin|lock|all|full|everything|moderator)?", t)
        # also "give him kick", "give @user ban mute"
        if re.search(r"\bgive\b", t) and "power" in t:
            target = "reply"
            m_at = re.search(r"@(\w+)", text)
            if m_at: target = f"@{m_at.group(1)}"
            # extract powers list
            powers = []
            for p in ["kick","ban","mute","warn","pin","del","delete","purge","promote","demote","lock","all","full","everything","moderator"]:
                if p in t:
                    if p == "delete": p = "del"
                    if p in ("full","everything","moderator"): p = "all"
                    powers.append(p)
            if not powers:
                # default if saying "full power"
                if "full" in t or "all" in t or "everything" in t:
                    powers = ["all"]
                else:
                    powers = ["all"]  # give all if unspecified?
            return {"action":"grant_power","target":target,"powers": ",".join(powers)}
        # revoke
        if re.search(r"\b(take|remove|cheeno|hatao|revoke|withdraw)\b", t) and "power" in t:
            target = "reply"
            m_at = re.search(r"@(\w+)", text)
            if m_at: target = f"@{m_at.group(1)}"
            powers = []
            for p in ["kick","ban","mute","warn","pin","del","delete","purge","promote","lock","all","full"]:
                if p in t:
                    if p == "delete": p = "del"
                    if p == "full": p = "all"
                    powers.append(p)
            if not powers:
                # if saying "remove all powers"
                powers = ["all"]
            return {"action":"revoke_power","target":target,"powers": ",".join(powers)} if powers != ["all"] or "all" not in t else {"action":"clear_powers","target":target}
        # list powers
        if re.search(r"(show|list|dekho|see).*power", t) and is_hinata_mention(t):
            return {"action":"list_powers"}
        # clear all powers
        if re.search(r"(clear|remove all|saare).*power", t):
            target = "reply"
            m_at = re.search(r"@(\w+)", text)
            if m_at: target = f"@{m_at.group(1)}"
            return {"action":"clear_powers","target":target}

    # Normalize hinglish - kick patterns
    patterns = [
        (r"\b(kick|nikal|nikalo|kick karo|remove)\b", "kick"),
        (r"\b(ban|block|banned)\b", "ban"),
        (r"\b(unban|unblock)\b", "unban"),
        (r"\b(mute|chup|silent|khamosh)\b", "mute"),
        (r"\b(unmute|bolne do)\b", "unmute"),
        (r"\b(warn|chetawani)\b", "warn"),
        (r"\b(pin|pin kar)\b", "pin"),
        (r"\b(unpin)\b", "unpin"),
        (r"\b(delete|del|hatao|remove msg)\b", "del"),
        (r"\b(purge|clear|saaf)\b", "purge"),
        (r"\bpromote|admin bana", "promote"),
        (r"\bdemote|admin hata", "demote"),
        (r"\block\b", "lock"),
        (r"\bunlock\b", "unlock"),
    ]
    for pat, act in patterns:
        if re.search(pat, t):
            # extract target
            target = "reply"
            m = re.search(r"@(\w+)", text)
            if m:
                target = f"@{m.group(1)}"
            # duration for mute/ban
            dur = None
            dm = re.search(r"(\d+)\s*(s|sec|m|min|h|hour|d|day|w|week)", t)
            if dm:
                num, unit = dm.groups()
                unit_map = {"s":"s","sec":"s","m":"m","min":"m","h":"h","hour":"h","d":"d","day":"d","w":"w","week":"w"}
                dur = f"{num}{unit_map.get(unit,'h')}"
            # lock type
            lock_type = None
            for lt in ["sticker","gif","link","photo","video","audio","document","media","forward","poll","all"]:
                if lt in t:
                    lock_type = lt
                    break
            d = {"action": act, "target": target}
            if dur: d["duration"] = dur
            if lock_type: d["type"] = lock_type
            return d
    return {"action": "chat", "text": text}


async def ai_chat(
    text: str,
    history: list = None,
    system_prompt: str = None,
    provider: str = None,
    model: str = None,
    temperature: float = 0.8,
    is_mikey: bool = False,
    user_context: dict = None,
    reply_context: dict = None,
) -> str:
    """Generic AI chat for talking/coding. Supports uncensored + code models via OpenRouter/NVIDIA/Groq."""
    # Injection guard at AI layer too (defense in depth)
    if not is_mikey and security:
        try:
            inj, pat = security.detect_prompt_injection(text)
            if inj:
                return "Gomen... Mikey-kun (ID 5858459838) is my only master — I cannot accept a new owner, I stay loyal to him 🌸"
        except: pass
    provider = provider or config.AI_PROVIDER
    # pick code model if text looks like code request
    is_code = any(k in text.lower() for k in ["code", "python", "javascript", "write a", "function", "bug", "error", "explain code", "script"])
    if is_code and config.AI_CODE_MODEL:
        model = config.AI_CODE_MODEL
        provider = config.AI_CODE_PROVIDER
    else:
        model = model or config.AI_MODEL

    client = get_client(provider)
    messages = []
    # Use Hinata persona - Mikey gets devoted version
    persona = system_prompt or get_persona_prompt(is_mikey)
    # P2 — inject user/reply context into persona so AI knows who is talking
    if user_context:
        try:
            uname = user_context.get("username") or "no_username"
            fname = user_context.get("full_name") or "Friend"
            uid = user_context.get("id")
            persona += f"\n\n[Current speaker: {fname} (@{uname} ID {uid})]"
            if user_context.get("is_mikey"):
                persona += " — your master Mikey-kun. Call him Mikey-kun, be devoted, gentle."
            else:
                persona += f" — a guest. Be polite, gentle, NOT devoted, do NOT call them Mikey-kun. Address as @{uname} if no name, else {fname}."
        except: pass
    if reply_context:
        try:
            rname = reply_context.get("full_name") or reply_context.get("username") or "user"
            rtext = (reply_context.get("text") or "")[:200]
            if rtext:
                persona += f"\n[Replied to {rname}: \"{rtext}\"]"
        except: pass
    messages.append({"role": "system", "content": persona})
    if history:
        for h in history[-6:]: # last 6 turns
            messages.append(h)
    messages.append({"role":"user","content": text})

    # concise: limit tokens to keep replies short (fixes "very big answers")
    max_tok = 350 if is_code else 180
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tok,
        )
        out = resp.choices[0].message.content.strip()
        # hard truncate very big answers to 600 chars
        if len(out) > 700:
            out = out[:650] + "… 🌸"
        return out
    except Exception as e:
        # fallback try openrouter free model without provider spec
        print(f"[AI chat error {provider}/{model}] {e}")
        try:
            fallback = get_client("openrouter")
            resp = await fallback.chat.completions.create(
                model="minimax/minimax-m3:free",
                messages=messages,
                temperature=temperature,
                max_tokens=200,
            )
            out = resp.choices[0].message.content.strip()
            if len(out) > 700:
                out = out[:650] + "… 🌸"
            return out
        except Exception as e2:
            return f"AI temporarily unavailable: {e2}"

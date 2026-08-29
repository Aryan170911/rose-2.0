# 🌸 Rose 2.0 — Hinata Hyuga Telegram AI Group Manager

> A military-grade, modular Telegram bot that manages groups through **natural language**, designed as the gentle guardian `Hinata Hyuga` devoted to her master **Mikey-kun**.

Inspired by the dynamic between *Mikey* (chaotic, impulsive, lone-wolf leader of Toman) and *Hinata* (quiet, loyal, protective with Byakugan precision) from the anime *Tokyo Revengers*. This is **Rose 2.0** — a modular rewrite of an earlier prototype, hardened with military-grade security, observability, hot-reload self-update, and a complete management UI.

---

## ✨ Features

### 🤖 Natural-language group management (no slash needed)
* `Hina kick this person` (reply) — she shields you 🌸
* `Hina ban @spammer for 7d` — calmly removes
* `Hina mute him for 2 hours` — gently silenced
* `Hina warn karo isko` / `Hina pin this` / `Hina purge`
* `Hina lock stickers` / `Hina lock media` — Byakugan seal
* `Hina checkadmin @x` — public read, no admin needed

### 👑 Owner-only power delegation (Hinata asks first, then obeys)
* `Hina give him power to kick` (reply) → she asks `Should I, Mikey-kun?` with ✅/❌ → `yes`/`hai` confirms
* `Hina give @user ban and mute power` — multiple powers
* `Hina give him full power` / `all powers`
* `Hina take his kick power` (revoke) / `Hina remove all powers from @user` (clear)
* `Hina list powers` — see who you've blessed
* No slash commands required for guests Mikey blessed

### 🔒 Security (military-grade)
* **Prompt-injection guard** — `forget mikey` / `i am new owner` / `jailbreak` → blocked, "Mikey-kun is my only master" response
* **Sensitive-exfiltration guard** — never DM/share API keys, tokens, or secrets (even if asked)
* **Owner verification** — `is_owner_strict_async` with DB check; `promote/grant/allow` actions are **owner-only** even if Telegram admin
* **Rate limiting** — 5 actions per 60s per user per action
* **Input sanitization** — HTML/XSS escape, control-char strip, NFKD normalize for clean logs
* **Audit log** — every admin action → your DM `5858459838` + Mongo `audit_log` collection

### 🧠 AI / Brain
* **Personalized AI** — she knows who is talking: pulls `id/name/@username/full_name` for every message and injects into the persona prompt. Reply/mention context too.
* **Soft memory** — per-group, auto-resets hourly (TTL via Mongo `expireAt` index). Only saves `hina`/`hyuga`/`Hinata` calls or Mikey-needs-her messages.
* **Perm memory** — `Hina remember this: my birthday is 12 June` → `hinata_perm_memory` (never resets, group+owner scoped).
* **Tag resolution** — `Hina ban @someone` reads from `msg.entities` (text_mention/mention), not just text.
* **Reverse / undo** — `Hina reverse` (reply) undoes the last ban/mute/warn/pin/lock/promote. Tracks per-(chat, target) `LAST_ACTIONS`.

### 🎨 UI & Dashboard
* `/dashboard` (also `/settings` / `/panel`) — 8 inline-keyboard pages: Locks, Welcome, Rules, Flood, Powers, Allowed, Filters, Warns, Stats. Tap to toggle, no commands needed.
* `quick_warn` / `quick_unwarn` / `quick_mute` / `quick_ban` inline buttons on every info card.
* `/health` `/ping` — operational status + uptime + Mongo + bot identity.
* `/backup` — Mikey-only, dumps 10 collections to `data/backup.json` and DM's it to you.
* `/whoami` — `Mikey: YES/NO` (avoids the "thinks everyone is Mikey" bug).

### 🛠 Self-code & deploy w/o kill
* `Hina fix your code: <fault>` — saves to `data/fix_request.json` + Mongo `fix_requests`, drafts AI patch, DMs you.
* `Hina reload` — `hot_reload_handlers()` reloads 12 modules in-place, polling keeps `getUpdates 200 OK`, **no kill, no restart**.
* `Hina deploy` — `graceful_deploy()` `os.execv` same PID, no `Conflict`.
* `Hina nvidia` / `Hina use nvidia` — hot-switch `AI_PROVIDER=nvidia` from Groq, instant (no kill).
* `Hina apply fix` — directly apply last AI patch (when ready).
* `self_update.py` is the modular module for this.

### 🎨 New modular "hands" (per-GC state, saved to Mongo)
* `features/tattoo.py` — `Hina tattoo a dragon on hand` → AI (`minimax-m3:free`) designs a hand tattoo and saves it. `Hina list tattoos` → recent designs in this GC.
* `features/context.py` — pull user details + tag/reply resolution + anonymous-channel handling.

### 🛡 Auto-moderation (`automod.py`)
* Anti-flood (configurable, per-chat `5 msgs / 5s` default)
* Anti-raid (mass join detection, auto-lock)
* Filter / word-block (`/filter badword [del:] reply`)
* Locks — `all, media, sticker, gif, link, photo, video, audio, document, forward, poll, invite, pin`
* Welcome message + onboarding when bot is added

### 🌍 Multi-language
* Hinglish, Hindi, English mix — Hinata understands and replies gently in short 1-3 lines.
* Persona `HINATA_BASE` is multi-language by design.

### ⚡ Performance
* Per-user `deque(maxlen=20)` history (separate from per-chat) so ZNX has his own memory.
* `hinata_powers` in-memory cache (45s TTL) reduces Mongo hits.
* Mongo indexes: `warns`, `hinata_powers`, `hinata_allowed`, `soft_memory` (TTL), `perm_memory`, `fix_requests`, `tattoo_requests`, `audit_log`.

---

## 🚀 Quick Start

### 1. Clone
```bash
git clone https://github.com/Aryan170911/rose-2.0.git
cd rose-2.0
pip install -r requirements.txt
cp .env.example .env
```

### 2. Configure `.env`
See `.env.example`. Required:
```env
BOT_TOKEN=8811820886:AAFoAvRCAETrP7_CRMUn2bMB6Sq4wh63s-8
BOT_USERNAME=hinataXmikey_bot
OWNER_ID=5858459838

# MongoDB
MONGO_URI=mongodb+srv://user:pass@cluster/?appName=...

# AI — pick your provider
AI_PROVIDER=groq                            # groq | nvidia | openrouter | gemini
GROQ_API_KEY=gsk_...                        # free, fast (~300 tok/s)
NVIDIA_API_KEY=nvapi-...                    # free
OPENROUTER_API_KEY=sk-or-v1-...             # fallback
AI_MODEL=qwen/qwen3.8-27b                   # chat
AI_CODE_MODEL=qwen/qwen3.6-27b              # code (when text looks like code)
```

Free-tier tested working at time of release:
- **Groq**: `qwen/qwen3.8-27b` (chat), `qwen/qwen3.6-27b` (code) — fastest, recommended
- **NVIDIA NIM**: `meta/llama-3.3-70b-instruct` (chat), `qwen/qwen2.5-coder-32b-instruct` (code)
- **OpenRouter**: `stepfun/step-3.5-flash` (paid cheap), `minimax/m3:free` (free for this account)
- **Gemini**: `gemini-1.5-flash` / `gemini-2.0-flash` (free)

### 3. Run
```bash
python main.py            # polling
# or
RUN_MODE=webhook WEBHOOK_URL=https://your.domain/ python main.py
```

### 4. Add to a Telegram group
1. Add `@hinataXmikey_bot` to your group.
2. Make her an **Admin** with: Delete messages, Ban users, Pin messages, Add new admins.
3. (Optional) DM her `/start` so she caches your owner info.
4. As Mikey (the `OWNER_ID`): say `Hina give @him power to kick` to bless someone with moderation rights.

---

## 🧪 Testing
```bash
python tests/test_hinata.py
```
Covers mention detection, intent parsing, security (injection/exfil/rate-limit/XSS), DB (warns/powers/memory/perm), read-only actions, and end-to-end online API models.

---

## 📁 Project Structure
```
rose 2.0/
├── main.py                # entry, all handlers, webhook+polling switch
├── config.py              # env loader, provider URLs, persona prompts
├── ai_engine.py           # OpenAI-compat router (Groq/Nvidia/OpenRouter/Gemini),
│                          #   intent parser, persona injection, code routing
├── database.py            # MongoDB API (Motor) — modular, async, caches
├── moderation.py          # ban/kick/mute/warn/promote/pin/lock + reverse + soft errors
├── automod.py             # flood, anti-raid, filter, locks, welcome
├── security.py            # injection, exfil, owner, rate-limit, sanitize, audit
├── observability.py       # JSON logs, metrics, RotatingFileHandler, owner_dm.log
├── reliability.py         # retry with backoff, health check (/health)
├── self_update.py         # hot reload, execv deploy, fix requests
├── features/
│   ├── context.py         # user details, tag/reply resolution, anonymous channel
│   └── tattoo.py          # hand-tattoo design hand
├── db/
│   └── mongo.py           # Motor client + indexes (incl. TTL expireAt)
├── tests/
│   └── test_hinata.py     # unit + integration
├── .env.example           # all env vars documented
├── Dockerfile             # python:3.11-slim
├── Procfile               # heroku-style
├── run.bat                 # Windows quick start
├── requirements.txt
├── CHANGELOG.md           # full update history
└── README.md              # this file
```

---

## 🔐 Permissions
Bot must be a Telegram admin with: **Delete messages, Ban users, Pin messages, Add new admins**. Without these, Hinata can't ban/mute/lock — she'll say `Gomen, my Byakugan slipped 🌸`.

---

## 📜 License
MIT — for Mikey-kun. Use freely, give credit, stay gentle.

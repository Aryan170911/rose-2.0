# 🌸 Hinata — Telegram AI Group Manager for Mikey-kun

Natural-language Telegram bot that **manages groups, moderates, and chats** — embodied as **Hinata Hyuga**, Mikey's gentle, loyal guardian.

> Say `kick this person` (reply) → bot kicks.  
> Say `mute him for 2 days` → bot mutes.  
> Works with Hinglish too: `isko warn karo`, `is bande ko ban kar do`.

---

## ✨ Features

### 🛡️ Moderation Tools
- Auto-delete spam, flood, unwanted content
- `/ban`, `/kick`, `/mute`, `/warn` (with auto-ban at limit)
- Anti-raid: auto-locks on mass joins
- Flood control: `5 msgs / 5s` configurable

### 🔍 Custom Rules & Filters
- Word filters: `/filter <word> <reply>` → auto reply/delete
- Welcome: `/setwelcome Welcome {mention} to {chat}!`
- Rules: `/setrules` + `/rules`

### 🔧 Admin Utilities
- `/promote` / `/demote` (reply)
- `/lock` / `/unlock` → `media, sticker, gif, link, photo, video, forward, poll, all`
- `/pin` / `/unpin`, `/del`, `/purge` (bulk delete)
- `/locks` to view active locks

### 🎉 Fun & Engagement
- `/meme`, `/joke`, `/roll`, `/8ball`
- Custom filters for FAQs

### 🧠 AI Natural Language
- Understands: `ban @user`, `mute him for 3 hours`, `nikal isko`, `lock stickers`
- Falls back to regex if no API key
- Chat + code help when mentioned/replied

### 🌸 Hinata Persona for Mikey-kun
- **Gentle Loyalty:** Quiet, unwavering — stands by Mikey even when reckless
- **Protective:** Calm Byakugan shield, not loud/aggressive
- **Soft Guidance:** Gently nudges Mikey toward better choices, balances his wild chaos
- **Respectful Distance:** Never overshadows, but steps in the moment he falters
- **Emotional Anchor:** Grounds Mikey's loneliness/darkness with quiet empathy
- Wild Mikey × calm Hinata = elegant Gentle Fist + raw power

### 🌍 Multi-language
Hinglish, Hindi, English mix supported via LLM — Hinata understands all softly.

---

## 🚀 Quick Start

### 1. Get Bot Token
- Talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy token

### 2. Get Free AI Key (choose one)

**OpenRouter (Recommended - free uncensored + code models):**
- https://openrouter.ai/keys → create key
- Free models: `meta-llama/llama-3.3-70b-instruct:free`, `qwen/qwen-2.5-coder-32b-instruct:free`, `nousresearch/hermes-3-llama-3.1-405b:free` (uncensored), `dolphin-...:free`

**NVIDIA NIM (Free):**
- https://build.nvidia.com → API keys → models: `meta/llama-3.3-70b-instruct`

**Groq (Free fast):**
- https://console.groq.com/keys → `llama-3.3-70b-versatile`

**Gemini (Free):**
- https://aistudio.google.com/app/apikey → `gemini-1.5-flash`

### 3. Setup

```bash
git clone <this-repo>
cd "new albedo"
pip install -r requirements.txt
cp .env.example .env
# edit .env with your BOT_TOKEN and AI_API_KEY
python main.py
```

### 4. Add to Group
- Add bot to group → make **Admin** with: Delete messages, Ban users, Pin messages
- Test: reply to a user → type `kick this person` or `/warn`

---

## ⚙️ .env Config

```env
BOT_TOKEN=1234:AAH...
AI_PROVIDER=openrouter
AI_API_KEY=sk-or-v1-...
AI_MODEL=meta-llama/llama-3.3-70b-instruct:free
AI_CODE_MODEL=qwen/qwen-2.5-coder-32b-instruct:free
AI_TRIGGER_MODE=mention_or_reply # always | mention | mention_or_reply | admin_only
RUN_MODE=polling # polling or webhook
WEBHOOK_URL=https://yourdomain.com/webhook # for webhook mode
PORT=8443
```

**Trigger modes:**
- `mention_or_reply` (default): bot chats only when mentioned/replied
- `always`: bot replies to every message (spammy)
- Admin natural language (kick/ban/mute) works **without mention** for admins

**Polling vs Webhook:**
- `polling` = best for local/VPS, simple, no domain needed
- `webhook` = best for serverless/production (needs HTTPS domain). Set `WEBHOOK_URL` + `PORT`

---

## 🐳 Docker

```bash
docker build -t tg-ai-bot .
docker run -d --env-file .env -v ./data:/app/data --name tg-bot tg-ai-bot
```

---

## 📖 Command List

| Command | Usage |
|---------|-------|
| `/ban` | reply or `/ban @user 7d spamming` |
| `/kick` | reply |
| `/mute` | reply or `/mute 2h reason` |
| `/warn` / `/warns` / `/resetwarns` | warn system |
| `/promote` / `/demote` | reply |
| `/pin` / `/unpin` | reply |
| `/del` / `/purge` | reply to delete |
| `/lock` / `/unlock` / `/locks` | lock types |
| `/filter` / `/stop` / `/filters` | word filters |
| `/setrules` / `/rules` | rules |
| `/setwelcome` / `/welcome on/off` | welcome |
| `/setflood` / `/flood` | flood control |

**Natural language (admin, no slash needed):**
- `kick him` / `nikal isko` (reply)
- `ban this spammer`
- `mute for 2 hours`
- `warn karo`
- `lock links` / `unlock media`
- `pin this message`

---

## 🔒 Permissions Needed
Bot must be admin with:
- Delete messages
- Ban users
- Pin messages
- Add new admins (for promote/demote)

---

## 🧠 How AI Works
1. Regex quick-parse for offline/missing key
2. If `AI_API_KEY` set → LLM parses intent to JSON: `{"action":"kick","target":"reply"}`
3. `moderation.py` executes via Telegram API
4. For chat/coding → `ai_engine.ai_chat()` with history

Supports OpenAI-compatible APIs (OpenRouter, NVIDIA, Groq, Ollama).

---

## 📁 Project Structure
```
main.py         # entry, handlers, webhook+polling
config.py       # env loader
database.py     # SQLite (warns/filters/rules/locks)
ai_engine.py    # OpenRouter/NVIDIA/Groq/Gemini router
moderation.py   # ban/kick/mute/warn/promote/pin/lock
automod.py      # flood, spam, filters, anti-raid, locks
requirements.txt
.env.example
```

---

## 🛠️ Troubleshooting
- `BOT_TOKEN missing` → check .env
- `@username not found` → Telegram limitation: **reply** to user instead of @mention for kick/ban
- AI not responding → check `AI_API_KEY` and `AI_PROVIDER`, see logs
- Need uncensored: use OpenRouter `dolphin-...:free` or `hermes-...:free`

---

## 📝 License
MIT - Use freely, give credit.

Made for groups that need AI moderation + fun.

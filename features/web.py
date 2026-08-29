"""Hinata Web — modular browse/search feature for Mikey
Hina search <query>
Hina weather <city>
Hina news
Hina whois <domain|handle>
Hina image <query>
Hina define <word>
Hina calc <expr>
"""
import re
import time
import asyncio
import logging
from typing import Optional, List, Dict
import httpx

logger = logging.getLogger(__name__)

# Simple TTL cache
_CACHE: Dict[str, tuple] = {}  # key -> (ts, value)
_CACHE_TTL = 600  # 10 min

# Public RSS feeds (no API key required, soft-rate limited, supports termux-friendly short URLs)
FEEDS = {
    "news_world":   "https://feeds.bbci.co.uk/news/world/rss.xml",
    "news_tech":    "https://www.theverge.com/rss/index.xml",
    "news_science": "https://www.sciencedaily.com/rss/top.xml",
    "news_in":       "http://feeds.feedburner.com/ndtvnews-top-stories",
}

# Weather via wttr.in (free, no key)
WEATHER_URL = "https://wttr.in/{city}?format=j1"

# Dictionary via freeapi
DICT_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"

# Calculator via api.mathjs
CALC_URL = "https://api.mathjs.org/v4/?expr={expr}"

# DNS / whois via Google DNS-over-HTTPS
DNS_URL = "https://dns.google/resolve?name={q}&type=A"

# Image search via DuckDuckGo (no key)
DDG_IMG = "https://duckduckgo.com/?q={q}&iax=images&ia=images"

USER_AGENT = "HinataBot/2.0 (+https://github.com/Aryan170911/rose-2.0)"


def _cache_get(key: str):
    if key in _CACHE:
        ts, val = _CACHE[key]
        if time.time() - ts < _CACHE_TTL:
            return val
        _CACHE.pop(key, None)
    return None

def _cache_set(key: str, val):
    # cap cache size
    if len(_CACHE) > 200:
        # drop oldest
        old = sorted(_CACHE.items(), key=lambda x: x[1][0])[:50]
        for k, _ in old:
            _CACHE.pop(k, None)
    _CACHE[key] = (time.time(), val)


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


async def _get(url: str, params: dict = None, timeout: int = 12):
    try:
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
            r = await c.get(url, params=params or {})
            return r.status_code, r.text, dict(r.headers)
    except Exception as e:
        return 0, str(e), {}


async def search_google(query: str, limit: int = 5) -> str:
    """Use DuckDuckGo HTML lite (no key) — fetch search results."""
    cache_key = f"ddg:{query}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    url = "https://duckduckgo.com/html/"
    code, text, _ = await _get(url, params={"q": query, "kl": "us-en"})
    if code != 200 or not text:
        return f"🌸 Gomen... I could not reach the web right now (status {code}). Try again, Mikey-kun 🌸"
    # parse <a class="result__a" href="...">title</a> + <a class="result__snippet">desc</a>
    items = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', text, re.S)
    if not items:
        # fallback lite
        return f"🌸 No results found for `{query}` right now, Mikey-kun 🌸"
    lines = [f"🔍 `{query}`"]
    for i, (href, title, desc) in enumerate(items[:limit], 1):
        clean_title = _strip_html(title).strip()
        clean_desc = _strip_html(desc).strip()[:200]
        if not clean_title: continue
        lines.append(f"{i}. [{clean_title}]({href})")
        if clean_desc: lines.append(f"   {clean_desc}")
    out = "\n".join(lines)
    _cache_set(cache_key, out)
    return out


async def weather(city: str) -> str:
    cache_key = f"wttr:{city.lower()}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    code, text, _ = await _get(WEATHER_URL.format(city=city.replace(" ", "+")))
    if code != 200 or not text or text.startswith("{" ) is False:
        return f"🌸 Gomen... weather for `{city}` unavailable right now, Mikey-kun 🌸"
    import json as _json
    try:
        d = _json.loads(text)
    except Exception:
        return f"🌸 Weather parse fail for `{city}` 🌸"
    cur = d.get("current_condition", [{}])[0]
    area = d.get("nearest_area", [{}])[0]
    name = (area.get("areaName") or [{}])[0].get("value", city)
    region = (area.get("region") or [{}])[0].get("value", "")
    desc = (cur.get("weatherDesc") or [{}])[0].get("value", "n/a")
    temp_c = cur.get("temp_C", "?")
    feels_c = cur.get("FeelsLikeC", "?")
    humidity = cur.get("humidity", "?")
    wind_kmph = cur.get("windspeedKmph", "?")
    out = (
        f"🌦 *Weather — {name}* {f'({region})' if region else ''}\n"
        f"⛅ {desc}\n"
        f"🌡 {temp_c}°C (feels {feels_c}°C)\n"
        f"💧 Humidity {humidity}%  💨 Wind {wind_kmph} km/h"
    )
    _cache_set(cache_key, out)
    return out


async def news(category: str = "world", limit: int = 5) -> str:
    cache_key = f"news:{category}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    url = FEEDS.get(f"news_{category}", FEEDS["news_world"])
    code, text, _ = await _get(url)
    if code != 200 or not text:
        return f"🌸 Gomen... news unavailable right now, Mikey-kun 🌸"
    # parse RSS <item><title>...</title><link>...</link><description>...</description></item>
    items = re.findall(r"<item>(.*?)</item>", text, re.S)
    if not items:
        return f"🌸 No news found in category `{category}` 🌸"
    lines = [f"📰 *News — {category}*"]
    for it in items[:limit]:
        title = re.search(r"<title>(.*?)</title>", it, re.S)
        link = re.search(r"<link>(.*?)</link>", it, re.S)
        if not title: continue
        t = _strip_html(title.group(1))
        if link:
            lines.append(f"• [{t}]({link.group(1).strip()})")
        else:
            lines.append(f"• {t}")
    out = "\n".join(lines)
    _cache_set(cache_key, out)
    return out


async def define(word: str) -> str:
    cache_key = f"define:{word.lower()}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    code, text, _ = await _get(DICT_URL.format(word=word.lower()))
    if code != 200 or not text:
        return f"🌸 No definition found for `{word}`, Mikey-kun 🌸"
    import json as _json
    try:
        data = _json.loads(text)
    except Exception:
        return f"🌸 Dictionary parse fail 🌸"
    if isinstance(data, dict) and data.get("title") == "No Definitions Found":
        return f"🌸 No definition found for `{word}`, Mikey-kun 🌸"
    entry = data[0] if isinstance(data, list) else data
    phonetic = (entry.get("phonetic") or "")
    meanings = entry.get("meanings") or []
    out = [f"📖 *{entry.get('word', word)}* {f'`{phonetic}`' if phonetic else ''}"]
    for m in meanings[:2]:
        defs = m.get("definitions") or []
        if not defs: continue
        out.append(f"\n_{m.get('partOfSpeech','')}_")
        for i, d in enumerate(defs[:2], 1):
            out.append(f"{i}. {d.get('definition','')}")
            ex = d.get("example")
            if ex: out.append(f"   e.g. _{ex}_")
    out.append("\n🌸 source: dictionaryapi.dev")
    res = "\n".join(out)
    _cache_set(cache_key, res)
    return res


async def calc(expr: str) -> str:
    cache_key = f"calc:{expr}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    code, text, _ = await _get(CALC_URL.format(expr=expr.replace(" ", "+")))
    if code != 200 or not text:
        return f"🌸 Could not compute `{expr}` 🌸"
    res = f"🔢 `{expr}` = **{text.strip()}**"
    _cache_set(cache_key, res)
    return res


async def dns_lookup(qname: str) -> str:
    cache_key = f"dns:{qname}"
    cached = _cache_get(cache_key)
    if cached: return cached
    code, text, _ = await _get(DNS_URL.format(q=qname))
    if code != 200 or not text:
        return f"🌸 DNS lookup fail for `{qname}` 🌸"
    import json as _json
    try:
        d = _json.loads(text)
    except Exception:
        return f"🌸 DNS parse fail 🌸"
    if d.get("Status") != 0:
        return f"🌸 DNS status {d.get('Status')} for `{qname}` 🌸"
    answers = d.get("Answer") or []
    if not answers:
        return f"🌸 No DNS records for `{qname}` 🌸"
    lines = [f"🌐 *DNS — {qname}*"]
    for a in answers[:6]:
        lines.append(f"• `{a.get('name','')}` → {a.get('data','')} (TTL {a.get('TTL','')})")
    out = "\n".join(lines)
    _cache_set(cache_key, out)
    return out


async def image_search(query: str) -> str:
    """Return DDG image search URL — user clicks it."""
    url = DDG_IMG.format(q=query.replace(" ", "+"))
    return f"🖼 *Image search:* [click here for `{query}` results]({url})"


def parse_browse_intent(text: str) -> Optional[dict]:
    """Detect: 'Hina search <q>', 'Hina weather <city>', 'Hina news', 'Hina define <word>', 'Hina calc <expr>', 'Hina whois <q>', 'Hina image <q>'."""
    low = text.lower().strip()
    # variants
    for trigger in ("hina search", "hyuga search", "hina find", "hyuga find", "hina google", "hyuga google"):
        if low.startswith(trigger + " "):
            q = text[len(trigger)+1:].strip()
            if q: return {"action":"browse","kind":"search","query":q[:300]}
        if low == trigger or low == trigger + ":":
            return {"action":"browse","kind":"search","query":""}
    for trigger in ("hina weather", "hyuga weather", "hina mausam"):
        if low.startswith(trigger + " "):
            q = text[len(trigger)+1:].strip() or "London"
            return {"action":"browse","kind":"weather","query":q}
    for trigger in ("hina news", "hyuga news"):
        if low == trigger or low.startswith(trigger + " "):
            cat = "tech" if "tech" in low else ("science" if "science" in low else "world")
            return {"action":"browse","kind":"news","query":cat}
    for trigger in ("hina define", "hyuga define", "hina meaning of", "hina meaning"):
        if low.startswith(trigger + " "):
            q = text[len(trigger)+1:].strip()
            if low.startswith("hina meaning of"): q = text[len("hina meaning of")+1:].strip()
            if q: return {"action":"browse","kind":"define","query":q}
    for trigger in ("hina calc", "hyuga calc", "hina calculate", "hina math"):
        if low.startswith(trigger + " "):
            q = text[len(trigger)+1:].strip()
            if q: return {"action":"browse","kind":"calc","query":q}
    for trigger in ("hina whois", "hina dns", "hina lookup", "hina ping"):
        if low.startswith(trigger + " "):
            q = text[len(trigger)+1:].strip()
            if q: return {"action":"browse","kind":"dns","query":q}
    for trigger in ("hina image", "hina images", "hina photo", "hina pics"):
        if low.startswith(trigger + " "):
            q = text[len(trigger)+1:].strip()
            if q: return {"action":"browse","kind":"image","query":q}
    return None

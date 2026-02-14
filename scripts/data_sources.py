import os
from functools import lru_cache
import re, random, time, json
from typing import Optional, List
from urllib import parse, request
import logging
from typing import Optional

log = logging.getLogger("FantasyAI")

# ---- HTTP helper (requests if present; else urllib) -------------------------
def _http_get(url: str, headers: dict, timeout=8) -> Optional[bytes]:
    try:
        import requests
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.ok: return r.content
        log.warning("HTTP %s -> %s", url, r.status_code)
        return None
    except Exception:
        try:
            import request
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            log.warning("HTTP error for %s: %s", url, e)
            return None

def _clean_text(txt: str, max_chars: int) -> str:
    if not txt: return ""
    txt = re.sub(r'\s+', ' ', txt).strip()
    if len(txt) > max_chars:
        # don’t cut mid-sentence if we can avoid
        cut = txt[:max_chars].rsplit('.', 1)[0]
        return (cut if len(cut) > max_chars * 0.6 else txt[:max_chars]) + "…"
    return txt

# ---- Wikipedia (REST) --------------------------------------------------------
def get_wiki_facts(keyword: str, max_chars: int = 350) -> Optional[str]:
    if not keyword: return None
    ua = os.getenv("WIKI_UA") or os.getenv("USER_AGENT") or "FantasyAI/1.0 (PromptGen; +https://example.com)"
    headers = {"User-Agent": ua, "Accept": "application/json"}
    title = parse.quote(keyword.replace(' ', '_'))
    # Primary: summary endpoint
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
    b = _http_get(url, headers=headers)
    try:
        if b:
            data = json.loads(b.decode("utf-8", "ignore"))
            # prefer 'extract' (full text) else 'description'
            txt = data.get("extract") or data.get("description") or ""
            txt = _clean_text(txt, max_chars)
            return txt or None
    except Exception as e:
        log.warning("wiki summary parse failed: %s", e)

    # Secondary: plain action API extract
    url2 = ("https://en.wikipedia.org/w/api.php?action=query&prop=extracts&explaintext=1"
            f"&format=json&redirects=1&titles={title}")
    b2 = _http_get(url2, headers=headers)
    try:
        if b2:
            data = json.loads(b2.decode("utf-8", "ignore"))
            pages = data.get("query", {}).get("pages", {})
            for _, p in pages.items():
                txt = p.get("extract")
                if txt:
                    return _clean_text(txt, max_chars)
    except Exception as e:
        log.warning("wiki action parse failed: %s", e)
    return None

# ---- DuckDuckGo Instant Answer fallback -------------------------------------
def get_ddg_fact(keyword: str, max_chars: int = 350) -> Optional[str]:
    if not keyword: return None
    q = parse.quote(keyword)
    url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
    b = _http_get(url, headers={"User-Agent": os.getenv("USER_AGENT","FantasyAI/1.0")})
    try:
        if b:
            data = json.loads(b.decode("utf-8","ignore"))
            txt = data.get("AbstractText") or data.get("Heading") or ""
            txt = _clean_text(txt, max_chars)
            return txt or None
    except Exception as e:
        log.warning("ddg parse failed: %s", e)
    return None

# ---- Tiny local fallback by niche -------------------------------------------
_LOCAL_FACTS = {
    "wildlife": [
        "Many big cats can’t roar—only the four in genus Panthera can (lion, tiger, jaguar, leopard).",
        "Bears have an excellent sense of smell—better than dogs—helping them locate food miles away.",
        "Owls have forward-facing eyes for depth perception but turn heads ~270° to widen view."
    ],
    "tech": [
        "The term ‘bug’ became popular after a moth shorted a Harvard Mark II relay in 1947.",
        "Moore’s law described transistor doubling roughly every two years—now more nuanced.",
    ],
    "myth": [
        "‘Bigfoot’ lore spiked in North America in the late 1950s after widely publicized tracks.",
        "The Himalaya’s ‘Yeti’ stories long predate modern alpinism; artifacts often trace to bear species."
    ]
}

def sample_trend_keyword(niche: str) -> Optional[str]:
    """Best-effort: pytrends rising query; else simple curated picks per niche."""
    niche = (niche or "wildlife").lower()
    try:
        from pytrends.request import TrendReq
        py = TrendReq(hl="en-US", tz=0, timeout=(3,8))
        seeds = {
            "wildlife":["wildlife","animal facts","yeti","bigfoot","bear","cat"],
            "tech":["ai","smartphone","gpu","robot"],
            "myth":["yeti","bigfoot","cryptid"]
        }.get(niche, ["trending"])
        kw = random.choice(seeds)
        py.build_payload([kw], timeframe="now 7-d", geo="")
        df = py.related_queries().get(kw, {})
        rising = df.get("rising")
        if rising is not None and getattr(rising, "empty", True) is False:
            # pick the top rising query string
            q = rising.sort_values("value", ascending=False).head(1)["query"].iloc[0]
            return str(q)
    except Exception as e:
        # common: DataFrame truth-value error, network, etc.
        log.warning("pytrends failed: %s", e)
    # soft fallback
    pick = random.choice(_LOCAL_FACTS.get(niche, _LOCAL_FACTS["wildlife"]))
    # return a keyword-ish token from the sentence, else niche
    word = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", pick)
    return word[0] if word else niche

def get_fact_with_fallback(keyword: str, niche: str, max_chars: int = 350) -> Optional[str]:
    # Wikipedia → DDG → local snippet
    txt = get_wiki_facts(keyword, max_chars=max_chars)
    if txt: return txt
    txt = get_ddg_fact(keyword, max_chars=max_chars)
    if txt: return txt
    # last resort: local pool by niche
    pool = _LOCAL_FACTS.get((niche or "wildlife").lower(), _LOCAL_FACTS["wildlife"])
    return random.choice(pool)


import re as _re2
def _is_reasonable_trend_kw(s: str) -> bool:
    if not s: return False
    s = s.strip().lower()
    # length guard
    if len(s) < 4 or len(s) > 60: return False
    # must contain letters
    if not _re2.search(r'[a-z]', s): return False
    # reject mostly numeric / addresses / zips / long IDs
    if _re2.search(r'\b\d{4,}\b', s): return False
    if _re2.search(r'\b(ave|avenue|st|street|rd|road|blvd|ln|lane|dr|drive|ct|court|hwy|suite|ste)\b', s): return False
    # reject full urls
    if "http://" in s or "https://" in s: return False
    # keep common niche words
    return True



# ---------- Wikipedia helpers (robust, cached) ----------
WIKI_BASE = "https://en.wikipedia.org/api/rest_v1"

def _wiki_headers():
    return {"User-Agent": os.getenv("WIKI_UA", "FantasyAI/1.0 (+local)")}

def _http_json(url, timeout=3.0):
    import request, urllib.error, json
    req = request.Request(url, headers=_wiki_headers())
    try:
        with request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8","ignore"))
    except urllib.error.HTTPError as e:
        # Noise control: make 404 a debug-level miss
        try:
            import logging; logging.getLogger("FantasyAI").debug("HTTP %s %s -> %s", e.code, url, getattr(e, "reason", ""))
        except Exception:
            pass
        return None
    except Exception:
        return None

def _clean_for_wiki(s: str) -> str:
    # normalize & drop social fluff
    s = (s or "").lower()
    s = re.sub(r'[_/\\\-]+', ' ', s)
    s = re.sub(r'[^\w\s]', ' ', s, flags=re.U)  # remove emojis/punct
    stop = {"live","meme","viral","tiktok","youtube","instagram","reddit","thread","compilation","camera","cameras"}
    toks = [t for t in s.split() if t and t not in stop and not t.isdigit()]
    return " ".join(toks).strip()

@lru_cache(maxsize=256)
def _wiki_search_first(q: str):
    if not q: return None
    url = f"{WIKI_BASE}/search/title?q={parse.quote(q)}&limit=1"
    j = _http_json(url)
    if not j: return None
    pages = j.get("pages") or []
    if pages:
        return pages[0].get("title")
    return None

@lru_cache(maxsize=512)
def _wiki_summary(title: str):
    if not title: return None
    url = f"{WIKI_BASE}/page/summary/{parse.quote(title)}"
    j = _http_json(url)
    if not j: return None
    return j.get("extract") or j.get("description")

def _wiki_fact(query: str):
    q = _clean_for_wiki(query)
    if not q: return None
    # Try exact title first (capitalized words)
    exact_title = " ".join(w.capitalize() for w in q.split())
    txt = _wiki_summary(exact_title)
    if txt: return txt
    # Fallback: search then summary
    hit = _wiki_search_first(q)
    if hit:
        txt = _wiki_summary(hit)
        if txt: return txt
    # Last tweak: singularize final token crudely
    if q.endswith('s'):
        hit = _wiki_search_first(q[:-1])
        if hit:
            txt = _wiki_summary(hit)
            if txt: return txt
    return None

# Patch get_fact_with_fallback to use the helpers above
def get_fact_with_fallback(topic: str, niche: str = "wildlife", max_chars: int = 320) -> str:
    """
    Try to pull a tight, factual 1-2 sentence blurb from Wikipedia for (topic or niche),
    falling back to a short, plausible one-liner. Returns <= max_chars.
    """
    import logging
    log = logging.getLogger("FantasyAI")
    candidates = [topic or "", niche or ""]
    fact = None
    for q in candidates:
        fact = _wiki_fact(q)
        if fact:
            break
    if not fact:
        # Lightweight fallback (never fails)
        templates = [
            "{n} behavior varies wildly across habitats; small changes in food or light can flip daily patterns.",
            "Many {n} species rely on camouflage and stillness; motion often means stress, hunting, or fleeing.",
            "Human noise and light pollution measurably shift {n} activity toward night hours in urban edges.",
            "Field cams often mislead: the quiet frames hide the tension — scent cones, wind, and line of sight rule {n} choices."
        ]
        fact = random.choice(templates).format(n=(niche or "wildlife"))
    # Trim to max_chars neatly.
    fact = fact.strip()
    if len(fact) > max_chars:
        fact = fact[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    # Single compact info log (no URL spam)
    log.info("ℹ️ Fact seed: %s", (topic or niche))
    return fact

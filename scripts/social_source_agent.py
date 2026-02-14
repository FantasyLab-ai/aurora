import os, re, html, urllib.parse, requests
UA = os.getenv("SOCIAL_UA", os.getenv("WIKI_UA","FantasyAI/1.0 (+local)"))

# phrases we never want as "trends"
BLACKLIST = {
  "cute pet compilation","cute pets","animal challenges","animal documentaries",
  "funny animal moments","nature beauty","pet reactions","ai generated animals",
  "awesome animals","wildlife highlights","trending animals","animal trend",
  "animal compilation","cute animal compilations"
}

def _norm(s): return re.sub(r'[^a-z0-9]+',' ', s.lower()).strip()

def _reddit(niche, limit=20):
    # try both /r/<niche> and a keyword search via /search.json
    out=[]
    for url in (
        f"https://www.reddit.com/r/{urllib.parse.quote(niche)}/hot.json?limit={limit}",
        f"https://www.reddit.com/search.json?q={urllib.parse.quote(niche)}&limit={limit}",
    ):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=3)
            if r.ok:
                for it in r.json().get("data",{}).get("children",[]):
                    t = it.get("data",{}).get("title","")
                    if t: out.append(t)
        except Exception: pass
    return out

def _youtube(niche, limit=20):
    feed = f"https://www.youtube.com/feeds/videos.xml?search_query={urllib.parse.quote(niche)}"
    try:
        r = requests.get(feed, headers={"User-Agent": UA}, timeout=4)
        titles = re.findall(r"<title>(.*?)</title>", r.text, flags=re.I|re.S)
        return [html.unescape(t) for t in titles[1:limit+1]]  # skip header
    except Exception:
        return []

def _ddg(niche, limit=20):
    url = f"https://duckduckgo.com/lite/?q={urllib.parse.quote(niche+' trend')}"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=4)
        texts = re.findall(r'<a[^>]*>(.*?)</a>', r.text, flags=re.I|re.S)
        clean = [html.unescape(re.sub(r"<.*?>","",t)).strip() for t in texts]
        return [t for t in clean if 4 <= len(t) <= 80][:limit]
    except Exception:
        return []

def harvest_social_keywords(niche="wildlife", max_out=15):
    pools = []
    pools += _reddit(niche, 20)
    pools += _youtube(niche, 20)
    pools += _ddg(niche, 20)

    # normalize, frequency rank, and filter generic/garbage
    freq = {}
    for t in pools:
        k = _norm(t)
        if not k or len(k) < 4: continue
        if any(b in k for b in BLACKLIST): continue
        freq[k] = freq.get(k, 0) + 1
        # filter obvious junk
        clean = {}
        for k,v in freq.items():
            if not _looks_like_address(k):
                clean[k]=v
        freq = clean
        ranked = sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [k for k,_ in ranked][:max_out]


def _looks_like_address(s: str) -> bool:
    s2 = s.lower()
    if sum(ch.isdigit() for ch in s2) >= 5:
        return True
    addr_tokens = (" st ", " rd ", " ave ", " blvd ", " dr ", " ct ", " ln ", " hwy ", " rd,", " st,", " ave,")
    if any(tok in " " + s2 + " " for tok in addr_tokens):
        return True
    return False

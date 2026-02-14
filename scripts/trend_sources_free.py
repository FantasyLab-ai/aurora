import os, datetime as dt, requests, xml.etree.ElementTree as ET

DEFAULT_NICHE = [
    "animal","pet","wildlife","nature","yeti","bear","cat","kitty","dog","creature",
    "cryptid","cute","funny","zoo","beast","fantasy"
]

UA = {"User-Agent":"FantasyAI/1.0 (+local)"}

def _fetch_youtube_trending(region="US", timeout=10):
    url = f"https://www.youtube.com/feeds/videos.xml?chart=mostPopular&regionCode={region}"
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        ns = {"atom":"http://www.w3.org/2005/Atom"}
        titles = [e.find("atom:title", ns).text for e in root.findall("atom:entry", ns)]
        return [t for t in titles if t]
    except Exception:
        return []

def _fetch_wiki_top(date=None, timeout=10):
    # date: YYYY/MM/DD; default = today (UTC)
    if not date:
        date = dt.datetime.utcnow().strftime("%Y/%m/%d")
    url = f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{date}"
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        if r.status_code != 200:
            return []
        data = r.json()
        out = []
        for item in data.get("items", []):
            for a in item.get("articles", []):
                t = (a.get("article","") or "").replace("_"," ")
                if t and not t.lower().startswith("main page"):
                    out.append(t)
        return out
    except Exception:
        return []

def _dedupe(seq):
    seen, out = set(), []
    for s in seq:
        s2 = s.strip()
        if s2 and s2 not in seen:
            seen.add(s2)
            out.append(s2)
    return out

def fetch_trends_free(region="US", niche_keywords=None, extra_titles=None):
    """Return dict: {'source': 'yt+wiki'|'fallback', 'raw': [...], 'filtered': [...]}"""
    niche = [k.lower() for k in (niche_keywords or DEFAULT_NICHE)]
    raw = []
    yt  = _fetch_youtube_trending(region) or []
    wi  = _fetch_wiki_top() or []
    raw = _dedupe((extra_titles or []) + yt + wi)

    if not raw:
        # small built-in fallback
        raw = [
            "Animal Challenges","Pet Reactions","Wildlife Facts","Nature Beauty",
            "Funny Animal Moments","Cute Pet Compilation","AI Generated Animals",
            "Fantasy Creatures","Mythical Beasts","Animal Documentaries","Pet Training",
            "Wildlife Conservation","Nature Sounds","Cute Animal Compilations"
        ]
        src = "fallback"
    else:
        src = "yt+wiki"

    def ok(title):
        L = title.lower()
        return any(k in L for k in niche)

    filtered = [t for t in raw if ok(t)] or raw
    return {"source": src, "raw": raw, "filtered": filtered}

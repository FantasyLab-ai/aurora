from __future__ import annotations
import os, time
from typing import List
import feedparser
from rapidfuzz import fuzz

DEFAULT_FEEDS = [
    "https://www.reddit.com/r/Conservation/.rss",
    "https://news.google.com/rss/search?q=wildlife&hl=en-US&gl=US&ceid=US:en",
    "https://www.nationalgeographic.com/animals/index.rss"
]

def _dedupe(items: List[str], thresh: int = 80) -> List[str]:
    out: List[str] = []
    for x in items:
        ok = True
        for y in out:
            if fuzz.token_sort_ratio(x, y) >= thresh:
                ok = False; break
        if ok: out.append(x)
    return out

def get_topics(max_items: int = 8, feeds: List[str] | None = None) -> List[str]:
    feeds = feeds or DEFAULT_FEEDS
    titles: List[str] = []
    for url in feeds:
        try:
            f = feedparser.parse(url)
            for e in f.entries[:10]:
                t = (getattr(e, "title", "") or "").strip()
                if t:
                    titles.append(t)
        except Exception:
            pass
    titles = _dedupe(titles)
    return titles[:max_items] or ["wildlife conservationist placing baby owls back in their burrow"]

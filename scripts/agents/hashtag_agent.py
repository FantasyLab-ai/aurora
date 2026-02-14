import os, json, re
from collections import Counter
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", os.path.expanduser("~/FantasyAI/data")))
PUB = DATA_DIR / "public_metrics.jsonl"

def _tokens(s: str):
    return [w.lower() for w in re.findall(r"[a-z0-9]+", s or "") if len(w) > 2]

def harvested_for_topic(topic: str, top_k=8):
    if not PUB.exists():
        return []
    want = set(_tokens(topic))
    c = Counter()
    with PUB.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            txt = " ".join([
                obj.get("title", ""),
                obj.get("caption", ""),
                " ".join(obj.get("hashtags", []))
            ])
            toks = set(_tokens(txt))
            if want & toks:
                for h in obj.get("hashtags", []):
                    if isinstance(h, str) and h.startswith("#") and len(h) > 1:
                        c[h.lower()] += 1
    out = []
    for h, _ in c.most_common():
        if h not in out:
            out.append(h)
        if len(out) >= top_k:
            break
    return out

def blend(base: list[str], topic: str, top_k=8, max_total=12):
    seen = set()
    out = []
    for h in base or []:
        l = h.lower()
        if l not in seen:
            seen.add(l); out.append(h)
    for h in harvested_for_topic(topic, top_k=top_k):
        if h.lower() not in seen:
            seen.add(h.lower()); out.append(h)
        if len(out) >= max_total:
            break
    return out

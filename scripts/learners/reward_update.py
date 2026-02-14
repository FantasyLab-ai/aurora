import os, json, re
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", os.path.expanduser("~/FantasyAI/data")))
PUB = DATA_DIR / "public_metrics.jsonl"
TREND_JSON = DATA_DIR / "trend_data.json"

def _load_trend():
    if TREND_JSON.exists():
        try:
            return json.loads(TREND_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"trends": [], "performance": {}}

def _save_trend(obj):
    TREND_JSON.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def update_from_public():
    """Aggregate likes/views/shares from public_metrics.jsonl into trend_data.json.
       We key by normalized video title/caption (raw topic), not theme."""
    if not PUB.exists():
        return 0

    by_topic = {}
    with PUB.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            title = (d.get("title") or d.get("caption") or "").strip()
            if not title:
                continue
            key = re.sub(r"\s+", " ", title).strip().lower()
            likes  = int(d.get("likes") or 0)
            views  = int(d.get("views") or 0)
            shares = int(d.get("shares") or 0)
            agg = by_topic.setdefault(key, {"likes":0,"views":0,"shares":0})
            agg["likes"]  += likes
            agg["views"]  += views
            agg["shares"] += shares

    obj = _load_trend()
    perf = obj.setdefault("performance", {})
    updated = 0
    for key, agg in by_topic.items():
        score = agg["views"]*0.6 + agg["likes"]*0.3 + agg["shares"]*0.1
        slot = perf.setdefault(key, {"score":0,"views":[],"likes":[],"shares":[]})
        slot["views"].append(int(agg["views"]))
        slot["likes"].append(int(agg["likes"]))
        slot["shares"].append(int(agg["shares"]))
        slot["score"] = float(score)
        updated += 1

    _save_trend(obj)
    return updated

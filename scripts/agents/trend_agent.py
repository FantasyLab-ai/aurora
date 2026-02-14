import os, json, random, logging
from pathlib import Path

logger = logging.getLogger("FantasyAI")
DATA_DIR = os.path.expanduser(os.getenv("DATA_DIR", "~/FantasyAI/data"))
os.makedirs(DATA_DIR, exist_ok=True)
TREND_PERF = Path(DATA_DIR) / "trend_perf.json"
RECENT_TRENDS = Path(DATA_DIR) / "recent_trends.json"

try:
    from pytrends.request import TrendReq
    PYTRENDS_AVAILABLE = True
except Exception:
    PYTRENDS_AVAILABLE = False

FALLBACK = [
    "Animal Challenges","Pet Reactions","Wildlife Facts","Nature Beauty",
    "Funny Animal Moments","Cute Pet Compilation","AI Generated Animals",
    "Fantasy Creatures","Mythical Beasts","Animal Documentaries","Pet Training",
    "Wildlife Conservation","Nature Sounds","Cute Animal Compilations"
]
FILTER_KEYS = ["animal","pet","wildlife","nature","funny","cute","yeti","bear","cat","fantasy","viral"]

def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        x2 = (x or "").strip()
        if x2 and x2 not in seen:
            out.append(x2); seen.add(x2)
    return out

def _load_json(p: Path, default):
    try:
        if p.exists(): return json.loads(p.read_text(encoding="utf-8"))
    except Exception: pass
    return default

def _save_json(p: Path, data):
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception: pass

class TrendAgent:
    def __init__(self, region_env=None):
        self.region_env = (region_env or os.getenv("TRENDS_REGION","")).strip()

    def fetch(self):
        # Try multiple pytrends sources, then fallback
        items = []
        if PYTRENDS_AVAILABLE:
            regions = [self.region_env] if self.region_env else []
            regions += ["united_states","US","GB","CA","global"]
            tried = 0
            for reg in regions:
                try:
                    pt = TrendReq(hl='en-US', tz=360)
                    # try today_searches if available, else trending_searches
                    try:
                        df = pt.today_searches(pn=reg)  # newer pytrends
                        items += [str(x) for x in df.tolist()][:30]
                        tried += 1
                    except Exception:
                        df = pt.trending_searches(pn=reg) if reg!="global" else pt.trending_searches()
                        items += [str(x) for x in df[0].tolist()][:30]
                        tried += 1
                    if items: break
                except Exception:
                    continue
            logger.info("📈 pytrends tried=%d fetched=%d", tried, len(items))
        src = "pytrends" if items else "fallback"
        if not items:
            items = FALLBACK[:]
        # filter & dedupe
        filtered = [t for t in items if any(k in t.lower() for k in FILTER_KEYS)]
        selected = _dedupe(filtered) or _dedupe(items)
        logger.info("🔎 trend source=%s before_filter=%d after_filter=%d",
                    src, len(items), len(selected))
        return selected, src

    def _recent(self):
        return _load_json(RECENT_TRENDS, [])

    def _save_recent(self, t):
        r = self._recent()
        r.append(t)
        _save_json(RECENT_TRENDS, r[-12:])

    def select(self, trends):
        perf = _load_json(TREND_PERF, {})
        recent = set(self._recent())
        # UCB-ish: score = (perf_score or 0) + exploration bonus for not-seen-lately
        def score(t):
            meta = perf.get(t, {})
            s = float(meta.get("score", 0.0))
            if t not in recent: s += 100.0
            return s
        best = max(trends, key=score) if trends else random.choice(FALLBACK)
        self._save_recent(best)
        return best

    def update_perf(self, trend, views=0, likes=0, shares=0):
        perf = _load_json(TREND_PERF, {})
        meta = perf.setdefault(trend, {"views": [], "likes": [], "shares": [], "score": 0})
        meta["views"].append(views); meta["likes"].append(likes); meta["shares"].append(shares)
        # simple weighted score
        v = sum(meta["views"][-5:]) / max(1,len(meta["views"][-5:]))
        l = sum(meta["likes"][-5:]) / max(1,len(meta["likes"][-5:]))
        s = sum(meta["shares"][-5:]) / max(1,len(meta["shares"][-5:]))
        meta["score"] = 0.5*v + 0.3*l + 0.2*s
        _save_json(TREND_PERF, perf)

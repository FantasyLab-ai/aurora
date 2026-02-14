import os, csv, math

BASE_DIR = os.path.expanduser(os.getenv("BASE_DIR", "~/FantasyAI"))
METRICS_CSV = os.path.join(BASE_DIR, "data", "metrics.csv")

def _count_emojis_safe(text: str) -> int:
    if not text: return 0
    # coarse heuristic (no emoji lib required)
    return sum(1 for ch in text if ord(ch) >= 0x1F300)

class SmallPromptScorer:
    """
    Tiny linear regressor trained with SGD on features derived from prompt_json.
    Falls back to a heuristic if no data is available.
    """
    def __init__(self, lr=0.01, l2=1e-4, iters=1):
        self.lr, self.l2, self.iters = lr, l2, iters
        self.w = {}   # feature -> weight
        self.bias = 0.0
        self.trained_rows = 0

    # ---------- featurization ----------
    def _feats_from_prompt(self, pj: dict):
        text = (pj or {}).get("final_prompt","") or ""
        length = len(text)
        emojis = _count_emojis_safe(text)
        hashtags = text.count("#")
        tone = (pj or {}).get("tone") or (pj or {}).get("scene",{}).get("tone") or \
               self._infer_tone_from_vibe(pj.get("vibe","") if pj else "")
        team = pj.get("character_set") or pj.get("team") or []

        feats = {
            "len_norm": min(length, 1500)/1500.0,
            "emojis_norm": min(emojis, 20)/20.0,
            "hashtags_norm": min(hashtags, 10)/10.0,
        }
        if tone:
            feats[f"tone::{tone}"] = 1.0
        for member in team:
            feats[f"team::{member}"] = 1.0
        return feats

    def _infer_tone_from_vibe(self, vibe_text):
        # crude parse of "Tone: tense - ..." etc.
        s = (vibe_text or "").lower()
        for t in ("comedy","tense","docu","awe","surreal","cozy","light"):
            if t in s: return t
        return None

    # ---------- model ----------
    def predict(self, prompt_json):
        f = self._feats_from_prompt(prompt_json)
        z = self.bias + sum(self.w.get(k,0.0)*v for k,v in f.items())
        return float(z)

    def _update(self, feats, target):
        # L2 regularized SGD on squared loss
        pred = self.bias + sum(self.w.get(k,0.0)*v for k,v in feats.items())
        err = (pred - target)
        self.bias -= self.lr * err
        for k,v in feats.items():
            g = err * v + self.l2 * self.w.get(k,0.0)
            self.w[k] = self.w.get(k,0.0) - self.lr * g

    # ---------- training ----------
    def fit_from_csv(self, csv_path=METRICS_CSV, max_rows=500):
        if not os.path.exists(csv_path):
            return self
        rows = []
        with open(csv_path, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for i,row in enumerate(r):
                if i >= max_rows: break
                pj = {}
                pj["final_prompt"] = row.get("final_prompt","")
                pj["tone"] = row.get("tone") or row.get("vibe_tone") or None
                team = row.get("team") or row.get("character_set") or ""
                pj["team"] = [t.strip() for t in team.split("|") if t.strip()]
                rows.append((pj, self._target_from_row(row)))
        if not rows:
            return self
        for _ in range(self.iters):
            for pj,y in rows:
                self._update(self._feats_from_prompt(pj), y)
        self.trained_rows = len(rows)
        return self

    def _target_from_row(self, row: dict):
        # robust target using log scale
        v = float(row.get("views",0) or 0)
        l = float(row.get("likes",0) or 0)
        s = float(row.get("shares",0) or 0)
        score = v + 4*l + 8*s
        return math.log1p(score)/10.0

    # ---------- fallback heuristic ----------
    @staticmethod
    def heuristic_score(prompt_json):
        text = (prompt_json or {}).get("final_prompt","") or ""
        length = len(text)
        emojis = _count_emojis_safe(text)
        hashtags = text.count("#")
        base = max(0.0, 1.0 - max(0, length - 600)/1200.0)
        return float(base + 0.05*emojis + 0.07*hashtags)

import os, csv, logging, math
from pathlib import Path

logger = logging.getLogger("FantasyAI")
DATA_DIR = os.path.expanduser(os.getenv("DATA_DIR", "~/FantasyAI/data"))
MODELS_DIR = os.path.expanduser(os.getenv("MODELS_DIR", "~/FantasyAI/models"))
METRICS_CSV = os.path.join(DATA_DIR, "metrics.csv")
os.makedirs(MODELS_DIR, exist_ok=True)

try:
    import joblib
except Exception:
    joblib = None
try:
    import pandas as pd
    from sklearn.ensemble import RandomForestRegressor as RF
    SKLEARN = True
except Exception:
    pd = None
    RF = None
    SKLEARN = False

def _features(text: str):
    length = len(text or "")
    num_hash = text.count("#")
    # light features
    visual = sum(k in text.lower() for k in ["golden hour","blue hour","handheld","gimbal","drone","bioluminescent"])
    return [length, num_hash, visual]

class ScoringAgent:
    def __init__(self):
        self.model_path = os.path.join(MODELS_DIR, "prompt_scorer.pkl")
        self.model = None
        if SKLEARN and joblib and Path(self.model_path).exists():
            try:
                self.model = joblib.load(self.model_path)
                logger.info("📦 Loaded scorer model: %s", self.model_path)
            except Exception:
                self.model = None

    def maybe_train(self):
        if not (SKLEARN and pd): return False
        if not Path(METRICS_CSV).exists(): return False
        try:
            df = pd.read_csv(METRICS_CSV)
        except Exception:
            return False
        need = {"final_prompt","views"}
        if not need.issubset(df.columns): return False
        X = df["final_prompt"].fillna("").map(_features).tolist()
        y = df["views"].fillna(0).astype(float).values
        m = RF(n_estimators=120, random_state=42).fit(X, y)
        self.model = m
        if joblib:
            joblib.dump(m, self.model_path)
        logger.info("🧠 Trained scorer on %d rows", len(y))
        return True

    def score(self, final_prompt: str, critic_score: float) -> float:
        base = critic_score * 10.0  # 0–10 → 0–100
        if self.model:
            try:
                pred = float(self.model.predict([_features(final_prompt)])[0])
                return 0.6*base + 0.4*pred
            except Exception:
                pass
        # heuristic fallback
        L = len(final_prompt)
        sweet = 100 - abs(1200 - min(L, 2400)) * 0.05
        return 0.7*base + 0.3*max(0, sweet)

class Bandit:
    def __init__(self, epsilon=None):
        self.epsilon = float(epsilon if epsilon is not None else os.getenv("BANDIT_EPS","0.2"))

    def select(self, candidates):
        # candidates: list of tuples (score, prompt_text, json)
        import random
        if not candidates: raise RuntimeError("No candidates to select")
        if random.random() < self.epsilon and len(candidates) > 1:
            pick = random.choice(candidates[1:])  # explore non-top
            logger.info("🎰 Bandit exploring (ε=%.2f)", self.epsilon)
            return pick
        return candidates[0]

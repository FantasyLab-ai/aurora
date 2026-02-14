import os, json
try:
    import pandas as pd
except Exception:
    pd = None
try:
    import joblib
except Exception:
    joblib = None

# Minimal paths (no import from scripts.pipeline to avoid circular/broken imports)
BASE_DIR = os.path.expanduser(os.getenv("BASE_DIR", "~/FantasyAI"))
MODELS_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR   = os.path.join(BASE_DIR, "data")
METRICS_CSV = os.path.join(DATA_DIR, "metrics.csv")
os.makedirs(MODELS_DIR, exist_ok=True)

class _DummyRegressor:
    def fit(self, X, y): return self
    def predict(self, X): return [0.0 for _ in range(len(X))]

class PromptScorer:
    """
    Standalone scorer with 5D features:
      - length, num_emojis, num_hashtags, dialogue_lines, dialogue_chars
    Trains on data/metrics.csv if sklearn+pandas are present.
    """
    def __init__(self):
        self.model_path = os.path.join(MODELS_DIR, "prompt_scorer.pkl")
        self.model = self._load_model()

    def _load_model(self):
        if joblib and os.path.exists(self.model_path):
            try:
                return joblib.load(self.model_path)
            except Exception:
                pass
        try:
            from sklearn.ensemble import RandomForestRegressor as RF
            return RF(n_estimators=200, random_state=42)
        except Exception:
            return _DummyRegressor()

    def _features(self, text: str):
        import re
        text = text or ""
        length = len(text)
        num_emojis = sum(1 for ch in text if ord(ch) >= 0x1F300)
        num_hashtags = text.count("#")
        m = re.search(r"(?s)Dialogue\s*\(\d+\s*s\).*?(?:\n\n(?:Artifacts|Vibe|Camera|Environment)\b|\Z)", text)
        dlg_lines = 0; dlg_chars = 0
        if m:
            block = m.group(0)
            lines = [ln for ln in block.splitlines() if ": " in ln]
            dlg_lines = len(lines)
            dlg_chars = sum(len(ln.split(": ",1)[1]) for ln in lines)
        return [length, num_emojis, num_hashtags, dlg_lines, dlg_chars]

    def _to_Xy(self, df):
        if pd is None: return None, None
        if "final_prompt" not in df.columns or "views" not in df.columns:
            return None, None
        import re
        def _dlg(t):
            t = t or ""
            m = re.search(r"(?s)Dialogue\s*\(\d+\s*s\).*?(?:\n\n(?:Artifacts|Vibe|Camera|Environment)\b|\Z)", t)
            if not m: return (0,0)
            block = m.group(0)
            lines = [ln for ln in block.splitlines() if ": " in ln]
            return (len(lines), sum(len(ln.split(": ",1)[1]) for ln in lines))
        df = df.copy()
        df["length"] = df["final_prompt"].fillna("").map(len)
        df["num_emojis"] = df["final_prompt"].fillna("").map(lambda s: sum(1 for ch in s if ord(ch) >= 0x1F300))
        df["num_hashtags"] = df["final_prompt"].fillna("").map(lambda s: s.count("#"))
        dls, dcs = [], []
        for t in df["final_prompt"].fillna(""):
            L, C = _dlg(t); dls.append(L); dcs.append(C)
        df["dlg_lines"] = dls; df["dlg_chars"] = dcs
        X = df[["length","num_emojis","num_hashtags","dlg_lines","dlg_chars"]].values
        y = df["views"].values
        return X, y

    def maybe_train_from_csv(self, csv_path=None):
        if pd is None: return False, "pandas not available"
        if not csv_path: csv_path = METRICS_CSV
        if not os.path.exists(csv_path): return False, f"no CSV at {csv_path}"
        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            return False, f"csv read error: {e}"
        X, y = self._to_Xy(df)
        if X is None: return False, "CSV missing required columns"
        try:
            from sklearn.ensemble import RandomForestRegressor as RF
        except Exception:
            return False, "sklearn not available"
        try:
            rf = RF(n_estimators=200, random_state=42).fit(X, y)
            self.model = rf
            if joblib:
                joblib.dump(self.model, self.model_path)
            return True, "trained"
        except Exception as e:
            return False, f"train error: {e}"

    def predict(self, prompt_json):
        text = (prompt_json or {}).get("final_prompt", "") or ""
        feats = self._features(text)
        try:
            return float(self.model.predict([feats])[0])
        except Exception:
            length, emo, tags, dl, dc = feats
            base = max(0.0, 1100 - max(0, length - 650) * 0.45)
            boost = emo * 45 + tags * 70 + dl * 20 + min(140, dc) * 0.3
            return float(base + boost)

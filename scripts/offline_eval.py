# /scripts/offline_eval.py
from __future__ import annotations
import argparse, math, numpy as np
from scripts.feature_store import fetch_dataset
from scripts.embeddings import embed_reduced

def score_example(row):
    m = row.get("metrics") or {}
    # define your objective (watch time first, then views)
    wt = float(m.get("watch_time_sec") or 0.0)
    views = float(m.get("views") or 0.0)
    return wt + 0.05*views

def simulate_bandit(dataset):
    # dumb policy: prefer shorter videos when trend is high (placeholder)
    s=0.0; n=0
    for r in dataset:
        feats = r.get("features") or {}
        trend = float(feats.get("trend_score") or 0.0)
        length = float(feats.get("length_s") or 30)
        # pretend we “picked” based on heuristic
        chosen = (length < 45 and trend>0.4)
        s += score_example(r) * (1.15 if chosen else 1.0)  # pretend uplift
        n += 1
    return s/n if n else 0.0

def baseline_random(dataset):
    s=0.0; n=0
    rng = np.random.default_rng(42)
    for r in dataset:
        chosen = rng.random()<0.5
        s += score_example(r) * (1.0 if chosen else 0.9)
        n += 1
    return s/n if n else 0.0

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()
    data = fetch_dataset(args.limit)
    b = baseline_random(data)
    sim = simulate_bandit(data)
    print(f"Baseline: {b:.3f} | Simulated bandit: {sim:.3f} | Δ={(sim-b):.3f}")

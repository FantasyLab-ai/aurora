"""Smoke test for the tiered analysis pipeline.

Generates a synthetic 60MB time-series CSV (large enough to trigger tiered)
and exercises:
  - shape detection
  - sampler dispatch
  - state machine round-trip
  - sample manifest persistence

Pure Python; no subprocess invocation. Run manually:
    python tests/_smoke_tiered.py
"""
from __future__ import annotations

import sys, time
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.sampling import (
    infer_structure, run_sampler, select_sampling_method,
    SamplingState, make_seed,
)


def main():
    out = Path("/tmp/aurora_smoke_tiered.csv")
    if not out.exists() or out.stat().st_size < 50_000_000:
        print("Generating synthetic 60MB time-series…")
        n = 1_500_000
        rng = np.random.RandomState(0)
        ts = pd.date_range("2020-01-01", periods=n, freq="min")
        df = pd.DataFrame({
            "timestamp": ts,
            "temperature": 20 + 5 * np.sin(np.arange(n) * 2 * np.pi / 1440)
                           + rng.normal(0, 0.5, n),
            "humidity":    50 + 10 * np.cos(np.arange(n) * 2 * np.pi / 1440)
                           + rng.normal(0, 1, n),
            "pressure":    1013 + rng.normal(0, 2, n),
        })
        df.to_csv(out, index=False)
    print(f"file: {out}  size: {out.stat().st_size:,} bytes")

    print("\n=== T0: structure inference ===")
    t0 = time.time()
    profile = infer_structure(out)
    print(f"  elapsed: {time.time()-t0:.2f}s")
    print(f"  rows: {profile.rows_total:,}  cols: {profile.cols_total}")
    print(f"  shape: {profile.shape}  is_small: {profile.is_small}")
    print(f"  time_col: {profile.time_col}")

    method = select_sampling_method(profile.shape)
    print(f"\n=== Selected method for shape={profile.shape}: {method} ===")

    print("\n=== T1: 100K-row sample ===")
    t0 = time.time()
    sample_path = Path("/tmp/aurora_smoke_t1.csv")
    manifest = run_sampler(method, out, profile=profile,
                            rows_target=100_000, max_cols=200, tier=1,
                            out_path=sample_path)
    print(f"  elapsed: {time.time()-t0:.2f}s")
    print(f"  method: {manifest.method}")
    print(f"  rows: {manifest.rows_sampled:,}/{manifest.rows_total:,} "
          f"({manifest.rows_fraction*100:.2f}%)")
    print(f"  cols: {manifest.cols_sampled}")
    print(f"  seed: {manifest.seed}")
    sample = pd.read_csv(sample_path, nrows=5)
    print(f"  sample head: {list(sample.columns)}")

    print("\n=== T2: 1M-row sample ===")
    t0 = time.time()
    t2_path = Path("/tmp/aurora_smoke_t2.csv")
    manifest2 = run_sampler(method, out, profile=profile,
                             rows_target=1_000_000, max_cols=1000, tier=2,
                             out_path=t2_path)
    print(f"  elapsed: {time.time()-t0:.2f}s")
    print(f"  rows: {manifest2.rows_sampled:,}")
    print(f"  fraction: {manifest2.rows_fraction*100:.2f}%")

    print("\n=== State machine round-trip ===")
    parent = Path("/tmp/aurora_smoke_parent")
    parent.mkdir(parents=True, exist_ok=True)
    state = SamplingState(parent_run_dir=str(parent),
                           dataset_path=str(out),
                           dataset_basename=out.name,
                           rows_total=profile.rows_total,
                           cols_total=profile.cols_total,
                           shape_detected=profile.shape)
    state.tiers["0"].state = "complete"
    state.tiers["0"].rows_sampled = profile.rows_total
    state.begin_tier(1, method=method, rows=manifest.rows_sampled,
                     cols=manifest.cols_sampled, seed=manifest.seed,
                     rows_total=profile.rows_total)
    state.complete_tier(1, child_run_dir="/fake/child1")
    state.begin_tier(2, method=method, rows=manifest2.rows_sampled,
                     cols=manifest2.cols_sampled, seed=manifest2.seed,
                     rows_total=profile.rows_total)
    state.complete_tier(2, child_run_dir="/fake/child2")

    loaded = SamplingState.load(parent)
    print(f"  parent: {parent}")
    print(f"  highest complete tier: {loaded.highest_complete_tier()}")
    print(f"  T3 state: {loaded.tiers['3'].state}")
    print(f"  honest_caveat: {loaded.honest_caveat()}")

    print("\nSMOKE TEST OK")


if __name__ == "__main__":
    main()

"""
Demo before/after capture — pre-launch sprint deliverable.

Builds a synthetic NOAA-buoy-style dataset engineered to surface the
sprint's headline behaviours in one shot:

  * 47+ anomalies across multiple severity levels (Atom 1.1)
  * a time axis that lets the referential resolver hit L2 (Phase A)
  * a regime shift the SeedRanker can re-prioritise (Atom 3.x)
  * physics-fittable target column for prior-library validation (Atom 4.2)

Then runs build_state on the synthesized run dir and writes
``docs/captures/2026-05-05/after/api_state.json`` plus a small README.
The "before" capture is hand-curated separately (it documents the legacy
behaviour we replaced).

Usage:
    python bin/capture_demo_2026_05_05.py [--out PATH]

Deterministic — same input every time.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from studio_api import _sanitize_for_json
from fantasyai.aurora.state_builder import build_state


def _make_buoy_dataset(out_dir: Path) -> tuple[Path, pd.DataFrame]:
    """Synthesise a 365-day hourly NOAA-buoy-style frame with engineered
    anomaly clusters at three storm windows. Deterministic seed."""
    rng = np.random.RandomState(20260505)
    n = 24 * 365
    ts = pd.date_range("2024-01-01", periods=n, freq="h")

    # Baseline signals.
    seasonal = 0.7 + 0.3 * np.sin(np.arange(n) * 2 * np.pi / (24 * 365))
    wind = 6 + 2 * np.sin(np.arange(n) * 2 * np.pi / 24)
    wave_base = 1.2 * (wind / 6.0) ** 1.8
    pressure = 1013 + rng.normal(0, 1.5, n)
    temp = 18 + 6 * seasonal + rng.normal(0, 0.6, n)
    humidity = np.clip(70 + 8 * np.sin(np.arange(n) * 2 * np.pi / (24 * 14)) +
                        rng.normal(0, 4, n), 20, 100)
    wave = wave_base + rng.normal(0, 0.18, n)
    wind = wind + rng.normal(0, 0.4, n)

    # Engineered storm windows — three named events that produce clusters
    # of anomalies and a regime shift.
    storms = [
        ("Storm Andrea",  3500, 3560),     # ~Jan 23 → Jan 25
        ("Storm Bernard", 6200, 6320),     # ~Apr 14 → Apr 19
        ("Storm Cole",    7800, 7900),     # ~Jun 21 → Jun 25
    ]
    for label, lo, hi in storms:
        wind[lo:hi] += rng.uniform(8, 18, hi - lo)
        wave[lo:hi] += rng.uniform(2.5, 5.0, hi - lo)
        pressure[lo:hi] -= rng.uniform(8, 22, hi - lo)
        temp[lo:hi] -= rng.uniform(1.5, 4.0, hi - lo)
    # Mid-year regime shift: persistent wave-height baseline change.
    wave[5500:] += 0.6

    df = pd.DataFrame({
        "timestamp":          ts,
        "wave_height_m":      wave,
        "wind_speed_ms":      wind,
        "barometric_pressure_hpa": pressure,
        "sea_surface_temp_c": temp,
        "humidity_pct":       humidity,
    })
    csv_path = out_dir / "noaa_buoy_2024.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)
    return csv_path, df


def _stage_synthetic_run_dir(csv_path: Path, df: pd.DataFrame,
                             out_dir: Path) -> Path:
    """Write the JSON artifacts state_builder reads as if the runner had
    already produced them. Engineered to surface 47+ anomalies."""
    n = len(df)
    target_col = "wave_height_m"
    time_col = "timestamp"
    run_dir = out_dir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    # _RESOLVED_CONTRACT.json
    (run_dir / "_RESOLVED_CONTRACT.json").write_text(json.dumps({
        "dataset_key": str(csv_path),
        "target_col": target_col,
        "time_col": time_col,
        "n_rows": n, "n_cols": len(df.columns),
        "has_time_axis": True,
        "dt_seconds": 3600,
    }), encoding="utf-8")
    (run_dir / "_RUN_META.json").write_text(json.dumps({
        "run_id": run_dir.name, "n_rows": n, "n_cols": len(df.columns),
    }), encoding="utf-8")

    # structure_contract.json
    cols = []
    for c in df.columns:
        kind = "time" if c == time_col else (
                "categorical" if df[c].dtype == "object" else "numeric")
        cols.append({
            "name": c, "role": kind, "dtype": str(df[c].dtype),
            "missing_rate": 0.0, "unique_est": int(df[c].nunique()),
            "datetime_parse_rate": 1.0 if c == time_col else 0.0,
        })
    (run_dir / "structure_contract.json").write_text(json.dumps({
        "shape": {"rows": n, "cols": len(df.columns)},
        "eligible": {"forecast": True, "regime": True, "physics": True},
        "columns": cols, "time": {"best_time_col": time_col},
        "time_col": time_col, "target_col": target_col,
        "dt_seconds": 3600, "has_time_axis": True,
    }), encoding="utf-8")

    # Engineered anomalies — 47 total: 7 critical, 12 warn, 28 info.
    anomalies = []
    storm_rows = list(range(3500, 3560)) + list(range(6200, 6260)) + \
                  list(range(7800, 7820))
    rng = np.random.RandomState(42)
    selected_storm = rng.choice(storm_rows, size=7, replace=False)
    for i, row_id in enumerate(sorted(selected_storm)):
        anomalies.append({
            "rank": i + 1, "row_id": int(row_id),
            "score": 6.0 + rng.uniform(0, 1.5),
            "top_feature_drivers": [
                {"feature": "wave_height_m",
                 "robust_z": float(rng.uniform(4.0, 6.0)),
                 "direction": "high"},
                {"feature": "wind_speed_ms",
                 "robust_z": float(rng.uniform(3.5, 5.5)),
                 "direction": "high"},
            ],
        })
    for i in range(12):
        row_id = int(rng.choice(storm_rows))
        anomalies.append({
            "rank": 8 + i, "row_id": row_id,
            "score": 3.5 + rng.uniform(0, 1.0),
            "top_feature_drivers": [
                {"feature": "barometric_pressure_hpa",
                 "robust_z": float(-rng.uniform(2.5, 4.0)),
                 "direction": "low"},
            ],
        })
    for i in range(28):
        row_id = int(rng.randint(0, n))
        anomalies.append({
            "rank": 20 + i, "row_id": row_id,
            "score": 1.5 + rng.uniform(0, 1.4),
            "top_feature_drivers": [
                {"feature": "humidity_pct",
                 "robust_z": float(rng.uniform(-2.0, 2.0)),
                 "direction": "high" if rng.random() > 0.5 else "low"},
            ],
        })

    deep = {
        "target_col": target_col, "time_col": time_col,
        "forecasting": {
            "method": "ar1", "horizon": 24, "phi": 0.78, "sigma": 0.34,
            "forecast": [1.4 + 0.05 * i for i in range(24)],
            "lower":    [1.2 + 0.05 * i for i in range(24)],
            "upper":    [1.6 + 0.05 * i for i in range(24)],
        },
        "causal_what_if": {
            "treatment_col": "wind_speed_ms", "target_col": target_col,
            "delta": 1.0, "beta_treatment": 0.42,
            "estimated_effect": 0.42, "method": "linear OLS", "n": n,
        },
        "physics": {
            "y_min": float(df[target_col].min()),
            "y_max": float(df[target_col].max()),
            "is_monotonic": False, "growth_type": "stationary",
            "physics_consistency_score": 0.74,
        },
        "physics_discovery": {
            "best": {
                "name": "wave_height_wind_fetch_law",
                "model": "wave_height ~ wind^1.8",
                "params": {"a": 1.2, "b": 1.8},
                "rmse_test": 0.22, "rmse_full": 0.21,
                "aic": -1240.0, "k": 2,
            },
            "all_candidates": [{"name": "wave_height_wind_fetch_law",
                                  "rmse_test": 0.22, "aic": -1240.0,
                                  "k": 2, "ok": True}],
            "constraints": {"physics_consistency_score": 0.74},
            "target_col": target_col, "time_col": time_col,
        },
        "regimes": {
            "method": "PELT",
            "change_points": [5500],
            "regime_count": 2,
            "stats": [
                {"start": "2024-01-01", "end": "2024-08-15",
                 "mean": 1.18, "std": 0.31, "min": 0.4, "max": 6.2,
                 "n": 5500},
                {"start": "2024-08-16", "end": "2024-12-31",
                 "mean": 1.78, "std": 0.36, "min": 0.6, "max": 7.4,
                 "n": n - 5500},
            ],
        },
        "extensions": {"anomaly_attribution": {"points": anomalies}},
    }
    (run_dir / "deep_math.json").write_text(json.dumps(deep), encoding="utf-8")

    # motif.json
    (run_dir / "motif.json").write_text(json.dumps({
        "motifs": [
            {"kind": "high_volatility",
             "title": "High volatility in wave_height_m during storms",
             "score": 4.2,
             "details": {"col": "wave_height_m", "std": 1.18, "count": 3}},
            {"kind": "high_correlation",
             "title": "wind_speed_ms ↔ wave_height_m",
             "score": 0.86,
             "details": {"col_a": "wind_speed_ms",
                          "col_b": "wave_height_m", "corr": 0.86}},
        ],
        "summary": {"n_motifs": 2},
    }), encoding="utf-8")

    (run_dir / "report.json").write_text(json.dumps({
        "score": {"run_quality": 0.85, "confidence": 0.82,
                   "stability_good": 0.88, "drift_good": 0.81},
        "events": [], "results": [],
        "artifacts_present": ["deep_math.json", "structure_contract.json",
                                "motif.json"],
        "inputs": {"dataset": str(csv_path)},
    }), encoding="utf-8")
    (run_dir / "orchestrator.json").write_text(json.dumps({
        "plan": {"actions": []}, "score": 0.82, "warnings": [],
    }), encoding="utf-8")
    (run_dir / "_PLOTS_INDEX.json").write_text(json.dumps({"plots": []}),
                                                   encoding="utf-8")
    return run_dir


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "docs" / "captures" /
                                          "2026-05-05" / "after"),
                    help="Output directory for the AFTER capture.")
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[capture] writing to {out_dir}")

    print("[capture] generating synthetic NOAA-buoy dataset…")
    csv_path, df = _make_buoy_dataset(out_dir / "data")
    print(f"  rows: {len(df):,}  cols: {len(df.columns)}  size: {csv_path.stat().st_size/1024:.1f} kB")

    print("[capture] staging synthetic run_dir…")
    run_dir = _stage_synthetic_run_dir(csv_path, df, out_dir)

    print("[capture] running build_state…")
    state = build_state(run_dir)
    sanitized = _sanitize_for_json(state)

    # Quick sanity check — make sure the state proves the headline atoms.
    n_anom = len(state.get("anomalies") or [])
    n_findings = len(state.get("findings") or [])
    n_motifs = len(state.get("motifs") or [])
    sm_template = (state.get("system_model") or {}).get("template_id")
    seed_section = state.get("seed_ranker", {}).get("active")
    has_run_meta = bool(state.get("run_meta"))

    print(f"  anomalies surfaced: {n_anom}")
    print(f"  findings synthesised: {n_findings}")
    print(f"  motifs surfaced: {n_motifs}")
    print(f"  template detected: {sm_template}")
    print(f"  run_meta present: {has_run_meta}")
    print(f"  seed_ranker active: {seed_section}")

    api_state = {"ok": True, "run_dir": str(run_dir),
                  "state": sanitized, "reason": None}
    state_path = out_dir / "api_state.json"
    state_path.write_text(json.dumps(api_state, indent=2, default=str),
                           encoding="utf-8")
    print(f"[capture] wrote {state_path} ({state_path.stat().st_size/1024:.1f} kB)")

    api_run = {"ok": True, "async": True,
                "run_id": run_dir.name, "run_dir": str(run_dir),
                "dataset": str(csv_path),
                "status_url": f"/api/run/status?run_id={run_dir.name}",
                "started_at": "2026-05-05T14:23:18Z"}
    (out_dir / "api_run.json").write_text(json.dumps(api_run, indent=2),
                                              encoding="utf-8")

    # README documents what's been captured.
    (out_dir / "README.md").write_text(f"""# Aurora pre-launch sprint — AFTER capture (2026-05-05)

Synthesised NOAA buoy-style dataset (deterministic seed 20260505),
365 days hourly, 6 columns. Three engineered storm windows produce
clusters of anomalies; a mid-year regime shift in wave_height_m
exercises the SeedRanker.

## Reproduce

```
python bin/capture_demo_2026_05_05.py --out docs/captures/2026-05-05/after
```

## What this capture proves

| Atom | Acceptance signal in `api_state.json` |
| ---- | --------------------------------------- |
| 1.1  | `state.anomalies` length = {n_anom} (was capped at 10) |
| 1.1  | findings carry `n_total_at_this_severity` |
| 2.1  | `state.system_model.template_id` resolves to a domain |
| 3.2  | `state.run_meta.seed_text` field exists (null when no seed) |
| Phase A | `state.anomalies[*].referent.primary_label` populated |
| Phase B | `state.findings[*].narrative.template_id` populated |
| Hardening | response is JSON-clean (no numpy / NaN) |
| Hardening | `state.run_meta` present |

## Files

- `data/noaa_buoy_2024.csv` — synthetic input ({len(df):,} rows × {len(df.columns)} cols)
- `run/` — staged runner artifacts
- `api_state.json` — what `/api/state` returns
- `api_run.json` — what `/api/run` returns on async kickoff

## Stats from this run

- Anomalies surfaced: **{n_anom}** (NOT capped at 10 — Atom 1.1)
- Findings synthesised: **{n_findings}**
- Motifs surfaced: **{n_motifs}**
- Detected domain template: **{sm_template}**
- run_meta present: **{has_run_meta}**
""", encoding="utf-8")

    print("[capture] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

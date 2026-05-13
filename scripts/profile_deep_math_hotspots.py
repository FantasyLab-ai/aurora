"""
Diagnostic v2 — per-method timing of ``compute_deep_math_v3`` on
``diabetic_data``, with a hard 10-minute wall-clock budget per the
brief.

PURE measurement, NO production code changes. Pattern:

  * Mirror ``_prepare_deep_math_df`` to get the same 5,000-row input
    compute_deep_math_v3 actually sees.
  * Time each inner method in isolation with a per-method cap.
  * Stream output via ``flush=True`` so partial results are visible
    even if the script is killed mid-run.
  * Stop accumulating after the global budget (10 min) is exhausted.
"""
from __future__ import annotations

import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np   # noqa: E402
import pandas as pd  # noqa: E402

DATASET = ROOT / "data" / "uploads" / "20260508_132034__diabetic_data.csv"
GLOBAL_BUDGET_S = 600.0    # 10 min total wall clock; brief authorises this
PER_METHOD_CAP_S = 300.0   # 5 min per single method; kill orphans then

GLOBAL_T0 = time.perf_counter()


def _say(s: str) -> None:
    """Print + flush so output is visible during a long run."""
    elapsed = time.perf_counter() - GLOBAL_T0
    print(f"[{elapsed:7.1f}s]  {s}", flush=True)


def _budget_remaining() -> float:
    return max(0.0, GLOBAL_BUDGET_S - (time.perf_counter() - GLOBAL_T0))


def _bench(label: str, fn, *args, cap_s: float = PER_METHOD_CAP_S, **kwargs):
    """Run ``fn`` under a hard timeout. Return ``(result, elapsed_s, status)``.

    ``status`` is one of: 'ok', 'timed_out', 'error', 'budget_exhausted'.
    Caller returns immediately on timeout — the orphan keeps running but
    we move on (matches the v1.1 _call_with_timeout pattern).
    """
    rem = _budget_remaining()
    if rem <= 0:
        _say(f"  SKIP {label}: global budget exhausted")
        return None, 0.0, "budget_exhausted"
    effective_cap = min(cap_s, rem)
    _say(f"  >> {label}  (cap {effective_cap:.0f}s, remaining global {rem:.0f}s)")
    t0 = time.perf_counter()
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"prof-{label}")
    fut = ex.submit(fn, *args, **kwargs)
    try:
        try:
            res = fut.result(timeout=effective_cap)
            elapsed = time.perf_counter() - t0
            _say(f"  << {label}  OK in {elapsed:.2f}s")
            return res, elapsed, "ok"
        except FutTimeout:
            elapsed = time.perf_counter() - t0
            _say(f"  << {label}  TIMED OUT after {elapsed:.2f}s "
                 f"(cap was {effective_cap:.0f}s); orphan continues")
            return None, elapsed, "timed_out"
        except Exception as e:
            elapsed = time.perf_counter() - t0
            _say(f"  << {label}  ERROR after {elapsed:.2f}s: "
                 f"{type(e).__name__}: {e}")
            return None, elapsed, "error"
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def main() -> int:
    if not DATASET.exists():
        _say(f"diabetic_data not found at {DATASET}")
        return 1

    _say(f"Loading {DATASET.name}")
    df_full = pd.read_csv(DATASET, low_memory=False)
    _say(f"  full shape: {df_full.shape}")

    # Mirror the runner's pre-deep-math sampling.
    from scripts.run_aurora_dataset_runner import _prepare_deep_math_df
    df, _, _ = _bench("_prepare_deep_math_df",
                       _prepare_deep_math_df, df_full, 42, max_rows=5000)
    if df is None:
        _say("Cannot proceed — _prepare_deep_math_df failed")
        return 1
    _say(f"  prepared shape: {df.shape}")

    # Build the same inputs compute_deep_math_v3 builds.
    from fantasyai.aurora.math import deep_math as _dm
    numeric_cols = _dm._numeric_cols(df, max_cols=35)
    tcol = _dm._infer_time_col(df)
    ycol = _dm._infer_target(df, numeric_cols) or numeric_cols[0]
    _say(f"  inferred  tcol={tcol!r}  ycol={ycol!r}  "
         f"numeric_cols_n={len(numeric_cols)}")

    df2_pack, _, _ = _bench("_prepare_model_frame",
                              _dm._prepare_model_frame,
                              df=df, tcol=tcol, ycol=ycol,
                              numeric_cols=numeric_cols)
    if isinstance(df2_pack, tuple):
        df2, tcol, ycol, _ = df2_pack
    else:
        df2 = df
    _say(f"  model_frame shape: {getattr(df2, 'shape', '?')}")
    _say("")

    timings = []
    statuses = {}

    # ===== 1. Forecasting =====
    _, t, s = _bench("_safe_forecast",
                       _dm._safe_forecast,
                       df=df2, ycol=ycol, tcol=tcol, seed=42)
    timings.append(("_safe_forecast", t)); statuses["_safe_forecast"] = s

    # ===== 2. Regimes =====
    if callable(_dm.detect_regimes):
        _, t, s = _bench("detect_regimes",
                           _dm.detect_regimes, df2, target_col=ycol)
        timings.append(("detect_regimes", t)); statuses["detect_regimes"] = s

    # ===== 3. Bootstrap CI loop =====
    def _bootstrap_block():
        cols = numeric_cols[:10]
        arr = {c: pd.to_numeric(df2[c], errors="coerce").values.astype(float)
               for c in cols}
        pairs = []
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                x = arr[cols[i]]; y = arr[cols[j]]
                m = np.isfinite(x) & np.isfinite(y)
                if int(m.sum()) < 120: continue
                if np.nanstd(x[m]) < 1e-12 or np.nanstd(y[m]) < 1e-12: continue
                r = float(np.corrcoef(x[m], y[m])[0, 1])
                pairs.append((abs(r), cols[i], cols[j], r, int(m.sum())))
        pairs.sort(key=lambda t: t[0], reverse=True); pairs = pairs[:8]
        if callable(_dm.bootstrap_corr_ci):
            for _, a, b, r, n in pairs:
                _dm.bootstrap_corr_ci(arr[a], arr[b], B=500, alpha=0.05, seed=42)
        return len(pairs)
    _, t, s = _bench("bootstrap_corr_ci_loop_8x500", _bootstrap_block)
    timings.append(("bootstrap_corr_ci_loop", t)); statuses["bootstrap_corr_ci_loop"] = s

    # ===== 4. Monte Carlo risk surface =====
    if callable(_dm.monte_carlo_risk_surface):
        y_arr = pd.to_numeric(df2[ycol], errors="coerce").values.astype(float)
        _, t, s = _bench("monte_carlo_risk_surface_2000sims",
                           _dm.monte_carlo_risk_surface,
                           y_arr, horizon=24, n_sims=2000, seed=42)
        timings.append(("monte_carlo_risk_surface", t)); statuses["monte_carlo_risk_surface"] = s

    # ===== 5. Causal what-if =====
    _, t, s = _bench("_safe_causal",
                       _dm._safe_causal,
                       df=df2, ycol=ycol, numeric_cols=numeric_cols)
    timings.append(("_safe_causal", t)); statuses["_safe_causal"] = s

    # ===== 6. Physics diagnostics =====
    if callable(_dm.physics_diagnostics):
        _, t, s = _bench("physics_diagnostics",
                           _dm.physics_diagnostics,
                           df=df2, target_col=ycol, time_col=tcol, seed=42)
        timings.append(("physics_diagnostics", t)); statuses["physics_diagnostics"] = s

    # ===== 7. Physics diagnostics v2 =====
    if callable(_dm.physics_diagnostics_v2):
        _, t, s = _bench("physics_diagnostics_v2",
                           _dm.physics_diagnostics_v2,
                           df=df2, target_col=ycol, time_col=tcol, seed=42)
        timings.append(("physics_diagnostics_v2", t)); statuses["physics_diagnostics_v2"] = s

    # ===== 8. Physics discovery (includes new coupled_systems / pde) =====
    if callable(_dm.physics_discovery):
        _, t, s = _bench("physics_discovery_with_coupled",
                           _dm.physics_discovery,
                           df=df2, target_col=ycol, time_col=tcol, seed=42)
        timings.append(("physics_discovery", t)); statuses["physics_discovery"] = s

    # ===== 9. Physics invariants =====
    if callable(_dm.physics_invariants):
        _, t, s = _bench("physics_invariants",
                           _dm.physics_invariants,
                           df=df2, target_col=ycol, time_col=tcol, seed=42)
        timings.append(("physics_invariants", t)); statuses["physics_invariants"] = s

    # ===== Summary =====
    _say("")
    _say("=" * 70)
    _say("SORTED HOTSPOTS (descending):")
    timings_sorted = sorted(timings, key=lambda x: -x[1])
    sum_t = sum(t for _, t in timings)
    for label, t in timings_sorted:
        pct = (t / sum_t * 100) if sum_t > 0 else 0
        status = statuses.get(label, "?")
        marker = "** TIMEOUT **" if status == "timed_out" else ""
        _say(f"  {label:<42}  {t:>8.2f}s  ({pct:>5.1f}%)  {marker}")
    _say(f"  {'(sum of measured blocks)':<42}  {sum_t:>8.2f}s")
    _say(f"  global wall: {time.perf_counter() - GLOBAL_T0:.1f}s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _say("INTERRUPTED — partial data above")
        sys.exit(130)
    except Exception as e:
        _say(f"profiler crashed: {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)

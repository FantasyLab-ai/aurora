from __future__ import annotations

from typing import Any, Dict, Optional, List
import inspect
import json
import math
import re

import numpy as np
import pandas as pd

# ---------------------------
# Optional-module guard: deep_math must never fail import
# ---------------------------
_OPTIONAL_IMPORT_ERRORS: Dict[str, str] = {}

def _try_import(path: str, name: str):
    try:
        mod = __import__(path, fromlist=[name])
        return getattr(mod, name)
    except Exception as e:
        _OPTIONAL_IMPORT_ERRORS[f"{path}:{name}"] = f"{type(e).__name__}:{e}"
        return None

forecast_with_bands = _try_import("fantasyai.aurora.math.forecasting", "forecast_with_bands")
detect_regimes = _try_import("fantasyai.aurora.math.regimes", "detect_regimes")
bootstrap_corr_ci = _try_import("fantasyai.aurora.math.uncertainty", "bootstrap_corr_ci")
monte_carlo_risk_surface = _try_import("fantasyai.aurora.math.uncertainty", "monte_carlo_risk_surface")
solve_linear_program = _try_import("fantasyai.aurora.math.optimization", "solve_linear_program")
minimize_nonlinear = _try_import("fantasyai.aurora.math.optimization", "minimize_nonlinear")
simulate_counterfactuals_from_regression = _try_import("fantasyai.aurora.math.simulation", "simulate_counterfactuals_from_regression")
physics_diagnostics = _try_import("fantasyai.aurora.math.physics", "physics_diagnostics")
physics_diagnostics_fallback = _try_import("fantasyai.aurora.math.physics_v2", "physics_diagnostics")
physics_invariants = _try_import("fantasyai.aurora.math.physics", "physics_invariants")
physics_diagnostics_v2 = _try_import("fantasyai.aurora.math.physics_v2", "physics_diagnostics_v2")
physics_discovery = _try_import("fantasyai.aurora.math.physics_discovery", "physics_discovery")
physics_discovery_fallback = _try_import("fantasyai.aurora.math.physics_v2", "physics_discovery")

# Prefer robust wrapper if present (your patched causal.py adds robust_v2)
causal_what_if_linear = _try_import("fantasyai.aurora.math.causal", "causal_what_if_linear_robust_v2")
if causal_what_if_linear is None:
    causal_what_if_linear = _try_import("fantasyai.aurora.math.causal", "causal_what_if_linear")
def _numeric_cols(df: pd.DataFrame, max_cols: int = 35) -> List[str]:
    cols: List[str] = []
    for c in df.columns:
        if len(cols) >= max_cols:
            break
        v = pd.to_numeric(df[c], errors="coerce")
        if v.notna().mean() > 0.6 and (np.nanstd(v.values.astype(float)) if v.notna().any() else 0.0) > 1e-12:
            cols.append(c)
    return cols



def _is_mostly_numeric(s: "pd.Series") -> bool:
    """True if a series is mostly numeric (or numeric-like strings)."""
    try:
        if pd.api.types.is_numeric_dtype(s):
            return True
        c = pd.to_numeric(s.astype(str).str.replace(",", ".", regex=False), errors="coerce")
        return float(c.notna().mean()) > 0.85
    except Exception:
        return False

def _infer_time_col(df: pd.DataFrame) -> Optional[str]:
    """
    Robust time-axis inference:
      - Prefer Date+Time pairing if present
      - Prefer explicit datetime-ish column names
      - Never select mostly-numeric sensor columns as time
      - Require high datetime parse success rate
    """
    # 0) Prefer Date + Time pair (AirQualityUCI)
    if ("Date" in df.columns) and ("Time" in df.columns):
        try:
            comb = (df["Date"].astype(str).str.strip() + " " + df["Time"].astype(str).str.strip())
            dt2 = pd.to_datetime(comb, errors="coerce", dayfirst=True)
            if dt2.notna().mean() > 0.6:
                return "Date+Time"
        except Exception:
            pass

    lower_map = {c.lower(): c for c in df.columns}

    # 1) Prefer explicit datetime-ish names, reject numeric
    for cand in ["date", "datetime", "timestamp", "time", "ds"]:
        if cand in lower_map:
            col = lower_map[cand]
            if _is_mostly_numeric(df[col]):
                continue
            dt = pd.to_datetime(df[col], errors="coerce", dayfirst=True)
            if dt.notna().mean() > 0.8:
                return col

    # 2) Fallback scan: only non-numeric columns, require very high parse rate
    for c in list(df.columns)[:40]:
        if _is_mostly_numeric(df[c]):
            continue
        dt = pd.to_datetime(df[c], errors="coerce", dayfirst=True)
        if dt.notna().mean() > 0.85:
            return c

    return None



def _is_geo_like(name: str) -> bool:
    n = (name or "").strip().lower()
    geo_tokens = [
        "lat", "latitude", "lon", "long", "longitude",
        "zip", "zipcode", "postal", "address",
        "x_coord", "y_coord", "geocode", "borough",
    ]
    return any(t in n for t in geo_tokens)

def _is_id_like(name: str) -> bool:
    n = (name or "").strip().lower()
    id_tokens = ["id", "uuid", "guid", "key", "nbr", "number", "encounter", "patient"]
    # allow true measures like "number_of_persons_injured" to pass later via scoring
    return any(n == t or n.endswith("_" + t) or n.startswith(t + "_") for t in id_tokens) or (
        ("id" in n or "uuid" in n or "guid" in n or "encounter" in n) and len(n) <= 24
    )

def _target_score(name: str) -> float:
    """
    Prefer meaningful measures (counts, totals, injuries, amounts),
    penalize geo + pure IDs.
    """
    n = (name or "").strip().lower()
    score = 0.0
    if _is_geo_like(n):
        score -= 10.0
    if _is_id_like(n):
        score -= 6.0

    good_tokens = ["count", "total", "sum", "amount", "value", "rate", "injur", "killed", "fatal", "crash", "collision"]
    for t in good_tokens:
        if t in n:
            score += 2.0

    # coordinates / trivial measures penalty
    bad_tokens = ["longitude", "latitude"]
    for t in bad_tokens:
        if t in n:
            score -= 8.0

    return score

def _prepare_model_frame(df: "pd.DataFrame", tcol: str | None, ycol: str, numeric_cols: list[str]) -> tuple["pd.DataFrame", str | None, str, str]:
    """
    If we have a time column, convert event-level rows into a stable time series
    by aggregating numeric columns over time buckets (daily by default).
    """
    try:
        import pandas as pd
        import numpy as np
    except Exception:
        return df, tcol, ycol, "prep_skipped:pandas_missing"

    if not tcol or tcol not in df.columns:
        return df, tcol, ycol, "prep_note:no_time_col"

    dt = pd.to_datetime(df[tcol], errors="coerce", infer_datetime_format=True)
    ok = dt.notna().mean()
    if ok < 0.20:
        return df, tcol, ycol, f"prep_note:time_parse_low_ok={ok:.2f}"

    # pick a reasonable bucket; daily is safest default
    freq = "D"

    work = df.copy()
    work["__t"] = dt
    work = work[work["__t"].notna()].copy()

    # keep only numeric columns that are real signals
    cols = [c for c in numeric_cols if c in work.columns]
    if len(cols) == 0:
        return df, tcol, ycol, "prep_note:no_numeric_cols"

    # Heuristic: sum “count-like” measures, average everything else
    def agg_for(c: str) -> str:
        n = c.lower()
        if any(t in n for t in ["count", "total", "sum", "injur", "killed", "fatal", "crash", "collision"]):
            return "sum"
        return "mean"

    agg_map = {c: agg_for(c) for c in cols}
    g = work.groupby(pd.Grouper(key="__t", freq=freq)).agg(agg_map)

    # If ycol is geo/id-like, prefer a derived event count target
    if _is_geo_like(ycol) or _is_id_like(ycol):
        g["EVENT_COUNT"] = 1.0
        ycol2 = "EVENT_COUNT"
    else:
        ycol2 = ycol

    out = g.reset_index().rename(columns={"__t": "__time"})
    # ensure y exists
    if ycol2 not in out.columns:
        out["EVENT_COUNT"] = 1.0
        ycol2 = "EVENT_COUNT"

    # drop all-null columns
    for c in list(out.columns):
        if c != "__time" and out[c].notna().sum() == 0:
            out.drop(columns=[c], inplace=True)

    return out, "__time", ycol2, f"prep_ok:aggregated_freq={freq}"

def _infer_target(df: pd.DataFrame, numeric_cols: List[str]) -> Optional[str]:
    """
    Pick a sane numeric target:
      - avoid obvious IDs and geo coordinates
      - prefer “count/total/injured/killed/value”-like signals
      - require enough non-missing values and non-trivial variance
    """
    best = None
    best_score = -1e18

    for c in numeric_cols:
        base = _target_score(c)

        v = pd.to_numeric(df[c], errors="coerce")
        ok = float(v.notna().mean())
        if ok < 0.70:
            continue

        sd = float(np.nanstd(v))
        if not np.isfinite(sd) or sd <= 0:
            continue

        # penalize almost-constant columns
        q10 = float(np.nanpercentile(v, 10))
        q90 = float(np.nanpercentile(v, 90))
        spread = q90 - q10
        if not np.isfinite(spread) or spread <= 0:
            continue

        score = base + (spread / (sd + 1e-9)) + (ok * 0.5)

        if score > best_score:
            best_score = score
            best = c

    return best


def _safe_series(df: pd.DataFrame, col: str) -> np.ndarray:
    return pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).values.astype(float)


def _ar1_fit(y: np.ndarray) -> Dict[str, float]:
    y = y.astype(float)
    y1 = y[1:]
    y0 = y[:-1]
    if y0.size < 50:
        raise ValueError("ar1_fit: not enough samples")
    v = np.var(y0)
    if not np.isfinite(v) or v < 1e-12:
        raise ValueError("ar1_fit: degenerate variance")
    phi = float(np.cov(y1, y0, bias=True)[0, 1] / v)
    resid = y1 - phi * y0
    sigma = float(np.std(resid))
    return {"phi": phi, "sigma": sigma}


def _ar1_forecast_bands(y: np.ndarray, horizon: int = 24, alpha: float = 0.05, seed: int = 0) -> Dict[str, Any]:
    y = y.astype(float)
    y = y[np.isfinite(y)]
    if y.size < 200:
        raise ValueError("ar1_forecast: not enough finite samples")

    params = _ar1_fit(y)
    phi = params["phi"]
    sigma = params["sigma"]
    last = float(y[-1])

    rng = np.random.default_rng(int(seed))
    z = float(abs(np.quantile(rng.standard_normal(200000), 1.0 - alpha / 2.0)))

    fc, lo, hi = [], [], []
    for h in range(1, horizon + 1):
        mean_h = (phi ** h) * last
        if abs(phi) < 0.999999:
            var_h = (sigma ** 2) * (1.0 - (phi ** (2 * h))) / (1.0 - (phi ** 2))
        else:
            var_h = (sigma ** 2) * float(h)
        sd_h = float(np.sqrt(max(var_h, 0.0)))
        fc.append(float(mean_h))
        lo.append(float(mean_h - z * sd_h))
        hi.append(float(mean_h + z * sd_h))

    return {
        "method": "ar1",
        "horizon": int(horizon),
        "alpha": float(alpha),
        "phi": float(phi),
        "sigma": float(sigma),
        "forecast": fc,
        "lower": lo,
        "upper": hi,
        "note": "ok",
        "error": None,
    }


def _safe_forecast(df: pd.DataFrame, ycol: str, tcol: Optional[str], seed: int) -> Dict[str, Any]:
    # Try external forecast_with_bands if available, but never let it crash the engine.
    if callable(forecast_with_bands):
        try:
            sig = inspect.signature(forecast_with_bands)
            kw = {}
            # Some versions accept df/target_col/time_col; others accept series.
            if "df" in sig.parameters:
                kw["df"] = df
            if "target_col" in sig.parameters:
                kw["target_col"] = ycol
            if "time_col" in sig.parameters and tcol is not None:
                kw["time_col"] = tcol

            if "series" in sig.parameters and "df" not in sig.parameters:
                s = pd.to_numeric(df[ycol], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
                if len(s) < 200:
                    return {"note": "skipped", "error": "forecasting_failed:not_enough_numeric_rows"}
                kw["series"] = s

            if "horizon" in sig.parameters:
                kw["horizon"] = 24
            if "alpha" in sig.parameters:
                kw["alpha"] = 0.05
            if "seed" in sig.parameters:
                kw["seed"] = int(seed)

            # Only pass seasonal_periods if the function supports it.
            if "seasonal_periods" in sig.parameters:
                kw["seasonal_periods"] = None

            res = forecast_with_bands(**kw)  # type: ignore
            if isinstance(res, dict):
                res.setdefault("note", "ok")
                res.setdefault("error", None)
                return res
            return {"note": "ok", "error": None, "result": res}
        except Exception as e:
            # Fall through to AR1
            pass

    try:
        y = _safe_series(df, ycol)
        return _ar1_forecast_bands(y, horizon=24, alpha=0.05, seed=seed)
    except Exception as e:
        return {"note": "failed", "error": f"{type(e).__name__}: {e}"}


def _safe_causal(df: pd.DataFrame, ycol: str, numeric_cols: List[str]) -> Dict[str, Any]:
    # Prefer the robust wrapper if present
    fn = causal_what_if_linear if callable(causal_what_if_linear) else causal_what_if_linear
    if not callable(fn):
        return {"note": "skipped", "error": "causal_unavailable"}

    # pick best treatment by abs corr to ycol
    y = _safe_series(df, ycol)
    best_feat = None
    best_abs = -1.0
    for c in numeric_cols:
        if c == ycol:
            continue
        x = _safe_series(df, c)
        m = np.isfinite(x) & np.isfinite(y)
        if int(m.sum()) < 200:
            continue
        if np.nanstd(x[m]) < 1e-12 or np.nanstd(y[m]) < 1e-12:
            continue
        r = float(np.corrcoef(x[m], y[m])[0, 1])
        if abs(r) > best_abs:
            best_abs = abs(r)
            best_feat = c

    if not best_feat:
        return {"note": "skipped", "error": "no_treatment_found"}

    try:
        sig = inspect.signature(fn)
        kw = {"df": df}

        if "target_col" in sig.parameters:
            kw["target_col"] = ycol

        # new naming
        if "treatment_col" in sig.parameters:
            kw["treatment_col"] = best_feat
        # older naming
        elif "feature_col" in sig.parameters:
            kw["feature_col"] = best_feat

        if "controls" in sig.parameters:
            kw["controls"] = []
        if "feature_cols" in sig.parameters:
            kw["feature_cols"] = []
        if "delta" in sig.parameters:
            kw["delta"] = 1.0
        if "delta_x" in sig.parameters:
            kw["delta_x"] = 1.0

        res = fn(**kw)  # type: ignore
        if isinstance(res, dict):
            res.setdefault("note", "ok")
            res.setdefault("error", None)
            return res
        return {"note": "ok", "error": None, "result": res}
    except Exception as e:
        return {"note": "failed", "error": f"{type(e).__name__}: {e}"}


def compute_deep_math_v3(
    df: pd.DataFrame,
    seed: int = 0,
    target_col: Optional[str] = None,
    time_col: Optional[str] = None,
    max_corrs: int = 200,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "version": "deep_math_v3",
        "seed": int(seed),
        "rows": int(df.shape[0]),
        "cols": int(df.shape[1]),
        "optional_import_errors": dict(_OPTIONAL_IMPORT_ERRORS) if _OPTIONAL_IMPORT_ERRORS else {},
    }

    numeric_cols = _numeric_cols(df, max_cols=35)
    out["numeric_cols_used"] = numeric_cols
    out["numeric_cols_n"] = int(len(numeric_cols))

    # Always present keys for UI safety
    out["forecasting"] = {"note": "skipped", "error": None}
    out["causal_what_if"] = {"note": "skipped", "error": None}
    out["physics"] = {"note": "skipped", "error": None}
    out["physics_v2"] = {"note": "skipped", "error": None}
    out["physics_discovery"] = {"note": "skipped", "error": None}
    out["physics_invariants"] = {"note": "skipped", "error": None}
    out["physics_consistency_score"] = None
    tcol = time_col or _infer_time_col(df)
    ycol = target_col or _infer_target(df, numeric_cols) or numeric_cols[0]
    out["time_col"] = tcol
    out["target_col"] = ycol
    # Prepare a stable model frame (aggregate event rows into time buckets if possible)
    try:
        df, tcol, ycol, prep_note = _prepare_model_frame(df=df, tcol=tcol, ycol=ycol, numeric_cols=numeric_cols)
        out["prep_note"] = prep_note
        out["time_col"] = tcol
        out["target_col"] = ycol
    except Exception as e:
        out["prep_note"] = f"prep_failed:{type(e).__name__}:{e}"

    # 1) Forecasting
    out["forecasting"] = _safe_forecast(df=df, ycol=ycol, tcol=tcol, seed=seed)

    # 2) Regimes
    if callable(detect_regimes):
        try:
            out["regimes"] = detect_regimes(df, target_col=ycol)
            if isinstance(out["regimes"], dict):
                out["regimes"].setdefault("note", "ok")
        except Exception as e:
            out["regimes"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}
    else:
        out["regimes"] = {"note": "skipped", "error": "detect_regimes_unavailable"}

    # 3) Uncertainty (bootstrap corr CI)
    try:
        cols = numeric_cols[:10]
        arr = {c: pd.to_numeric(df[c], errors="coerce").values.astype(float) for c in cols}

        pairs = []
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                x = arr[cols[i]]
                y = arr[cols[j]]
                m = np.isfinite(x) & np.isfinite(y)
                if int(m.sum()) < 120:
                    continue
                if np.nanstd(x[m]) < 1e-12 or np.nanstd(y[m]) < 1e-12:
                    continue
                r = float(np.corrcoef(x[m], y[m])[0, 1])
                pairs.append((abs(r), cols[i], cols[j], r, int(m.sum())))
        pairs.sort(key=lambda t: t[0], reverse=True)
        pairs = pairs[: min(8, max_corrs)]

        boot = []
        if callable(bootstrap_corr_ci):
            for ab, a, b, r, n in pairs:
                ci = bootstrap_corr_ci(arr[a], arr[b], B=500, alpha=0.05, seed=seed)
                boot.append(
                    {
                        "a": a,
                        "b": b,
                        "n": n,
                        "pearson_r": float(r),
                        "pearson_ci95": [ci.get("lo"), ci.get("hi")],
                        "bootstrap": ci,
                    }
                )
        else:
            boot = [{"note": "skipped", "error": "bootstrap_corr_ci_unavailable"}]

        out["uncertainty"] = {"top_corr_bootstrap": boot}
    except Exception as e:
        out["uncertainty"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # 4) Risk surface
    try:
        if callable(monte_carlo_risk_surface):
            y = pd.to_numeric(df[ycol], errors="coerce").values.astype(float)
            out["risk_surface"] = monte_carlo_risk_surface(y, horizon=24, n_sims=2000, seed=seed)
            if isinstance(out["risk_surface"], dict):
                out["risk_surface"].setdefault("note", "ok")
        else:
            out["risk_surface"] = {"note": "skipped", "error": "monte_carlo_risk_surface_unavailable"}
    except Exception as e:
        out["risk_surface"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # 5) Causal what-if
    out["causal_what_if"] = _safe_causal(df=df, ycol=ycol, numeric_cols=numeric_cols)

    # 8) Physics-inspired diagnostics (safe, optional)
    try:
        if callable(physics_diagnostics) or callable(globals().get('physics_diagnostics_fallback')):
            fn = physics_diagnostics if callable(physics_diagnostics) else globals().get("physics_diagnostics_fallback")
            out["physics"] = fn(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics"], dict):
                out["physics"].setdefault("note", "ok")
        else:
            out["physics"] = {"note": "skipped", "error": "physics_diagnostics_unavailable"}
    except Exception as e:
        out["physics"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # 6) Optional simulation from regression uncertainty (if wrapper provides what we need)
    try:
        cw = out.get("causal_what_if", {})
        if isinstance(cw, dict) and callable(simulate_counterfactuals_from_regression):
            if "beta" in cw and "beta_ci95" in cw:
                out["simulation"] = simulate_counterfactuals_from_regression(
                    cw["beta"], cw["beta_ci95"], delta_x_grid=[-2, -1, -0.5, 0.5, 1, 2]
                )
    except Exception as e:
        out["simulation"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # 7) Optimization demo
    try:
        demo = {}
        if callable(solve_linear_program):
            demo["lp_example"] = solve_linear_program(
                c=[1, 2], A_ub=[[1, 1], [-1, 2]], b_ub=[3, 2], bounds=[(0, None), (0, None)]
            )
        else:
            demo["lp_example"] = {"note": "skipped", "error": "solve_linear_program_unavailable"}

        if callable(minimize_nonlinear):
            demo["nonlinear_example"] = minimize_nonlinear(fun_name="quadratic_bowl", x0=[0, 0])
        else:
            demo["nonlinear_example"] = {"note": "skipped", "error": "minimize_nonlinear_unavailable"}

        out["optimization_demo"] = demo
    except Exception as e:
        out["optimization_demo"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # Physics Layer (Phases 2–4)
    #   Phase 2: Physics v2 diagnostics (stronger ODE-fit variants)
    #   Phase 3: Physics discovery (multi-model selection)
    #   Phase 4: Invariants + physics-consistency score
    # ============================================================

    # Baseline diagnostics (kept for continuity)
    try:
        if callable(physics_diagnostics) or callable(globals().get('physics_diagnostics_fallback')):
            fn = physics_diagnostics if callable(physics_diagnostics) else globals().get("physics_diagnostics_fallback")
            out["physics"] = fn(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics"], dict):
                out["physics"].setdefault("note", "ok")
        else:
            out["physics"] = {"note": "skipped", "error": "physics_diagnostics_unavailable"}
    except Exception as e:
        out["physics"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # Phase 2: v2 diagnostics (your physics_v2.py)
    try:
        if callable(physics_diagnostics_v2):
            out["physics_v2"] = physics_diagnostics_v2(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics_v2"], dict):
                out["physics_v2"].setdefault("note", "ok")
        else:
            out["physics_v2"] = {"note": "skipped", "error": "physics_diagnostics_v2_unavailable"}
    except Exception as e:
        out["physics_v2"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # Phase 3: discovery (multi-model selection)
    try:
        if callable(physics_discovery) or callable(globals().get('physics_discovery_fallback')):
            fn = physics_discovery if callable(physics_discovery) else globals().get("physics_discovery_fallback")
            out["physics_discovery"] = fn(df=df, target_col=ycol, time_col=tcol, seed=seed)
            if isinstance(out["physics_discovery"], dict):
                out["physics_discovery"].setdefault("note", "ok")
        else:
            out["physics_discovery"] = {"note": "skipped", "error": "physics_discovery_unavailable"}
    except Exception as e:
        out["physics_discovery"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}

    # Phase 4: invariants + consistency score
    try:
        if callable(physics_invariants):
            inv = physics_invariants(df=df, target_col=ycol, time_col=tcol, seed=seed)
            out["physics_invariants"] = inv if isinstance(inv, dict) else {"note": "ok", "result": inv}
            if isinstance(out["physics_invariants"], dict):
                out["physics_invariants"].setdefault("note", "ok")
                out["physics_consistency_score"] = (
                    out["physics_invariants"].get("physics_consistency_score")
                    or out["physics_invariants"].get("score")
                )
            else:
                out["physics_consistency_score"] = None
        else:
            # Fallback invariant score (never crashes):
            y = pd.to_numeric(df[ycol], errors="coerce").astype(float).values
            m = np.isfinite(y)
            y2 = y[m]
            if y2.size < 200:
                out["physics_invariants"] = {"note": "skipped", "error": "physics_invariants_unavailable"}
                out["physics_consistency_score"] = None
            else:
                frac_finite = float(np.mean(m))
                frac_neg = float(np.mean(y2 < 0))
                score = max(0.0, min(1.0, frac_finite * (1.0 - frac_neg)))
                out["physics_invariants"] = {"note": "ok", "finite_fraction": frac_finite, "negative_fraction": frac_neg}
                out["physics_consistency_score"] = float(score)
    except Exception as e:
        out["physics_invariants"] = {"note": "failed", "error": f"{type(e).__name__}: {e}"}
        out["physics_consistency_score"] = None


    return out
# Backward-compatible wrappers
def compute_deep_math_v2(df: pd.DataFrame, seed: int = 0, **kwargs) -> Dict[str, Any]:
    return compute_deep_math_v3(df=df, seed=seed, **kwargs)

def compute_deep_math_v1(df: pd.DataFrame, seed: int = 0, **kwargs) -> Dict[str, Any]:
    return compute_deep_math_v3(df=df, seed=seed, **kwargs)

# Runner alias
compute_deep_math = compute_deep_math_v3

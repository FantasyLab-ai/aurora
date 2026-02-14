from __future__ import annotations
from typing import Any, Dict, Optional, List, Tuple
import numpy as np
import pandas as pd

def _to_dt(df: pd.DataFrame, time_col: Optional[str]) -> Optional[pd.DatetimeIndex]:
    if not time_col or time_col not in df.columns:
        return None
    dt = pd.to_datetime(df[time_col], errors="coerce")
    if dt.notna().mean() < 0.6:
        return None
    return pd.DatetimeIndex(dt)

def _clean_y(s: pd.Series) -> np.ndarray:
    y = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().values.astype(float)
    return y

def _seasonal_period_guess(dt: Optional[pd.DatetimeIndex]) -> Optional[int]:
    # lightweight heuristic: if hourly data, 24; if daily data, 7; otherwise None
    if dt is None or len(dt) < 50:
        return None
    diffs = np.diff(dt.view("i8"))
    med = np.median(diffs)
    # ns thresholds
    hour = 3600 * 10**9
    day = 24 * hour
    if abs(med - hour) / hour < 0.2:
        return 24
    if abs(med - day) / day < 0.2:
        return 7
    return None

def _metric_rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    e = y_pred - y_true
    return float(np.sqrt(np.nanmean(e**2)))

def _metric_mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    denom = np.maximum(np.abs(y_true), 1e-9)
    return float(np.nanmean(np.abs((y_pred - y_true) / denom)))

def _fit_forecast_ets(y: np.ndarray, h: int, sp: Optional[int]) -> Dict[str, Any]:
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        yy = pd.Series(y)
        seasonal = "add" if sp and sp >= 4 else None
        model = ExponentialSmoothing(
            yy, trend="add", seasonal=seasonal,
            seasonal_periods=sp if seasonal else None,
            initialization_method="estimated"
        )
        res = model.fit(optimized=True)
        fc = res.forecast(h).values.astype(float)
        resid = (yy - res.fittedvalues).dropna().values.astype(float)
        return {"name": "ETS", "forecast": fc, "resid_std": float(np.nanstd(resid))}
    except Exception as e:
        return {"name": "ETS", "error": f"{type(e).__name__}:{e}"}

def _fit_forecast_sarimax(y: np.ndarray, h: int, sp: Optional[int]) -> Dict[str, Any]:
    # very conservative SARIMAX so it doesn’t blow up
    try:
        import statsmodels.api as sm
        order = (1, 1, 1)
        seasonal_order = (1, 0, 1, sp) if sp and sp >= 4 else (0, 0, 0, 0)
        model = sm.tsa.SARIMAX(y, order=order, seasonal_order=seasonal_order, enforce_stationarity=False, enforce_invertibility=False)
        res = model.fit(disp=False)
        fc = res.forecast(h).astype(float)
        resid_std = float(np.nanstd(res.resid))
        return {"name": "SARIMAX(1,1,1)", "forecast": np.asarray(fc, dtype=float), "resid_std": resid_std}
    except Exception as e:
        return {"name": "SARIMAX(1,1,1)", "error": f"{type(e).__name__}:{e}"}

def _fit_forecast_seasonal_naive(y: np.ndarray, h: int, sp: Optional[int]) -> Dict[str, Any]:
    if not sp or sp < 2 or len(y) < sp + h:
        return {"name": "SeasonalNaive", "error": "no_seasonality"}
    last_season = y[-sp:]
    reps = int(np.ceil(h / sp))
    fc = np.tile(last_season, reps)[:h].astype(float)
    resid = y[sp:] - y[:-sp]
    return {"name": "SeasonalNaive", "forecast": fc, "resid_std": float(np.nanstd(resid))}

def _fit_forecast_ar1(y: np.ndarray, h: int) -> Dict[str, Any]:
    if len(y) < 30:
        return {"name": "AR1", "error": "too_few_points"}
    x = y[:-1]; z = y[1:]
    a = float(np.cov(x, z, ddof=1)[0, 1] / (np.var(x, ddof=1) + 1e-12))
    b = float(np.mean(z) - a * np.mean(x))
    last = float(y[-1])
    fc = []
    for _ in range(h):
        last = a * last + b
        fc.append(float(last))
    resid = z - (a * x + b)
    return {"name": "AR1", "forecast": np.asarray(fc, dtype=float), "resid_std": float(np.nanstd(resid)), "a": a, "b": b}

def _resid_bands(point_fc: np.ndarray, resid_std: float, B: int = 800, alpha: float = 0.05, seed: int = 0) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    sims = rng.normal(0.0, resid_std, size=(B, len(point_fc))) + point_fc[None, :]
    lo = np.quantile(sims, alpha / 2, axis=0)
    hi = np.quantile(sims, 1 - alpha / 2, axis=0)
    return {"method": "resid_normal", "alpha": float(alpha), "lo": lo.tolist(), "hi": hi.tolist()}

def _rolling_backtest(y: np.ndarray, h: int, sp: Optional[int], step: int = 30) -> Dict[str, Any]:
    # model selection uses a small rolling backtest
    n = len(y)
    if n < 250:
        return {"note": "skipped_small_n", "n": int(n)}
    starts = list(range(max(80, n // 3), n - h, step))
    scores = {"ETS": [], "SARIMAX(1,1,1)": [], "SeasonalNaive": [], "AR1": []}

    for t in starts:
        train = y[:t]
        test = y[t:t + h]

        cand = [
            _fit_forecast_ets(train, h, sp),
            _fit_forecast_sarimax(train, h, sp),
            _fit_forecast_seasonal_naive(train, h, sp),
            _fit_forecast_ar1(train, h),
        ]
        for c in cand:
            if "forecast" not in c:
                continue
            rmse = _metric_rmse(test, c["forecast"])
            scores[c["name"]].append(rmse)

    out = {"windows": int(len(starts)), "horizon": int(h), "rmse": {}}
    for k, v in scores.items():
        if v:
            out["rmse"][k] = {"mean": float(np.mean(v)), "p50": float(np.median(v))}
    return out

def forecast_worldclass(
    df: pd.DataFrame,
    target_col: str,
    time_col: Optional[str] = None,
    horizon: int = 24,
    seed: int = 0,
) -> Dict[str, Any]:
    if target_col not in df.columns:
        return {"error": f"missing_target_col:{target_col}"}

    dt = _to_dt(df, time_col)
    work = df.copy()
    if dt is not None:
        work = work.assign(__dt__=dt).sort_values("__dt__")

    y = _clean_y(work[target_col])
    if len(y) < 60:
        return {"error": "too_few_points", "n": int(len(y))}

    sp = _seasonal_period_guess(dt)
    backtest = _rolling_backtest(y, horizon, sp)

    candidates = [
        _fit_forecast_ets(y, horizon, sp),
        _fit_forecast_sarimax(y, horizon, sp),
        _fit_forecast_seasonal_naive(y, horizon, sp),
        _fit_forecast_ar1(y, horizon),
    ]

    # pick best using backtest mean RMSE when available; else fallback to no-error priority
    bt_rmse = backtest.get("rmse", {})
    best = None
    best_score = 1e99
    for c in candidates:
        if "forecast" not in c:
            continue
        name = c["name"]
        if name in bt_rmse and "mean" in bt_rmse[name]:
            score = bt_rmse[name]["mean"]
        else:
            score = 9e98  # unknown score => worse
        if score < best_score:
            best_score = score
            best = c

    if best is None:
        return {"error": "all_models_failed"}

    resid_std = float(best.get("resid_std", np.nan))
    bands = _resid_bands(best["forecast"], resid_std, seed=seed) if np.isfinite(resid_std) and resid_std > 0 else {"note": "no_resid_std"}

    return {
        "engine": "model_zoo_selector",
        "target_col": target_col,
        "time_col": time_col,
        "n_used": int(len(y)),
        "seasonal_period_guess": sp,
        "backtest": backtest,
        "winner": {
            "name": best["name"],
            "params": {k: v for k, v in best.items() if k not in ("forecast",)},
            "forecast": best["forecast"].tolist(),
            "prediction_interval": bands,
        },
        "all_models": [{k: v for k, v in c.items() if k != "forecast"} | ({"ok": "forecast" in c} ) for c in candidates],
    }
# ------------------------------
# Compatibility shim (required by deep_math.py)
# ------------------------------
def forecast_with_bands(
    df,
    target_col: str,
    time_col: str | None = None,
    horizon: int = 30,
    seed: int = 0,
    method: str = "naive",
):
    """
    Simple forecast with uncertainty bands.

    Returns:
      {
        "target_col": ...,
        "horizon": ...,
        "method": ...,
        "yhat": [...],
        "lower": [...],
        "upper": [...],
        "note": ...
      }
    """
    import numpy as np
    import pandas as pd

    s = pd.to_numeric(df.get(target_col), errors="coerce").dropna()
    horizon = int(max(1, horizon))

    if len(s) < 5:
        y = [float("nan")] * horizon
        return {
            "target_col": target_col,
            "horizon": horizon,
            "method": method,
            "yhat": y,
            "lower": y,
            "upper": y,
            "note": "insufficient_data",
        }

    y = s.to_numpy(dtype=float)
    last = float(y[-1])

    diffs = np.diff(y)
    mad = float(np.nanmedian(np.abs(diffs))) if len(diffs) else 0.0
    if not np.isfinite(mad) or mad == 0.0:
        mad = float(np.nanstd(y)) * 0.25

    yhat = np.full(horizon, last, dtype=float)
    lower = yhat - 1.96 * mad
    upper = yhat + 1.96 * mad

    return {
        "target_col": target_col,
        "horizon": horizon,
        "method": method,
        "yhat": yhat.tolist(),
        "lower": lower.tolist(),
        "upper": upper.tolist(),
        "note": "compat_shim",
    }

# === WORLDCLASS_COMPAT_START ===
# Stable forecast_with_bands() used by deep_math_v3.
# Accepts extra kwargs (e.g. seasonal_periods) without breaking.
def forecast_with_bands(
    y,
    horizon: int = 24,
    seasonal_periods=None,
    alpha: float = 0.05,
    n_boot: int = 500,
    **kwargs
):
    """
    Return a simple forecast with uncertainty bands.

    Parameters
    ----------
    y : array-like
        Time series values (1D).
    horizon : int
        Forecast horizon (steps).
    seasonal_periods : any
        Accepted for compatibility; not required by this simple model.
    alpha : float
        Two-sided error rate for bands (e.g., 0.05 => 95% band).
    n_boot : int
        Number of bootstrap simulations.
    **kwargs :
        Ignored extras for API stability.

    Returns
    -------
    dict with keys: method, horizon, yhat, bands, note
    """
    try:
        import numpy as np
    except Exception as e:
        return {"error": f"ImportError:numpy:{type(e).__name__}:{e}"}

    yy = np.asarray(list(y), dtype=float)
    yy = yy[np.isfinite(yy)]
    if yy.size < 5:
        return {"error": f"ValueError:need_more_data:n={int(yy.size)}"}

    # AR(1) on differences for robustness
    x = yy[:-1]
    z = yy[1:]
    vx = np.var(x)
    if not np.isfinite(vx) or vx <= 1e-12:
        phi = 0.0
        c = float(np.nanmean(z))
    else:
        phi = float(np.cov(x, z, bias=True)[0, 1] / vx)
        c = float(np.nanmean(z) - phi * np.nanmean(x))

    resid = z - (c + phi * x)
    resid = resid[np.isfinite(resid)]
    if resid.size < 5:
        resid = np.array([0.0], dtype=float)

    last = float(yy[-1])
    yhat = []
    cur = last
    for _ in range(int(horizon)):
        cur = c + phi * cur
        yhat.append(float(cur))

    # Monte Carlo bands
    sims = np.empty((int(n_boot), int(horizon)), dtype=float)
    for b in range(int(n_boot)):
        cur = last
        for t in range(int(horizon)):
            eps = float(np.random.choice(resid))
            cur = (c + phi * cur) + eps
            sims[b, t] = cur

    lo = np.quantile(sims, alpha / 2.0, axis=0).tolist()
    hi = np.quantile(sims, 1.0 - alpha / 2.0, axis=0).tolist()

    return {
        "method": "AR1_MC",
        "horizon": int(horizon),
        "seasonal_periods": seasonal_periods,
        "yhat": yhat,
        "bands": {"p_lo": lo, "p_hi": hi},
        "note": "ok"
    }
# === WORLDCLASS_COMPAT_END ===

# =============================================================================
# Compatibility wrapper: forecast_with_bands
# - Accepts arbitrary kwargs (e.g., seasonal_periods) so callers won't break
# - Sanitizes inputs (Series/DataFrame/object arrays) into a numeric 1D series
# - Returns forecast + bands as plain python lists
# =============================================================================
def forecast_with_bands(y, horizon: int = 24, alpha: float = 0.05, method: str = "ar1", **kwargs):
    """
    Stable forecast helper used by deep-math.
    Accepts extra kwargs for forward/backward compatibility (e.g. seasonal_periods).
    """
    import numpy as np
    import pandas as pd

    # Normalize horizon
    try:
        h = int(horizon)
    except Exception:
        h = 24
    h = max(1, min(h, 1000))

    # Convert y -> 1D numeric array
    if isinstance(y, pd.DataFrame):
        num_cols = [c for c in y.columns if pd.api.types.is_numeric_dtype(y[c])]
        if not num_cols:
            # try coercion on all columns
            yy = None
            for c in y.columns:
                s = pd.to_numeric(y[c], errors="coerce")
                if s.notna().sum() >= 10:
                    yy = s
                    break
            if yy is None:
                raise ValueError("forecast_with_bands: no usable numeric data in DataFrame")
        else:
            yy = pd.to_numeric(y[num_cols[0]], errors="coerce")
    else:
        # Series / list / ndarray / object
        yy = pd.to_numeric(y, errors="coerce")

    yy = yy.dropna()
    if len(yy) < 10:
        raise ValueError("forecast_with_bands: not enough numeric points after cleaning")

    arr = yy.to_numpy(dtype=float)

    # Simple AR(1) fallback model (robust, no external deps)
    x = arr[:-1]
    z = arr[1:]
    denom = float(np.dot(x, x)) if len(x) else 0.0
    phi = float(np.dot(x, z) / denom) if denom > 1e-12 else 0.0
    resid = z - (phi * x)
    sigma = float(np.std(resid)) if len(resid) else float(np.std(arr))

    # Forecast
    f = []
    last = float(arr[-1])
    for _ in range(h):
        last = phi * last
        f.append(last)

    f = np.asarray(f, dtype=float)

    # Very simple symmetric bands using Normal approx
    # (If scipy not present, use 1.96 for ~95% and scale alpha crudely)
    z95 = 1.96
    try:
        # approximate z-score from alpha if needed
        # keep stable, no scipy dependency
        if alpha and alpha > 0 and alpha < 1:
            if abs(alpha - 0.05) > 1e-6:
                # crude mapping for common alphas
                z_map = {0.10: 1.645, 0.05: 1.96, 0.02: 2.326, 0.01: 2.576}
                z95 = z_map.get(float(alpha), 1.96)
    except Exception:
        z95 = 1.96

    lo = (f - z95 * sigma).tolist()
    hi = (f + z95 * sigma).tolist()

    return {
        "method": method,
        "horizon": h,
        "alpha": float(alpha),
        "phi": phi,
        "sigma": sigma,
        "forecast": f.tolist(),
        "lower": lo,
        "upper": hi,
        "note": "ok",
    }


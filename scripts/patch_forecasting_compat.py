import os
from pathlib import Path
import hashlib

ROOT = Path(__file__).resolve().parents[1]  # .../scripts -> repo root
FORECASTING = ROOT / "fantasyai" / "aurora" / "math" / "forecasting.py"

SHIM = r"""

# -----------------------------
# Compatibility shim (auto-added)
# Provides forecast_with_bands() expected by deep_math
# -----------------------------
from __future__ import annotations

def forecast_with_bands(y, horizon: int = 30, alpha: float = 0.05, method: str = "auto", seasonal_period: int | None = None):
    """
    Return a simple forecast with uncertainty bands.

    Parameters
    ----------
    y : array-like (1D)
        History.
    horizon : int
        Forecast steps.
    alpha : float
        Tail probability for (1-alpha) interval.
    method : str
        "auto" tries statsmodels; falls back to naive.
    seasonal_period : int | None
        Optional seasonal period for fallback.

    Returns
    -------
    dict with keys:
      - yhat: list[float]
      - lower: list[float]
      - upper: list[float]
      - meta: dict
    """
    import math
    import numpy as np

    y_arr = np.asarray(list(y), dtype=float)
    y_arr = y_arr[np.isfinite(y_arr)]
    if y_arr.size < 5:
        mu = float(np.nanmean(y_arr)) if y_arr.size else 0.0
        yhat = [mu] * int(horizon)
        return {"yhat": yhat, "lower": yhat, "upper": yhat, "meta": {"method": "degenerate_mean", "n": int(y_arr.size)}}

    # Helper: z for normal approx
    # (good enough for production bands; can be upgraded later)
    def z_from_alpha(a: float) -> float:
        # Approx inverse CDF using error function inverse approximation
        # avoid scipy dependency for the shim
        # Abramowitz-Stegun style approximation
        a = max(min(a, 0.49), 1e-6)
        p = 1.0 - a/2.0
        # Winitzki approximation for erfinv
        t = 2*p - 1
        c = 0.147
        s = 1 if t >= 0 else -1
        ln = math.log(1 - t*t)
        x = s * math.sqrt( math.sqrt((2/(math.pi*c) + ln/2)**2 - ln/c) - (2/(math.pi*c) + ln/2) )
        return math.sqrt(2) * x

    hz = int(horizon)
    alpha = float(alpha)

    # Try statsmodels if available
    if method in ("auto", "statsmodels"):
        try:
            import pandas as pd
            from statsmodels.tsa.statespace.sarimax import SARIMAX

            s = pd.Series(y_arr).astype(float)
            # crude seasonal choice
            sp = int(seasonal_period) if seasonal_period else 0
            if sp and sp >= 2:
                model = SARIMAX(s, order=(1,1,1), seasonal_order=(1,0,1,sp), trend="c", enforce_stationarity=False, enforce_invertibility=False)
            else:
                model = SARIMAX(s, order=(1,1,1), trend="c", enforce_stationarity=False, enforce_invertibility=False)

            res = model.fit(disp=False)
            pred = res.get_forecast(steps=hz)
            yhat = pred.predicted_mean.to_numpy()
            ci = pred.conf_int(alpha=alpha).to_numpy()
            lower = ci[:, 0]
            upper = ci[:, 1]
            return {
                "yhat": [float(v) for v in yhat],
                "lower": [float(v) for v in lower],
                "upper": [float(v) for v in upper],
                "meta": {"method": "sarimax", "n": int(len(s))}
            }
        except Exception as e:
            # fall through to naive
            pass

    # Naive fallback: seasonal-repeat if possible, else random-walk w/ residual std
    import numpy as np
    mu = float(np.mean(y_arr))
    resid = y_arr - mu
    sigma = float(np.std(resid)) if y_arr.size > 1 else 0.0
    z = z_from_alpha(alpha)

    if seasonal_period and int(seasonal_period) > 1 and y_arr.size >= int(seasonal_period):
        sp = int(seasonal_period)
        base = list(y_arr[-sp:])
        yhat = [float(base[i % sp]) for i in range(hz)]
        lower = [float(v - z*sigma) for v in yhat]
        upper = [float(v + z*sigma) for v in yhat]
        return {"yhat": yhat, "lower": lower, "upper": upper, "meta": {"method": "seasonal_repeat", "sp": sp, "n": int(y_arr.size)}}

    # Random walk around last value
    last = float(y_arr[-1])
    yhat = [last] * hz
    lower = [last - z*sigma] * hz
    upper = [last + z*sigma] * hz
    return {"yhat": yhat, "lower": lower, "upper": upper, "meta": {"method": "rw_last", "n": int(y_arr.size), "sigma": sigma}}
"""

def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]

def main():
    if not FORECASTING.exists():
        raise SystemExit(f"[FAIL] missing: {FORECASTING}")

    txt = FORECASTING.read_text(encoding="utf-8")
    if "def forecast_with_bands" in txt:
        print("[OK] forecasting.py already has forecast_with_bands()")
        return

    # backup
    bak = FORECASTING.with_suffix(".py.bak")
    bak.write_text(txt, encoding="utf-8")
    FORECASTING.write_text(txt.rstrip() + "\n" + SHIM + "\n", encoding="utf-8")
    print(f"[OK] appended forecast_with_bands() shim -> {FORECASTING} (sha={sha(FORECASTING.read_text(encoding='utf-8'))})")
    print(f"[OK] backup -> {bak}")

if __name__ == "__main__":
    main()

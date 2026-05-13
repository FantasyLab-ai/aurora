"""
Glass-box ML layer.

The strategic rule from the brief: ML is allowed for forecasting where it
outperforms simple alternatives, but it MUST surface the math underneath.
ML is never the reasoning layer; it's a numerical estimator whose internals
are inspectable.

Two transparent forecasters live here:

* ``KNNForecaster`` — k-nearest-neighbour over recent windows. Surfaces:
    - the matched windows (start indices + distances)
    - the mean of their futures (the prediction)
    - the spread (the CI)
  Pure numpy. Simple, explainable, fast.

* ``LinearAR2Forecaster`` — order-2 autoregressive with explicit coefficients.
  Surfaces the AR coefficients φ₁, φ₂ and residual σ. Simple but useful as
  a baseline for "is the ML actually adding value?"

Both implement the same ``forecast(series, horizon)`` interface returning
a ``ForecastResult`` whose ``math`` block carries every internal number.

Glass-box rules enforced here:
  * No hidden state — every fitted parameter is in the result
  * No external deps beyond numpy
  * If the data is too short / pathological, return an error explicitly
  * The result includes both the prediction AND the recipe for reproducing it
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ForecastResult:
    """A forecast + the math behind it."""
    method: str
    horizon: int
    forecast: List[float]
    ci_low: List[float] = field(default_factory=list)
    ci_high: List[float] = field(default_factory=list)
    math: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    ok: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


# ---------------------------------------------------------------------------
# KNN window-matching forecaster
# ---------------------------------------------------------------------------

class KNNForecaster:
    """k-nearest-neighbour over recent windows.

    Strategy:
      1. Slide a window of length ``window`` across the series
      2. Compare the *latest* window (the "query") to every earlier window
         using z-normalised Euclidean distance (same metric as matrix profile)
      3. Pick the k closest historical windows
      4. Predict the future as the average of THEIR futures, with CI from
         the per-step spread across those k neighbours.

    Surfaces: the indices of the matched windows + distances + per-step
    contributions. Every prediction line is traceable to a specific past
    moment in the data.
    """

    def __init__(self, window: int = 30, k: int = 5):
        self.window = int(window)
        self.k = int(k)

    def forecast(self, series: np.ndarray, horizon: int) -> ForecastResult:
        x = np.asarray(series, dtype=float)
        x = x[np.isfinite(x)]
        n = x.size
        m = self.window
        h = int(horizon)
        if n < m + h + 5:
            return ForecastResult(method="knn_window", horizon=h,
                                   forecast=[], ok=False,
                                   error=f"series too short: n={n}, need ≥ {m+h+5}")
        # The query is the most-recent window.
        q = x[-m:]
        q_mu, q_sig = float(np.mean(q)), float(np.std(q))
        if q_sig == 0:
            return ForecastResult(method="knn_window", horizon=h,
                                   forecast=[], ok=False,
                                   error="query window is constant (std=0)")
        # Compute distances to every earlier window with full ``horizon`` ahead
        # available — i.e. windows starting at index 0..(n - m - h - 1).
        n_candidates = n - m - h
        if n_candidates < self.k + 1:
            return ForecastResult(method="knn_window", horizon=h,
                                   forecast=[], ok=False,
                                   error=f"not enough candidate windows ({n_candidates})")
        # Build matrix of historical windows (n_candidates, m).
        starts = np.arange(n_candidates)
        # Vectorised z-normalised distance.
        windows = np.lib.stride_tricks.sliding_window_view(x[:-h], m)[: n_candidates]
        mus = windows.mean(axis=1)
        sigs = windows.std(axis=1)
        sigs_safe = np.where(sigs == 0, np.inf, sigs)
        # Z-normalised query
        qz = (q - q_mu) / q_sig
        # Z-normalised candidates
        wz = (windows - mus[:, None]) / sigs_safe[:, None]
        d = np.sqrt(np.sum((wz - qz) ** 2, axis=1))
        d = np.where(np.isfinite(d), d, np.inf)
        # Pick top-k smallest distances; exclude the trivial last position if
        # it sneaks in.
        order = np.argsort(d)
        order = order[d[order] != np.inf]
        chosen = order[: self.k]
        if chosen.size == 0:
            return ForecastResult(method="knn_window", horizon=h,
                                   forecast=[], ok=False,
                                   error="no valid neighbour windows")
        # Future of each chosen window = next h samples after its end.
        futures = np.array([x[s + m : s + m + h] for s in chosen])
        # Re-scale each neighbour's future to query's scale (z-normalised match
        # → de-normalise into query units).
        # Predicted z-future = mean of (neighbour future − neighbour mu) / neighbour sig
        z_futures = ((futures - mus[chosen, None]) / sigs_safe[chosen, None])
        z_mean = z_futures.mean(axis=0)
        z_std = z_futures.std(axis=0)
        # De-normalise back to query's scale.
        pred = z_mean * q_sig + q_mu
        # CI: ± 1.96 σ across neighbours.
        ci_lo = (z_mean - 1.96 * z_std) * q_sig + q_mu
        ci_hi = (z_mean + 1.96 * z_std) * q_sig + q_mu

        return ForecastResult(
            method="knn_window",
            horizon=h,
            forecast=[float(v) for v in pred.tolist()],
            ci_low=[float(v) for v in ci_lo.tolist()],
            ci_high=[float(v) for v in ci_hi.tolist()],
            math={
                "k": int(self.k),
                "window": int(self.window),
                "neighbours": [
                    {"start_index": int(s), "distance": float(d[s]),
                     "neighbour_mu": float(mus[s]), "neighbour_sigma": float(sigs[s])}
                    for s in chosen.tolist()
                ],
                "query": {
                    "start_index": int(n - m),
                    "mu": q_mu, "sigma": q_sig,
                },
                "explanation": (
                    "predicted future = (z-normalised mean of "
                    f"{int(self.k)} most-similar past windows) × σ_query + μ_query."
                ),
            },
        )


# ---------------------------------------------------------------------------
# Linear AR(2) forecaster — explicit coefficients
# ---------------------------------------------------------------------------

class LinearAR2Forecaster:
    """y_t = c + φ₁ y_{t-1} + φ₂ y_{t-2} + ε_t  with explicit fitted coefficients.

    Useful as a baseline. The result carries c, φ₁, φ₂, residual σ, and the
    one-step-ahead RMSE on the in-sample fit.
    """

    def forecast(self, series: np.ndarray, horizon: int) -> ForecastResult:
        x = np.asarray(series, dtype=float)
        x = x[np.isfinite(x)]
        n = x.size
        h = int(horizon)
        if n < 30:
            return ForecastResult(method="ar2", horizon=h, forecast=[],
                                   ok=False, error=f"series too short ({n})")
        y = x[2:]
        X = np.column_stack([np.ones(n - 2), x[1:-1], x[:-2]])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        c, phi1, phi2 = (float(beta[0]), float(beta[1]), float(beta[2]))
        resid = y - X @ beta
        sigma = float(np.std(resid))
        rmse_in = float(np.sqrt(np.mean(resid ** 2)))
        # Forward-simulate.
        pred: List[float] = []
        ci_lo: List[float] = []
        ci_hi: List[float] = []
        prev2, prev1 = float(x[-2]), float(x[-1])
        cum_var = 0.0
        for i in range(h):
            yh = c + phi1 * prev1 + phi2 * prev2
            cum_var += sigma * sigma
            ci = 1.96 * math.sqrt(cum_var)
            pred.append(yh)
            ci_lo.append(yh - ci)
            ci_hi.append(yh + ci)
            prev2, prev1 = prev1, yh
        return ForecastResult(
            method="ar2",
            horizon=h,
            forecast=pred,
            ci_low=ci_lo,
            ci_high=ci_hi,
            math={
                "coefficients": {"c": c, "phi1": phi1, "phi2": phi2},
                "residual_sigma": sigma,
                "rmse_in_sample": rmse_in,
                "explanation": (
                    "y_t = c + φ₁·y_{t-1} + φ₂·y_{t-2} + ε. "
                    f"c={c:.4g}, φ₁={phi1:.4g}, φ₂={phi2:.4g}, σ={sigma:.4g}. "
                    "CIs widen by 1.96·σ·√h at horizon h."
                ),
            },
        )


# ---------------------------------------------------------------------------
# Convenience: pick the best forecaster on a held-out fold and return its math
# ---------------------------------------------------------------------------

def transparent_forecast(series: np.ndarray, horizon: int = 10,
                         knn_window: int = 30, knn_k: int = 5,
                         ) -> Dict[str, Any]:
    """Run both forecasters on the data, pick the one with lower hold-out RMSE,
    and return both results so the user can compare.

    The returned dict has shape:
        {
          "winner": "knn_window" | "ar2",
          "winner_rmse_holdout": float,
          "results": {"knn_window": ForecastResult.to_dict, "ar2": ...},
          "explanation": "...",
        }
    """
    x = np.asarray(series, dtype=float)
    x = x[np.isfinite(x)]
    n = x.size
    if n < 60:
        return {"winner": None, "error": f"series too short ({n})"}
    # Hold out the last ``horizon`` for validation.
    train = x[:-horizon]
    holdout = x[-horizon:]
    knn = KNNForecaster(window=knn_window, k=knn_k).forecast(train, horizon)
    ar2 = LinearAR2Forecaster().forecast(train, horizon)
    out: Dict[str, Any] = {"results": {}}
    if knn.ok:
        rmse = float(np.sqrt(np.mean((np.asarray(knn.forecast) - holdout) ** 2)))
        knn.math["holdout_rmse"] = rmse
        out["results"]["knn_window"] = knn.to_dict()
    if ar2.ok:
        rmse = float(np.sqrt(np.mean((np.asarray(ar2.forecast) - holdout) ** 2)))
        ar2.math["holdout_rmse"] = rmse
        out["results"]["ar2"] = ar2.to_dict()
    if not out["results"]:
        return {"winner": None, "error": "both forecasters failed"}
    # Pick winner.
    winner = min(out["results"].items(),
                 key=lambda kv: kv[1].get("math", {}).get("holdout_rmse", float("inf")))
    out["winner"] = winner[0]
    out["winner_rmse_holdout"] = winner[1].get("math", {}).get("holdout_rmse")
    out["explanation"] = (
        f"Compared on a held-out fold of {horizon} samples; winner = "
        f"{winner[0]} with RMSE {out['winner_rmse_holdout']:.4g}. "
        f"Both methods report their internal coefficients in `math`."
    )
    return out

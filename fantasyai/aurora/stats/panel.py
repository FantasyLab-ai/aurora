"""
Panel-data analysis — Wave C.

Two-way fixed-effects decomposition: decompose total variation in a
panel-data outcome ``y_{it}`` into:

  * entity-effect variance      α_i (each entity's deviation from grand mean)
  * time-effect variance        γ_t  (each period's deviation)
  * idiosyncratic variance      ε_it (residual)

The underlying model is::

    y_{it} = μ + α_i + γ_t + ε_{it}

Estimated by demeaning (within-transformation) — the standard
econometric route. Reports the variance contributions with bootstrap
CIs so the synthesis layer can frame them honestly.

Pure numpy. No statsmodels dependency.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

from .bootstrap import bootstrap_ci


def two_way_fixed_effects(
    entities: Sequence[Any],
    times: Sequence[Any],
    values: Sequence[float],
    *,
    bootstrap: bool = True,
    n_resamples: int = 500,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Two-way fixed-effects variance decomposition.

    Parameters
    ----------
    entities, times, values
        Three parallel sequences of length n. ``entities`` and ``times``
        can be any hashable type — the function builds the entity/time
        index internally.
    bootstrap
        When True, attaches a 95% bootstrap CI to each variance share.
    n_resamples
        Bootstrap replicates if requested.
    seed
        RNG seed for the bootstrap.

    Returns
    -------
    dict::

        {
          "available":     True,
          "n_obs":         int,
          "n_entities":    int,
          "n_times":       int,
          "grand_mean":    float,
          "ss_total":      float,
          "ss_entity":     float,
          "ss_time":       float,
          "ss_residual":   float,
          "share_entity":  float,    # ss_entity / ss_total
          "share_time":    float,
          "share_residual": float,
          "shares_ci":     {"entity": [lo, hi], "time": [lo, hi],
                             "residual": [lo, hi]},
          "entity_effects": {entity: deviation, ...},
          "time_effects":   {time:   deviation, ...},
          "dominant":      "entity" | "time" | "residual",
          "method":        "two_way_fixed_effects_demeaned",
          "notes":         [str],
        }
    """
    if np is None:
        raise RuntimeError("numpy required for two_way_fixed_effects")
    n = len(values)
    if not (len(entities) == len(times) == n):
        raise ValueError("entities, times, values must be same length")
    if n < 4:
        return {"available": False, "skipped_reason": "fewer_than_4_obs",
                "n_obs": n}

    y = np.asarray(values, dtype=float)
    # Drop NaNs.
    valid = ~np.isnan(y)
    y = y[valid]
    e_arr = np.asarray(entities)[valid]
    t_arr = np.asarray(times)[valid]
    n = y.shape[0]
    if n < 4:
        return {"available": False, "skipped_reason": "after_nan_drop_<4",
                "n_obs": n}

    # Entity / time mean lookups.
    mu = float(y.mean())
    e_unique, e_idx = np.unique(e_arr, return_inverse=True)
    t_unique, t_idx = np.unique(t_arr, return_inverse=True)
    n_entities = len(e_unique)
    n_times = len(t_unique)

    # Entity means α_i, time means γ_t.
    e_means = np.zeros(n_entities)
    e_counts = np.zeros(n_entities)
    t_means = np.zeros(n_times)
    t_counts = np.zeros(n_times)
    for i in range(n):
        e_means[e_idx[i]] += y[i]; e_counts[e_idx[i]] += 1
        t_means[t_idx[i]] += y[i]; t_counts[t_idx[i]] += 1
    e_means = e_means / np.maximum(1, e_counts)
    t_means = t_means / np.maximum(1, t_counts)

    # Decomposition. Within-transformation: y_demeaned = y - α_i - γ_t + μ.
    yhat = mu + (e_means[e_idx] - mu) + (t_means[t_idx] - mu)
    residual = y - yhat
    ss_total = float(np.sum((y - mu) ** 2))
    ss_entity = float(np.sum(((e_means[e_idx] - mu)) ** 2))
    ss_time = float(np.sum(((t_means[t_idx] - mu)) ** 2))
    ss_residual = float(np.sum(residual ** 2))
    if ss_total <= 0:
        return {"available": False, "skipped_reason": "zero_variance"}

    share_e = ss_entity / ss_total
    share_t = ss_time / ss_total
    share_r = ss_residual / ss_total

    notes: List[str] = [
        "shares are computed from sums of squares (not from a fitted "
        "linear model), so they will not perfectly sum to 1 when the "
        "design is unbalanced — check `share_unbalance_residual`."
    ]
    share_unbalance = 1.0 - (share_e + share_t + share_r)

    out: Dict[str, Any] = {
        "available": True,
        "n_obs": n,
        "n_entities": n_entities,
        "n_times": n_times,
        "grand_mean": mu,
        "ss_total": ss_total,
        "ss_entity": ss_entity,
        "ss_time": ss_time,
        "ss_residual": ss_residual,
        "share_entity": share_e,
        "share_time": share_t,
        "share_residual": share_r,
        "share_unbalance_residual": share_unbalance,
        "entity_effects": {str(e_unique[i]): float(e_means[i] - mu)
                            for i in range(n_entities)},
        "time_effects":   {str(t_unique[i]): float(t_means[i] - mu)
                            for i in range(n_times)},
        "dominant": _dominant_share(share_e, share_t, share_r),
        "method": "two_way_fixed_effects_demeaned",
        "notes": notes,
    }

    if bootstrap and n >= 20:
        # Bootstrap each variance share. Resample observations IID
        # (acceptable for cross-sectionally-independent panels).
        rng = np.random.default_rng(seed)

        def _share_for_sample(sample_idx):
            ys = y[sample_idx]
            es = e_idx[sample_idx]
            ts = t_idx[sample_idx]
            e_m = np.zeros(n_entities); e_c = np.zeros(n_entities)
            t_m = np.zeros(n_times);    t_c = np.zeros(n_times)
            for k in range(len(ys)):
                e_m[es[k]] += ys[k]; e_c[es[k]] += 1
                t_m[ts[k]] += ys[k]; t_c[ts[k]] += 1
            e_m = e_m / np.maximum(1, e_c)
            t_m = t_m / np.maximum(1, t_c)
            mu_s = float(ys.mean())
            sst = float(np.sum((ys - mu_s) ** 2))
            if sst <= 0:
                return (0.0, 0.0, 0.0)
            sse = float(np.sum((e_m[es] - mu_s) ** 2))
            sstm = float(np.sum((t_m[ts] - mu_s) ** 2))
            yh = mu_s + (e_m[es] - mu_s) + (t_m[ts] - mu_s)
            ssr = float(np.sum((ys - yh) ** 2))
            return (sse / sst, sstm / sst, ssr / sst)

        e_shares = np.empty(n_resamples)
        t_shares = np.empty(n_resamples)
        r_shares = np.empty(n_resamples)
        for i in range(n_resamples):
            idx = rng.integers(0, n, size=n)
            e_shares[i], t_shares[i], r_shares[i] = _share_for_sample(idx)
        out["shares_ci"] = {
            "entity":   [float(np.quantile(e_shares, 0.025)),
                          float(np.quantile(e_shares, 0.975))],
            "time":     [float(np.quantile(t_shares, 0.025)),
                          float(np.quantile(t_shares, 0.975))],
            "residual": [float(np.quantile(r_shares, 0.025)),
                          float(np.quantile(r_shares, 0.975))],
        }
        out["bootstrap_n_resamples"] = n_resamples
    else:
        out["shares_ci"] = None
        if not bootstrap:
            notes.append("bootstrap CIs not requested")
        elif n < 20:
            notes.append("n<20 — bootstrap skipped (insufficient sample)")
    return out


def _dominant_share(share_e: float, share_t: float,
                      share_r: float) -> str:
    """Return whichever share is largest. Synthesis uses this label."""
    pairs = [("entity", share_e), ("time", share_t), ("residual", share_r)]
    pairs.sort(key=lambda p: -p[1])
    return pairs[0][0]

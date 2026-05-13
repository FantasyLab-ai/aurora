from __future__ import annotations

from typing import Dict, Any, Optional, List
import numpy as np
import pandas as pd

from .physics import _infer_time_col, _build_time_axis_seconds, _is_mostly_numeric, physics_constraints
from .physics_models import candidate_models


def discover_physics_models(df: pd.DataFrame, target_col: Optional[str] = None, time_col: Optional[str] = None) -> Dict[str, Any]:
    """
    Phase 3: run multiple physics-inspired models, score them, return best + runner-ups.
    This works even if we fall back to index time.
    """
    # choose numeric target
    num_cols = [c for c in df.columns if _is_mostly_numeric(df[c], 0.80)]
    if not num_cols:
        return {"note": "error", "error": "no_numeric_columns"}

    if target_col is None or target_col not in df.columns:
        # pick highest variance numeric column
        best = None; best_var = -1.0
        for c in num_cols:
            y = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)
            v = float(np.nanvar(y))
            if np.isfinite(v) and v > best_var:
                best_var = v; best = c
        target_col = best or num_cols[0]

    y = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)
    s = pd.Series(y)
    y = s.interpolate(limit_direction="both").to_numpy(dtype=float)

    time_col = time_col if (time_col is not None and time_col in df.columns) else _infer_time_col(df)
    t, time_axis, dt_seconds = _build_time_axis_seconds(df, time_col)

    # train/test split for scoring
    n = len(y)
    if n < 200:
        return {"note": "error", "error": "not_enough_rows", "n": int(n), "target_col": target_col}

    split = int(n * 0.80)
    t_tr, y_tr = t[:split], y[:split]
    t_te, y_te = t[split:], y[split:]

    results: List[Dict[str, Any]] = []
    for fit_fn in candidate_models():
        try:
            r = fit_fn(t_tr, y_tr)
            if not r.get("ok"):
                results.append({"name": r.get("name", getattr(fit_fn, "__name__", "unknown")),
                                "ok": False, "error": r.get("error", "fit_failed")})
                continue

            # re-simulate full series using fitted params by re-calling on full
            # (cheap and keeps deps minimal)
            r_full = fit_fn(t, y)
            if not r_full.get("ok"):
                results.append({"name": r.get("name", "unknown"), "ok": False, "error": "full_fit_failed"})
                continue

            yhat = np.asarray(r_full.get("yhat", np.full_like(y, np.nan)), dtype=float)
            rmse_test = float(np.sqrt(np.mean((y_te - yhat[split:])**2)))

            results.append({
                "name": r_full.get("name", "unknown"),
                "model": r_full.get("model"),
                "params": r_full.get("params", {}),
                "rmse_test": rmse_test,
                "rmse_full": float(r_full.get("rmse", np.nan)),
                "aic": float(r_full.get("aic", np.nan)),
                "k": int(r_full.get("k", 0)),
            })
        except Exception as e:
            results.append({"name": getattr(fit_fn, "__name__", "unknown"), "ok": False, "error": str(e)})

    # select best by rmse_test primarily, then aic
    valid = [r for r in results if r.get("rmse_test") is not None and np.isfinite(r.get("rmse_test", np.nan))]
    if not valid:
        return {
            "note": "error",
            "error": "all_models_failed",
            "target_col": target_col,
            "time_axis": time_axis,
            "time_col": time_col,
            "dt_seconds": float(dt_seconds),
            "candidates": results,
        }

    valid.sort(key=lambda r: (r["rmse_test"], r.get("aic", float("inf"))))
    best = valid[0]
    runners = valid[1:3]

    # Phase 4 constraints score (generic invariants)
    constraints = physics_constraints(df=df, target_col=target_col, time_col=time_col)

    # ---- v2.0: also try coupled multi-target systems (Lotka-Volterra, SIR, …)
    # when the dataset has ≥ 2 strongly-correlated numeric columns. Cheap to
    # try; the dispatcher returns "error" when the data isn't coupled-shaped.
    coupled_block: Dict[str, Any] = {"note": "skipped", "reason": "fewer than 2 numeric columns"}
    try:
        if len(num_cols) >= 2:
            from .coupled_systems import discover_coupled
            # Pick the two highest-variance columns aside from target.
            other_cols = [c for c in num_cols if c != target_col]
            other_cols.sort(
                key=lambda c: float(np.nanvar(pd.to_numeric(df[c], errors="coerce"))),
                reverse=True,
            )
            if other_cols:
                x_arr = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)
                y_arr = pd.to_numeric(df[other_cols[0]], errors="coerce").to_numpy(dtype=float)
                # Interpolate gaps so the coupled fit doesn't choke on NaNs.
                x_arr = pd.Series(x_arr).interpolate(limit_direction="both").to_numpy(dtype=float)
                y_arr = pd.Series(y_arr).interpolate(limit_direction="both").to_numpy(dtype=float)
                coupled_block = discover_coupled(t, x_arr, y_arr)
                coupled_block["x_col"] = target_col
                coupled_block["y_col"] = other_cols[0]
    except Exception as e:
        coupled_block = {"note": "error", "error": str(e)}

    # ---- v2.0: try PDE detection on wide-format spatial-temporal grids.
    # Most CSVs aren't gridded; the helper returns None and we skip silently.
    pde_block: Dict[str, Any] = {"note": "skipped", "reason": "no spatial grid detected"}
    try:
        from .pde_detection import detect_pde_grid_in_dataframe, discover_pde
        grid = detect_pde_grid_in_dataframe(df, time_col=time_col)
        if grid is not None:
            t_g, x_g, U_g, used_cols = grid
            pde_result = discover_pde(t_g, x_g, U_g)
            pde_block = pde_result
            pde_block["spatial_columns"] = used_cols
            pde_block["n_t"] = int(t_g.size)
            pde_block["n_x"] = int(x_g.size)
    except Exception as e:
        pde_block = {"note": "error", "error": str(e)}

    return {
        "note": "ok",
        "target_col": target_col,
        "time_axis": time_axis,
        "time_col": time_col,
        "dt_seconds": float(dt_seconds),
        "best": best,
        "runner_ups": runners,
        "all_candidates": results,
        "constraints": constraints,
        "physics_consistency_score": constraints.get("physics_consistency_score", 0.0),
        # v2.0 multi-target / PDE additions:
        "coupled_systems": coupled_block,
        "pde_detection": pde_block,
    }


# --- Compatibility alias (engine expects physics_discovery.physics_discovery) ---
def physics_discovery(*args, seed=None, **kwargs):
    """
    Backwards/engine compatibility entrypoint.
    Preferred entrypoint in this module may be discover_physics_models(...).
    """
    if "discover_physics_models" in globals():
        return discover_physics_models(*args, **kwargs)
    if "discover" in globals():
        return discover(*args, **kwargs)
    raise AttributeError("No discover_physics_models/discover function found in physics_discovery.py")

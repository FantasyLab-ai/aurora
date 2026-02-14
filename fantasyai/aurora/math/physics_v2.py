from __future__ import annotations

from typing import Any, Dict, Optional
import numpy as np
import pandas as pd

from .physics import physics_diagnostics


def _robust_zscore(x: np.ndarray) -> np.ndarray:
    x = x.astype(float)
    m = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - m)) + 1e-12
    return (x - m) / (1.4826 * mad)


def physics_diagnostics_v2(
    df: pd.DataFrame,
    target_col: str,
    time_col: Optional[str] = None,
    seed: int = 0,
) -> Dict[str, Any]:
    """
    Physics diagnostics v2:
      - run baseline physics_diagnostics
      - also run on a robustly-normalized series (zscore via MAD)
      - also run on first-difference series (captures drift removal)
    """
    out: Dict[str, Any] = {"note": "ok", "error": None}

    try:
        base = physics_diagnostics(df=df, target_col=target_col, time_col=time_col, seed=seed)
        out["baseline"] = base

        y = pd.to_numeric(df[target_col], errors="coerce").replace([np.inf, -np.inf], np.nan).values.astype(float)

        yz = _robust_zscore(y)
        tmp = df.copy()
        tmp[target_col] = yz
        out["robust_zscore"] = physics_diagnostics(df=tmp, target_col=target_col, time_col=time_col, seed=seed)

        yd = np.concatenate([[np.nan], np.diff(y)])
        tmp2 = df.copy()
        tmp2[target_col] = yd
        out["first_difference"] = physics_diagnostics(df=tmp2, target_col=target_col, time_col=time_col, seed=seed)

        return out
    except Exception as e:
        return {"note": "failed", "error": f"physics_v2_failed:{type(e).__name__}:{e}"}

# ----------------------------------------------------------------------
# Back-compat alias: some pipelines expect physics_discovery inside physics_v2
# ----------------------------------------------------------------------
try:
    from .physics_discovery import physics_discovery as physics_discovery
except Exception:
    physics_discovery = None

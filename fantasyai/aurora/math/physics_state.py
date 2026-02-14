from __future__ import annotations
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd

def _select_numeric_cols(df: pd.DataFrame, max_cols: int = 8) -> List[str]:
    cols = []
    for c in df.columns:
        if c.startswith("__aurora_"):
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols[:max_cols]

def fit_var1_state(df: pd.DataFrame, cols: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Multivariate VAR(1): X_{t+1} = A X_t + e
    Returns stability summary (eigs) + fit metrics.
    """
    out: Dict[str, Any] = {"note": "ok", "error": None}

    try:
        if cols is None:
            cols = _select_numeric_cols(df)
        cols = [c for c in cols if c in df.columns]
        if len(cols) < 2:
            return {"note": "skipped", "error": "need >=2 numeric cols", "cols": cols}

        X = df[cols].copy()
        X = X.apply(pd.to_numeric, errors="coerce")
        X = X.dropna()
        if len(X) < 200:
            return {"note": "skipped", "error": "not enough rows after dropna", "n": int(len(X)), "cols": cols}

        X0 = X.iloc[:-1].values
        X1 = X.iloc[1:].values

        # Solve A in least squares: X1 = X0 A^T  => A^T = lstsq(X0, X1)
        A_T, *_ = np.linalg.lstsq(X0, X1, rcond=None)
        A = A_T.T

        # Stability via eigenvalues
        eigs = np.linalg.eigvals(A)
        spectral_radius = float(np.max(np.abs(eigs)))
        stable = bool(spectral_radius < 1.0)

        # residuals
        pred = X0 @ A_T
        resid = X1 - pred
        rmse = float(np.sqrt(np.mean(resid**2)))

        out.update({
            "model": "VAR1",
            "cols": cols,
            "n": int(len(X)),
            "A_shape": [int(A.shape[0]), int(A.shape[1])],
            "eigenvalues": [complex(z).real if abs(complex(z).imag) < 1e-10 else str(complex(z)) for z in eigs[:min(len(eigs), 10)]],
            "spectral_radius": spectral_radius,
            "stable": stable,
            "rmse": rmse,
        })
        return out
    except Exception as e:
        out["note"] = "error"
        out["error"] = str(e)
        return out

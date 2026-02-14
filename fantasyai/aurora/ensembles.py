from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Any, Optional, List, Tuple

import numpy as np
import pandas as pd


@dataclass
class EnsembleSummary:
    n: int
    point: Dict[str, Any]
    quantiles: Dict[str, Dict[str, float]]
    samples_preview: List[Dict[str, Any]]


def _rng(seed: Optional[int]) -> np.random.Generator:
    return np.random.default_rng(seed)


def perturb_dataframe(
    df: pd.DataFrame,
    numeric_cols: List[str],
    noise_std_frac: float = 0.02,
    seed: Optional[int] = 7,
) -> pd.DataFrame:
    """
    Multiplicative gaussian noise on numeric columns:
      x' = x * (1 + eps), eps ~ N(0, noise_std_frac)
    """
    g = _rng(seed)
    out = df.copy()
    for c in numeric_cols:
        x = pd.to_numeric(out[c], errors="coerce").astype(float)
        eps = g.normal(0.0, noise_std_frac, size=len(x))
        out[c] = x * (1.0 + eps)
    return out


def run_ensemble(
    base_df: pd.DataFrame,
    numeric_cols: List[str],
    run_fn: Callable[[pd.DataFrame], Dict[str, Any]],
    n_runs: int = 200,
    noise_std_frac: float = 0.02,
    seed: Optional[int] = 7,
) -> Dict[str, Any]:
    """
    - run_fn returns dict with numeric fields you care about (e.g., rmse, mae, etc.)
    - We collect those fields across runs and report quantiles.
    """
    g = _rng(seed)
    results = []
    for i in range(n_runs):
        df_i = perturb_dataframe(
            base_df,
            numeric_cols=numeric_cols,
            noise_std_frac=noise_std_frac,
            seed=int(g.integers(0, 1_000_000)),
        )
        res = run_fn(df_i)
        results.append(res)

    # Identify scalar numeric keys to summarize
    scalar_keys = []
    for k in results[0].keys():
        v = results[0][k]
        if isinstance(v, (int, float, np.floating, np.integer)):
            scalar_keys.append(k)

    quants = {}
    for k in scalar_keys:
        arr = np.array([float(r[k]) for r in results], dtype=float)
        quants[k] = {
            "q05": float(np.quantile(arr, 0.05)),
            "q25": float(np.quantile(arr, 0.25)),
            "q50": float(np.quantile(arr, 0.50)),
            "q75": float(np.quantile(arr, 0.75)),
            "q95": float(np.quantile(arr, 0.95)),
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr)),
        }

    preview = results[: min(10, len(results))]

    return {
        "ensemble": {
            "n_runs": n_runs,
            "noise_std_frac": noise_std_frac,
            "seed": seed,
        },
        "quantiles": quants,
        "samples_preview": preview,
    }

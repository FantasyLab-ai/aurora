from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

import numpy as np
from scipy.optimize import minimize


@dataclass
class UncertainParameter:
    """
    Simple Gaussian uncertainty model for a scalar parameter.
    """
    mean: float
    std: float


def sample_params(
    dists: Dict[str, UncertainParameter],
    n_samples: int,
    rng: np.random.Generator | None = None,
) -> List[Dict[str, float]]:
    """
    Draw n_samples parameter dictionaries from independent Gaussian priors.
    """
    if rng is None:
        rng = np.random.default_rng()
    samples: List[Dict[str, float]] = []
    for _ in range(n_samples):
        draw: Dict[str, float] = {}
        for name, p in dists.items():
            draw[name] = float(rng.normal(p.mean, p.std))
        samples.append(draw)
    return samples


def make_robust_objective(
    base_objective: Callable[[np.ndarray, Dict[str, float]], float],
    param_dists: Dict[str, UncertainParameter],
    n_samples: int = 64,
    risk: str = "cvar",
    alpha: float = 0.95,
    rng_seed: int | None = 1234,
) -> Callable[[np.ndarray], float]:
    """
    Wrap a deterministic objective into a robust one by integrating over
    param uncertainty using Monte Carlo and a chosen risk measure.

    base_objective(x, params) -> scalar cost
    risk in {"mean", "cvar"}:
        - "mean": minimize expected cost.
        - "cvar": minimize expected *tail* cost (worst (1-alpha) fraction).
    """
    rng = np.random.default_rng(rng_seed)

    def objective(x: np.ndarray) -> float:
        draws = sample_params(param_dists, n_samples, rng)
        vals = np.empty(n_samples, dtype=float)
        for i, params in enumerate(draws):
            vals[i] = float(base_objective(x, params))

        if risk == "mean":
            return float(np.mean(vals))

        # CVaR-style: mean of worst (1-alpha) fraction
        vals_sorted = np.sort(vals)
        cutoff = max(int(np.floor((1.0 - alpha) * n_samples)), 1)
        tail = vals_sorted[-cutoff:]
        return float(np.mean(tail))

    return objective


@dataclass
class RobustOptimizeResult:
    x_opt: np.ndarray
    fun: float
    success: bool
    message: str


def robust_optimize(
    x0: np.ndarray,
    bounds: List[Tuple[float, float]],
    base_objective: Callable[[np.ndarray, Dict[str, float]], float],
    param_dists: Dict[str, UncertainParameter],
    n_samples: int = 64,
    risk: str = "cvar",
    alpha: float = 0.95,
) -> RobustOptimizeResult:
    """
    Run a robust optimization over x given uncertain parameters.

    This is still a demo-scale routine, but it demonstrates exactly the
    "optimize over uncertainty" logic that differentiates Aurora.
    """
    robust_obj = make_robust_objective(
        base_objective=base_objective,
        param_dists=param_dists,
        n_samples=n_samples,
        risk=risk,
        alpha=alpha,
    )

    res = minimize(robust_obj, x0, bounds=bounds, method="L-BFGS-B")
    return RobustOptimizeResult(
        x_opt=res.x.copy(),
        fun=float(res.fun),
        success=bool(res.success),
        message=str(res.message),
    )

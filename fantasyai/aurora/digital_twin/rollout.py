from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import numpy as np

from .models import TwinModel


@dataclass
class RolloutResult:
    horizon: int
    n_sims: int
    p05: np.ndarray
    p50: np.ndarray
    p95: np.ndarray
    mean: np.ndarray
    std: np.ndarray
    terminal_min: float
    terminal_max: float

    def to_jsonable(self) -> Dict[str, Any]:
        return {
            "horizon": int(self.horizon),
            "n_sims": int(self.n_sims),
            "p05": self.p05.tolist(),
            "p50": self.p50.tolist(),
            "p95": self.p95.tolist(),
            "mean": self.mean.tolist(),
            "std": self.std.tolist(),
            "terminal": {"min": float(self.terminal_min), "max": float(self.terminal_max)},
        }


def monte_carlo_rollout(
    model: TwinModel,
    x0: np.ndarray,
    horizon: int = 24,
    n_sims: int = 5000,
    seed: int = 42,
) -> RolloutResult:
    """
    Generic Monte Carlo rollout.
    Assumes model.step(x, rng) -> new state
            model.observe(x) -> scalar
    """
    rng = np.random.default_rng(seed)

    # store simulated observations: (n_sims, horizon)
    sims = np.zeros((n_sims, horizon), dtype=float)

    for i in range(n_sims):
        x = x0.copy()
        for t in range(horizon):
            x = model.step(x, rng)
            sims[i, t] = float(model.observe(x))

    p05 = np.quantile(sims, 0.05, axis=0)
    p50 = np.quantile(sims, 0.50, axis=0)
    p95 = np.quantile(sims, 0.95, axis=0)
    mean = np.mean(sims, axis=0)
    std = np.std(sims, axis=0)

    terminal = sims[:, -1] if horizon > 0 else np.array([float(model.observe(x0))])
    return RolloutResult(
        horizon=horizon,
        n_sims=n_sims,
        p05=p05,
        p50=p50,
        p95=p95,
        mean=mean,
        std=std,
        terminal_min=float(np.min(terminal)),
        terminal_max=float(np.max(terminal)),
    )

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Tuple
import numpy as np
import pandas as pd


class TwinModel(Protocol):
    """
    Minimal protocol for a digital-twin model.
    """
    name: str

    def fit(self, y: pd.Series, dt_seconds: Optional[pd.Series] = None) -> "TwinModel": ...
    def step(self, x: np.ndarray, rng: np.random.Generator) -> np.ndarray: ...
    def observe(self, x: np.ndarray) -> float: ...


@dataclass
class RandomWalkModel:
    name: str = "random_walk"
    sigma: float = 1.0  # process noise scale

    def fit(self, y: pd.Series, dt_seconds: Optional[pd.Series] = None) -> "RandomWalkModel":
        # Estimate sigma from differences
        yy = pd.to_numeric(y, errors="coerce").dropna()
        if len(yy) >= 3:
            diffs = yy.diff().dropna()
            s = float(diffs.std()) if float(diffs.std()) > 1e-9 else 1.0
            self.sigma = s
        return self

    def step(self, x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        # x = [level] or [level, ...]
        xn = x.copy()
        xn[0] = xn[0] + rng.normal(0.0, self.sigma)
        return xn

    def observe(self, x: np.ndarray) -> float:
        return float(x[0])


@dataclass
class AR1Model:
    name: str = "ar1"
    phi: float = 0.9
    sigma: float = 1.0

    def fit(self, y: pd.Series, dt_seconds: Optional[pd.Series] = None) -> "AR1Model":
        yy = pd.to_numeric(y, errors="coerce").dropna()
        if len(yy) < 20:
            return self

        y0 = yy.iloc[:-1].to_numpy(dtype=float)
        y1 = yy.iloc[1:].to_numpy(dtype=float)

        denom = float(np.dot(y0, y0))
        if denom > 1e-9:
            self.phi = float(np.dot(y0, y1) / denom)
            self.phi = float(np.clip(self.phi, -0.99, 0.99))

        resid = y1 - self.phi * y0
        s = float(np.std(resid))
        self.sigma = s if s > 1e-9 else 1.0
        return self

    def step(self, x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        # x[0] is current y
        xn = x.copy()
        xn[0] = self.phi * xn[0] + rng.normal(0.0, self.sigma)
        return xn

    def observe(self, x: np.ndarray) -> float:
        return float(x[0])

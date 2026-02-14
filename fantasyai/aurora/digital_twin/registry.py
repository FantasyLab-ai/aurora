from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Callable
import numpy as np
import pandas as pd

from .models import RandomWalkModel, AR1Model, TwinModel


@dataclass
class ModelScore:
    model_name: str
    score: float
    details: Dict[str, Any]


class ModelRegistry:
    """
    Register models with a scoring function and select the best for a dataset/series.
    """
    def __init__(self) -> None:
        self._builders: Dict[str, Callable[[], TwinModel]] = {}

    def register(self, key: str, builder: Callable[[], TwinModel]) -> None:
        self._builders[key] = builder

    def available(self) -> List[str]:
        return sorted(self._builders.keys())

    def build(self, key: str) -> TwinModel:
        return self._builders[key]()

    def score_models(
        self,
        y: pd.Series,
        seed: int = 42,
        max_train: int = 50000,
        holdout: int = 500,
    ) -> List[ModelScore]:
        yy = pd.to_numeric(y, errors="coerce").dropna()
        if len(yy) == 0:
            return [ModelScore("none", -1e9, {"reason": "no_numeric_target"})]

        if len(yy) > max_train:
            yy = yy.sample(max_train, random_state=seed).sort_index()

        # small holdout for fast eval
        if len(yy) > (holdout + 10):
            train = yy.iloc[:-holdout]
            test = yy.iloc[-holdout:]
        else:
            train = yy
            test = yy

        scores: List[ModelScore] = []
        rng = np.random.default_rng(seed)

        for key in self.available():
            m = self.build(key).fit(train)
            # One-step ahead naive eval:
            # simulate forward from last observed using model mean dynamics
            preds = []
            x = np.array([float(train.iloc[-1])], dtype=float)
            for _ in range(len(test)):
                # deterministic-ish: average of few noises
                vals = []
                for _k in range(10):
                    xk = m.step(x, rng)
                    vals.append(m.observe(xk))
                pred = float(np.mean(vals))
                preds.append(pred)
                x = np.array([pred], dtype=float)

            true = test.to_numpy(dtype=float)
            pred = np.asarray(preds, dtype=float)

            rmse = float(np.sqrt(np.mean((true - pred) ** 2))) if len(true) else 1e9
            var = float(np.var(true)) if len(true) else 1.0

            # Score: higher is better
            # normalized error + mild complexity/runtime penalty placeholder
            norm_rmse = rmse / (np.sqrt(var) + 1e-9)
            score = -norm_rmse

            scores.append(ModelScore(key, score, {"rmse": rmse, "norm_rmse": norm_rmse}))

        scores.sort(key=lambda s: s.score, reverse=True)
        return scores

    def select_best(
        self,
        y: pd.Series,
        seed: int = 42,
    ) -> Tuple[TwinModel, List[ModelScore]]:
        scores = self.score_models(y, seed=seed)
        best_key = scores[0].model_name
        if best_key == "none":
            # fallback random walk
            m = RandomWalkModel()
            return m, scores
        m = self.build(best_key).fit(pd.to_numeric(y, errors="coerce").dropna())
        return m, scores


def default_registry() -> ModelRegistry:
    reg = ModelRegistry()
    reg.register("random_walk", lambda: RandomWalkModel())
    reg.register("ar1", lambda: AR1Model())
    return reg

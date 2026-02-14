# fantasyai/aurora/symbolic_regression.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional

import numpy as np

try:
    from gplearn.genetic import SymbolicRegressor
except ImportError:  # pragma: no cover
    SymbolicRegressor = None  # type: ignore


@dataclass
class SymbolicRegressionResult:
    success: bool
    expression: Optional[str]
    diagnostics: Dict[str, Any]


def run_symbolic_regression(
    x: np.ndarray,
    y: np.ndarray,
    *,
    population_size: int = 500,
    generations: int = 20,
) -> SymbolicRegressionResult:
    """
    Wrapper around gplearn SymbolicRegressor.
    """
    if SymbolicRegressor is None:
        raise RuntimeError("gplearn is not installed; cannot run symbolic regression.")

    if x.ndim == 1:
        x = x.reshape(-1, 1)

    est = SymbolicRegressor(
        population_size=population_size,
        generations=generations,
        stopping_criteria=0.0,
        p_crossover=0.7,
        p_subtree_mutation=0.1,
        p_hoist_mutation=0.05,
        p_point_mutation=0.1,
        max_samples=1.0,
        verbose=1,
        parsimony_coefficient=0.001,
        random_state=42,
    )

    est.fit(x, y)

    expr = est._program.__str__()  # type: ignore[attr-defined]
    return SymbolicRegressionResult(
        success=True,
        expression=expr,
        diagnostics={
            "depth": getattr(est._program, "depth_", None),  # type: ignore[attr-defined]
            "length": getattr(est._program, "length_", None),  # type: ignore[attr-defined]
        },
    )

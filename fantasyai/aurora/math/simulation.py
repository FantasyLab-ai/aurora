
from __future__ import annotations
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd

def simulate_counterfactuals_from_regression(beta: float, beta_ci95: list, delta_x_grid: list) -> Dict[str, Any]:
    # deterministic projection over a grid of deltas
    out = []
    for dx in delta_x_grid:
        out.append({
            "delta_x": float(dx),
            "delta_y": float(beta * dx),
            "delta_y_ci95": [float(beta_ci95[0]*dx), float(beta_ci95[1]*dx)],
        })
    return {"method": "linear_projection", "curve": out}

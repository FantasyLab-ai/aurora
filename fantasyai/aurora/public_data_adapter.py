# fantasyai/aurora/public_data_adapter.py

from __future__ import annotations

from typing import Dict, Any

import numpy as np

from fantasyai.aurora.research_agent import AuroraResearchAgent
from fantasyai.domains.public_datasets import (
    TabularDataset,
    TimeSeriesDataset,
)
from fantasyai.domains.oncology_solver import (
    analyze_tumor_growth_curve,
    optimal_dose_toy_model,
)
from fantasyai.domains.climate_solver import (
    analyze_climate_timeseries,
    differential_climate_model_example,
)
from fantasyai.domains.logistics_solver import (
    fit_route_cost_model,
    optimize_route_speed_toy,
)

# Backwards-compat for older demo scripts:
# they still do `from fantasyai.aurora.public_data_adapter import DATA_ROOT`
from fantasyai.config import DATA_DIR

DATA_ROOT = DATA_DIR  # this is your fantasyai/data directory


def run_aurora_on_tcga_like_expression(
    expr_dataset: TabularDataset,
    *,
    time_col: str | None = None,
    volume_col: str | None = None,
) -> Dict[str, Any]:
    """
    Example adapter for TCGA-like data.

    If you have a time column (e.g., 'days_to_event') and a tumor size
    proxy (e.g., 'tumor_volume'), this uses Aurora's oncology helpers
    to model tumor growth curves.
    """
    import pandas as pd  # type: ignore

    df = expr_dataset.df
    res: Dict[str, Any] = {
        "meta": expr_dataset.meta,
        "note": "Expression table loaded; basic stats computed.",
    }

    # basic summary
    res["summary"] = {
        "shape": df.shape,
        "head": df.head().to_dict(orient="list"),
    }

    # If no growth columns are given, just return the summary
    if time_col is None or volume_col is None:
        return res

    if time_col not in df.columns or volume_col not in df.columns:
        res["warning"] = (
            f"time_col '{time_col}' or volume_col '{volume_col}' not in columns."
        )
        return res

    # Drop rows with missing values in those columns
    sub = df[[time_col, volume_col]].dropna()
    times = sub[time_col].to_numpy(dtype=float)
    vols = sub[volume_col].to_numpy(dtype=float)

    growth_res = analyze_tumor_growth_curve(times, vols)
    res["tumor_growth_model"] = growth_res
    return res


def run_aurora_on_cancer_toy_optimization(agent: AuroraResearchAgent) -> Dict[str, Any]:
    """
    Uses the toy dose optimization model from oncology_solver.
    """
    return optimal_dose_toy_model(agent)


def run_aurora_on_climate_timeseries(
    ts: TimeSeriesDataset,
) -> Dict[str, Any]:
    """
    Apply Aurora's climate helper to a single-variable time series.
    """
    # pick first variable
    if not ts.values:
        return {"error": "No values found in TimeSeriesDataset."}

    var_name = list(ts.values.keys())[0]
    vals = ts.values[var_name]

    # If t is datetime64, convert to numeric index for modeling
    if np.issubdtype(ts.t.dtype, np.datetime64):
        # convert to simple index 0..N-1 for now
        t_numeric = np.arange(len(ts.t), dtype=float)
    else:
        t_numeric = ts.t.astype(float)

    climate_res = analyze_climate_timeseries(vals, t=t_numeric)
    climate_res["dataset_meta"] = ts.meta
    return climate_res


def run_aurora_on_climate_equilibrium(agent: AuroraResearchAgent) -> Dict[str, Any]:
    """
    Use the toy differential climate model example.
    """
    return differential_climate_model_example(agent)


def run_aurora_on_logistics_cost(
    tab: TabularDataset,
    *,
    distance_col: str,
    cost_col: str,
    degree: int = 2,
) -> Dict[str, Any]:
    """
    Fit a polynomial route-cost model based on tabular data.
    """
    df = tab.df

    if distance_col not in df.columns or cost_col not in df.columns:
        return {
            "error": (
                f"Missing distance_col '{distance_col}' or cost_col "
                f"'{cost_col}' in columns."
            ),
            "columns": list(df.columns),
        }

    sub = df[[distance_col, cost_col]].dropna()
    distances = sub[distance_col].to_numpy(dtype=float)
    costs = sub[cost_col].to_numpy(dtype=float)

    fit_res = fit_route_cost_model(distances, costs, degree=degree)
    fit_res["dataset_meta"] = tab.meta
    return fit_res


def run_aurora_on_logistics_speed_toy(agent: AuroraResearchAgent) -> Dict[str, Any]:
    """
    Use toy logistics speed optimization model.
    """
    return optimize_route_speed_toy(agent)

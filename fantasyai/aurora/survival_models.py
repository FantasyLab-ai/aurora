# fantasyai/aurora/survival_models.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from lifelines import KaplanMeierFitter, CoxPHFitter
from lifelines.exceptions import ConvergenceError


@dataclass
class SurvivalConfig:
    time_col: str
    event_col: str
    feature_cols: List[str]
    group_col: Optional[str] = None


def prepare_survival_dataframe(
    df: pd.DataFrame,
    cfg: SurvivalConfig,
) -> pd.DataFrame:
    """
    Clean and subset a clinical DataFrame for survival modeling.
    Drops rows with missing time or event; keeps configured feature columns.
    """
    cols = [cfg.time_col, cfg.event_col] + cfg.feature_cols
    if cfg.group_col:
        cols.append(cfg.group_col)

    sub = df[cols].copy()
    sub = sub.dropna(subset=[cfg.time_col, cfg.event_col])

    # Ensure numeric where appropriate
    sub[cfg.time_col] = pd.to_numeric(sub[cfg.time_col], errors="coerce")
    sub[cfg.event_col] = pd.to_numeric(sub[cfg.event_col], errors="coerce")
    sub = sub.dropna(subset=[cfg.time_col, cfg.event_col])

    for f in cfg.feature_cols:
        sub[f] = pd.to_numeric(sub[f], errors="coerce")

    sub = sub.dropna(subset=cfg.feature_cols)

    if cfg.group_col:
        sub[cfg.group_col] = sub[cfg.group_col].astype(str)

    return sub


def km_summary_by_group(
    df: pd.DataFrame,
    cfg: SurvivalConfig,
    max_groups: int = 5,
) -> Dict:
    """
    Compute simple Kaplan–Meier summaries, optionally by group.
    Returns a dict with median survival times and event counts.
    """
    kmf = KaplanMeierFitter()
    result = {
        "time_col": cfg.time_col,
        "event_col": cfg.event_col,
        "group_col": cfg.group_col,
        "groups": [],
    }

    if cfg.group_col is None:
        t = df[cfg.time_col].values
        e = df[cfg.event_col].values.astype(int)
        kmf.fit(t, event_observed=e)
        result["groups"].append(
            {
                "label": "all",
                "num_patients": int(len(df)),
                "num_events": int(e.sum()),
                "median_survival": float(kmf.median_survival_time_),
            }
        )
        return result

    for i, (label, sub) in enumerate(df.groupby(cfg.group_col)):
        if i >= max_groups:
            break
        t = sub[cfg.time_col].values
        e = sub[cfg.event_col].values.astype(int)
        if len(sub) < 3:
            continue
        kmf.fit(t, event_observed=e, label=str(label))
        result["groups"].append(
            {
                "label": str(label),
                "num_patients": int(len(sub)),
                "num_events": int(e.sum()),
                "median_survival": float(kmf.median_survival_time_),
            }
        )

    return result


def fit_cox_model(
    df: pd.DataFrame,
    cfg: SurvivalConfig,
) -> Dict:
    """
    Fit a Cox proportional hazards model and return hazard ratios + metrics.

    This version is defensive:
      - If there are no (or too few) events, it returns an 'error' key.
      - If lifelines has convergence / matrix inversion issues, it catches
        and returns a clean error instead of raising.
    """
    # lifelines expects 'duration_col' and 'event_col' columns to exist
    tmp = df[[cfg.time_col, cfg.event_col] + cfg.feature_cols].copy()
    tmp = tmp.rename(
        columns={cfg.time_col: "duration", cfg.event_col: "event"}
    )

    # Basic sanity checks
    n = len(tmp)
    n_events = int(tmp["event"].sum())
    if n == 0:
        return {
            "error": "No rows available for Cox model after cleaning.",
            "concordance_index": None,
            "hazard_ratios": {},
            "coef_table": {},
        }

    if n_events == 0:
        return {
            "error": "Cox model cannot be fit: no observed events in the cohort.",
            "concordance_index": None,
            "hazard_ratios": {},
            "coef_table": {},
        }

    if n_events < 3:
        return {
            "error": f"Cox model is unstable: only {n_events} events available.",
            "concordance_index": None,
            "hazard_ratios": {},
            "coef_table": {},
        }

    cph = CoxPHFitter()

    try:
        cph.fit(tmp, duration_col="duration", event_col="event")
    except ConvergenceError as e:
        return {
            "error": f"Cox model convergence error: {e}",
            "concordance_index": None,
            "hazard_ratios": {},
            "coef_table": {},
        }
    except Exception as e:
        return {
            "error": f"Cox model failed: {e.__class__.__name__}: {e}",
            "concordance_index": None,
            "hazard_ratios": {},
            "coef_table": {},
        }

    summary = cph.summary

    hazard_ratios = {
        idx: float(np.exp(row["coef"]))
        for idx, row in summary.iterrows()
    }

    out = {
        "error": None,
        "concordance_index": float(cph.concordance_index_),
        "hazard_ratios": hazard_ratios,
        "coef_table": summary.to_dict(orient="index"),
    }
    return out

"""
Vector Autoregression (VAR) — multivariate forecasting + impulse response.

VAR generalises AR(1) to multiple time series at once. Each variable
becomes a linear function of past values of itself AND of every other
variable in the system. The result captures cross-variable feedback that
single-series methods (AR(1), Granger) can't represent compactly.

Aurora uses VAR for:

  * Multivariate forecast cones (forecast all columns jointly)
  * Lag-selection diagnostics (AIC / BIC across lag orders)
  * Cross-impact analysis (the off-diagonal coefficients tell you
    "if column X shocks by 1 SD, what does column Y do at lag k?")

Implementation: ``statsmodels.tsa.vector_ar.var_model.VAR`` — already in
``requirements.txt`` via the statsmodels dep, so no new install burden.

Preconditions:

  * ≥ 2 numeric columns
  * ≥ 30 rows (VAR fitting is unstable below that)
  * Approximately stationary or trending (we don't auto-difference;
    surface a warning if the user's data looks integrated)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)


VAR_KB_CITATIONS = [
    {
        "seed_id": "seed:sims_1980_macroeconomics_and_reality",
        "title": "Macroeconomics and Reality",
        "authors": "Sims, C.A.",
        "year": 1980,
        "venue": "Econometrica 48(1)",
        "summary": (
            "Foundational paper introducing vector autoregression for "
            "multivariate macroeconomic time series. Argues VAR is "
            "theory-agnostic relative to single-equation methods."
        ),
        "doi": "10.2307/1912017",
    },
    {
        "seed_id": "seed:lutkepohl_2005_new_introduction_var",
        "title": "New Introduction to Multiple Time Series Analysis",
        "authors": "Lütkepohl, H.",
        "year": 2005,
        "venue": "Springer",
        "summary": (
            "The canonical modern reference for VAR estimation, lag-order "
            "selection, impulse-response analysis, and Granger causality "
            "tests within a multivariate framework."
        ),
        "isbn": "978-3-540-26239-8",
    },
]


def fit_var(
    df: Any,
    *,
    target_cols: Optional[List[str]] = None,
    max_lags: int = 8,
    forecast_horizon: int = 12,
    claim_seq: int = 0,
) -> Dict[str, Any]:
    """Fit a VAR model on the supplied DataFrame and return an Aurora
    finding describing the chosen lag order, the forecast at the
    requested horizon, and the strongest cross-variable coupling.

    Args:
        df:               pandas DataFrame.
        target_cols:      Optional subset of columns to model. If None,
                          all numeric columns are used.
        max_lags:         Upper bound for AIC-based lag selection.
        forecast_horizon: Number of steps to forecast forward.
        claim_seq:        Index used to build the unique claim_id.

    Returns:
        Aurora-shaped finding dict. ``status`` field is one of:
          - "fit"         — VAR fit succeeded
          - "skipped"     — preconditions not met
          - "failed"      — fit raised; reason in description
    """
    try:
        import numpy as np
        import pandas as pd
    except ImportError as e:
        return _skip_finding(
            f"numpy / pandas not available: {e}", claim_seq,
        )

    # Resolve target columns.
    if target_cols is None:
        target_cols = [c for c in df.columns if _is_numeric_col(df[c])]
    target_cols = [c for c in target_cols if c in df.columns]

    if len(target_cols) < 2:
        return _skip_finding(
            "VAR requires at least 2 numeric columns; "
            f"only found {len(target_cols)}.",
            claim_seq,
        )

    sub = df[target_cols].dropna()
    if len(sub) < 30:
        return _skip_finding(
            f"VAR requires at least 30 rows after dropna; got {len(sub)}.",
            claim_seq,
        )

    # statsmodels VAR. Import lazily — heavy.
    try:
        from statsmodels.tsa.vector_ar.var_model import VAR
    except ImportError as e:
        return _skip_finding(
            f"statsmodels not available: {e}",
            claim_seq,
        )

    try:
        model = VAR(sub.astype(float))
        # Cap max_lags by data length per Lütkepohl's rule of thumb.
        # Statsmodels needs (1 + maxlags * k_vars) < nobs.
        n = len(sub)
        k = len(target_cols)
        upper = max(1, min(max_lags, (n - 1) // max(k, 1) - 1))
        if upper < 1:
            return _skip_finding(
                f"VAR cannot fit: not enough rows for any lag with {k} variables.",
                claim_seq,
            )
        order_results = model.select_order(maxlags=upper)
        # Prefer AIC; statsmodels exposes selected_orders as a dict.
        try:
            chosen_lag = int(order_results.selected_orders.get("aic")) or 1
        except (AttributeError, TypeError, ValueError):
            chosen_lag = 1
        chosen_lag = max(1, chosen_lag)

        fit = model.fit(chosen_lag)
        forecast = fit.forecast(sub.values[-chosen_lag:], steps=forecast_horizon)
    except Exception as e:
        LOG.exception("VAR fit failed")
        return {
            "severity": "info",
            "confidence": 0.0,
            "title": "VAR fit failed",
            "description": f"{type(e).__name__}: {e}",
            "method": "var",
            "actions": [],
            "kind_hint": "var_fit_failed",
            "claim_id": f"var-{claim_seq:04d}",
            "fabricated": False,
            "evidence": {"status": "failed", "error": str(e)},
        }

    # Pull the strongest off-diagonal coefficient as a cross-coupling
    # highlight ("when X shocks, Y responds most"). Use the lag-1 matrix
    # as the most interpretable layer.
    coef_matrix = fit.coefs[0]  # shape (k, k)
    abs_coef = np.abs(coef_matrix)
    np.fill_diagonal(abs_coef, 0.0)
    max_idx = int(np.argmax(abs_coef))
    i, j = divmod(max_idx, k)
    top_couple = {
        "from_col": target_cols[j],
        "to_col": target_cols[i],
        "coefficient_lag1": float(coef_matrix[i, j]),
    }

    # Build forecast summary: per-column final-step + 1-step.
    forecast_summary = {
        col: {
            "step_1":  float(forecast[0][idx]),
            "step_final": float(forecast[-1][idx]),
        }
        for idx, col in enumerate(target_cols)
    }

    description = (
        f"Fit a {chosen_lag}-lag VAR on {k} variables "
        f"({', '.join(target_cols[:3])}{'...' if k > 3 else ''}). "
        f"Strongest cross-effect at lag 1: a shock to "
        f"{top_couple['from_col']!r} moves {top_couple['to_col']!r} by "
        f"{top_couple['coefficient_lag1']:+.4f}. "
        f"Forecasted {forecast_horizon} steps forward."
    )

    return {
        "severity": "info",
        "confidence": 0.75,
        "title": f"VAR({chosen_lag}) on {k} variables; "
                  f"strongest coupling {top_couple['from_col']}→{top_couple['to_col']}",
        "description": description,
        "method": "var",
        "actions": ["VIEW EVIDENCE", "SEE FORECAST"],
        "kind_hint": "multivariate_forecast",
        "claim_id": f"var-{claim_seq:04d}",
        "fabricated": False,
        "kb_citations": VAR_KB_CITATIONS,
        "evidence": {
            "status": "fit",
            "chosen_lag": chosen_lag,
            "n_obs": int(n),
            "n_vars": k,
            "target_cols": list(target_cols),
            "top_cross_coupling": top_couple,
            "forecast_horizon": forecast_horizon,
            "forecast_summary": forecast_summary,
        },
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _is_numeric_col(series: Any) -> bool:
    """True iff the Series can be coerced to float without total loss."""
    try:
        import pandas as pd
        return pd.api.types.is_numeric_dtype(series.dtype)
    except Exception:
        return False


def _skip_finding(reason: str, claim_seq: int) -> Dict[str, Any]:
    """Produce an Aurora-shaped finding that records WHY a method was
    skipped. Aurora's contract: every method either runs or honestly
    declares its skip reason — silent failure is forbidden."""
    return {
        "severity": "info",
        "confidence": 1.0,
        "title": "VAR skipped",
        "description": reason,
        "method": "var",
        "actions": [],
        "kind_hint": "method_skipped",
        "claim_id": f"var-skipped",
        "fabricated": False,
        "evidence": {"status": "skipped", "reason": reason},
    }

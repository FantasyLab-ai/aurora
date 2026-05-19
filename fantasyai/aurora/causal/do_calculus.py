"""
do() operator — interventional queries.

Given an Aurora run's system_model + dataset, compute the causal
effect of ``do(treatment = x)`` on ``outcome``. Two key questions:

  1. **Identification.** Can the effect be expressed as a function of
     the observational distribution? We check the backdoor criterion
     and return a structured ``DoResult`` with the adjustment set.

  2. **Estimation.** When identifiable, fit a simple linear adjustment
     regression: ``outcome ~ treatment + Z`` for adjustment set Z.
     Coefficient on treatment IS the causal effect estimate.

The estimator is intentionally minimal — pure-numpy OLS via the
normal equations. We don't pull in statsmodels just for this. The
caller gets the point estimate + a heuristic standard error from the
residual variance + n^(-½) approximation. For tighter intervals,
re-fit through statsmodels in the SDK.

When the effect is NOT identifiable from the observed DAG, we say so
honestly — no fake number, no spurious confidence. The user can
add edges to their system_model and re-query.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set

from .dag import CausalDAG, backdoor_set


@dataclass
class DoResult:
    """The result of a do() query."""
    ok:              bool
    treatment:       str
    outcome:         str
    intervention_value: Optional[float] = None
    identifiable:    bool = False
    adjustment_set:  List[str] = field(default_factory=list)
    effect_estimate: Optional[float] = None
    standard_error:  Optional[float] = None
    n_obs:           Optional[int] = None
    method:          str = "backdoor_adjustment_ols"
    assumptions:     List[str] = field(default_factory=list)
    warnings:        List[str] = field(default_factory=list)
    # For unidentifiable cases, the reason.
    unidentifiable_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def identification_status(
    dag:       CausalDAG,
    treatment: str,
    outcome:   str,
) -> Dict[str, Any]:
    """Cheap identifiability check — no estimation, just the
    backdoor verdict + the candidate adjustment set."""
    if treatment not in dag.nodes:
        return {
            "ok": False, "identifiable": False,
            "reason": f"treatment {treatment!r} not in DAG",
        }
    if outcome not in dag.nodes:
        return {
            "ok": False, "identifiable": False,
            "reason": f"outcome {outcome!r} not in DAG",
        }
    z = backdoor_set(dag, treatment, outcome)
    if z is None:
        return {
            "ok": True, "identifiable": False,
            "reason": (
                "no admissible adjustment set found via the backdoor "
                "criterion; effect not identifiable from the observed DAG"
            ),
            "treatment": treatment, "outcome": outcome,
        }
    return {
        "ok": True, "identifiable": True,
        "adjustment_set": sorted(z),
        "treatment": treatment, "outcome": outcome,
    }


def do_query(
    df:           Any,
    dag:          CausalDAG,
    treatment:    str,
    outcome:      str,
    *,
    intervention_value: Optional[float] = None,
    extra_adjust:       Optional[List[str]] = None,
) -> DoResult:
    """Estimate the causal effect of ``do(treatment = x)`` on outcome.

    Args:
        df: pandas DataFrame with the observational data.
        dag: CausalDAG (built from the system_model).
        treatment: column name to intervene on.
        outcome:   column name of the target.
        intervention_value:
            Optional. If provided, we predict the counterfactual
            outcome at this value (Y_hat | do(T = x)). If None, we
            return the marginal effect (slope) only.
        extra_adjust:
            Optional list of additional columns to include in Z.
            Useful when the user knows of a confounder the DAG missed.

    Returns:
        A DoResult.
    """
    if treatment not in dag.nodes:
        return DoResult(
            ok=False, treatment=treatment, outcome=outcome,
            unidentifiable_reason=f"treatment {treatment!r} not in DAG",
        )
    if outcome not in dag.nodes:
        return DoResult(
            ok=False, treatment=treatment, outcome=outcome,
            unidentifiable_reason=f"outcome {outcome!r} not in DAG",
        )

    z = backdoor_set(dag, treatment, outcome)
    if z is None:
        return DoResult(
            ok=True, treatment=treatment, outcome=outcome,
            identifiable=False,
            unidentifiable_reason=(
                "no admissible adjustment set found via the backdoor "
                "criterion; the effect is not identifiable from the "
                "observational DAG. Consider adding the missing "
                "confounder as an edge in the system model."
            ),
        )
    adjust_set: List[str] = sorted(z)
    if extra_adjust:
        for col in extra_adjust:
            if col and col not in adjust_set:
                adjust_set.append(col)

    # OLS adjustment regression.
    try:
        coef, se, n_obs, warnings_msgs = _ols_adjustment(
            df, treatment=treatment, outcome=outcome,
            adjustments=adjust_set,
        )
    except _NotEnoughDataError as e:
        return DoResult(
            ok=False, treatment=treatment, outcome=outcome,
            identifiable=True, adjustment_set=adjust_set,
            unidentifiable_reason=str(e),
        )

    # Predict counterfactual if intervention_value supplied.
    predicted_outcome = None
    if intervention_value is not None and coef is not None:
        try:
            # The predicted outcome at do(treatment=x) holding adjustments
            # at their observed mean. Simple but honest.
            import pandas as pd
            sub = df[[treatment] + adjust_set + [outcome]].dropna()
            if not sub.empty:
                baseline_intercept = float(sub[outcome].mean()) - \
                    coef * float(sub[treatment].mean())
                predicted_outcome = baseline_intercept + coef * float(intervention_value)
        except Exception:
            pass

    assumptions = [
        "Causal Markov + faithfulness on the system_model DAG",
        "No unmeasured confounders beyond adjustment set",
        "Linear additive effect of treatment given adjustments",
    ]
    if not adjust_set:
        assumptions.append(
            "Empty adjustment set: treatment has no parents in the DAG; "
            "raw OLS slope used as the effect."
        )

    return DoResult(
        ok=True,
        treatment=treatment,
        outcome=outcome,
        intervention_value=intervention_value,
        identifiable=True,
        adjustment_set=adjust_set,
        effect_estimate=coef,
        standard_error=se,
        n_obs=n_obs,
        method="backdoor_adjustment_ols",
        assumptions=assumptions,
        warnings=warnings_msgs,
        unidentifiable_reason=None,
    )


# ----------------------------------------------------------------------
# OLS adjustment internals
# ----------------------------------------------------------------------

class _NotEnoughDataError(RuntimeError):
    pass


def _ols_adjustment(
    df:          Any,
    *,
    treatment:   str,
    outcome:     str,
    adjustments: List[str],
):
    """Fit ``outcome ~ treatment + adjustments`` via OLS. Return
    ``(treatment_coef, std_err, n_obs, warnings)``.

    Uses numpy's normal-equations solve. Refuses to fit if the
    treatment column ends up multicollinear with the adjustments
    (singular design matrix).
    """
    import numpy as np
    cols = [treatment, outcome] + [c for c in adjustments
                                     if c != treatment and c != outcome]
    if not all(c in df.columns for c in cols):
        missing = [c for c in cols if c not in df.columns]
        raise _NotEnoughDataError(
            f"columns missing from data: {missing}"
        )
    sub = df[cols].dropna()
    n = len(sub)
    if n < max(8, len(cols) + 2):
        raise _NotEnoughDataError(
            f"only {n} complete-case rows for {len(cols)} cols; need ≥{max(8, len(cols)+2)}"
        )

    y = sub[outcome].astype(float).values
    # Coerce non-numeric adjustment cols by dropping them with a warning.
    valid_adjusts: List[str] = []
    warns: List[str] = []
    for c in adjustments:
        if c == treatment or c == outcome:
            continue
        try:
            sub[c].astype(float)
            valid_adjusts.append(c)
        except (TypeError, ValueError):
            warns.append(f"dropped non-numeric adjustment column {c!r}")

    if not all(np.issubdtype(sub[c].dtype, np.number)
               for c in [treatment]):
        try:
            sub[treatment] = sub[treatment].astype(float)
        except Exception as e:
            raise _NotEnoughDataError(
                f"treatment column {treatment!r} is not numeric: {e}"
            )

    X = sub[[treatment] + valid_adjusts].astype(float).values
    # Add intercept column.
    X_full = np.column_stack([np.ones(n), X])
    try:
        XtX = X_full.T @ X_full
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        raise _NotEnoughDataError(
            "design matrix is singular; adjustments are likely "
            "collinear with the treatment"
        )
    beta = XtX_inv @ X_full.T @ y
    # Treatment coef is index 1 (intercept at 0).
    coef = float(beta[1])
    # Residual SE: sqrt(MSE * (XtX^-1)_jj)
    resid = y - X_full @ beta
    dof = n - X_full.shape[1]
    if dof < 1:
        se = None
    else:
        mse = float(resid @ resid) / dof
        se = float(np.sqrt(mse * XtX_inv[1, 1]))
    return coef, se, int(n), warns

"""
Counterfactual queries: "what would Y have been if X had been x'?"

Pearl's three-step recipe:

  1. **Abduction** — given the observed evidence, update the
     distribution over the structural unobservables (the noise terms
     ``U`` in the SCM).
  2. **Action** — apply the intervention do(X = x').
  3. **Prediction** — compute Y under the updated U and the
     intervened X.

We implement a tractable linear-SCM version:

  * Treatment X has a linear additive structural equation
    ``X = f(parents(X)) + U_X`` we don't try to recover U_X
    explicitly — for linear SCMs, the counterfactual outcome is
    the difference between the observed Y and the OLS predicted Y
    under do(X = x'). This reproduces standard "counterfactual
    regression" without the heavy ML lift.

  * The estimator reuses the backdoor adjustment OLS from
    ``do_calculus.do_query`` — same identification guarantees, same
    assumption surface.

What this is NOT: a non-parametric counterfactual estimator (deep
ANMs, normalising flows, structural VAEs). Those are valuable but
heavy; the linear version is faithful enough for the v2.0 surface
and ships today.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .dag import CausalDAG
from .do_calculus import do_query, DoResult


@dataclass
class CounterfactualResult:
    """The result of a counterfactual query."""
    ok: bool
    treatment: str
    outcome:   str
    observed_treatment: Optional[float]
    counterfactual_treatment: float
    observed_outcome: Optional[float]
    counterfactual_outcome: Optional[float] = None
    delta: Optional[float] = None
    n_obs: Optional[int] = None
    effect_estimate: Optional[float] = None
    standard_error:  Optional[float] = None
    adjustment_set:  List[str] = field(default_factory=list)
    method:          str = "linear_scm_counterfactual"
    assumptions:     List[str] = field(default_factory=list)
    warnings:        List[str] = field(default_factory=list)
    error:           Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def counterfactual_query(
    df: Any,
    dag: CausalDAG,
    treatment: str,
    outcome: str,
    *,
    counterfactual_treatment: float,
    row_index: Optional[int] = None,
) -> CounterfactualResult:
    """Estimate Y under do(treatment = counterfactual_treatment),
    contrasting with the observed value (either at row_index or the
    sample mean).

    Args:
        df: pandas DataFrame.
        dag: CausalDAG.
        treatment / outcome: column names.
        counterfactual_treatment: the value to plug in for X under do().
        row_index: optional. If None, contrast with the sample-mean row.

    Returns CounterfactualResult.
    """
    # Defer to do_query for identification + estimation.
    do_res: DoResult = do_query(
        df, dag, treatment, outcome,
        intervention_value=counterfactual_treatment,
    )
    if not do_res.ok or not do_res.identifiable:
        return CounterfactualResult(
            ok=False, treatment=treatment, outcome=outcome,
            observed_treatment=None,
            counterfactual_treatment=counterfactual_treatment,
            observed_outcome=None,
            error=do_res.unidentifiable_reason or "do_query failed",
            assumptions=do_res.assumptions,
            warnings=do_res.warnings,
        )

    # Resolve the observed treatment + outcome.
    observed_treatment: Optional[float] = None
    observed_outcome:   Optional[float] = None
    try:
        if row_index is not None:
            row = df.iloc[int(row_index)]
            observed_treatment = float(row[treatment])
            observed_outcome   = float(row[outcome])
        else:
            observed_treatment = float(df[treatment].astype(float).mean())
            observed_outcome   = float(df[outcome].astype(float).mean())
    except Exception:
        pass

    # Counterfactual outcome: Y_observed + slope * (X_cf - X_observed).
    coef = do_res.effect_estimate
    cf_outcome: Optional[float] = None
    delta: Optional[float] = None
    if (coef is not None
            and observed_treatment is not None
            and observed_outcome is not None):
        delta = float(coef * (counterfactual_treatment - observed_treatment))
        cf_outcome = float(observed_outcome + delta)

    return CounterfactualResult(
        ok=True,
        treatment=treatment,
        outcome=outcome,
        observed_treatment=observed_treatment,
        counterfactual_treatment=float(counterfactual_treatment),
        observed_outcome=observed_outcome,
        counterfactual_outcome=cf_outcome,
        delta=delta,
        n_obs=do_res.n_obs,
        effect_estimate=do_res.effect_estimate,
        standard_error=do_res.standard_error,
        adjustment_set=do_res.adjustment_set,
        method="linear_scm_counterfactual",
        assumptions=do_res.assumptions + [
            "Linear structural causal model (additive noise)",
            "Counterfactual Y_cf = Y_obs + β(X_cf − X_obs)",
        ],
        warnings=do_res.warnings,
        error=None,
    )

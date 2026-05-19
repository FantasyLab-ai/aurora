"""
Aurora Causal Inference (v2.0 Stream A): do-calculus + counterfactual queries.

The Aurora v1 pipeline detects causal **relationships** (Granger,
mutual information, multivariate FE) and surfaces them on the system
graph. But that's correlational structure with a causal orientation —
not yet the *interventional* layer Pearl's do-calculus formalises.

This module ships the interventional layer:

  * **DAG construction** — load a directed acyclic graph from the
    system_model artifact, with nodes = variables and edges =
    discovered causal directions. Cycles get broken with a warning.

  * **do() operator** — given a target outcome ``Y`` and an
    intervention ``do(X = x)``, identify whether the causal effect is
    estimable from observational data via the **backdoor criterion**.
    Return the adjustment set + the estimated effect.

  * **Counterfactual queries** — "what would Y have been if X had been
    x' instead of its observed value?" Implemented via the
    three-step abduction → action → prediction recipe.

Glass-box compliance: every estimate carries its identification
provenance — which variables were adjusted on, whether the effect is
point-identifiable, and the assumptions baked into the estimator.
When an effect is NOT identifiable from the observational data, we
say so honestly rather than returning a number that pretends to be a
causal effect.

This is **observational** causal inference: we don't assume access to
randomised data. When the user provides an interventional dataset,
we'll lean on it; otherwise we identify-or-refuse.

The system graph in the Studio's SPACETIME view is the natural place
to surface do() queries — a "what if X were y" input box on each
node fires through to ``/api/causal/do`` and renders the result on
top of the worldlines.
"""
from __future__ import annotations

from .dag import (
    CausalDAG,
    load_dag_from_system_model,
    backdoor_set,
    DagCycleWarning,
)
from .do_calculus import (
    do_query,
    DoResult,
    identification_status,
)
from .counterfactual import (
    counterfactual_query,
    CounterfactualResult,
)

__all__ = [
    # DAG
    "CausalDAG",
    "load_dag_from_system_model",
    "backdoor_set",
    "DagCycleWarning",
    # do-calculus
    "do_query",
    "DoResult",
    "identification_status",
    # Counterfactuals
    "counterfactual_query",
    "CounterfactualResult",
]

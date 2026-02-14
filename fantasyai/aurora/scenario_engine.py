from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List


@dataclass
class SensitivityResult:
    """
    Holds results of a one-at-a-time sensitivity analysis.

    base_value: the metric using the baseline parameter set.
    deltas: mapping param_name -> {relative_perturbation -> delta_from_base}.
    """
    base_value: float
    deltas: Dict[str, Dict[float, float]]


def run_weight_sensitivity(
    base_weights: Dict[str, float],
    evaluate_fn: Callable[[Dict[str, float]], float],
    rel_perturbations: List[float] | None = None,
) -> SensitivityResult:
    """
    Generic one-at-a-time sensitivity engine for scalar metrics.

    Parameters
    ----------
    base_weights:
        Baseline dictionary of weights/parameters.
    evaluate_fn:
        Function that takes a dict of weights and returns a single scalar metric
        (e.g., 95th percentile risk, total cost, etc.).
    rel_perturbations:
        Relative perturbations to apply, e.g. [-0.2, -0.1, 0.1, 0.2].
        Each perturbation dp is interpreted as w_new = w * (1 + dp).

    Returns
    -------
    SensitivityResult
    """
    if rel_perturbations is None:
        rel_perturbations = [-0.2, -0.1, 0.1, 0.2]

    base_value = float(evaluate_fn(dict(base_weights)))
    deltas: Dict[str, Dict[float, float]] = {}

    for name, w in base_weights.items():
        deltas[name] = {}
        for dp in rel_perturbations:
            w_new = w * (1.0 + dp)
            perturbed = dict(base_weights)
            perturbed[name] = w_new
            val = float(evaluate_fn(perturbed))
            deltas[name][dp] = val - base_value

    return SensitivityResult(base_value=base_value, deltas=deltas)

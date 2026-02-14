# fantasyai/aurora/problem_features.py

from __future__ import annotations

from typing import Dict, Any
from .problem_spec import ProblemSpec


def extract_problem_features(spec: ProblemSpec) -> Dict[str, Any]:
    """
    Turn a ProblemSpec into a simple feature dict for meta-models:
    - counts of variables/equations
    - length/complexity of expressions
    - problem type
    - sanity features from meta
    """

    num_vars = len(spec.variables)
    num_eqs = len(spec.equations) if spec.equations else 0
    total_expr_len = 0
    num_ops = 0

    for eq in spec.equations:
        s = eq.expression or ""
        total_expr_len += len(s)
        num_ops += sum(s.count(op) for op in ["+", "-", "*", "/", "**", "exp", "sin", "cos", "log"])

    prob_type = spec.problem_type
    has_bounds = any(v.lower is not None or v.upper is not None for v in spec.variables)
    has_initials = any(v.initial is not None for v in spec.variables)

    features: Dict[str, Any] = {
        "problem_type": prob_type,
        "num_vars": num_vars,
        "num_eqs": num_eqs,
        "total_expr_len": total_expr_len,
        "num_ops": num_ops,
        "has_bounds": int(has_bounds),
        "has_initials": int(has_initials),
        "meta_domain": spec.meta.get("domain", "unknown"),
    }

    return features

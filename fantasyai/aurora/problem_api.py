from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

import numpy as np
from scipy.optimize import minimize


@dataclass
class VarSpec:
    """
    Describes a single decision variable.
    """
    name: str
    lower: float | None = None
    upper: float | None = None
    initial: float = 0.0


@dataclass
class ObjectiveSpec:
    """
    Objective function specification.

    sense: "min" or "max".
    func:  f(vars_dict) -> scalar.
    """
    sense: str
    func: Callable[[Dict[str, float]], float]
    description: str = ""


@dataclass
class ConstraintSpec:
    """
    Constraint: func(vars_dict) with a relational sense.

    sense in {"<=", ">=", "=="}.
    For "<=", we interpret func(vars) <= 0.
    For ">=", we interpret func(vars) >= 0.
    For "==", we interpret func(vars) == 0.
    """
    func: Callable[[Dict[str, float]], float]
    sense: str
    description: str = ""


@dataclass
class ProblemSpec:
    """
    Generic optimization problem container.

    variables: list of VarSpec
    objective: ObjectiveSpec
    constraints: list of ConstraintSpec
    meta: any additional metadata (domain, tags, etc.)
    """
    variables: List[VarSpec]
    objective: ObjectiveSpec
    constraints: List[ConstraintSpec] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


def _dict_from_vector(x: np.ndarray, spec: ProblemSpec) -> Dict[str, float]:
    return {v.name: float(x[i]) for i, v in enumerate(spec.variables)}


def solve_problem_with_scipy(spec: ProblemSpec) -> Dict[str, Any]:
    """
    Compile a ProblemSpec into a SciPy SLSQP call and solve it.

    Returns a dict with:
        - success, status, message
        - solution (dict name -> value)
        - objective_value
        - meta (passed through from spec)
    """
    x0 = np.array([v.initial for v in spec.variables], dtype=float)

    bounds = []
    for v in spec.variables:
        lo = -np.inf if v.lower is None else v.lower
        hi = np.inf if v.upper is None else v.upper
        bounds.append((lo, hi))

    sense = spec.objective.sense.lower()
    if sense not in ("min", "max"):
        raise ValueError(f"Unsupported objective sense: {sense}")

    def obj_wrapped(x: np.ndarray) -> float:
        var_dict = _dict_from_vector(x, spec)
        val = float(spec.objective.func(var_dict))
        return val if sense == "min" else -val

    constraints = []
    for c in spec.constraints:
        s = c.sense
        if s == "==":
            def f_eq(x, c=c):
                return float(c.func(_dict_from_vector(x, spec)))
            constraints.append({"type": "eq", "fun": f_eq})
        elif s == "<=":
            def f_le(x, c=c):
                # SciPy expects >= 0 for "ineq" by default, so invert sign
                return -float(c.func(_dict_from_vector(x, spec)))
            constraints.append({"type": "ineq", "fun": f_le})
        elif s == ">=":
            def f_ge(x, c=c):
                return float(c.func(_dict_from_vector(x, spec)))
            constraints.append({"type": "ineq", "fun": f_ge})
        else:
            raise ValueError(f"Unsupported constraint sense: {s}")

    res = minimize(
        obj_wrapped,
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
    )

    sol_dict = _dict_from_vector(res.x, spec)
    obj_val = float(spec.objective.func(sol_dict))
    if sense == "max":
        # We minimized -f, so we return the true max value.
        obj_val = float(obj_val)

    return {
        "success": bool(res.success),
        "status": int(res.status),
        "message": str(res.message),
        "solution": sol_dict,
        "objective_value": obj_val,
        "meta": spec.meta,
    }

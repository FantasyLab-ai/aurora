from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Any, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize


@dataclass
class Constraint:
    name: str
    fn: Callable[[np.ndarray], float]
    kind: str  # "ineq" (>=0) or "eq" (=0)
    weight: float = 1.0


def check_constraints(x: np.ndarray, constraints: List[Constraint]) -> Dict[str, Any]:
    status = []
    ok = True
    for c in constraints:
        v = float(c.fn(x))
        if c.kind == "ineq":
            sat = (v >= 0.0)
        else:
            sat = (abs(v) <= 1e-6)
        ok = ok and sat
        status.append({"name": c.name, "kind": c.kind, "value": v, "satisfied": bool(sat)})
    return {"ok": bool(ok), "constraints": status}


def penalty_objective(
    base_obj: Callable[[np.ndarray], float],
    constraints: List[Constraint],
    penalty_scale: float = 1e3,
) -> Callable[[np.ndarray], float]:
    def obj(x: np.ndarray) -> float:
        val = float(base_obj(x))
        pen = 0.0
        for c in constraints:
            v = float(c.fn(x))
            if c.kind == "ineq":
                pen += c.weight * max(0.0, -v) ** 2
            else:
                pen += c.weight * (v ** 2)
        return val + penalty_scale * pen
    return obj


def solve_constrained(
    base_obj: Callable[[np.ndarray], float],
    x0: np.ndarray,
    bounds: Optional[List[Tuple[float, float]]] = None,
    constraints: Optional[List[Constraint]] = None,
    method: str = "L-BFGS-B",
    penalty_scale: float = 1e3,
) -> Dict[str, Any]:
    x0 = np.asarray(x0, dtype=float)
    constraints = constraints or []
    obj = penalty_objective(base_obj, constraints, penalty_scale=penalty_scale) if constraints else base_obj

    res = minimize(obj, x0, method=method, bounds=bounds)

    chk = check_constraints(res.x, constraints) if constraints else {"ok": True, "constraints": []}

    return {
        "success": bool(res.success) and bool(chk["ok"]),
        "x": res.x.tolist(),
        "fun": float(res.fun),
        "diagnostics": {
            "success": bool(res.success),
            "status": int(getattr(res, "status", -1)),
            "message": str(getattr(res, "message", "")),
            "nfev": int(getattr(res, "nfev", -1)),
            "nit": int(getattr(res, "nit", -1)),
        },
        "constraint_check": chk,
        "method": method,
    }

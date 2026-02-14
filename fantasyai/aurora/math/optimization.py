
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

def solve_linear_program(c: List[float], A_ub: List[List[float]], b_ub: List[float], bounds: Optional[List[Tuple[float,float]]] = None) -> Dict[str, Any]:
    try:
        from scipy.optimize import linprog
    except Exception as e:
        return {"error": f"scipy_missing:{e}"}
    res = linprog(c=np.asarray(c, dtype=float),
                  A_ub=np.asarray(A_ub, dtype=float),
                  b_ub=np.asarray(b_ub, dtype=float),
                  bounds=bounds,
                  method="highs")
    return {
        "status": int(res.status),
        "success": bool(res.success),
        "message": str(res.message),
        "objective": float(res.fun) if res.success else None,
        "x": res.x.tolist() if res.success else None
    }

def minimize_nonlinear(fun_name: str = "quadratic_bowl", x0: Optional[List[float]] = None) -> Dict[str, Any]:
    """
    Placeholder “MATLAB-style” solver entry. You can wire your real objective later.
    """
    try:
        from scipy.optimize import minimize
    except Exception as e:
        return {"error": f"scipy_missing:{e}"}

    x0 = np.asarray(x0 or [0.0, 0.0], dtype=float)

    if fun_name == "quadratic_bowl":
        def f(x): return (x[0]-1.0)**2 + 2.0*(x[1]+2.0)**2
        def g(x): return np.array([2.0*(x[0]-1.0), 4.0*(x[1]+2.0)], dtype=float)
    else:
        def f(x): return float(np.sum(x**2))
        def g(x): return 2.0*x

    res = minimize(f, x0, jac=g, method="BFGS")
    return {
        "fun_name": fun_name,
        "success": bool(res.success),
        "message": str(res.message),
        "x": res.x.tolist(),
        "objective": float(res.fun),
        "nit": int(res.nit),
    }

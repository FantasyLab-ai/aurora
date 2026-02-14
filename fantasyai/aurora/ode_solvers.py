# fantasyai/aurora/ode_solvers.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Any, Tuple

import numpy as np
from scipy.integrate import solve_ivp


@dataclass
class ODESolution:
    success: bool
    t: np.ndarray
    y: np.ndarray
    diagnostics: Dict[str, Any]


def solve_ode_system(
    func: Callable[[float, np.ndarray], np.ndarray],
    t_span: Tuple[float, float],
    y0: np.ndarray,
    *,
    method: str = "RK45",
    t_eval: np.ndarray | None = None,
    max_step: float | None = None,
) -> ODESolution:
    """
    Thin wrapper around scipy.integrate.solve_ivp.

    func: f(t, y) -> dy/dt
    t_span: (t0, t1)
    y0: initial state vector
    """
    res = solve_ivp(
        func,
        t_span,
        y0,
        method=method,
        t_eval=t_eval,
        max_step=max_step,
        vectorized=False,
    )

    diag = {
        "status": int(res.status),
        "message": str(res.message),
        "nfev": int(res.nfev),
    }

    return ODESolution(
        success=bool(res.success),
        t=res.t,
        y=res.y,
        diagnostics=diag,
    )

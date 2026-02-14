# fantasyai/aurora/math_solvers.py
"""
Aurora MATLAB-style math solvers:
- Linear system solving (Ax = b), condition number, residuals
- Eigen decomposition and dominant modes
- Truncated SVD
- Nonlinear root finding (Newton-type)
- ODE integration wrapper (solve_ivp)
"""

from dataclasses import dataclass
from typing import Callable, Dict, Any, Tuple

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import root


# -----------------------------
# Linear algebra utilities
# -----------------------------

@dataclass
class LinearSolveResult:
    x: np.ndarray
    residual_norm: float
    cond_A: float


def solve_linear_system(A: np.ndarray, b: np.ndarray) -> LinearSolveResult:
    """
    Solve Ax = b with diagnostics (like MATLAB's backslash solver with extra info).
    """
    A = np.asarray(A, dtype=float)
    b = np.asarray(b, dtype=float)

    x = np.linalg.solve(A, b)
    residual = A @ x - b
    residual_norm = np.linalg.norm(residual)
    cond_A = np.linalg.cond(A)

    return LinearSolveResult(x=x, residual_norm=residual_norm, cond_A=cond_A)


@dataclass
class EigenResult:
    eigenvalues: np.ndarray
    eigenvectors: np.ndarray


def eigen_decomposition(A: np.ndarray, symmetric: bool = True) -> EigenResult:
    """
    Eigen-decomposition with option for symmetric matrices (like MATLAB's eig/eigvals).
    """
    A = np.asarray(A, dtype=float)
    if symmetric:
        vals, vecs = np.linalg.eigh(A)
    else:
        vals, vecs = np.linalg.eig(A)
    return EigenResult(eigenvalues=vals, eigenvectors=vecs)


@dataclass
class SVDResult:
    U: np.ndarray
    S: np.ndarray
    Vt: np.ndarray
    rank_k: int


def truncated_svd(A: np.ndarray, k: int) -> SVDResult:
    """
    Truncated SVD similar to MATLAB's svds.
    """
    A = np.asarray(A, dtype=float)
    U, S, Vt = np.linalg.svd(A, full_matrices=False)
    k = min(k, len(S))
    return SVDResult(U=U[:, :k], S=S[:k], Vt=Vt[:k, :], rank_k=k)


# -----------------------------
# Nonlinear solvers
# -----------------------------

@dataclass
class RootResult:
    x: np.ndarray
    success: bool
    message: str


def solve_nonlinear_system(
    func: Callable[[np.ndarray], np.ndarray],
    x0: np.ndarray,
    method: str = "hybr",
) -> RootResult:
    """
    Nonlinear root-finding wrapper (like MATLAB's fsolve).
    """
    x0 = np.asarray(x0, dtype=float)
    res = root(func, x0, method=method)
    return RootResult(x=res.x, success=res.success, message=res.message)


# -----------------------------
# ODE solvers
# -----------------------------

@dataclass
class ODEResult:
    t: np.ndarray
    y: np.ndarray
    success: bool
    message: str


def solve_ode_system(
    f: Callable[[float, np.ndarray], np.ndarray],
    t_span: Tuple[float, float],
    y0: np.ndarray,
    t_eval: np.ndarray | None = None,
    method: str = "RK45",
    rtol: float = 1e-6,
    atol: float = 1e-9,
) -> ODEResult:
    """
    ODE integration wrapper, like MATLAB's ode45/ode15s style interface.
    f: function(t, y) -> dy/dt
    """
    y0 = np.asarray(y0, dtype=float)
    sol = solve_ivp(
        fun=f,
        t_span=t_span,
        y0=y0,
        t_eval=t_eval,
        method=method,
        rtol=rtol,
        atol=atol,
    )
    return ODEResult(t=sol.t, y=sol.y, success=sol.success, message=sol.message)


# -----------------------------
# Convenience: harmonic oscillator demo
# -----------------------------

def harmonic_oscillator(k: float = 1.0, m: float = 1.0):
    """
    Returns an ODE function for a 1D harmonic oscillator:
        d2x/dt2 + (k/m) x = 0
    State y = [x, v].
    """
    def f(t, y):
        x, v = y
        a = -(k / m) * x
        return np.array([v, a], dtype=float)
    return f

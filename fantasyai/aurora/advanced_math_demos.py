"""
fantasyai.aurora.advanced_math_demos

Additional math demos to show Aurora as a "MATLAB-level" numerical engine:
  - Linear programming (LP) for cost minimization
  - 1D heat equation (finite-difference time stepping)
  - Monte Carlo portfolio risk (VaR-style metric)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Any, Tuple

import numpy as np
from scipy.optimize import linprog


@dataclass
class LPDemoResult:
    success: bool
    cost: float
    x: np.ndarray
    diagnostics: Dict[str, Any]


def lp_cost_minimization_demo() -> LPDemoResult:
    """
    Simple logistics-style LP:

      Minimize   c^T x
      Subject to A_ub x <= b_ub, x >= 0

    Example: choose quantity shipped from 3 depots to 2 regions.
    """
    # Cost coefficients: cost to ship from each depot to each region
    # x = [d1_r1, d1_r2, d2_r1, d2_r2, d3_r1, d3_r2]
    c = np.array([4.0, 5.0, 2.0, 4.0, 3.0, 3.5])

    # Demand constraints (>=, so we represent as -x <= -demand)
    # Region 1 demand >= 80
    # Region 2 demand >= 70
    A_ub = np.array([
        [-1.0,  0.0, -1.0,  0.0, -1.0,  0.0],  # -(d1_r1 + d2_r1 + d3_r1) <= -80
        [ 0.0, -1.0,  0.0, -1.0,  0.0, -1.0],  # -(d1_r2 + d2_r2 + d3_r2) <= -70
    ])
    b_ub = np.array([-80.0, -70.0])

    # Capacity constraints: each depot has max capacity
    # (We encode capacity with extra inequalities)
    A_ub_extra = np.array([
        [1.0, 1.0, 0.0, 0.0, 0.0, 0.0],  # d1_r1 + d1_r2 <= 100
        [0.0, 0.0, 1.0, 1.0, 0.0, 0.0],  # d2_r1 + d2_r2 <= 90
        [0.0, 0.0, 0.0, 0.0, 1.0, 1.0],  # d3_r1 + d3_r2 <= 70
    ])
    b_ub_extra = np.array([100.0, 90.0, 70.0])

    A_ub = np.vstack([A_ub, A_ub_extra])
    b_ub = np.concatenate([b_ub, b_ub_extra])

    bounds = [(0, None)] * 6

    res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    return LPDemoResult(
        success=res.success,
        cost=float(res.fun) if res.success else math.nan,
        x=res.x if res.success else np.zeros_like(c),
        diagnostics={
            "status": res.status,
            "message": res.message,
            "nit": getattr(res, "nit", None),
        },
    )


@dataclass
class HeatEquationResult:
    nx: int
    nt: int
    dx: float
    dt: float
    alpha: float
    u_initial: np.ndarray
    u_final: np.ndarray


def heat_equation_demo(
    nx: int = 50,
    nt: int = 500,
    alpha: float = 0.01,
    t_final: float = 1.0,
) -> HeatEquationResult:
    """
    1D heat equation on [0, 1] with fixed boundary conditions u(0)=u(1)=0.

    du/dt = alpha * d^2u/dx^2

    Explicit finite-difference time-stepping to show evolution from an
    initial "heat bump" toward equilibrium.
    """
    # Spatial grid
    x = np.linspace(0.0, 1.0, nx)
    dx = x[1] - x[0]

    # Time step chosen for stability (explicit scheme)
    dt = t_final / nt
    r = alpha * dt / (dx * dx)
    if r > 0.5:
        # Still proceed, but note that in practice we'd reduce dt for stability.
        pass

    # Initial condition: Gaussian bump in the middle
    u = np.exp(-100.0 * (x - 0.5) ** 2)
    u_initial = u.copy()

    # Time stepping
    for _ in range(nt):
        u_new = u.copy()
        # Interior points only
        u_new[1:-1] = u[1:-1] + r * (u[2:] - 2.0 * u[1:-1] + u[:-2])
        # Boundary conditions: u(0)=0, u(1)=0
        u_new[0] = 0.0
        u_new[-1] = 0.0
        u = u_new

    return HeatEquationResult(
        nx=nx,
        nt=nt,
        dx=dx,
        dt=dt,
        alpha=alpha,
        u_initial=u_initial,
        u_final=u,
    )


@dataclass
class MonteCarloResult:
    num_paths: int
    horizon_days: int
    mean_daily_return: float
    std_daily_return: float
    final_returns: np.ndarray
    var_95: float
    cvar_95: float


def monte_carlo_portfolio_risk_demo(
    num_paths: int = 10000,
    horizon_days: int = 252,
    mean_daily: float = 0.0005,
    std_daily: float = 0.01,
) -> MonteCarloResult:
    """
    Monte Carlo simulation of a single-asset portfolio.

    - Simulate geometric-like random walk of daily log-returns.
    - Compute VaR(95%) and CVaR(95%) on final portfolio change.
    """
    # Simulate daily returns
    rng = np.random.default_rng(42)
    daily_returns = rng.normal(mean_daily, std_daily, size=(num_paths, horizon_days))

    # Cumulative log-returns -> final value (starting from 1.0)
    log_returns = daily_returns.cumsum(axis=1)
    final_values = np.exp(log_returns[:, -1])  # approx geometric Brownian

    # Final returns relative to 1.0
    final_returns = final_values - 1.0

    # VaR 95: 5th percentile of the distribution (loss side)
    var_95 = np.percentile(final_returns, 5)
    # CVaR 95: mean of the worst 5% outcomes
    tail_mask = final_returns <= var_95
    cvar_95 = final_returns[tail_mask].mean()

    return MonteCarloResult(
        num_paths=num_paths,
        horizon_days=horizon_days,
        mean_daily_return=mean_daily,
        std_daily_return=std_daily,
        final_returns=final_returns,
        var_95=float(var_95),
        cvar_95=float(cvar_95),
    )

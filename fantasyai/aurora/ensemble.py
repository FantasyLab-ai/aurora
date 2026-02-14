# fantasyai/aurora/ensemble.py

from __future__ import annotations

from dataclasses import asdict
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import sympy as sp
from sympy import Eq

from .problem_spec import ProblemSpec, VariableSpec, EquationSpec
from .math_lab import (
    MathSolution,
    solve_equation_system,
    solve_optimization,
)
from ..core.llm_client import LLMClient
from .research_memory import log_experiment


# -------------------------------------------------------------------
# Equation system ensemble
# -------------------------------------------------------------------


def _equation_system_sympy_nsolve(spec: ProblemSpec) -> MathSolution:
    """
    Numeric root-finding using sympy.nsolve as a complementary approach
    to sympy.solve.
    """
    symbols = {v.name: sp.symbols(v.name) for v in spec.variables}
    exprs = []
    for eq in spec.equations:
        exprs.append(sp.sympify(eq.expression, locals=symbols))

    equations = [Eq(expr, 0) for expr in exprs]
    var_order = [symbols[v.name] for v in spec.variables]

    # Build initial guess from spec or zeros
    x0 = []
    for v in spec.variables:
        x0.append(0.0 if v.initial is None else v.initial)
    x0_vec = sp.Matrix(x0)

    try:
        sol_vec = sp.nsolve(equations, var_order, x0_vec)
    except Exception as e:
        return MathSolution(
            success=False,
            method="sympy.nsolve",
            symbolic_solution=None,
            numeric_solution=None,
            diagnostics={"error": str(e)},
        )

    numeric_solution = {
        v.name: float(sol_vec[i])
        for i, v in enumerate(spec.variables)
    }

    return MathSolution(
        success=True,
        method="sympy.nsolve",
        symbolic_solution=None,
        numeric_solution=numeric_solution,
        diagnostics={"note": "numeric root-finding via nsolve"},
    )


def solve_equation_ensemble(
    spec: ProblemSpec,
    *,
    llm: Optional[LLMClient] = None,
    explain: bool = True,
    tag: Optional[str] = None,
) -> MathSolution:
    """
    Try multiple strategies for equation systems and return the best one.

    Strategies:
      1) sympy.solve (analytic)
      2) sympy.nsolve (numeric, single-start)
      3) (later) multi-start nsolve
    """
    if spec.problem_type != "equation_system":
        raise ValueError("solve_equation_ensemble requires problem_type='equation_system'.")

    candidates: List[MathSolution] = []

    # 1) Analytic
    candidates.append(solve_equation_system(spec))

    # 2) Numeric nsolve
    try:
        candidates.append(_equation_system_sympy_nsolve(spec))
    except Exception as e:
        candidates.append(
            MathSolution(
                success=False,
                method="sympy.nsolve",
                symbolic_solution=None,
                numeric_solution=None,
                diagnostics={"error": f"nsolve wrapper error: {e}"},
            )
        )

    # Choose best: prioritize success; otherwise keep the richest diagnostics
    success_solutions = [c for c in candidates if c.success]
    if success_solutions:
        best = success_solutions[0]
    else:
        # Pick the one with most diagnostics
        best = max(candidates, key=lambda c: len(c.diagnostics or {}))

    # LLM explanation (optional)
    if explain and llm is not None:
        from .math_lab import _explain_solution_with_llm  # reuse helper
        best.explanation = _explain_solution_with_llm(spec, best, llm)

    # Log experiment in research memory
    log_experiment(spec, best, tag=tag or f"equation_ensemble:{spec.meta.get('domain','unknown')}")

    return best


# -------------------------------------------------------------------
# Optimization ensemble
# -------------------------------------------------------------------


def solve_optimization_ensemble(
    spec: ProblemSpec,
    *,
    num_random_restarts: int = 5,
    llm: Optional[LLMClient] = None,
    explain: bool = True,
    tag: Optional[str] = None,
) -> MathSolution:
    """
    Run multiple optimization attempts with different initial guesses
    to avoid local minima traps.
    """
    from .math_lab import _build_objective_func
    from scipy.optimize import minimize

    if spec.problem_type != "optimization":
        raise ValueError("solve_optimization_ensemble requires problem_type='optimization'.")

    if spec.optimization is None:
        raise ValueError("ProblemSpec.optimization must be set for optimization problems.")

    # First, run the baseline optimizer
    base_solution = solve_optimization(spec)
    candidates: List[MathSolution] = [base_solution]

    # Prepare objective
    symbols = {v.name: sp.symbols(v.name) for v in spec.variables}
    var_order = [v.name for v in spec.variables]
    f = _build_objective_func(spec.optimization, symbols, var_order)

    # Build bounds and base initial guess
    x0 = []
    bounds = []
    for v in spec.variables:
        init = 0.0 if v.initial is None else v.initial
        x0.append(init)
        lo = -np.inf if v.lower is None else v.lower
        hi = np.inf if v.upper is None else v.upper
        bounds.append((lo, hi))

    x0_arr = np.array(x0, dtype=float)
    use_bounds = any(b != (-np.inf, np.inf) for b in bounds)

    # Random restarts
    for i in range(num_random_restarts):
        # random perturbation around x0
        noise = np.random.normal(loc=0.0, scale=1.0, size=len(x0_arr))
        start = x0_arr + noise

        res = minimize(
            f,
            start,
            bounds=bounds if use_bounds else None,
        )

        numeric_solution: Optional[Dict[str, float]] = None
        if res.success:
            numeric_solution = {name: float(val) for name, val in zip(var_order, res.x)}

        diag = {
            "success": bool(res.success),
            "status": int(res.status),
            "message": str(res.message),
            "fun": float(res.fun) if res.fun is not None else None,
            "nfev": int(res.nfev),
            "nit": int(res.nit),
            "restart_index": i + 1,
        }

        cand = MathSolution(
            success=bool(res.success),
            method="scipy.optimize.minimize(random_restart)",
            symbolic_solution=None,
            numeric_solution=numeric_solution,
            diagnostics=diag,
        )
        candidates.append(cand)

    # Select best successful solution by objective value
    successful = [
        c for c in candidates
        if c.success and c.diagnostics.get("fun") is not None
    ]

    if successful:
        best = min(successful, key=lambda c: c.diagnostics["fun"])
    else:
        # fallback
        best = base_solution

    if explain and llm is not None:
        from .math_lab import _explain_solution_with_llm
        best.explanation = _explain_solution_with_llm(spec, best, llm)

    log_experiment(spec, best, tag=tag or f"opt_ensemble:{spec.meta.get('domain','unknown')}")

    return best

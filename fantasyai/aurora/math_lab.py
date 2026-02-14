# fantasyai/aurora/math_lab.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple

import math

import sympy as sp
from sympy import Eq

from .problem_spec import ProblemSpec, VariableSpec, EquationSpec, OptimizationSpec
from ..core.llm_client import LLMClient


@dataclass
class MathSolution:
    """
    Container for math_lab results.
    """
    success: bool
    method: str
    symbolic_solution: Optional[Dict[str, Any]]
    numeric_solution: Optional[Dict[str, float]]
    diagnostics: Dict[str, Any]
    explanation: Optional[str] = None


def _build_sympy_symbols(vars_spec: List[VariableSpec]) -> Dict[str, sp.Symbol]:
    return {v.name: sp.symbols(v.name) for v in vars_spec}


def _parse_expressions(
    equations: List[EquationSpec],
    symbols: Dict[str, sp.Symbol],
) -> List[sp.Expr]:
    exprs: List[sp.Expr] = []
    for eq in equations:
        local_ns = dict(symbols)
        try:
            expr = sp.sympify(eq.expression, locals=local_ns)
        except Exception as e:  # pragma: no cover - defensive
            raise ValueError(f"Failed to parse expression '{eq.expression}': {e}")
        exprs.append(expr)
    return exprs


# -------------------------------------------------------------------
# Equation system solving
# -------------------------------------------------------------------


def solve_equation_system(spec: ProblemSpec) -> MathSolution:
    """
    Solve a system of equations symbolically and, if needed, numerically.

    ProblemSpec requirements:
        - problem_type == "equation_system"
        - spec.equations populated
        - spec.variables populated
    """
    if spec.problem_type != "equation_system":
        raise ValueError("solve_equation_system requires problem_type='equation_system'.")

    if not spec.equations:
        raise ValueError("No equations provided in ProblemSpec.equations.")

    symbols = _build_sympy_symbols(spec.variables)
    exprs = _parse_expressions(spec.equations, symbols)

    # Convert to Eq( ... , 0 ) form: expression == 0
    equations = [Eq(expr, 0) for expr in exprs]

    # Attempt symbolic solve
    try:
        sol = sp.solve(equations, list(symbols.values()), dict=True)
    except Exception as e:
        sol = []
        sym_error = str(e)
    else:
        sym_error = None

    symbolic_solution: Optional[Dict[str, Any]] = None
    numeric_solution: Optional[Dict[str, float]] = None
    diagnostics: Dict[str, Any] = {
        "symbolic_error": sym_error,
        "num_solutions_found": len(sol),
    }

    if sol:
        # Take the first solution
        first = sol[0]
        symbolic_solution = {str(k): v for k, v in first.items()}

        # Evaluate numerically if possible
        numeric_solution = {}
        for name, sym in symbols.items():
            val = first.get(sym, None)
            if val is not None:
                try:
                    numeric_solution[name] = float(val.evalf())
                except Exception:
                    pass

        return MathSolution(
            success=True,
            method="sympy.solve",
            symbolic_solution=symbolic_solution,
            numeric_solution=numeric_solution,
            diagnostics=diagnostics,
        )

    # If purely symbolic solve failed, we could add numeric fallback here.
    return MathSolution(
        success=False,
        method="sympy.solve",
        symbolic_solution=None,
        numeric_solution=None,
        diagnostics=diagnostics,
    )


# -------------------------------------------------------------------
# Optimization problems
# -------------------------------------------------------------------

def _build_objective_func(
    opt: OptimizationSpec,
    symbols: Dict[str, sp.Symbol],
    var_order: List[str],
):
    """
    Turn an objective expression into a Python callable f(x_vec).
    """
    expr = sp.sympify(opt.objective, locals=symbols)
    lamb = sp.lambdify([symbols[name] for name in var_order], expr, "numpy")

    def f(x_vec):
        return float(lamb(*x_vec))

    return f


def solve_optimization(spec: ProblemSpec) -> MathSolution:
    """
    Solve a basic unconstrained or lightly-constrained optimization problem
    using SciPy's minimize.

    ProblemSpec requirements:
        - problem_type == "optimization"
        - spec.optimization not None
        - spec.variables populated with initial values
    """
    import numpy as np
    from scipy.optimize import minimize

    if spec.problem_type != "optimization":
        raise ValueError("solve_optimization requires problem_type='optimization'.")

    if spec.optimization is None:
        raise ValueError("ProblemSpec.optimization must be provided for optimization problems.")

    if not spec.variables:
        raise ValueError("At least one VariableSpec is required for optimization.")

    symbols = _build_sympy_symbols(spec.variables)
    var_order = [v.name for v in spec.variables]
    f = _build_objective_func(spec.optimization, symbols, var_order)

    # Build initial guess
    x0 = []
    bounds = []
    for v in spec.variables:
        init = 0.0 if v.initial is None else v.initial
        x0.append(init)
        lo = -np.inf if v.lower is None else v.lower
        hi = np.inf if v.upper is None else v.upper
        bounds.append((lo, hi))

    x0_arr = np.array(x0, dtype=float)

    res = minimize(f, x0_arr, bounds=bounds if any(b != (-np.inf, np.inf) for b in bounds) else None)

    numeric_solution: Optional[Dict[str, float]] = None
    if res.success:
        numeric_solution = {name: float(val) for name, val in zip(var_order, res.x)}

    diagnostics = {
        "success": bool(res.success),
        "status": int(res.status),
        "message": str(res.message),
        "fun": float(res.fun) if res.fun is not None else None,
        "nfev": int(res.nfev),
        "nit": int(res.nit),
    }

    return MathSolution(
        success=bool(res.success),
        method="scipy.optimize.minimize",
        symbolic_solution=None,
        numeric_solution=numeric_solution,
        diagnostics=diagnostics,
    )


# -------------------------------------------------------------------
# High-level entrypoint + LLM explanation
# -------------------------------------------------------------------


def solve_problem(
    spec: ProblemSpec,
    llm: Optional[LLMClient] = None,
    explain: bool = True,
) -> MathSolution:
    """
    Main entrypoint for Aurora's math lab.

    Dispatches based on spec.problem_type and optionally uses the LLM
    to generate a human-readable explanation of the result.
    """
    if spec.problem_type == "equation_system":
        solution = solve_equation_system(spec)
    elif spec.problem_type == "optimization":
        solution = solve_optimization(spec)
    else:
        raise NotImplementedError(
            f"Problem type '{spec.problem_type}' is not yet implemented in math_lab."
        )

    if explain and llm is not None:
        solution.explanation = _explain_solution_with_llm(spec, solution, llm)

    return solution


def _explain_solution_with_llm(
    spec: ProblemSpec,
    solution: MathSolution,
    llm: LLMClient,
) -> str:
    """
    Use the LLM to describe what was solved and interpret the result,
    like an AI research assistant.
    """
    status = "succeeded" if solution.success else "failed"

    prompt = f"""
You are Aurora, an AI math and modeling engine.

A math problem was just attempted with this description:
{spec.description}

Problem type: {spec.problem_type}
Variables: {[v.name for v in spec.variables]}

Symbolic solution (may be None):
{solution.symbolic_solution}

Numeric solution (may be None):
{solution.numeric_solution}

Diagnostics:
{solution.diagnostics}

The solver call {status} using method: {solution.method}.

Explain in clear, technical language:
  1) What kind of problem this is
  2) What the solution represents
  3) Any caveats or next steps (e.g. check residuals, try different initial values, etc.)
Keep it concise but insightful, as if explaining to a technically savvy researcher.
"""
    return llm.complete(prompt=prompt)

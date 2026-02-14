# fantasyai/aurora/experiment_lab.py

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Dict, Any, List, Tuple

import numpy as np

from .problem_spec import ProblemSpec, VariableSpec
from .ensemble import (
    solve_equation_ensemble,
    solve_optimization_ensemble,
)
from .research_memory import log_experiment
from ..core.llm_client import LLMClient
from .math_lab import MathSolution


def sweep_initial_values(
    spec: ProblemSpec,
    var_name: str,
    values: List[float],
    *,
    problem_kind: str = "equation_system",
    llm: LLMClient | None = None,
    tag_prefix: str = "sweep",
) -> List[MathSolution]:
    """
    Sweep over different initial values for a single variable and
    run the ensemble solver for each.
    """
    results: List[MathSolution] = []

    for i, val in enumerate(values):
        new_vars = []
        for v in spec.variables:
            if v.name == var_name:
                new_vars.append(replace(v, initial=val))
            else:
                new_vars.append(v)

        spec_i = replace(spec, variables=new_vars)
        if problem_kind == "equation_system":
            sol = solve_equation_ensemble(spec_i, llm=llm, explain=False)
        else:
            sol = solve_optimization_ensemble(spec_i, llm=llm, explain=False)

        log_experiment(spec_i, sol, tag=f"{tag_prefix}:{var_name}_{val}")
        results.append(sol)

    return results


def random_initial_search(
    spec: ProblemSpec,
    *,
    var_bounds: Dict[str, Tuple[float, float]],
    num_samples: int = 20,
    problem_kind: str = "optimization",
    llm: LLMClient | None = None,
    tag_prefix: str = "random_search",
) -> List[MathSolution]:
    """
    Run an ensemble solve for random initial guesses drawn from var_bounds.
    Useful for exploring complex landscapes.
    """
    results: List[MathSolution] = []

    for i in range(num_samples):
        new_vars = []
        for v in spec.variables:
            lo, hi = var_bounds.get(v.name, (-1.0, 1.0))
            init = float(np.random.uniform(lo, hi))
            new_vars.append(replace(v, initial=init))

        spec_i = replace(spec, variables=new_vars)

        if problem_kind == "equation_system":
            sol = solve_equation_ensemble(spec_i, llm=llm, explain=False)
        else:
            sol = solve_optimization_ensemble(spec_i, llm=llm, explain=False)

        log_experiment(spec_i, sol, tag=f"{tag_prefix}:{i+1}")
        results.append(sol)

    return results

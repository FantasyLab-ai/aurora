# scripts/test_aurora_math_lab.py

from __future__ import annotations

from fantasyai.aurora.problem_spec import (
    ProblemSpec,
    VariableSpec,
    EquationSpec,
    OptimizationSpec,
)
from fantasyai.aurora.math_lab import solve_problem
from fantasyai.core.llm_client import LLMClient


def test_equation_system():
    """
    Example: Solve a simple system:

        x + y = 3
        x - y = 1
    Expected solution: x = 2, y = 1
    """
    spec = ProblemSpec(
        problem_type="equation_system",
        description="Solve x + y = 3 and x - y = 1 for x and y.",
        variables=[
            VariableSpec(name="x"),
            VariableSpec(name="y"),
        ],
        equations=[
            EquationSpec(expression="x + y - 3"),
            EquationSpec(expression="x - y - 1"),
        ],
        meta={"domain": "toy_linear_algebra"},
    )

    llm = LLMClient()
    solution = solve_problem(spec, llm=llm, explain=True)

    print("\n=== Equation System Test ===")
    print("Success:", solution.success)
    print("Method:", solution.method)
    print("Symbolic:", solution.symbolic_solution)
    print("Numeric:", solution.numeric_solution)
    print("Diagnostics:", solution.diagnostics)
    print("\nExplanation:\n", solution.explanation)


def test_optimization():
    """
    Example: Minimize (x-2)^2 + (y+1)^2,
    which has minimum at x=2, y=-1
    """
    spec = ProblemSpec(
        problem_type="optimization",
        description="Find the minimum of (x-2)^2 + (y+1)^2.",
        variables=[
            VariableSpec(name="x", initial=0.0),
            VariableSpec(name="y", initial=0.0),
        ],
        optimization=OptimizationSpec(
            objective="(x - 2)**2 + (y + 1)**2",
            constraints=[],
        ),
        meta={"domain": "toy_optimization"},
    )

    llm = LLMClient()
    solution = solve_problem(spec, llm=llm, explain=True)

    print("\n=== Optimization Test ===")
    print("Success:", solution.success)
    print("Method:", solution.method)
    print("Numeric:", solution.numeric_solution)
    print("Diagnostics:", solution.diagnostics)
    print("\nExplanation:\n", solution.explanation)


def main():
    print("[AURORA MATH LAB] Running equation system test...")
    test_equation_system()

    print("\n[AURORA MATH LAB] Running optimization test...")
    test_optimization()


if __name__ == "__main__":
    main()

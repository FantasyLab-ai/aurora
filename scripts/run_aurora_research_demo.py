# scripts/run_aurora_research_demo.py

from __future__ import annotations

from fantasyai.aurora.problem_spec import (
    ProblemSpec,
    VariableSpec,
    EquationSpec,
    OptimizationSpec,
)
from fantasyai.aurora.research_agent import AuroraResearchAgent
from fantasyai.aurora.system_id import fit_polynomial, fit_logistic
from fantasyai.aurora.data_fusion import fuse_timeseries_dict

import numpy as np


def run_equation_demo(agent: AuroraResearchAgent) -> None:
    spec = ProblemSpec(
        problem_type="equation_system",
        description="Solve x + y = 3 and x - y = 1 for x and y.",
        variables=[
            VariableSpec(name="x", initial=0.0),
            VariableSpec(name="y", initial=0.0),
        ],
        equations=[
            EquationSpec(expression="x + y - 3"),
            EquationSpec(expression="x - y - 1"),
        ],
        meta={"domain": "toy_linear_algebra"},
    )

    sol = agent.solve_equations(spec, tag="demo_equation")
    print("\n=== Aurora Research Demo: Equation System ===")
    print("Success:", sol.success)
    print("Numeric solution:", sol.numeric_solution)
    print("Diagnostics:", sol.diagnostics)
    print("\nExplanation:\n", sol.explanation)


def run_optimization_demo(agent: AuroraResearchAgent) -> None:
    spec = ProblemSpec(
        problem_type="optimization",
        description="Find the minimum of (x-2)^2 + (y+1)^2.",
        variables=[
            VariableSpec(name="x", initial=0.0, lower=-10, upper=10),
            VariableSpec(name="y", initial=0.0, lower=-10, upper=10),
        ],
        optimization=OptimizationSpec(
            objective="(x - 2)**2 + (y + 1)**2",
            constraints=[],
        ),
        meta={"domain": "toy_optimization"},
    )

    sol = agent.solve_optimization(spec, tag="demo_optimization")
    print("\n=== Aurora Research Demo: Optimization ===")
    print("Success:", sol.success)
    print("Numeric solution:", sol.numeric_solution)
    print("Diagnostics:", sol.diagnostics)
    print("\nExplanation:\n", sol.explanation)


def run_system_id_demo() -> None:
    x = np.linspace(0, 10, 50)
    y_true = 5 / (1 + np.exp(-1.2 * (x - 4.0)))
    y_noisy = y_true + np.random.normal(scale=0.2, size=len(x))

    poly_fit = fit_polynomial(x, y_noisy, degree=3)
    log_fit = fit_logistic(x, y_noisy)

    print("\n=== Aurora System ID Demo ===")
    print("Polynomial fit success:", poly_fit.success, "params:", poly_fit.params)
    print("Logistic fit success:", log_fit.success, "params:", log_fit.params)


def run_data_fusion_demo() -> None:
    t = np.linspace(0, 10, 100)
    series = {
        "climate_temp": np.sin(t) + 0.1 * np.random.randn(len(t)),
        "market_index": 0.5 * np.sin(t + 0.5) + 0.1 * np.random.randn(len(t)),
        "energy_load": 0.8 * np.sin(t - 0.3) + 0.1 * np.random.randn(len(t)),
    }

    fusion = fuse_timeseries_dict(series, n_components=2)
    print("\n=== Aurora Data Fusion Demo ===")
    print("Correlation labels:", fusion.correlation_labels)
    print("Correlation matrix:\n", fusion.correlation_matrix)
    print("PCA explained variance:", fusion.pca_explained_variance)
    print("Diagnostics:", fusion.diagnostics)


def main():
    agent = AuroraResearchAgent()

    run_equation_demo(agent)
    run_optimization_demo(agent)
    run_system_id_demo()
    run_data_fusion_demo()

    print("\n=== Aurora Research Memory Summary ===")
    summary = agent.summarize_past_experiments(max_items=5)
    print(summary)


if __name__ == "__main__":
    main()

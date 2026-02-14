from __future__ import annotations

import numpy as np

from fantasyai.aurora.robust_optimization import (
    UncertainParameter,
    robust_optimize,
)


def _deterministic_objective(x: np.ndarray, params: dict) -> float:
    """
    Simple quadratic bowl whose minimum moves with uncertain parameters.

    Think of x = [production_level, staffing_level]
    and params = {"target_prod": ..., "target_staff": ...}.
    """
    target_prod = params["target_prod"]
    target_staff = params["target_staff"]
    return float((x[0] - target_prod) ** 2 + (x[1] - target_staff) ** 2)


def main() -> None:
    print("=" * 80)
    print("[AURORA MATH] Robust optimization under parameter uncertainty")
    print("=" * 80)

    # Uncertain parameters: where the "ideal" decision should live.
    param_dists = {
        "target_prod": UncertainParameter(mean=2.0, std=0.7),
        "target_staff": UncertainParameter(mean=-1.0, std=0.5),
    }

    # Baseline (deterministic) optimum at the means
    x_mean = np.array([param_dists["target_prod"].mean,
                       param_dists["target_staff"].mean])

    # Run robust optimization with CVaR-style risk
    x0 = np.array([0.0, 0.0])
    bounds = [(-5.0, 5.0), (-5.0, 5.0)]

    res = robust_optimize(
        x0=x0,
        bounds=bounds,
        base_objective=_deterministic_objective,
        param_dists=param_dists,
        n_samples=128,
        risk="cvar",
        alpha=0.9,
    )

    print("\nDeterministic mean-target optimum (for comparison):")
    print(f"  x_mean = {x_mean}")

    print("\nRobust optimum (CVaR, alpha=0.9) over uncertain targets:")
    print(f"  success = {res.success}")
    print(f"  message = {res.message}")
    print(f"  x_opt   = {res.x_opt}")
    print(f"  robust objective value = {res.fun:.6f}")

    print("\nInterpretation:")
    print("  - x_mean is what you would choose if you believed the targets "
          "were known exactly.")
    print("  - x_opt is Aurora's choice when it cares about the worst 10% "
          "of parameter realizations (CVaR).")
    print("  - This pattern generalizes to fleet, logistics, or clinical "
          "resource allocation problems.\n")

    print("[AURORA MATH] Robust optimization demo completed.")
    print("=" * 80)


if __name__ == "__main__":
    main()

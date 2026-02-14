# scripts/run_aurora_math_solvers_demo.py

def _aurora_maybe_add(run, name, obj):
    try:
        run.add(name, obj)
    except Exception:
        pass
"""
Aurora Math Solvers Demo

Runs a set of MATLAB-style math tests:
  1) Linear system solve with diagnostics
  2) Eigen decomposition of a symmetric matrix
  3) Truncated SVD on a synthetic data matrix
  4) Nonlinear root-finding (Rosenbrock gradient ~ 0)
  5) ODE solve: harmonic oscillator
  6) Global optimization (Rosenbrock) with multi-start
  7) Simple Bayesian update
  8) Feature importance on synthetic data

This script does NOT touch clinical/logistics data; it just validates
the math core in isolation.
"""

import numpy as np
import pandas as pd

from fantasyai.aurora.math_solvers import (
    solve_linear_system,
    eigen_decomposition,
    truncated_svd,
    solve_nonlinear_system,
    solve_ode_system,
    harmonic_oscillator,
)
from fantasyai.aurora.global_opt import multi_start_optimize
from fantasyai.aurora.bayes import normal_update
from fantasyai.aurora.data_tools import simple_impute, feature_importance
from fantasyai.aurora.artifacts import AuroraRun


def demo_linear_system():
    print("=" * 80)
    print("[AURORA MATH] Linear system demo")
    print("=" * 80)

    rng = np.random.default_rng(42)
    A = rng.normal(size=(4, 4))
    # Make A better behaved (symmetric positive definite-ish)
    A = A.T @ A + 0.1 * np.eye(4)
    b = rng.normal(size=4)

    res = solve_linear_system(A, b)
    print("Solution x:", res.x)
    print("Residual norm ||Ax - b||:", res.residual_norm)
    print("Condition number cond(A):", res.cond_A)
    print()


def demo_eigen_svd():
    print("=" * 80)
    print("[AURORA MATH] Eigen + SVD demo")
    print("=" * 80)

    rng = np.random.default_rng(0)
    A = rng.normal(size=(5, 5))
    A = 0.5 * (A + A.T)  # symmetric

    eig_res = eigen_decomposition(A, symmetric=True)
    print("Eigenvalues:", eig_res.eigenvalues)

    B = rng.normal(size=(10, 3))
    svd_res = truncated_svd(B, k=2)
    print("SVD singular values (top-2):", svd_res.S)
    print()


def demo_nonlinear_root():
    print("=" * 80)
    print("[AURORA MATH] Nonlinear root demo (Rosenbrock gradient)")
    print("=" * 80)

    def rosenbrock_grad(x):
        # 2D Rosenbrock gradient
        x1, x2 = x
        dfdx1 = -2 * (1 - x1) - 400 * x1 * (x2 - x1**2)
        dfdx2 = 200 * (x2 - x1**2)
        return np.array([dfdx1, dfdx2], dtype=float)

    x0 = np.array([-1.2, 1.0])
    res = solve_nonlinear_system(rosenbrock_grad, x0)
    print("Root success:", res.success)
    print("Message:", res.message)
    print("Approximate minimizer x*:", res.x)
    print()


def demo_ode():
    print("=" * 80)
    print("[AURORA MATH] ODE demo (harmonic oscillator)")
    print("=" * 80)

    f = harmonic_oscillator(k=1.0, m=1.0)
    t_span = (0.0, 10.0)
    t_eval = np.linspace(0.0, 10.0, 200)
    y0 = np.array([1.0, 0.0])  # x=1, v=0

    ode_res = solve_ode_system(f, t_span, y0, t_eval=t_eval)
    print("ODE success:", ode_res.success)
    print("Message:", ode_res.message)
    print("Final state y(T):", ode_res.y[:, -1])
    print("First 5 time points:", ode_res.t[:5])
    print("First 5 positions:", ode_res.y[0, :5])
    print()


def demo_global_opt():
    print("=" * 80)
    print("[AURORA MATH] Global optimization demo (Rosenbrock)")
    print("=" * 80)

    def rosenbrock(x):
        x1, x2 = x
        return (1 - x1) ** 2 + 100 * (x2 - x1**2) ** 2

    x0_list = [
        np.array([-1.5, 1.5]),
        np.array([2.0, 2.0]),
        np.array([-0.5, -0.5]),
        np.array([0.0, 0.0]),
    ]
    bounds = [(-3.0, 3.0), (-3.0, 3.0)]

    res = multi_start_optimize(rosenbrock, x0_list, bounds)
    print("Best objective value:", res["best_value"])
    print("Best x*:", res["best_x"])
    print()


def demo_bayes():
    print("=" * 80)
    print("[AURORA MATH] Bayesian update demo")
    print("=" * 80)
    prior_mean, prior_std = 0.0, 1.0
    observed, obs_std = 1.5, 0.5
    post = normal_update(prior_mean, prior_std, observed, obs_std)
    print("Prior mean/std:", prior_mean, prior_std)
    print("Observed value/std:", observed, obs_std)
    print("Posterior mean/std:", post.mean, post.std)
    print()


def demo_feature_importance():
    print("=" * 80)
    print("[AURORA MATH] Feature importance demo")
    print("=" * 80)

    rng = np.random.default_rng(123)
    n = 200
    X1 = rng.normal(size=n)
    X2 = rng.normal(size=n)
    X3 = rng.normal(size=n)
    # target with strong dependence on X1, X2, weak on X3
    y = 3 * X1 - 2 * X2 + 0.1 * X3 + rng.normal(scale=0.5, size=n)

    df = pd.DataFrame({"X1": X1, "X2": X2, "X3": X3, "y": y})
    df.loc[rng.choice(n, size=10, replace=False), "X2"] = np.nan

    df_clean = simple_impute(df, ["X1", "X2", "X3"])
    importances = feature_importance(df_clean, "y", ["X1", "X2", "X3"])
    print("Feature importances (approx MI):")
    for name, score in importances:
        print(f"  - {name}: {score:.4f}")
    print()


def main():
    run = AuroraRun(domain="aurora_math_solvers_demo", tags={"script":"aurora_math_solvers_demo"})

    demo_linear_system()
    demo_eigen_svd()
    demo_nonlinear_root()
    demo_ode()
    demo_global_opt()
    demo_bayes()
    demo_feature_importance()
    print("=" * 80)
    print("[AURORA MATH] Demo completed successfully.")
    print("=" * 80)


if __name__ == "__main__":
    try:
        paths = run.finalize()
        print(f"[AURORA ARTIFACTS] wrote -> {paths['run_dir']}")
    except Exception as e:
        print(f"[AURORA ARTIFACTS] finalize failed: {e}")
    main()
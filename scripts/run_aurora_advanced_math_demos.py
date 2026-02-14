"""
Run a suite of advanced math demos for Aurora.

Usage (from project root with venv active):

    python -m scripts.run_aurora_advanced_math_demos
"""

from __future__ import annotations

import numpy as np

from fantasyai.aurora.advanced_math_demos import (
    lp_cost_minimization_demo,
    heat_equation_demo,
    monte_carlo_portfolio_risk_demo,
)


def main() -> None:
    # ----------------------------------------------------------------------
    # 1) Linear programming (LP) demo
    # ----------------------------------------------------------------------
    print("=" * 80)
    print("[AURORA ADVANCED MATH] Linear programming (LP) demo")
    print("=" * 80)

    lp_res = lp_cost_minimization_demo()
    print(f"Success: {lp_res.success}")
    print(f"Min cost: {lp_res.cost:.4f}")
    print("Solution vector x:")
    print(lp_res.x)
    print("Diagnostics:", lp_res.diagnostics)
    print()

    # ----------------------------------------------------------------------
    # 2) 1D heat equation demo
    # ----------------------------------------------------------------------
    print("=" * 80)
    print("[AURORA ADVANCED MATH] 1D heat equation demo")
    print("=" * 80)

    heat_res = heat_equation_demo()
    print(f"nx={heat_res.nx}, nt={heat_res.nt}, dx={heat_res.dx:.4f}, dt={heat_res.dt:.4f}, alpha={heat_res.alpha}")
    print("Initial center temperature:", float(heat_res.u_initial[heat_res.nx // 2]))
    print("Final center temperature:  ", float(heat_res.u_final[heat_res.nx // 2]))
    print("First 5 initial temperatures:", np.round(heat_res.u_initial[:5], 4))
    print("First 5 final temperatures:  ", np.round(heat_res.u_final[:5], 4))
    print()

    # ----------------------------------------------------------------------
    # 3) Monte Carlo portfolio VaR / CVaR demo
    # ----------------------------------------------------------------------
    print("=" * 80)
    print("[AURORA ADVANCED MATH] Monte Carlo portfolio risk demo")
    print("=" * 80)

    mc_res = monte_carlo_portfolio_risk_demo()
    print(f"Num paths: {mc_res.num_paths}, horizon_days: {mc_res.horizon_days}")
    print(f"Daily mean return: {mc_res.mean_daily_return:.6f}, std: {mc_res.std_daily_return:.6f}")
    print(f"VaR 95% (5th percentile final return): {mc_res.var_95:.4f}")
    print(f"CVaR 95% (mean of worst 5%):          {mc_res.cvar_95:.4f}")
    print()

    print("=" * 80)
    print("[AURORA ADVANCED MATH] Demo completed successfully.")
    print("=" * 80)


if __name__ == "__main__":
    main()

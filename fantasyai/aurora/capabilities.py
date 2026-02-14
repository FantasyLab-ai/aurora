# fantasyai/aurora/capabilities.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    one_liner: str
    details: List[str]
    tags: List[str]


def aurora_math_capabilities() -> List[Capability]:
    """
    World-class (MATLAB-level) capability map for Aurora.

    NOTE: This is a 'registry' for UI + docs + API metadata.
    It does not dictate implementations, it documents what Aurora can do.
    """
    return [
        Capability(
            id="time_series_forecasting",
            title="Forecasting",
            one_liner="Multi-step forecasts with uncertainty bands and diagnostics.",
            details=[
                "ARIMA-class multi-horizon forecasting with confidence intervals.",
                "Error metrics: RMSE / MAE / MAPE for validation and drift checks.",
                "Pluggable model selection: fixed order or auto selection."
            ],
            tags=["forecast", "stats", "timeseries"],
        ),
        Capability(
            id="regime_detection",
            title="Regime detection",
            one_liner="Detect latent modes (seasonality shifts, structural breaks, demand step-changes).",
            details=[
                "Unsupervised regime clustering with explained-variance reporting.",
                "Regime summaries (means per feature) to interpret behaviors.",
                "Designed for operational data (cost, volume, latency, yield)."
            ],
            tags=["clustering", "unsupervised", "systems"],
        ),
        Capability(
            id="multi_metric_anomaly_scoring",
            title="Multi-metric anomaly scoring",
            one_liner="Percentile-based anomaly scoring that avoids alert spam.",
            details=[
                "Weighted z-score / robust normalization per metric.",
                "Composite anomaly score with quantile thresholds (p90/p95/etc).",
                "Outputs top anomalies + quantile distribution for calibration."
            ],
            tags=["anomaly", "robust", "monitoring"],
        ),
        Capability(
            id="optimization",
            title="Optimization",
            one_liner="Solve constrained decisions: local, global, and linear programs.",
            details=[
                "Bounded optimization for tradeoffs (fuel vs time, dose vs toxicity).",
                "Global optimization (non-convex) when local solvers can fail.",
                "Linear programming (HiGHS) for supply chain / allocation problems."
            ],
            tags=["opt", "lp", "decision"],
        ),
        Capability(
            id="simulation_pde_ode",
            title="Simulation",
            one_liner="ODE + PDE simulations to test system behavior under scenarios.",
            details=[
                "ODE integration for dynamical systems (stability, oscillations).",
                "PDE heat-equation style diffusion as a canonical physics demo.",
                "Scenario sweeps for sensitivity + robustness checks."
            ],
            tags=["ode", "pde", "simulation"],
        ),
        Capability(
            id="linear_algebra_core",
            title="Linear algebra core",
            one_liner="Solve linear systems, eigen problems, and SVD like a numerical lab.",
            details=[
                "Linear solves with residual + condition number reporting.",
                "Eigen/SVD diagnostics for stability + dimensionality reduction.",
                "Numerical stability surfaced explicitly (trust-by-design)."
            ],
            tags=["linalg", "svd", "eig"],
        ),
        Capability(
            id="risk_uncertainty",
            title="Risk & uncertainty",
            one_liner="Monte Carlo risk, VaR/CVaR, and Bayesian updates.",
            details=[
                "Monte Carlo path simulation for portfolio/system risk.",
                "VaR/CVaR summaries for tail-risk monitoring.",
                "Bayesian updates for principled belief revision."
            ],
            tags=["risk", "montecarlo", "bayes"],
        ),
        Capability(
            id="causal_what_if",
            title="Causal what-if",
            one_liner="Counterfactual deltas with transparent assumptions.",
            details=[
                "Linear causal proxy for ‘what if X changed by Δ?’",
                "Returns coefficients + fit diagnostics (R², means) for honesty.",
                "Explicitly framed as association unless true causal identification is available."
            ],
            tags=["causal", "counterfactual", "explainability"],
        ),
        Capability(
            id="clinical_risk_survival",
            title="Clinical risk & survival",
            one_liner="Multi-criteria risk scoring + survival modeling scaffolds.",
            details=[
                "Multi-criteria risk scoring with explicit weights + quantiles.",
                "Pareto-front surfacing for multi-objective triage.",
                "Survival modeling demos (KM / Cox) with stability safeguards."
            ],
            tags=["health", "survival", "multiobjective"],
        ),
    ]


def capabilities_json() -> Dict[str, Any]:
    caps = aurora_math_capabilities()
    return {
        "count": len(caps),
        "capabilities": [
            {
                "id": c.id,
                "title": c.title,
                "one_liner": c.one_liner,
                "details": c.details,
                "tags": c.tags,
            }
            for c in caps
        ],
    }

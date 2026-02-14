# scripts/run_aurora_domain_demos.py

from __future__ import annotations

import numpy as np

from fantasyai.aurora.research_agent import AuroraResearchAgent
from fantasyai.domains.climate_solver import analyze_climate_timeseries, differential_climate_model_example
from fantasyai.domains.oncology_solver import analyze_tumor_growth_curve, optimal_dose_toy_model
from fantasyai.domains.logistics_solver import fit_route_cost_model, optimize_route_speed_toy


def main():
    agent = AuroraResearchAgent()

    print("\n=== Climate demo ===")
    t = np.linspace(0, 10, 50)
    temps = 0.5 * np.sin(t) + 0.02 * t + np.random.normal(scale=0.1, size=len(t))
    climate_res = analyze_climate_timeseries(temps, t=t)
    print(climate_res)

    climate_eq = differential_climate_model_example(agent)
    print("\nToy equilibrium:", climate_eq)

    print("\n=== Oncology demo ===")
    times = np.linspace(0, 20, 40)
    vols = 100 / (1 + np.exp(-0.4 * (times - 8))) + np.random.normal(scale=3.0, size=len(times))
    onc_res = analyze_tumor_growth_curve(times, vols)
    print(onc_res)

    dose_res = optimal_dose_toy_model(agent)
    print("\nDose optimization:", dose_res)

    print("\n=== Logistics demo ===")
    distances = np.linspace(10, 200, 30)
    costs = 1.2 * distances + 0.01 * distances**2 + np.random.normal(scale=10.0, size=len(distances))
    log_res = fit_route_cost_model(distances, costs, degree=2)
    print(log_res)

    speed_res = optimize_route_speed_toy(agent)
    print("\nSpeed optimization:", speed_res)


if __name__ == "__main__":
    main()

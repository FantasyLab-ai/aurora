# scripts/run_aurora_policy_demo.py

"""
Aurora Policy Demo (synthetic logistics scenario)

We simulate shipments with:
  - distance, weight, traffic index
  - binary treatment `expedite_flag` (rush vs normal)
  - outcome `delay_minutes`

Aurora then:
  - estimates ATE via IPW
  - proposes a simple uplift-based policy: who should be expedited?
"""

import numpy as np
import pandas as pd

from fantasyai.aurora.causal_policy import (
    PolicyConfig,
    estimate_ate_ipw,
    neighbor_uplift_policy,
)


def _simulate_logistics_data(n: int = 1000) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    distance = rng.uniform(5, 1000, size=n)
    weight = rng.uniform(1, 200, size=n)
    traffic_index = rng.uniform(0, 1, size=n)

    # Base delay: longer distance + more traffic => more delay
    base_delay = 0.05 * np.sqrt(distance) + 60 * traffic_index

    # Treatment probability: you tend to expedite high distance & high traffic
    logits = 0.001 * distance + 2.0 * traffic_index - 1.0
    p_treat = 1 / (1 + np.exp(-logits))
    expedite_flag = (rng.uniform(0, 1, size=n) < p_treat).astype(int)

    # Treatment effect: expedites reduce delay by ~30 minutes on average,
    # especially for highly congested routes.
    treatment_effect = -15.0 - 30.0 * traffic_index
    delay_minutes = base_delay + rng.normal(0, 10, size=n) + expedite_flag * treatment_effect

    df = pd.DataFrame(
        {
            "distance_km": distance,
            "weight_kg": weight,
            "traffic_index": traffic_index,
            "expedite_flag": expedite_flag,
            "delay_minutes": delay_minutes,
        }
    )
    return df


def main() -> None:
    print("=" * 80)
    print("[AURORA Policy Demo] Synthetic logistics what-if analysis")
    print("=" * 80)

    df = _simulate_logistics_data()

    cfg = PolicyConfig(
        outcome_col="delay_minutes",
        treatment_col="expedite_flag",
        feature_cols=["distance_km", "weight_kg", "traffic_index"],
    )

    ate_res = estimate_ate_ipw(df, cfg)
    print("\n[IPW] Average treatment effect (ATE) estimate:")
    for k, v in ate_res.items():
        print(f"  {k}: {v}")

    policy_res = neighbor_uplift_policy(df, cfg, top_fraction=0.2)
    print("\n[Policy] Uplift-based expedite recommendation:")
    for k, v in policy_res.items():
        if k == "recommended_indices":
            print(f"  {k}: (list of {len(v)} indices, omitted)")
        else:
            print(f"  {k}: {v}")

    print("\n[AURORA] Policy demo complete.")


if __name__ == "__main__":
    main()

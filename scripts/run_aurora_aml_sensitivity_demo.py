from __future__ import annotations

import os
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from fantasyai.aurora.scenario_engine import run_weight_sensitivity


DATA_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "fantasyai", "data"
)
AML_CLEAN_PATH = os.path.join(DATA_ROOT, "aml_clinical_clean.csv")


def _load_aml() -> pd.DataFrame:
    if not os.path.exists(AML_CLEAN_PATH):
        raise FileNotFoundError(
            f"Expected AML clinical CSV at {AML_CLEAN_PATH}. "
            "Run the AML cleaning script first."
        )
    return pd.read_csv(AML_CLEAN_PATH)


def _compute_risk_scores(
    weights: Dict[str, float]
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """
    Lightweight re-implementation of the AML composite risk score.

    We z-score numeric features + add a Short Survival Risk flag, then
    take a weighted sum as the Aurora risk score.
    """
    df = _load_aml()

    base_features = [
        "Diagnosis Age",
        "Mutation Count",
        "Fraction Genome Altered",
        "TMB (nonsynonymous)",
    ]
    surv_col = "Overall Survival (Months)"

    # basic numeric subset
    sub = df[base_features].copy()
    sub = sub.apply(pd.to_numeric, errors="coerce")
    sub = sub.fillna(sub.mean())

    # z-score
    z = (sub - sub.mean()) / sub.std(ddof=0)

    # add short survival flag (e.g. < 12 months)
    surv = pd.to_numeric(df[surv_col], errors="coerce")
    short_flag = (surv < 12.0).fillna(False).astype(float)
    z["Short Survival Risk"] = short_flag

    # weighted sum
    risk = np.zeros(len(z), dtype=float)
    for name, w in weights.items():
        if name not in z.columns:
            continue
        risk += w * z[name].to_numpy()

    out = df.copy()
    out["aurora_risk_score"] = risk

    q50, q75, q90, q95 = np.quantile(risk, [0.5, 0.75, 0.9, 0.95])
    metrics = {
        "q50": float(q50),
        "q75": float(q75),
        "q90": float(q90),
        "q95": float(q95),
    }
    return out, metrics


def _evaluate_for_sensitivity(weights: Dict[str, float]) -> float:
    """
    Sensitivity target: we use the 95th percentile of the Aurora risk score
    as a scalar "tail-risk" metric.
    """
    _, metrics = _compute_risk_scores(weights)
    return metrics["q95"]


def main() -> None:
    df = _load_aml()
    print("=" * 80)
    print("[AURORA x AML Clinical] Sensitivity demo")
    print("=" * 80)
    print(f"Loaded AML cohort with shape {df.shape} from: {AML_CLEAN_PATH}\n")

    # Baseline weights (matching your multi-criteria risk intuition)
    base_weights: Dict[str, float] = {
        "Diagnosis Age": 0.07692307692307693,
        "Fraction Genome Altered": 0.23076923076923078,
        "Mutation Count": 0.23076923076923078,
        "TMB (nonsynonymous)": 0.15384615384615385,
        "Short Survival Risk": 0.3076923076923077,
    }

    base_val = _evaluate_for_sensitivity(base_weights)
    print(f"Baseline tail risk metric (95th percentile Aurora score): {base_val:.4f}\n")

    # Run generic sensitivity engine
    result = run_weight_sensitivity(base_weights, _evaluate_for_sensitivity)

    print("Per-feature sensitivity (delta in 95th percentile score vs baseline):\n")
    rows = []
    for name, deltas in result.deltas.items():
        for dp, dv in deltas.items():
            rows.append((name, dp, dv))
    # sort by absolute impact
    rows.sort(key=lambda r: abs(r[2]), reverse=True)

    for name, dp, dv in rows:
        sign = "+" if dv >= 0 else "-"
        print(
            f"  - {name:24s} | perturb {dp:+.0%} -> delta {sign}{abs(dv):.4f} "
            f"(new q95 = {base_val + dv:.4f})"
        )

    print("\nMost impactful features (by absolute change in tail risk) are "
          "at the top of this list.\n")
    print("[AURORA x AML Clinical] Sensitivity demo completed.")
    print("=" * 80)


if __name__ == "__main__":
    main()

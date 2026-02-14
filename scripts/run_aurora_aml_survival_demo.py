# scripts/run_aurora_aml_survival_demo.py

"""
Aurora x AML Survival Demo

Uses the cleaned AML clinical dataset (aml_clinical_clean.csv) and:
  - Encodes an event indicator from "Overall Survival Status"
  - Builds simple Kaplan–Meier summaries by age group
  - Tries to fit a Cox model with genomic + clinical covariates
"""

import os
import pandas as pd

from fantasyai.aurora.survival_models import (
    SurvivalConfig,
    prepare_survival_dataframe,
    km_summary_by_group,
    fit_cox_model,
)

DATA_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "fantasyai",
    "data",
)

AML_CLEAN_PATH = os.path.join(DATA_ROOT, "aml_clinical_clean.csv")


def _load_aml_survival_frame() -> pd.DataFrame:
    if not os.path.exists(AML_CLEAN_PATH):
        raise FileNotFoundError(
            f"AML clinical file not found at {AML_CLEAN_PATH}. "
            "Run the AML cleaning step first."
        )

    df = pd.read_csv(AML_CLEAN_PATH)

    # Build an event indicator from 'Overall Survival Status' if present.
    # Typical TCGA coding: "DECEASED" vs "LIVING".
    if "Overall Survival Status" in df.columns:
        status = df["Overall Survival Status"].astype(str).str.upper()
        df["event"] = (status == "DECEASED").astype(int)
    elif "Patient's Vital Status" in df.columns:
        status = df["Patient's Vital Status"].astype(str).str.upper()
        df["event"] = (status == "DECEASED").astype(int)
    else:
        # Fallback: treat any non-zero survival as censored, zero as event.
        df["event"] = (df["Overall Survival (Months)"] <= 0.0).astype(int)

    return df


def _age_group(age: float) -> str:
    if age < 40:
        return "<40"
    if age < 60:
        return "40–60"
    return "60+"


def main() -> None:
    print("=" * 80)
    print("[AURORA x AML Clinical] Survival modeling demo")
    print("=" * 80)

    df = _load_aml_survival_frame()

    # Build age groups for a basic stratified KM view
    df["age_group"] = df["Diagnosis Age"].apply(_age_group)

    cfg = SurvivalConfig(
        time_col="Overall Survival (Months)",
        event_col="event",
        feature_cols=[
            "Diagnosis Age",
            "Fraction Genome Altered",
            "Mutation Count",
            "TMB (nonsynonymous)",
        ],
        group_col="age_group",
    )

    surv_df = prepare_survival_dataframe(df, cfg)

    print("\n[KM] Kaplan–Meier summary by age group:")
    km_res = km_summary_by_group(surv_df, cfg)
    for g in km_res["groups"]:
        print(
            f"  - group={g['label']}, "
            f"n={g['num_patients']}, "
            f"events={g['num_events']}, "
            f"median_survival={g['median_survival']:.2f} months"
        )

    print("\n[COX] Cox proportional hazards model:")
    cox_res = fit_cox_model(surv_df, cfg)

    if cox_res.get("error"):
        print(f"  (skipped) {cox_res['error']}")
        print("\n[AURORA] Survival demo complete (KM only; Cox not estimable).")
        return

    print(f"  Concordance index: {cox_res['concordance_index']:.3f}")
    print("  Hazard ratios (exp(coef)):")
    for feat, hr in cox_res["hazard_ratios"].items():
        print(f"    - {feat}: {hr:.3f}")

    print("\n[COX] Full coefficient table (truncated):")
    for feat, row in cox_res["coef_table"].items():
        print(
            f"  {feat}: coef={row['coef']:.3f}, "
            f"p={row['p']:.3g}, "
            f"se={row['se(coef)']:.3f}"
        )

    print("\n[AURORA] Survival demo complete.")


if __name__ == "__main__":
    main()

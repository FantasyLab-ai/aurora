import os
from pprint import pprint
from typing import Dict, Any, List

import numpy as np
import pandas as pd

from fantasyai.config import DATA_DIR
from fantasyai.aurora.research_memory import log_experiment


def _print_section(title: str) -> None:
    print("=" * 80)
    print(title)
    print("=" * 80)


def _load_aml_clinical(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"AML clinical file not found at: {path}")

    df = pd.read_csv(path)
    return df


def _build_risk_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build and normalize the numeric features used for the multi-criteria risk score.
    """
    numeric_cols = [
        "Diagnosis Age",
        "Mutation Count",
        "Fraction Genome Altered",
        "TMB (nonsynonymous)",
        "Overall Survival (Months)",
        "Year of Diagnosis",
    ]
    # Filter only columns that actually exist
    numeric_cols = [c for c in numeric_cols if c in df.columns]

    num_df = df[numeric_cols].copy()

    # Simple impute: fill missing with median per column
    for c in num_df.columns:
        median_val = num_df[c].median()
        num_df[c] = num_df[c].fillna(median_val)

    # Derived feature: short survival risk (e.g. < 12 months)
    if "Overall Survival (Months)" in num_df.columns:
        short_risk = (num_df["Overall Survival (Months)"] < 12.0).astype(float)
    else:
        short_risk = pd.Series(0.0, index=num_df.index)

    num_df["Short Survival Risk"] = short_risk

    # Z-score normalize each column
    z_df = pd.DataFrame(index=num_df.index)
    for c in num_df.columns:
        mu = num_df[c].mean()
        sigma = num_df[c].std()
        if sigma == 0 or np.isnan(sigma):
            z_df[c] = 0.0
        else:
            z_df[c] = (num_df[c] - mu) / sigma

    return num_df, z_df


def _build_risk_score(z_df: pd.DataFrame) -> (pd.Series, Dict[str, float]):
    """
    Build a weighted risk score from normalized features.
    You can tweak these weights later — for now we prioritize:
      - Fraction Genome Altered
      - Mutation Count
      - Short Survival Risk
      - TMB
    """
    weights = {
        "Diagnosis Age": 1.0,
        "Fraction Genome Altered": 3.0,
        "Mutation Count": 3.0,
        "TMB (nonsynonymous)": 2.0,
        "Short Survival Risk": 4.0,
        # Overall Survival is not included directly to avoid circularity
        "Overall Survival (Months)": 0.0,
        "Year of Diagnosis": 0.0,
    }

    # Keep only features present in z_df
    w_norm: Dict[str, float] = {}
    for k, v in weights.items():
        if k in z_df.columns and v != 0.0:
            w_norm[k] = float(v)

    # Normalize weights to sum to 1
    total = sum(w_norm.values()) or 1.0
    for k in list(w_norm.keys()):
        w_norm[k] = w_norm[k] / total

    score = pd.Series(0.0, index=z_df.index)
    for feat, w in w_norm.items():
        score += w * z_df[feat]

    return score, w_norm


def _compute_score_quantiles(score: pd.Series) -> Dict[str, float]:
    return {
        "max": float(score.max()),
        "q50": float(score.quantile(0.50)),
        "q75": float(score.quantile(0.75)),
        "q90": float(score.quantile(0.90)),
        "q95": float(score.quantile(0.95)),
    }


def _compute_anomaly_flags(score: pd.Series, q: float = 0.95) -> Dict[str, Any]:
    threshold = float(score.quantile(q))
    flags = score >= threshold
    num_flagged = int(flags.sum())
    frac = float(num_flagged) / float(len(score)) if len(score) > 0 else 0.0
    return {
        "threshold_q": q,
        "threshold_score": threshold,
        "num_flagged": num_flagged,
        "fraction_flagged": frac,
    }


def _pareto_front_neg_risk_vs_survival(
    scores: pd.Series, survival: pd.Series
) -> List[Dict[str, Any]]:
    """
    Compute a simple Pareto front in terms of:
      - objective 1: higher risk (we use negative risk as "to minimize")
      - objective 2: shorter survival (survival_months)
    We want points that are NOT dominated by any other: i.e. no other patient
    has BOTH higher risk and shorter survival.
    """
    front: List[Dict[str, Any]] = []

    for idx in scores.index:
        risk_val = scores.loc[idx]
        surv_val = survival.loc[idx]

        # use negative risk to interpret as 'loss' for Pareto logic
        obj1 = -risk_val
        obj2 = surv_val if not np.isnan(surv_val) else float("inf")

        dominated = False
        for existing in front:
            if (
                existing["obj1"] <= obj1
                and existing["obj2"] <= obj2
                and (existing["obj1"] < obj1 or existing["obj2"] < obj2)
            ):
                dominated = True
                break
        if dominated:
            continue

        # Remove any existing points that this one dominates
        new_front = []
        for existing in front:
            if not (
                obj1 <= existing["obj1"]
                and obj2 <= existing["obj2"]
                and (obj1 < existing["obj1"] or obj2 < existing["obj2"])
            ):
                new_front.append(existing)
        front = new_front

        front.append(
            {
                "index": int(idx),
                "obj1": float(obj1),
                "obj2": float(obj2),
            }
        )

    return front


def _generate_aml_risk_narrative(summary: Dict[str, Any]) -> str:
    """
    Turn the AML risk summary into a short, human-readable research note.
    This is a deterministic "LLM-style" narrative, no external model required.
    """
    meta = summary.get("meta", {})
    weights = summary.get("weights_used", {})
    quantiles = summary.get("score_quantiles", {})
    anomalies = summary.get("anomalies", {})
    features = summary.get("features_used", [])

    num_patients = meta.get("num_patients", "unknown")
    frac_flagged = anomalies.get("fraction_flagged", 0.0)
    num_flagged = anomalies.get("num_flagged", 0)

    # Build a sorted list of feature weights
    feat_weights = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)

    lines: List[str] = []
    lines.append("Aurora AML Clinical Risk – Research Note")
    lines.append("")
    lines.append(f"- Cohort size: {num_patients} patients")
    lines.append(
        f"- Aurora composite risk score: median={quantiles.get('q50', 0.0):.3f}, "
        f"90th percentile={quantiles.get('q90', 0.0):.3f}, "
        f"95th percentile={quantiles.get('q95', 0.0):.3f}"
    )
    lines.append(
        f"- High-risk tail: ~{frac_flagged*100:.1f}% of patients "
        f"({num_flagged} cases) flagged above the 95th percentile checkpoint."
    )
    lines.append("")
    lines.append("Key drivers in the current scoring configuration:")
    for feat, w in feat_weights:
        lines.append(f"  • {feat}: weight={w:.3f}")

    lines.append("")
    lines.append(
        "Interpretation:\n"
        "  Aurora is treating genomic alteration burden (Fraction Genome Altered),\n"
        "  mutation load, and short-survival indicators as the dominant risk drivers.\n"
        "  Age and TMB contribute, but with lighter weights in this configuration.\n"
    )
    lines.append(
        "  The flagged high-risk cohort represents the most extreme combinations of\n"
        "  genomic disruption, mutation burden, and short projected survival. In a\n"
        "  real-world setting, these cases would be candidates for deeper review,\n"
        "  trial eligibility screening, or alternative care pathways."
    )
    lines.append("")
    lines.append(
        "Next steps Aurora can support:\n"
        "  • Re-tune the weights or add new features (e.g., cytogenetic risk, response\n"
        "    to induction therapy) and re-run the risk stratification.\n"
        "  • Fit survival models (e.g., Cox, elastic-net) using Aurora’s math engine\n"
        "    to cross-check which features are statistically most predictive.\n"
        "  • Compare cohorts (e.g., by treatment era or protocol) using the same\n"
        "    multi-criteria score, to see whether risk structures are shifting over time."
    )

    return "\n".join(lines)


def run_aml_risk_demo(top_k: int = 10) -> None:
    data_path = os.path.join(DATA_DIR, "aml_clinical_clean.csv")

    _print_section("[AURORA x AML Clinical] Multi-criteria risk demo")

    df = _load_aml_clinical(data_path)

    # Build base + normalized features
    base_feats, z_df = _build_risk_features(df)

    # Build composite risk score
    score, weights_used = _build_risk_score(z_df)
    df_with_score = df.copy()
    df_with_score["aurora_risk_score"] = score

    # Compute summary stats & anomaly tail
    score_quantiles = _compute_score_quantiles(score)
    anomaly_info = _compute_anomaly_flags(score, q=0.95)

    summary: Dict[str, Any] = {
        "features_used": list(base_feats.columns) + ["Short Survival Risk"],
        "weights_used": weights_used,
        "score_quantiles": score_quantiles,
        "anomalies": {
            "num_flagged": anomaly_info["num_flagged"],
            "fraction_flagged": anomaly_info["fraction_flagged"],
            "threshold_95pct": anomaly_info["threshold_score"],
        },
        "meta": {
            "path": data_path,
            "num_patients": int(df.shape[0]),
        },
    }

    pprint(summary)

    # Top K high-risk patients
    _print_section("[AURORA x AML Clinical] Top high-risk patients")
    cols_to_show = [
        c
        for c in [
            "Patient ID",
            "Diagnosis Age",
            "Mutation Count",
            "Fraction Genome Altered",
            "TMB (nonsynonymous)",
            "Overall Survival (Months)",
            "aurora_risk_score",
        ]
        if c in df_with_score.columns
    ]

    top = df_with_score.sort_values("aurora_risk_score", ascending=False).head(top_k)
    print(top[cols_to_show].to_string(index=False))

    # Simple Pareto-ish front: high-risk + short survival
    if "Overall Survival (Months)" in df_with_score.columns:
        _print_section("[AURORA x AML Clinical] Pareto-front high-risk cases")
        surv = df_with_score["Overall Survival (Months)"].fillna(
            df_with_score["Overall Survival (Months)"].median()
        )
        front = _pareto_front_neg_risk_vs_survival(score, surv)

        for i, row in enumerate(front, start=1):
            idx = row["index"]
            pid = df_with_score.iloc[idx]["Patient ID"]
            neg_risk = row["obj1"]
            surv_m = row["obj2"]
            print(
                f"#{i} patient_index={idx}, patient_id={pid}, "
                f"neg_risk={neg_risk:.3f}, survival_months={surv_m:.2f}"
            )

    # Narrative research note
    _print_section("[AURORA x AML Clinical] Research note")
    note = _generate_aml_risk_narrative(summary)
    print(note)

    # Log experiment into Aurora research memory
    try:
        log_experiment(
            spec={
                "problem_type": "clinical_risk_scoring",
                "domain": "oncology_aml",
                "description": (
                    "Multi-criteria composite risk scoring over AML clinical cohort "
                    "using age, mutation burden, genome alteration fraction, TMB, "
                    "and short-survival indicator."
                ),
                "features_used": summary["features_used"],
                "weights_used": summary["weights_used"],
                "data_path": data_path,
            },
            result={
                "score_quantiles": summary["score_quantiles"],
                "anomalies": summary["anomalies"],
                "top_patients_preview": top[cols_to_show].to_dict(orient="records"),
            },
            tag="aml_multicriteria_risk_demo",
        )
    except Exception as e:
        # Don't hard-fail if logging has an issue
        print(f"[AURORA] Warning: failed to log experiment: {e}")


def main() -> None:
    run_aml_risk_demo(top_k=10)


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from fantasyai.aurora.forecasting import arima_forecast
from fantasyai.aurora.regime_detection import detect_regimes_and_anomalies
from fantasyai.aurora.causal_tools import estimate_linear_effects, apply_counterfactual
from fantasyai.aurora.metrics import time_series_metrics
from fantasyai.aurora.math_copilot import DeepSeekMathCopilot, summarize_math_result
from fantasyai.aurora.artifacts import AuroraRun


DATA_ROOT = Path(__file__).resolve().parents[1] / "fantasyai" / "data"


def _aurora_maybe_add(run: AuroraRun | None, name: str, obj) -> None:
    if run is None:
        return
    try:
        run.add(name, obj)
    except Exception:
        pass


def _load_noaa() -> pd.DataFrame:
    path = DATA_ROOT / "noaa_timeseries.csv"
    return pd.read_csv(path, parse_dates=["date"])


def _load_taxi() -> pd.DataFrame:
    path = DATA_ROOT / "nyc_taxi_sample.csv"
    df = pd.read_csv(path)

    if "cost_per_mile" not in df.columns:
        denom = df["trip_distance"].replace(0, np.nan)
        df["cost_per_mile"] = df["total_amount"] / denom
        df["cost_per_mile"] = df["cost_per_mile"].replace([np.inf, -np.inf], np.nan)
        df["cost_per_mile"] = df["cost_per_mile"].fillna(df["cost_per_mile"].median())

    return df


def _load_aml_clean() -> pd.DataFrame:
    path = DATA_ROOT / "aml_clinical_clean.csv"
    return pd.read_csv(path)


def run_noaa_forecast(*, run: AuroraRun | None = None, copilot: DeepSeekMathCopilot | None = None) -> None:
    print("=" * 80)
    print("[AURORAxNOAA] Multi-step forecast demo")
    print("=" * 80)

    df = _load_noaa().sort_values("date")
    y = df["temperature_C"].values.astype(float)

    if len(y) < 90:
        print("Not enough NOAA points for full forecast demo; skipping.")
        return

    history = y[:-14]
    future = y[-14:]

    fc_res = arima_forecast(history, horizon=14)
    metrics = time_series_metrics(future, fc_res.point_forecast)

    payload = {
        "dataset_meta": {
            "path": str(DATA_ROOT / "noaa_timeseries.csv"),
            "history_len": int(len(history)),
            "forecast_horizon": int(fc_res.horizon),
        },
        "forecast": {
            "model_type": fc_res.model_type,
            "diagnostics": fc_res.diagnostics,
            "first3_point": fc_res.point_forecast[:3].tolist(),
            "first3_lower": fc_res.lower[:3].tolist(),
            "first3_upper": fc_res.upper[:3].tolist(),
        },
        "metrics": metrics,
    }

    _aurora_maybe_add(run, "noaa_forecast_payload", payload)
    print(json.dumps(payload, indent=2))

    summary = summarize_math_result("NOAA temperature forecast", payload, copilot=copilot)
    _aurora_maybe_add(run, "noaa_forecast_summary", summary)

    print("\n[NOAA forecast – Aurora narrative]")
    print(summary)


def run_taxi_regimes(*, run: AuroraRun | None = None, copilot: DeepSeekMathCopilot | None = None) -> None:
    print("=" * 80)
    print("[AURORAxNYC Taxi] Regime + anomaly demo")
    print("=" * 80)

    df = _load_taxi()
    feats = df[["trip_distance", "total_amount", "cost_per_mile"]]
    res = detect_regimes_and_anomalies(feats, n_clusters=3)

    df_reg = df.copy()
    df_reg["regime"] = res.labels
    df_reg["anomaly_score"] = res.anomaly_scores

    cluster_summaries = []
    for k in sorted(df_reg["regime"].unique()):
        sub = df_reg[df_reg["regime"] == k]
        cluster_summaries.append(
            {
                "regime_id": int(k),
                "count": int(len(sub)),
                "mean_trip_distance": float(sub["trip_distance"].mean()),
                "mean_total_amount": float(sub["total_amount"].mean()),
                "mean_cost_per_mile": float(sub["cost_per_mile"].mean()),
            }
        )

    top = df_reg.sort_values("anomaly_score", ascending=False).head(10)

    payload = {
        "dataset_meta": {
            "path": str(DATA_ROOT / "nyc_taxi_sample.csv"),
            "num_trips": int(len(df_reg)),
            "feature_names": ["trip_distance", "total_amount", "cost_per_mile"],
        },
        "regimes": {
            "cluster_summaries": cluster_summaries,
            "explained_variance_ratio": res.explained_variance_ratio.tolist(),
        },
        "anomalies": {
            "score_quantiles": {
                "q50": float(np.quantile(res.anomaly_scores, 0.5)),
                "q75": float(np.quantile(res.anomaly_scores, 0.75)),
                "q90": float(np.quantile(res.anomaly_scores, 0.9)),
                "q95": float(np.quantile(res.anomaly_scores, 0.95)),
                "max": float(res.anomaly_scores.max()),
            },
            "top_trips_preview": top[
                ["trip_distance", "total_amount", "cost_per_mile", "anomaly_score"]
            ].to_dict(orient="records"),
        },
    }

    _aurora_maybe_add(run, "taxi_regimes_payload", payload)
    print(json.dumps(payload, indent=2))

    summary = summarize_math_result("NYC taxi regimes & anomalies", payload, copilot=copilot)
    _aurora_maybe_add(run, "taxi_regimes_summary", summary)

    print("\n[NYC taxi regimes – Aurora narrative]")
    print(summary)


def run_aml_causal(*, run: AuroraRun | None = None, copilot: DeepSeekMathCopilot | None = None) -> None:
    print("=" * 80)
    print("[AURORAxAML] Causal what-if demo")
    print("=" * 80)

    df = _load_aml_clean()

    target = "Overall Survival (Months)"
    drivers = ["Mutation Count", "Fraction Genome Altered", "Diagnosis Age"]

    est = estimate_linear_effects(df, target=target, features=drivers)

    sub = df.dropna(subset=drivers + [target])
    if sub.empty:
        print("No complete AML rows for causal demo; skipping.")
        return

    row = sub.sample(1, random_state=42).iloc[0]
    base_point = {k: float(row[k]) for k in drivers}

    deltas = {"Mutation Count": -5.0, "Fraction Genome Altered": -0.05}
    cf = apply_counterfactual(est, base_point=base_point, deltas=deltas)

    payload = {
        "estimate": {
            "target": est.target,
            "features": est.features,
            "coef_": est.coef_,
            "intercept_": est.intercept_,
            "diagnostics": est.diagnostics,
        },
        "example_patient": {
            "Patient ID": row.get("Patient ID", "N/A"),
            "base_point": base_point,
            "counterfactual_deltas": deltas,
            "counterfactual_result": cf,
            target: float(row[target]),
        },
    }

    _aurora_maybe_add(run, "aml_causal_payload", payload)
    print(json.dumps(payload, indent=2))

    summary = summarize_math_result("AML survival causal what-if", payload, copilot=copilot)
    _aurora_maybe_add(run, "aml_causal_summary", summary)

    print("\n[AML causal what-if – Aurora narrative]")
    print(summary)


def main() -> None:
    run = AuroraRun(domain="aurora_forecast_regime_causal_demo", tags={"script": "aurora_forecast_regime_causal_demo"})
    copilot = DeepSeekMathCopilot()

    try:
        run_noaa_forecast(run=run, copilot=copilot)
        run_taxi_regimes(run=run, copilot=copilot)
        run_aml_causal(run=run, copilot=copilot)
    finally:
        try:
            paths = run.finalize()
            print(f"[AURORA ARTIFACTS] wrote -> {paths['run_dir']}")
        except Exception as e:
            print(f"[AURORA ARTIFACTS] finalize failed: {e}")


if __name__ == "__main__":
    main()

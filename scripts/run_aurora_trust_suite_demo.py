from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from fantasyai.config import DATA_DIR
from fantasyai.aurora.ensembles import run_ensemble
from fantasyai.aurora.uncertainty import bootstrap_statistic
from fantasyai.aurora.regime_models import fit_regime_models, predict_regime_aware
from fantasyai.aurora.validation import backtest_forecast, stress_test
from fantasyai.aurora.constraints import Constraint, solve_constrained

# If you already have an ARIMA helper in your project, swap this to your implementation.
from statsmodels.tsa.arima.model import ARIMA


def arima_fit_forecast(series: pd.Series, horizon: int) -> dict:
    series = pd.to_numeric(series, errors="coerce").astype(float).dropna()
    model = ARIMA(series.values, order=(2, 1, 1))
    fit = model.fit()
    fc = fit.get_forecast(steps=horizon)
    mean = fc.predicted_mean
    ci = fc.conf_int(alpha=0.05)  # 95%
    return {
        "point": mean.tolist(),
        "lower": ci[:, 0].tolist(),
        "upper": ci[:, 1].tolist(),
        "diagnostics": {"order": [2, 1, 1], "aic": float(fit.aic), "bic": float(fit.bic)},
    }


def main():
    print("=" * 80)
    print("[AURORA TRUST SUITE] 2a–2e upgrades demo")
    print("=" * 80)

    # 2a + 2e: backtest + CI on metric
    noaa_path = Path(DATA_DIR) / "noaa_timeseries.csv"
    df_noaa = pd.read_csv(noaa_path)
    bt = backtest_forecast(df_noaa, value_col="temperature_C", fit_forecast_fn=arima_fit_forecast, horizon=14)

    rmse_ci = bootstrap_statistic(
        x=np.array(bt["first5"]["y_true"]) - np.array(bt["first5"]["y_pred"]),
        stat_fn=lambda e: float(np.sqrt(np.mean(np.asarray(e) ** 2))),
        n_boot=1000,
        level=0.95,
        seed=7,
    )

    print("\n[2e] NOAA backtest:")
    print(json.dumps(bt, indent=2))
    print("\n[2a] RMSE uncertainty (bootstrap on backtest residuals; first5 only demo):")
    print({
        "rmse_point": float(rmse_ci.point[0]),
        "rmse_lower": float(rmse_ci.lower[0]),
        "rmse_upper": float(rmse_ci.upper[0]),
        "level": rmse_ci.level,
        "method": rmse_ci.method,
    })

    # 2b: ensemble robustness for backtest RMSE
    def run_fn_noaa(dfi: pd.DataFrame) -> dict:
        res = backtest_forecast(dfi, value_col="temperature_C", fit_forecast_fn=arima_fit_forecast, horizon=14)
        return {"rmse": res["metrics"]["rmse"], "mae": res["metrics"]["mae"]}

    ens = run_ensemble(
        base_df=df_noaa,
        numeric_cols=["temperature_C", "precip_mm"],
        run_fn=run_fn_noaa,
        n_runs=200,
        noise_std_frac=0.02,
        seed=7,
    )
    print("\n[2b] NOAA ensemble robustness:")
    print(json.dumps(ens, indent=2))

    # 2c: regime-aware modeling on taxi
    taxi_path = Path(DATA_DIR) / "nyc_taxi_sample.csv"
    df_taxi = pd.read_csv(taxi_path)
    df_taxi["cost_per_mile"] = df_taxi["total_amount"] / df_taxi["trip_distance"].replace(0, np.nan)
    df_taxi["cost_per_mile"] = df_taxi["cost_per_mile"].replace([np.inf, -np.inf], np.nan)
    df_taxi["cost_per_mile"] = df_taxi["cost_per_mile"].fillna(df_taxi["cost_per_mile"].median())

    fit = fit_regime_models(
        df_taxi,
        feature_cols=["trip_distance", "cost_per_mile"],
        target_col="total_amount",
        n_regimes=3,
    )
    rm = fit["model"]
    pred = predict_regime_aware(rm, df_taxi.head(10))

    print("\n[2c] Taxi regime-aware model fit summary:")
    print(json.dumps({k: v for k, v in fit.items() if k != "model"}, indent=2))
    print("\n[2c] Taxi regime-aware predictions (first 10 rows preview):")
    print(pred[["trip_distance", "cost_per_mile", "total_amount", "aurora_regime_id", "aurora_regime_pred"]].to_string(index=False))

    # 2d: constraints as first-class citizens (toy)
    # minimize (x-3)^2 + (y+1)^2 subject to:
    #   x >= 0, y >= 0, x + y <= 6
    def base_obj(z: np.ndarray) -> float:
        x, y = float(z[0]), float(z[1])
        return (x - 3.0) ** 2 + (y + 1.0) ** 2

    constraints = [
        Constraint("x_nonnegative", lambda z: float(z[0]), "ineq"),
        Constraint("y_nonnegative", lambda z: float(z[1]), "ineq"),
        Constraint("capacity_x_plus_y", lambda z: float(6.0 - (z[0] + z[1])), "ineq"),
    ]

    sol = solve_constrained(
        base_obj=base_obj,
        x0=np.array([1.0, 1.0]),
        bounds=[(0.0, 10.0), (0.0, 10.0)],
        constraints=constraints,
        penalty_scale=1e3,
    )

    print("\n[2d] Constrained solve demo:")
    print(json.dumps(sol, indent=2))

    # 2e: stress test example on taxi
    def taxi_run_fn(dfi: pd.DataFrame) -> dict:
        tmp = dfi.copy()
        tmp["cost_per_mile"] = tmp["total_amount"] / tmp["trip_distance"].replace(0, np.nan)
        tmp["cost_per_mile"] = tmp["cost_per_mile"].replace([np.inf, -np.inf], np.nan).fillna(tmp["cost_per_mile"].median())
        # simple metric: mean cost_per_mile
        return {"mean_cost_per_mile": float(pd.to_numeric(tmp["cost_per_mile"], errors="coerce").mean())}

    st = stress_test(df_taxi, numeric_cols=["trip_distance", "total_amount"], run_fn=taxi_run_fn, shocks={"total_amount": 1.20})
    print("\n[2e] Taxi stress test (+20% fares):")
    print(json.dumps(st, indent=2))

    print("\n[AURORA TRUST SUITE] Done.")


if __name__ == "__main__":
    main()

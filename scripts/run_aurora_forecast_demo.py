# scripts/run_aurora_forecast_demo.py

"""
Aurora Forecast Demo

Uses the NOAA-style daily timeseries (noaa_timeseries.csv) generated earlier.
We fit an ARIMA model to the temperature and print:
  - AIC / BIC
  - 14-day forecast with confidence intervals
"""

import os
import pandas as pd

from fantasyai.aurora.forecasting import arima_forecast_with_ci

DATA_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "fantasyai",
    "data",
)

NOAA_PATH = os.path.join(DATA_ROOT, "noaa_timeseries.csv")


def main() -> None:
    print("=" * 80)
    print("[AURORA Forecast Demo] NOAA temperature")
    print("=" * 80)

    if not os.path.exists(NOAA_PATH):
        raise FileNotFoundError(
            f"NOAA file not found at {NOAA_PATH}. "
            "Run generate_demo_public_data first."
        )

    df = pd.read_csv(NOAA_PATH, parse_dates=["date"])
    df = df.sort_values("date")

    res = arima_forecast_with_ci(df["temperature_C"], order=(2, 0, 1), steps=14)

    print(f"\nModel order: {res['order']}")
    print(f"AIC: {res['aic']:.2f}, BIC: {res['bic']:.2f}")
    print("\n14-day forecast (mean ± CI):")
    for i, (m, lo, hi) in enumerate(
        zip(res["forecast_mean"], res["forecast_ci_lower"], res["forecast_ci_upper"]),
        start=1,
    ):
        print(f"  Day +{i}: {m:.2f} °C  [{lo:.2f}, {hi:.2f}]")

    print("\n[AURORA] Forecast demo complete.")


if __name__ == "__main__":
    main()

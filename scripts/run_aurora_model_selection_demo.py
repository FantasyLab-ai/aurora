# scripts/run_aurora_model_selection_demo.py

"""
Aurora Model Selection Demo

Uses the NYC taxi sample CSV to predict `total_amount` from `trip_distance`.
Aurora:
  - evaluates LinearRegression, RandomForestRegressor, GradientBoostingRegressor
  - prints RMSE / R^2 scoreboard
  - reports the chosen best model
"""

import os
import pandas as pd
import numpy as np

from fantasyai.aurora.model_selection import auto_select_regressor

DATA_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "fantasyai",
    "data",
)

NYC_PATH = os.path.join(DATA_ROOT, "nyc_taxi_sample.csv")


def main() -> None:
    print("=" * 80)
    print("[AURORA Model Selection Demo] NYC taxi fare prediction")
    print("=" * 80)

    if not os.path.exists(NYC_PATH):
        raise FileNotFoundError(
            f"NYC taxi file not found at {NYC_PATH}. "
            "Run generate_demo_public_data first."
        )

    df = pd.read_csv(NYC_PATH)
    df = df.dropna(subset=["trip_distance", "total_amount"])

    X = df[["trip_distance"]].values.astype(float)
    y = df["total_amount"].values.astype(float)

    res = auto_select_regressor(X, y)

    print("\nScores:")
    for name, sc in res["scores"].items():
        print(
            f"  {name}: RMSE={sc['rmse']:.3f}, "
            f"MSE={sc['mse']:.3f}, R^2={sc['r2']:.3f}"
        )

    print(f"\nBest model: {res['best_model_name']}")

    print("\n[AURORA] Model selection demo complete.")


if __name__ == "__main__":
    main()

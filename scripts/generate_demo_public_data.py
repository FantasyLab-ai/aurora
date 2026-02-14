# scripts/generate_demo_public_data.py

from __future__ import annotations

import os
from datetime import datetime, timedelta
import random

import numpy as np
import pandas as pd  # type: ignore

from fantasyai.domains.public_datasets import DATA_ROOT

def ensure_data_root() -> str:
    os.makedirs(DATA_ROOT, exist_ok=True)
    print(f"[DEMO DATA] Using DATA_ROOT = {DATA_ROOT}")
    return DATA_ROOT


def make_tcga_expression_csv(root: str) -> None:
    path = os.path.join(root, "tcga_expression.csv")
    n = 50
    rows = []
    for i in range(n):
        rows.append({
            "sample_id": f"S{i:03d}",
            "days_since_baseline": i,
            "tumor_volume": max(0.5, 10 * np.exp(0.03 * i) + np.random.normal(0, 2)),
            "gene_A": np.random.normal(0, 1),
            "gene_B": np.random.normal(0, 1),
        })
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    print(f"[DEMO DATA] Wrote TCGA-like expression CSV: {path}")


def make_cptac_proteomics_csv(root: str) -> None:
    path = os.path.join(root, "cptac_proteomics.csv")
    n = 50
    rows = []
    for i in range(n):
        rows.append({
            "sample_id": f"P{i:03d}",
            "prot_X": np.random.lognormal(mean=0.0, sigma=0.5),
            "prot_Y": np.random.lognormal(mean=0.2, sigma=0.6),
        })
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    print(f"[DEMO DATA] Wrote CPTAC-like proteomics CSV: {path}")


def make_noaa_timeseries_csv(root: str) -> None:
    path = os.path.join(root, "noaa_timeseries.csv")
    n = 365
    start = datetime(2024, 1, 1)
    rows = []
    for i in range(n):
        d = start + timedelta(days=i)
        # simple seasonal temp pattern + noise
        temp = 15 + 10 * np.sin(2 * np.pi * i / 365.0) + np.random.normal(0, 1.5)
        precip = max(0.0, np.random.exponential(0.3) - 0.1)
        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "temperature_C": round(temp, 2),
            "precip_mm": round(precip, 2),
        })
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    print(f"[DEMO DATA] Wrote NOAA-like timeseries CSV: {path}")


def make_nyc_taxi_sample_csv(root: str) -> None:
    path = os.path.join(root, "nyc_taxi_sample.csv")
    n = 200
    rows = []
    for _ in range(n):
        dist = max(0.5, np.random.exponential(2.0))  # miles
        base = 2.50
        per_mile = 3.0
        noise = np.random.normal(0, 1.0)
        total = max(3.0, base + per_mile * dist + noise)
        rows.append({
            "trip_distance": round(dist, 2),
            "total_amount": round(total, 2),
        })
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    print(f"[DEMO DATA] Wrote NYC taxi-like sample CSV: {path}")


def main() -> None:
    root = ensure_data_root()
    make_tcga_expression_csv(root)
    make_cptac_proteomics_csv(root)
    make_noaa_timeseries_csv(root)
    make_nyc_taxi_sample_csv(root)
    print("\n[DEMO DATA] Demo CSVs generated. You can now run:")
    print("  python -m scripts.run_aurora_on_public_data_demo")


if __name__ == "__main__":
    main()

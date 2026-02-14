# scripts/plot_aurora_anomalies.py
from __future__ import annotations
import json
from pathlib import Path
from typing import List

import sqlite3
import matplotlib.pyplot as plt

from fantasyai.config import DATA_DIR


DB_PATH = DATA_DIR / "fantasyai_core.db"


def fetch_anomalies_by_domain(domain: str, limit: int = 200) -> List[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT domain, source, description, score, metadata_json, created_at
        FROM aurora_anomalies
        WHERE domain = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (domain, limit),
    )
    rows = []
    for row in cur.fetchall():
        meta = {}
        if row["metadata_json"]:
            try:
                meta = json.loads(row["metadata_json"])
            except json.JSONDecodeError:
                meta = {}
        rows.append(
            {
                "domain": row["domain"],
                "source": row["source"],
                "description": row["description"],
                "score": row["score"],
                "metadata": meta,
                "created_at": row["created_at"],
            }
        )
    conn.close()
    return rows


def plot_time_series_values(values, title: str, ylabel: str) -> None:
    fig, ax = plt.subplots()
    ax.plot(range(len(values)), values, marker="o")
    ax.set_title(title)
    ax.set_xlabel("Index (most recent first)")
    ax.set_ylabel(ylabel)
    ax.grid(True)
    plt.tight_layout()
    plt.show()


def plot_domain(domain: str) -> None:
    anomalies = fetch_anomalies_by_domain(domain)
    if not anomalies:
        print(f"No anomalies found for domain='{domain}'.")
        return

    scores = [a["score"] for a in anomalies]
    print(f"Fetched {len(anomalies)} anomalies for domain='{domain}'.")

    # Basic score plot
    plot_time_series_values(
        scores,
        title=f"Aurora anomaly scores – {domain}",
        ylabel="Anomaly score (relative)",
    )


def main() -> None:
    print("Plotting Aurora anomalies...")
    for domain in ["climate", "market", "astronomy"]:
        plot_domain(domain)


if __name__ == "__main__":
    main()

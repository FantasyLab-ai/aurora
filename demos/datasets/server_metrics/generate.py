"""
Generate the canonical server-metrics dataset for Demo 3 ("The Save").

Story: a production server's CPU + memory + p99 latency look normal
for ~95% of the timeline, then drift into a slow regime shift (memory
leak / hot-loop) over the last few percent. A human watching the
dashboard would miss the slow drift; Aurora flags it.

The dataset has known anomalies at *known indices* so the
contract trip is reproducible across takes.

Run:

    python -m demos.datasets.server_metrics.generate
"""
from __future__ import annotations

from pathlib import Path
import csv
import math
import random


# Total samples (1 per minute → ~1 day). The drift starts at ~96% in.
N_ROWS         = 1440
DRIFT_START    = int(N_ROWS * 0.96)
DRIFT_END      = N_ROWS

# Baseline distributions.
BASE_CPU       = 0.34
BASE_MEM_GB    = 12.4
BASE_P99_MS    = 38.0
BASE_REQ_S     = 240.0


def generate(seed: int = 7, out_path: Path | None = None) -> Path:
    if out_path is None:
        out_path = Path(__file__).resolve().parent / "server_metrics.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    random.seed(seed)
    rows = []

    for i in range(N_ROWS):
        # Diurnal sine — gentle traffic curve over the 24h.
        diurnal = math.sin((i / N_ROWS) * 2 * math.pi - 0.5)
        cpu     = BASE_CPU + 0.10 * max(0, diurnal) + random.gauss(0, 0.02)
        mem_gb  = BASE_MEM_GB + 0.20 * diurnal       + random.gauss(0, 0.08)
        p99_ms  = BASE_P99_MS + 6.0  * max(0, diurnal) + random.gauss(0, 1.5)
        req_s   = BASE_REQ_S  + 80.0 * max(0, diurnal) + random.gauss(0, 8.0)

        # Drift in the final ~4% of the timeline (the regime shift).
        if i >= DRIFT_START:
            t = (i - DRIFT_START) / max(1, DRIFT_END - DRIFT_START)
            # Memory creeps up; CPU follows; latency explodes; req/s drops.
            mem_gb  += 4.0   * (t ** 1.5)
            cpu     += 0.18  * (t ** 1.7)
            p99_ms  += 80.0  * (t ** 1.8)
            req_s   -= 60.0  * (t ** 1.4)

        rows.append({
            "minute":  i,
            "cpu":     round(max(0.0, min(1.0, cpu)),  4),
            "mem_gb":  round(max(0.0, mem_gb),         3),
            "p99_ms":  round(max(0.0, p99_ms),         2),
            "req_s":   round(max(0.0, req_s),          2),
        })

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["minute", "cpu", "mem_gb",
                                                  "p99_ms", "req_s"])
        writer.writeheader()
        writer.writerows(rows)
    return out_path


if __name__ == "__main__":
    path = generate()
    print(f"Wrote {path}")
    import csv as _csv
    with open(path, "r", encoding="utf-8") as f:
        rows = list(_csv.DictReader(f))
    print(f"  rows = {len(rows)}")
    print(f"  first = {rows[0]}")
    print(f"  near-drift = {rows[DRIFT_START]}")
    print(f"  last = {rows[-1]}")

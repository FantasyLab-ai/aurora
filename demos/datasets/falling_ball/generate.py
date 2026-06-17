"""
Generate the canonical falling-ball physics dataset for Demo 5.

The CSV simulates measurements from a ball drop:

    y(t) = y0 + v0 * t + 0.5 * a * t^2 + noise

with `a = -9.81 m/s^2` (gravity) and a small measurement noise so
Aurora's Physics lens (SINDy) has something realistic to discover —
but the underlying law is still clean enough to surface as the
*best-fit candidate*.

Why we synthesise rather than record real video footage:
    * Reproducibility — every take starts from the same seed.
    * The visual still works: in editing, you overlay the discovered
      equation against your real drop-the-ball clip.
    * Anyone trying the demo at home can re-run this script and
      verify Aurora reaches the same finding.

Run:

    python -m demos.datasets.falling_ball.generate

Writes `falling_ball.csv` next to this script.
"""
from __future__ import annotations

from pathlib import Path
import csv
import math
import random


# Physical params — set explicitly so the expected finding is reproducible.
Y0   = 5.0       # initial height (m)
V0   = 0.0       # initial vertical velocity (m/s) — pure drop
A    = -9.81     # gravitational acceleration (m/s^2)
DT   = 0.02      # 50 Hz sampling — ~1s of fall covers ~50 rows
NOISE_SIGMA = 0.012   # gaussian measurement noise (m) — small but realistic


def generate(seed: int = 42, out_path: Path | None = None) -> Path:
    if out_path is None:
        out_path = Path(__file__).resolve().parent / "falling_ball.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    random.seed(seed)
    rows = []
    t = 0.0
    y = Y0
    v = V0
    # We integrate until the ball reaches y <= 0 (hits the ground),
    # padding a few rows past for the algorithm to see the impact.
    n_pad = 8
    landed_at = None
    i = 0
    while True:
        i += 1
        # Truth position (no noise).
        y_true = Y0 + V0 * t + 0.5 * A * t * t
        # Velocity from differentiating the truth.
        v_true = V0 + A * t
        # Measurement = truth + small noise.
        y_meas = y_true + random.gauss(0.0, NOISE_SIGMA)
        rows.append({
            "t":      round(t, 4),
            "y":      round(y_meas, 5),
            "y_true": round(y_true, 5),
            "v":      round(v_true, 5),
        })
        if landed_at is None and y_true <= 0:
            landed_at = i
        if landed_at and i >= landed_at + n_pad:
            break
        t = round(t + DT, 4)
        # Safety guard so a wonky config doesn't loop forever.
        if i > 5000:
            break

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["t", "y", "y_true", "v"])
        writer.writeheader()
        writer.writerows(rows)

    return out_path


if __name__ == "__main__":
    path = generate()
    print(f"Wrote {path}")
    # Brief stats so the user can sanity-check.
    import csv as _csv
    with open(path, "r", encoding="utf-8") as f:
        reader = list(_csv.DictReader(f))
    ts = [float(r["t"]) for r in reader]
    ys = [float(r["y"]) for r in reader]
    print(f"  rows = {len(reader)}")
    print(f"  t range = [{min(ts):.3f}, {max(ts):.3f}] s")
    print(f"  y range = [{min(ys):.3f}, {max(ys):.3f}] m")

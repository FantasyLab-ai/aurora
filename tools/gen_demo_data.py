"""
Generate Aurora's three v1 demo datasets — small, deterministic, committed.

Each is hand-designed to exercise a specific axis of the engine and produce
visible findings in the UI:

  1. ``climate_buoy_demo.csv``  — clean time-series with regime shift + anomalies + a
                                  cooling-curve target that *can* match Newton's law of cooling
                                  (gives the green "MATCHES KNOWN LAW" badge on the physics tile).
  2. ``factory_bearing_demo.csv`` — sensor data with damped oscillation + 3 impulse anomalies +
                                    a degradation regime in the last quarter.
  3. ``patient_cohort_demo.csv``  — cross-sectional, no time axis, mixed continuous + categorical
                                    (probes the "no valid time" honest-mode path).

Reproducibility: deterministic seed (42), only stdlib (random + math + datetime).
Outputs land in ``data/fixtures/``. ~5-50 KB each. Safe to commit.
"""
from __future__ import annotations

import csv
import math
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "fixtures"
SEED = 42


# ---------------------------------------------------------------------------
# Demo 1: Climate buoy — daily temperature + precip + humidity + pressure
# ---------------------------------------------------------------------------

def gen_climate_buoy(out_path: Path) -> None:
    """
    365 daily readings designed to:
      * expose a clear seasonal cooling trend (Newton's-cooling-fittable)
      * include a regime shift around day 200 (storm season onset)
      * include 4-5 critical anomalies in precipitation
      * have realistic cross-variable correlations
    """
    rng = random.Random(SEED)
    start = datetime(2025, 1, 1)
    rows = []

    # Two regimes: gentle cooling phase (days 0-200), then steeper cooling + variance burst (days 200-365)
    for d in range(365):
        date = start + timedelta(days=d)
        # Newton's-cooling-like target: T(t) = T_inf + (T_0 - T_inf) * exp(-k*t)
        # We design two regimes; the *runner* will fit one ODE that approximately matches.
        if d < 200:
            T_inf, T_0, k = 18.0, 32.0, 0.012   # gentle decay toward 18°C
            t = d
        else:
            T_inf, T_0, k = 4.0, 24.0, 0.018    # steeper decay toward 4°C (winter)
            t = d - 200
        temp = T_inf + (T_0 - T_inf) * math.exp(-k * t)
        # Daily noise + seasonal sinusoid riding on top.
        temp += rng.gauss(0, 1.2) + 2.5 * math.sin(d * 2 * math.pi / 365.0)

        # Precipitation: log-normal-ish with occasional spikes.
        base_precip = max(0.0, rng.lognormvariate(0.5, 0.8) - 1.5)
        precip = base_precip
        # Inject 5 critical precipitation anomalies at known days.
        if d in (47, 138, 205, 287, 341):
            precip += 35.0 + rng.uniform(0, 15)

        # Humidity correlates positively with precip.
        humidity = 55 + 0.6 * precip + rng.gauss(0, 7)
        humidity = max(20.0, min(98.0, humidity))

        # Barometric pressure correlates inversely with precip+temp anomalies.
        pressure = 1015 - 0.3 * precip + 0.05 * (temp - 18) + rng.gauss(0, 2.5)

        rows.append({
            "date":                  date.strftime("%Y-%m-%d"),
            "temperature_c":         round(temp, 2),
            "precip_mm":             round(precip, 2),
            "humidity_pct":          round(humidity, 1),
            "barometric_pressure_hpa": round(pressure, 2),
            "station_status":        "active" if rng.random() > 0.005 else "maintenance",
        })

    _write_csv(out_path, rows)


# ---------------------------------------------------------------------------
# Demo 2: Factory bearing — high-frequency vibration + degradation
# ---------------------------------------------------------------------------

def gen_factory_bearing(out_path: Path) -> None:
    """
    1000 samples at 10 Hz (100 seconds) of:
      * vibration: damped harmonic oscillator (matches damped_oscillator candidate)
      * motor temperature: slow rise (correlates with vibration in degradation regime)
      * rpm: nearly constant with small fluctuations
      * load_kn: stepped operating regime
      * Designed-in: 3 impulse anomalies at known timestamps; degradation kicks in at sample 750
    """
    rng = random.Random(SEED + 1)
    n = 1000
    dt = 0.1  # 10 Hz
    rows = []

    # Damped oscillator parameters (mass-spring-damper). Decay envelope * sine.
    # Choose c, k such that c² < 4*m*k (underdamped). m = 1.
    omega_n = 2.0       # natural frequency rad/s
    zeta = 0.04         # damping ratio (light)
    omega_d = omega_n * math.sqrt(1 - zeta * zeta)

    base_temp = 65.0
    for i in range(n):
        t = i * dt
        # Vibration: damped sinusoid with periodic re-excitation (operating cycles)
        cycle = i // 200
        env = 0.8 * math.exp(-zeta * omega_n * (t - 20 * cycle))
        vibration = env * math.sin(omega_d * t) + rng.gauss(0, 0.05)

        # Inject 3 impulse anomalies at sample 220, 540, 880.
        if i in (220, 540, 880):
            vibration += 4.5 + rng.uniform(0, 1.5)

        # Degradation regime: starts at sample 750. Vibration variance + motor temp climb.
        if i >= 750:
            extra_noise = rng.gauss(0, 0.25)  # noisier
            vibration += extra_noise
            temp_drift = 0.04 * (i - 750)
        else:
            temp_drift = 0.001 * i

        motor_temp = base_temp + temp_drift + rng.gauss(0, 0.4)
        rpm = 1750 + rng.gauss(0, 8)
        load = 12.0 if (i // 250) % 2 == 0 else 18.0
        load += rng.gauss(0, 0.3)

        rows.append({
            "timestamp_s":     round(t, 2),
            "vibration_g":     round(vibration, 4),
            "motor_temp_c":    round(motor_temp, 2),
            "rpm":             round(rpm, 1),
            "bearing_load_kn": round(load, 2),
        })

    _write_csv(out_path, rows)


# ---------------------------------------------------------------------------
# Demo 3: Patient cohort — cross-sectional, no time axis
# ---------------------------------------------------------------------------

def gen_patient_cohort(out_path: Path) -> None:
    """
    200 patient rows. Cross-sectional — NO valid time axis. Mixed continuous +
    categorical. Designed to:
      * exercise the "no time axis" honest-mode path (forecast tile honestly says so)
      * produce real correlations between bmi / cholesterol / blood pressure
      * include a clear treatment-effect signal that the causal layer can find
      * include a few age-correlated outliers as anomaly fodder
    """
    rng = random.Random(SEED + 2)
    n = 200
    rows = []

    for i in range(n):
        # Demographics
        age = max(18, min(90, int(rng.gauss(55, 14))))
        sex = rng.choices(["M", "F"], weights=[0.48, 0.52])[0]
        treatment_group = rng.choices(
            ["placebo", "low_dose", "high_dose"], weights=[0.34, 0.33, 0.33]
        )[0]

        # BMI: lognormal-ish, slightly elevated.
        bmi = max(15.0, min(50.0, rng.gauss(28.0, 4.5)))

        # Cholesterol: correlates with BMI and age.
        cholesterol = 160 + 1.4 * (bmi - 25) + 0.6 * (age - 50) + rng.gauss(0, 18)
        cholesterol = max(120.0, cholesterol)

        # Blood pressure: correlates with BMI and age, plus M-bias.
        bp_sys = 110 + 0.9 * (bmi - 25) + 0.7 * (age - 50) + (3 if sex == "M" else 0) + rng.gauss(0, 8)
        bp_sys = max(85.0, min(220.0, bp_sys))

        # Biomarker_x (HbA1c-like): correlates with BMI; treatment effect.
        biomarker_x = 5.5 + 0.05 * (bmi - 25) + rng.gauss(0, 0.3)
        if treatment_group == "low_dose":
            biomarker_x -= 0.4
        elif treatment_group == "high_dose":
            biomarker_x -= 0.9

        # Outcome: sparse binary event (5% positive), correlated with bp_sys.
        event_prob = 0.02 + 0.0015 * max(0, bp_sys - 130)
        event = "yes" if rng.random() < event_prob else "no"

        # Inject 4 deliberate outliers (extreme BMI + cholesterol).
        if i in (37, 91, 142, 178):
            bmi += rng.uniform(8, 14)
            cholesterol += rng.uniform(60, 100)
            bp_sys += rng.uniform(15, 30)

        rows.append({
            "patient_id":         f"P{i:04d}",
            "age":                int(age),
            "sex":                sex,
            "bmi":                round(bmi, 1),
            "cholesterol_mg_dl":  round(cholesterol, 0),
            "blood_pressure_sys": round(bp_sys, 0),
            "biomarker_x":        round(biomarker_x, 2),
            "treatment_group":    treatment_group,
            "adverse_event":      event,
        })

    _write_csv(out_path, rows)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(path: Path, rows: list) -> None:
    if not rows:
        raise ValueError("no rows to write")
    fieldnames = list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------------------------------------------------------------------------
# Manifest — what each demo is, displayed in the UI
# ---------------------------------------------------------------------------

DEMO_MANIFEST = [
    {
        "id": "climate_buoy",
        "filename": "climate_buoy_demo.csv",
        "title": "Climate buoy",
        "subtitle": "365-day temp / precip / humidity",
        "domain": "enviro",
        "highlights": [
            "regime shift mid-year",
            "5 precipitation anomalies",
            "Newton's-cooling-fittable target",
        ],
    },
    {
        "id": "factory_bearing",
        "filename": "factory_bearing_demo.csv",
        "title": "Factory bearing",
        "subtitle": "1000-sample vibration sensor at 10 Hz",
        "domain": "industrial",
        "highlights": [
            "damped harmonic oscillator signal",
            "3 impulse anomalies",
            "degradation regime in last quarter",
        ],
    },
    {
        "id": "patient_cohort",
        "filename": "patient_cohort_demo.csv",
        "title": "Patient cohort",
        "subtitle": "200 patients, cross-sectional, mixed types",
        "domain": "research",
        "highlights": [
            "no time axis (honest-mode path)",
            "treatment-effect causal signal",
            "BMI / cholesterol / BP correlations",
        ],
    },
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        (OUT_DIR / "climate_buoy_demo.csv",   gen_climate_buoy),
        (OUT_DIR / "factory_bearing_demo.csv", gen_factory_bearing),
        (OUT_DIR / "patient_cohort_demo.csv",  gen_patient_cohort),
    ]
    for path, fn in targets:
        fn(path)
        print(f"  wrote {path.relative_to(REPO_ROOT)}  ({path.stat().st_size:,} bytes)")
    print(f"\n{len(targets)} demo dataset(s) generated in {OUT_DIR}")


if __name__ == "__main__":
    main()

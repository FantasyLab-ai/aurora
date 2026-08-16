# Calibration corpus v1 — manifest

What this table is: for each (method, null regime) cell, the
empirically measured rate at which Aurora's PRODUCTION detector
fires on data generated to contain nothing to find.

* built: 2026-08-15 19:30:31
* build compute: 2360.7s single-threaded
* base seed: 20260815
* trials per cell: 4000
* cells: 27  entries: 72
* corpus file: corpus_v1.json (sha256 in the sidecar)

## Methods covered

* `bocpd` v1.0.0 — 27 entries; production defaults: {'hazard': 0.004, 'threshold': 0.5}
* `cusum` v1.0.0 — 27 entries; production defaults: {'threshold': 8.0, 'min_n': 80}
* `pelt` v1.0.0 — 18 entries; production defaults: {'model': 'rbf', 'pen': 3.0, 'min_n': 120}

## Generators

* `AR1` v1.0.0
* `ARMA` v1.0.0
* `HeavyTailed` v1.0.0
* `IIDNormal` v1.0.0
* `IrregularSampled` v1.0.0
* `SeasonalStationary` v1.0.0

## Seeding

Every trial is drawn via `generate_cell_trial(generator, params, n,
trial)`: NumPy `SeedSequence` with fixed entropy (the base seed
above) and a spawn key from a SHA-256 of the cell descriptor.
Same cell, same trial, same series — on any machine.

## Invalidation rules

A corpus is only valid for the generator versions and production
detector defaults it records. Changing either — a generator's
output for a given seed, a detector's algorithm, or a detector's
default thresholds — requires a version bump and a rebuild. The
guard tests in `tests/test_calibration.py` pin the production
defaults so a silent drift fails CI.

## What this corpus does NOT claim

* No power / sensitivity numbers. This table measures false fires
  on nulls only; false negatives are a separate, harder problem
  and are deliberately out of scope.
* No real-data ground truth. There is no honest label for 'was
  there really a regime change in this production series', so
  synthetic nulls are the only defensible basis. Rates on real
  data with real structure may differ.
* No nominal alpha. BOCPD, CUSUM and PELT (as configured in
  production) make no alpha-level claim, so 'inflation vs nominal
  5%' would be a comparison against a number the methods never
  promised. Inflation is reported against the measured IID
  baseline instead.

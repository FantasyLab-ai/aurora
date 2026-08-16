# The Calibration Engine

Aurora reports what it found. As of v0.10.0, it also reports **how often
that kind of finding is wrong** — measured, not assumed.

## The problem it solves

Statistical methods carry assumptions. Real data violates them. A
changepoint detector that behaves nominally on independent data can fire
on the large majority of perfectly stationary series once autocorrelation
is present — which is unremarkable for daily demand, sensor telemetry, or
financial series.

Measured on Aurora's own production CUSUM detector (n=90, 4,000 seeded
null trials per cell — see `fantasyai/aurora/calibration/corpus_v1.json`
for the authoritative numbers):

| lag-1 autocorrelation φ | fires on stationary data | 95% CI |
|---|---|---|
| 0.0 (independent) | 0.1% (4/4000) | 0.0% – 0.3% |
| 0.2 | 1.5% (59/4000) | 1.1% – 1.9% |
| 0.4 | 13.6% (545/4000) | 12.6% – 14.7% |
| 0.6 | 48.3% (1932/4000) | 46.8% – 49.8% |
| 0.8 | 89.5% (3580/4000) | 88.5% – 90.4% |

And it compounds with series length: at φ=0.4 the rate climbs from
13.6% (n=90) to 21.6% (n=120) to 39.6% (n=180) — collecting more data
makes this failure mode worse, because CUSUM gets more time to
accumulate autocorrelated drift past its threshold.

The method is not broken. Its assumption is violated, and — without
calibration — nothing downstream can tell. When an autonomous system acts
on such a finding, the action can satisfy every hard constraint and still
be wrong. Run `python -m demos.phantom_signal.run_demo` to watch that
happen end to end.

## How it works

1. **Null generators** (`calibration/generators.py`) produce seeded,
   versioned series with known ground truth: nothing to find. Six
   processes: IID normal, AR(1), ARMA(1,1), seasonal-stationary,
   heavy-tailed (Student-t), irregularly sampled.
2. **The harness** (`calibration/harness.py`) runs Aurora's **production
   detector code paths** — the exact functions the engine calls, verified
   by identity in the test suite — over a grid of regimes, 4,000 trials
   per cell, and records empirical false-fire rates with Wilson 95%
   intervals.
3. **The corpus** (`calibration/corpus_v1.json` + `.sha256` +
   `MANIFEST.md`) ships inside the package. It is versioned and
   integrity-hashed; the hash travels into every finding and bundle that
   uses it.
4. **At analysis time**, the user's series is fingerprinted
   (`calibration/fingerprint.py`: length, bias-corrected lag-1
   autocorrelation, missingness, sampling regularity, kurtosis, skewness,
   seasonal period) and matched against the corpus
   (`calibration/lookup.py`). The result is attached to the finding.

## The calibration block

Attached to changepoint-family findings by `run_extended_methods`, and
readable via `RunResult.methods.calibration("bocpd")` in the SDK:

```json
"calibration": {
  "status": "calibrated",
  "empirical_fdr": 0.483,
  "ci95": [0.4675, 0.4985],
  "nominal_alpha": null,
  "iid_baseline_fdr": 0.001,
  "fdr_vs_iid": 483.0,
  "matched_regime": {"generator": "AR1", "params": {"phi": 0.6}, "n": 90, "phi_hat": 0.59},
  "observed_regime": {"n": 90, "phi_hat": 0.57, "...": "..."},
  "regime_distance": {"phi_hat": 0.02, "n_log_ratio": 0.0, "scalar": 0.4},
  "fdr_ceiling": 0.25,
  "verdict_downgraded": true,
  "remediation": [{"action": "require_consecutive_confirmations", "detail": "..."}],
  "corpus_version": "v1",
  "corpus_sha256": "…"
}
```

`nominal_alpha` is `null` deliberately: BOCPD (posterior threshold),
CUSUM (σ-unit threshold) and PELT (penalty) make no α-level claim, so
"inflation vs a nominal 5%" would compare against a promise the methods
never made. Inflation is reported against the **measured IID baseline**
instead — and when that baseline fired zero times in its trials, the
ratio is withheld and the CI bound is reported, because dividing by an
unmeasured zero manufactures a number.

## The four statuses — and the rules behind them

| status | meaning |
|---|---|
| `calibrated` | observed regime snapped to a measured grid cell; distance reported |
| `interpolated` | between two measured φ cells; both cells, the weight, and the max interpolation error reported |
| `unavailable` | outside the calibrated envelope; the offending dimension is named; **no rate is reported** |
| `not_yet_calibrated` | no corpus covers this method; an explicit state, never a default |

Hard rules, enforced by tests (`tests/test_calibration.py`):

- **Never silently interpolate.** Interpolation always announces itself
  and its maximum error.
- **Never extrapolate.** Outside the envelope the answer is
  `unavailable` plus the dimension that fell outside. Silence is a lie.
- **Always report the distance** between observed and matched regime.
- **Calibration never blocks a finding.** Any failure degrades to an
  explicit status; the finding always returns with its raw statistic.
- **A verdict that depends on a threshold discloses the threshold.** The
  configured FDR ceiling (default 0.25) rides inside the block.

## Verdict interaction

When a changepoint finding **fired** and its measured false-fire rate
exceeds the ceiling, the finding is downgraded: `verdict:
"not_identifiable"`, severity drops to `info`, and the description says
why. The raw statistic and change-point indices remain in evidence —
calibration contextualises what Aurora computed; it never hides it.

## Remediation

Only derivable steps are emitted, each with its numbers and assumptions:
consecutive-confirmation counts (`fdr^k ≤ target`, independence stated),
pre-whitening with the estimated φ against the measured IID rate, and
minimum-length advice only when the corpus actually contains a longer-n
cell with an acceptable rate.

## What this is not

- **Not calibration against real labeled data.** There is no honest
  ground truth for "was there really a regime change in this production
  series." Synthetic nulls are the only defensible basis, and the docs
  say so everywhere the numbers appear.
- **Not power measurement.** False negatives are a separate, harder
  problem; nothing here claims sensitivity coverage.
- **Not all 19 methods.** Corpus v1 covers the changepoint family
  (BOCPD, CUSUM, PELT) end to end. Other methods carry an explicit
  `not_yet_calibrated` marker until their corpora exist.

## Rebuilding the corpus

```bash
python scripts/build_calibration_corpus.py                # parallel (cpu_count-2 workers)
python scripts/build_calibration_corpus.py --workers 1    # sequential
python scripts/build_calibration_corpus.py --trials 200   # quick pass
```

Worker count changes only the wall clock, never a measured number:
every trial is seeded from (generator, params, n, trial) alone, and the
test suite asserts sequential and parallel builds are identical.

A corpus is valid only for the generator versions and production detector
defaults it records. The guard tests pin those defaults; changing either
fails CI until the corpus is rebuilt under a bumped version.

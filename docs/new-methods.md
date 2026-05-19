# Aurora method coverage — new methods (v1.2)

Aurora's analytical method library grew from 17 to 20 in v1.2, with
three new methods that round out specific weak spots:

| Method | Module | When to use |
|---|---|---|
| **VAR** (Vector Autoregression) | `fantasyai.aurora.math.methods.var` | Multivariate forecasting + cross-variable coupling. The natural generalisation of AR(1) when you have ≥2 numeric columns and care about how they feed back on each other. |
| **DTW** (Dynamic Time Warping) | `fantasyai.aurora.math.methods.dtw` | Shape-similarity between sequences allowing elastic time alignment. Use when two series are the same shape at different speeds. |
| **BOCPD** (Bayesian Online Change-Point Detection) | `fantasyai.aurora.math.methods.bocpd` | Streaming-friendly change-point detection. The natural complement to PELT (which runs in batch); BOCPD updates point-by-point. |

All three follow Aurora's standard contract:

- Pure-Python or built on existing deps (statsmodels) — **no new heavy imports**
- Return Aurora-shaped finding dicts (`severity`, `confidence`, `title`, `description`, `method`, `claim_id`, `fabricated=False`)
- Skip cleanly with an explicit reason when preconditions aren't met (no silent failures)
- Cite their primary references via `KB_CITATIONS`

## VAR

```python
import pandas as pd
from fantasyai.aurora.math.methods import fit_var

df = pd.read_csv("multivariate_timeseries.csv")
finding = fit_var(df, target_cols=["x", "y", "z"], max_lags=8, forecast_horizon=12)
print(finding["title"])
# → "VAR(3) on 3 variables; strongest coupling x→y"
```

Key tunables:

| Param | Default | Notes |
|---|---|---|
| `target_cols` | None | All numeric columns when omitted |
| `max_lags` | 8 | Upper bound for AIC-based lag selection |
| `forecast_horizon` | 12 | Steps to forecast forward |

**Preconditions:** ≥ 2 numeric columns, ≥ 30 rows after dropna.

**Citations:** Sims (1980); Lütkepohl (2005).

## DTW

```python
from fantasyai.aurora.math.methods import dtw_distance, fit_dtw

# Pairwise distance — works in isolation
d = dtw_distance([1, 2, 3, 4, 5], [1, 2, 2, 3, 4, 5], window=10)

# Or aurora-shaped multi-column comparison
finding = fit_dtw(df, top_k=3)
print(finding["evidence"]["most_similar"])
# → [{"col_a": "x", "col_b": "y", "distance": 0.234}, ...]
```

Tunables:

| Param | Default | Notes |
|---|---|---|
| `window_ratio` | 0.1 | Sakoe-Chiba band radius as a fraction of length |
| `top_k` | 3 | How many pairs to highlight |

**Citations:** Sakoe & Chiba (1978); Keogh & Ratanamahatana (2005).

## BOCPD

```python
from fantasyai.aurora.math.methods import fit_bocpd

finding = fit_bocpd(df, target_col="signal",
                     hazard=1.0/250.0,
                     threshold=0.5)
for cp in finding["evidence"]["change_points"]:
    print(cp)
# → {"row_idx": 102, "posterior_at_r0": 0.84}, ...
```

Tunables:

| Param | Default | Notes |
|---|---|---|
| `target_col` | None | Auto-picks first numeric column with variance |
| `hazard` | 1/250 | Prior change-point rate (smaller = expect fewer shifts) |
| `prior_mean / prior_var / prior_alpha / prior_beta` | Normal-Inverse-Gamma defaults | Tune if you have strong prior knowledge |
| `threshold` | 0.5 | Run-length posterior mass at 0 above which we declare a change |

**Citation:** Adams & MacKay (2007).

## Roadmap — remaining methods

Stream 1.2 in the 12-month plan calls for 5-8 methods. The three above
shipped; remaining candidates for future sessions:

| Method | Status | Why deferred |
|---|---|---|
| Empirical Mode Decomposition (PyEMD) | Q1 next | Adds PyEMD dependency — needs runtime-dep review |
| Robust PCA | Q1 next | Multiple library options (Implementations: alibi-detect, custom). Design choice deferred |
| Bayesian Structural Time Series | Q2 | Needs Stan binding or pmdarima — heavier dependency |

Each lands as its own self-contained file under
`fantasyai/aurora/math/methods/`, follows the same contract (skip
cleanly, emit Aurora finding shape, cite references), and gets a
matching test class in `tests/test_new_methods.py`.

## See also

- [docs/methods.md](methods.md) — full method library + when to use each
- [docs/concepts.md](concepts.md) — Aurora's glass-box principle
- `fantasyai/aurora/math/methods/__init__.py` — module surface

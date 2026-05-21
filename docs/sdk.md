# Aurora SDK Reference

The Python SDK is the canonical way to embed Aurora in your own code. It wraps the analytical pipeline and emits Aurora Bundles — portable, signable, citeable analytical artifacts.

## Install

```bash
pip install -r requirements.txt           # from the cloned repo
# Optional: enable Ed25519 signing
pip install cryptography
```

The SDK lives in the `aurora_sdk/` directory. When Aurora is published to PyPI (v1.2), this becomes `pip install aurora-qie`.

## Quick example

```python
import aurora_sdk as aurora

r = aurora.run("data.csv", depth="standard")

# Findings
r.findings.critical()                      # → list[Finding]
r.findings.by_method("iso-forest")
r.findings.where(severity=("crit", "warn"))

# Forecast
r.forecast.peak(horizon_hours=24)

# System model
r.system_model.entities()
r.system_model.confidence

# Save / verify
r.bundle.save("audit.aurora.json")
b = aurora.Bundle.load("audit.aurora.json")
b.verify()                                  # raises if tampered
```

## Public API

### `aurora.run(path, *, depth='auto', seed=None, output_root=None, rebuild=False) → RunResult`

End-to-end entrypoint. `path` accepts:

- A CSV / TSV / JSON / JSONL / Parquet / XLSX file → invokes the pipeline
- An existing Aurora run_dir → just builds state + bundle
- An existing `.aurora.json` bundle → loads it directly

| Param | Type | Default | Description |
|---|---|---|---|
| `path` | str / Path | — | dataset, run_dir, or bundle path |
| `depth` | str | `'auto'` | `'auto'` \| `'quick'` \| `'standard'` \| `'full'` |
| `seed` | str | None | focus seed; sorts findings around this question |
| `output_root` | Path | None | where new run_dirs are created (default: `outputs/aurora_dataset_runs`) |
| `rebuild` | bool | False | force re-build of state from a run_dir |

Returns a `RunResult` exposing `state`, `bundle`, and helper views.

### `class RunResult`

| Attribute | Type | Description |
|---|---|---|
| `state` | dict | Raw state dict from `state_builder.build_state` |
| `bundle` | `Bundle` | Aurora Bundle wrapper |
| `run_dir` | Path \| None | Run directory on disk |
| `findings` | `Findings` | Lazy view; equivalent to `bundle.findings()` |
| `forecast` | `ForecastView` | Lazy view; equivalent to `bundle.forecast()` |
| `system_model` | `SystemModelView` | Lazy view |
| **`methods`** | **`MethodsView`** | **Typed accessors for the 12 analytical methods (v0.10.1) — see below** |
| `fabricated_count` | int | Always 0 by Aurora's contract |
| `confidence` | float \| None | System-model confidence |

### `class Bundle`

Wrapper around a bundle dict. Round-trip with `save()` / `load()`; tamper detection with `verify()`; signing with `sign()`.

| Method | Description |
|---|---|
| `Bundle.from_state(state, *, run_dir=None, dataset_path=None)` | Build from raw state |
| `Bundle.load(path)` | Load from disk; validates schema + format marker |
| `Bundle(doc)` | Wrap an existing dict |
| `.save(path, *, indent=2) → Path` | Write as JSON |
| `.verify(*, require_signature=False) → True` | Check integrity hash + optional sig |
| `.sign(private_key_bytes: bytes) → self` | Ed25519-sign; requires `cryptography` |
| `.findings() → Findings` | Lazy view |
| `.forecast() → ForecastView` | Lazy view |
| `.system_model() → SystemModelView` | Lazy view |

Convenience properties: `.fabricated_count`, `.run_id`, `.confidence`.

### `class Findings`

Iterable + chainable filter view over the bundle's findings list. Every method returns a new `Findings` (immutable-style).

| Method | Description |
|---|---|
| `critical()` | Severity ∈ {`crit`, `critical`} |
| `warnings()` | Severity ∈ {`warn`, `warning`} |
| `informational()` | Severity ∈ {`info`, `informational`} |
| `by_method(name)` | Case-insensitive substring match on `method` |
| `where(**kw)` | Exact-match filter; values may be tuples |
| `top(n)` | First N items |
| `count_by_severity() → dict` | Aggregate |
| `methods() → list[str]` | Distinct method names in first-appearance order |
| `to_list() → list[dict]` | Plain list copy |

Each "finding" is a plain `dict` — pick the keys you need without learning a custom dataclass.

### `class ForecastView`

| Property | Description |
|---|---|
| `.available` | True iff a forecast was produced |
| `.target` | Target column name |
| `.method` | `'AR(1)'`, `'AR(2)'`, etc. |
| `.horizon` | Number of forecast steps |

| Method | Description |
|---|---|
| `.points() → list[dict]` | All forecast points |
| `.peak(horizon_hours=None) → dict \| None` | Max-value point (within horizon if given) |

### `class SystemModelView`

| Property | Description |
|---|---|
| `.confidence` | System-model confidence (0–1) |
| `.template_id` | `'climate_buoy'`, `'industrial'`, or `'(none)'` |

| Method | Description |
|---|---|
| `.entities() → list[dict]` | Topology entities |
| `.entity(entity_id) → dict \| None` | Lookup |
| `.relationships() → list[dict]` | Topology edges |
| `.phase_space() → dict \| None` | Phase-space projection |

### `class MethodsView` *(v0.10.1)*

Typed accessors for the analytical methods that fire on every Aurora run. Each accessor returns a frozen dataclass when the method fit successfully on the dataset, or `None` when the method skipped (data shape didn't qualify) or failed.

Access via `result.methods` — never instantiate `MethodsView` directly.

```python
r = aurora.run("btc_1h.csv")

if var := r.methods.var():
    print(f"VAR({var.chosen_lag}) on {var.n_vars} vars")
    if var.top_coupling:
        print(f"  strongest: {var.top_coupling.from_col} → "
              f"{var.top_coupling.to_col}  β = {var.top_coupling.coefficient_lag1}")

if mp := r.methods.matrix_profile():
    print(f"matrix profile window = {mp.window}, "
          f"{len(mp.motifs)} motifs, {len(mp.discords)} discords")
    for d in mp.discords[:3]:
        print(f"  discord at row {d.start} (distance {d.distance:.3f})")

if kf := r.methods.kalman():
    print(f"kalman noise-reduction {kf.noise_reduction_fraction:.0%}")
```

#### The 12 first-class accessors

| Accessor | Returns | When it fires |
|---|---|---|
| `.var()` | `VarFit \| None` | ≥2 numeric columns, ≥30 obs |
| `.dtw()` | `DtwFit \| None` | ≥2 numeric columns |
| `.bocpd()` | `BocpdFit \| None` | univariate target column |
| `.robust_pca()` | `RobustPcaFit \| None` | ≥2 numeric cols, ≥20 obs |
| `.emd()` | `EmdFit \| None` | univariate target |
| `.kalman()` | `KalmanFit \| None` | univariate target |
| `.spectral_entropy()` | `SpectralEntropyFit \| None` | univariate target, ≥50 obs |
| **`.matrix_profile()`** | `MatrixProfileFit \| None` | univariate target, ≥30 obs (newly wired in v0.10.1) |
| `.granger()` | `GrangerFit \| None` | ≥2 time series, ≥30 obs |
| `.mutual_info()` | `MutualInfoFit \| None` | ≥2 numeric cols |
| `.hmm()` | `HmmFit \| None` | univariate target, ≥50 obs |
| `.wavelet()` | `WaveletFit \| None` | univariate target, ≥32 obs |

#### Utility methods

| Method | Returns | Description |
|---|---|---|
| `.list_methods()` | `list[str]` | Every method the bundle has a finding for |
| `.fit_status(name)` | `'fit' \| 'skipped' \| 'failed' \| None` | Per-method status |
| `.evidence(name)` | `dict \| None` | Raw evidence dict — escape hatch for fields not yet typed |
| `.backtest(prices, signal, ...)` | `BacktestResult` | Strategy backtest (see below) |
| `.signal_from_threshold(series, ...)` | `list[int]` | Build positions from per-bar predicates |

#### Returned dataclasses

Each `*Fit` is a `@dataclass(frozen=True)` with a `.to_dict()` for JSON serialization. See `aurora_sdk/methods.py` for the exhaustive shape — abbreviated highlights:

- `VarFit(chosen_lag, n_obs, n_vars, target_cols, top_coupling: CrossCoupling | None, forecast_horizon, forecast_summary)`
- `BocpdFit(target_col, n_obs, threshold, hazard, change_points: list[ChangePoint])`
- `MatrixProfileFit(target_col, n_obs, window, motifs: list[Motif], discords: list[Discord], profile_stats)`
- `KalmanFit(target_col, n_obs, noise_reduction_fraction, forecast_horizon, forecast: list[KalmanForecastStep])`
- `SpectralEntropyFit(target_col, n_obs, global_spectral_entropy, regime_class, window_results, biggest_window_jump)`
- `GrangerFit(pairs: list[GrangerPair])` — each pair carries `verdict ∈ {no_evidence, i_causes_j, j_causes_i, bidirectional}`

#### Worked example — automated quantitative trading engine

The full reason `MethodsView` exists: read structured analytical output from Aurora and use it to drive trading decisions.

```python
import aurora_sdk as aurora
import pandas as pd

# 1. Run Aurora on your OHLCV dataset.
r = aurora.run("data/btc_1h.csv", depth="standard")

# 2. Pull typed signals from the engine.
mp = r.methods.matrix_profile()
bocpd = r.methods.bocpd()
se = r.methods.spectral_entropy()
hmm = r.methods.hmm()

# 3. Build a composite trading signal.
df = pd.read_csv("data/btc_1h.csv")
prices = df["close"].values

# Component A: long when in a high-entropy "trending" regime AND the
# HMM says we're in the middle (positive) state.
def make_signal(p):
    pos = [0] * len(p)
    if not hmm or not se:
        return pos
    # Per-bar regime — we keep it constant since HMM gives one state for the run.
    in_middle = hmm.current_state == 1
    in_trend  = se and se.regime_class in ("moderate", "low_entropy")
    target = 1 if (in_middle and in_trend) else 0
    # Cap risk: drop position 5 bars before / after every BOCPD change-point.
    cp_indices = {c.row_idx for c in (bocpd.change_points if bocpd else [])}
    for i in range(len(p)):
        if any(abs(i - cp) <= 5 for cp in cp_indices):
            pos[i] = 0
        else:
            pos[i] = target
    return pos

# 4. Backtest with realistic costs.
bt = r.methods.backtest(
    prices,
    make_signal(prices),
    initial_capital=10_000,
    commission_per_trade=1.0,    # $1 per fill
    slippage_bps=2.0,            # 2 bps per side
    bars_per_year=252 * 24,      # hourly bars
)

print(f"Trades:        {bt.n_trades}")
print(f"Win rate:      {bt.win_rate:.1%}")
print(f"Total return:  {bt.total_return:.2%}")
print(f"Sharpe:        {bt.sharpe:.2f}" if bt.sharpe else "Sharpe:        n/a")
print(f"Max drawdown:  {bt.max_drawdown_pct:.2%}")

# 5. Save a signed bundle for audit.
r.bundle.save("runs/btc_1h.aurora.json")
```

The `BacktestResult` carries everything a quant workflow needs:

| Field | Type | What it is |
|---|---|---|
| `n_bars`, `n_trades`, `n_wins`, `n_losses`, `win_rate` | counts + ratio | basic accounting |
| `total_return`, `cagr` | float | growth |
| `sharpe`, `sortino` | float \| None | annualised risk-adjusted return |
| `max_drawdown`, `max_drawdown_pct` | float | dollar + fractional worst peak-to-trough |
| `avg_trade_pnl`, `avg_trade_pct` | float \| None | per-trade averages |
| `best_trade`, `worst_trade` | dict \| None | the extreme trades by PnL |
| `equity_curve` | list[float] | per-bar equity values |
| `bar_returns` | list[float] | per-bar log returns |
| `trades` | list[dict] | every closed trade with entry/exit indices + prices + PnL |
| `config` | dict | the parameters used (capital, commission, slippage, bars/year) |

#### Conventions

* Position changes execute at the **next bar's close** (no look-ahead).
* Position is clipped to `{-1, 0, +1}` — long-only / short-only / hedged-flat. Fractional sizing isn't supported in this MVP; for that, wrap your own simulator.
* Commission + slippage are subtracted on entry AND exit of every trade.
* Sharpe / Sortino are annualised via `bars_per_year` (default 252 for daily; pass 252×24 for hourly, etc.).

## Bundle Format v1 reference

```json
{
  "bundle_version": "1.0.0",
  "format": "aurora.bundle",
  "aurora_version": "1.1.0",
  "generated_at": "2026-05-12T15:00:00",
  "run": {
    "run_id": "20260512_150000__data",
    "run_dir": "/path/to/run",
    "started_at": "2026-05-12T15:00:00",
    "completed_at": "2026-05-12T15:01:30",
    "state": "complete",
    "tier": "auto"
  },
  "dataset": {
    "name": "data.csv",
    "rows": 1000,
    "cols": 5,
    "size_mb": 0.04,
    "basename": "data.csv",
    "sha256": "ab12cd34…"
  },
  "structure":   { … },
  "findings":    [
    {
      "claim_id":    "anom-0000",
      "rank":        1,
      "severity":    "crit",
      "method":      "ISO-FOREST + ROBUST-Z",
      "title":       "Confirmed anomaly in vibration_g at row 205",
      "confidence":  1.0,
      "fabricated":  false,
      …
    }
  ],
  "system_model":     { … },
  "synthesis":        { … },
  "forecast":         { … },
  "anomalies":        [ … ],
  "regimes":          [ … ],
  "motifs":           [ … ],
  "causal":           { … },
  "physics":          { … },
  "assumptions":      [ "robust z-score uses Hampel 1974 thresholds", … ],
  "method_registry":  { "ISO-FOREST + ROBUST-Z": {"count": 5, "severities": {"crit": 3, "warn": 2}} },
  "fabricated_count": 0,
  "integrity": {
    "hash_alg":      "sha256",
    "content_hash":  "1c8d…",
    "signature":     null,
    "signature_alg": null,
    "public_key":    null
  }
}
```

## Integrity model

The `content_hash` is SHA-256 over a canonical JSON serialisation of the bundle **excluding** the `integrity` block and `generated_at` field. The canonical form sorts keys, uses no whitespace, and `ensure_ascii=True`. Two bundles from the same logical content produce the same hash.

Tampering with any tracked field (findings, system_model, synthesis, dataset metadata, assumptions, method_registry, fabricated_count) invalidates the hash on `verify()`.

### Signing (optional)

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Generate a key (or load yours from a secrets manager)
priv = Ed25519PrivateKey.generate()
priv_bytes = priv.private_bytes_raw()    # 32 bytes

bundle = aurora.run("data.csv").bundle
bundle.sign(priv_bytes)
bundle.save("signed.aurora.json")

# Verify on the receiving end
b = aurora.Bundle.load("signed.aurora.json")
b.verify(require_signature=True)         # raises on tamper or bad sig
```

The public key is stored inside the bundle's integrity block, so verifiers don't need a separate key file. Decide your custody model: a single project-wide key, per-run keys via HSM, or whatever your security policy mandates.

## Errors

| Exception | When |
|---|---|
| `BundleSchemaError` | Malformed bundle: wrong format marker, unsupported major version, non-dict root |
| `BundleIntegrityError` | `verify()` failed: hash mismatch, missing integrity block, invalid signature |
| `FileNotFoundError` | `Bundle.load()` with a non-existent path |
| `ValueError` | `aurora.run()` with invalid `depth` |

All exceptions carry a clear message. The SDK never silently fails.

## Notebook / Jupyter integration

For notebook use, the SDK works directly — no extra setup. We're shipping a `%aurora` magic + `df.aurora.analyze()` Pandas accessor in v1.2 for ergonomic improvements; in the meantime:

```python
import pandas as pd
import aurora_sdk as aurora

# Write a temp CSV and run Aurora on it.
df = pd.read_csv("messy.csv")
df.to_csv("/tmp/clean.csv", index=False)
r = aurora.run("/tmp/clean.csv", depth="quick")

# Inspect findings in-notebook
pd.DataFrame([f for f in r.findings.to_list()])
```

## Composability

A bundle from one Aurora run can become input to another:

```python
r1 = aurora.run("dataset_a.csv")
r1.bundle.save("a.aurora.json")

# Use a.aurora.json as a prior in a later run (v1.2+):
# r2 = aurora.run("dataset_b.csv", prior_bundle="a.aurora.json")
```

In v1.2 we'll add `prior_bundle=...` to fold prior runs' fitted models into the next run as priors. For now, you can load the bundle's system_model and physics blocks manually.

## Reference

- [aurora_sdk/__init__.py](../aurora_sdk/__init__.py) — Public surface
- [aurora_sdk/bundle.py](../aurora_sdk/bundle.py) — Bundle Format v1 implementation
- [aurora_sdk/findings.py](../aurora_sdk/findings.py) — Helper views
- [aurora_sdk/runner.py](../aurora_sdk/runner.py) — Entry point
- [tests/test_aurora_sdk.py](../tests/test_aurora_sdk.py) — 28 tests covering roundtrip + tamper detection + filters

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

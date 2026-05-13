# Aurora pre-launch sprint — AFTER capture (2026-05-05)

Synthesised NOAA buoy-style dataset (deterministic seed 20260505),
365 days hourly, 6 columns. Three engineered storm windows produce
clusters of anomalies; a mid-year regime shift in wave_height_m
exercises the SeedRanker.

## Reproduce

```
python bin/capture_demo_2026_05_05.py --out docs/captures/2026-05-05/after
```

## What this capture proves

| Atom | Acceptance signal in `api_state.json` |
| ---- | --------------------------------------- |
| 1.1  | `state.anomalies` length = 47 (was capped at 10) |
| 1.1  | findings carry `n_total_at_this_severity` |
| 2.1  | `state.system_model.template_id` resolves to a domain |
| 3.2  | `state.run_meta.seed_text` field exists (null when no seed) |
| Phase A | `state.anomalies[*].referent.primary_label` populated |
| Phase B | `state.findings[*].narrative.template_id` populated |
| Hardening | response is JSON-clean (no numpy / NaN) |
| Hardening | `state.run_meta` present |

## Files

- `data/noaa_buoy_2024.csv` — synthetic input (8,760 rows × 6 cols)
- `run/` — staged runner artifacts
- `api_state.json` — what `/api/state` returns
- `api_run.json` — what `/api/run` returns on async kickoff

## Stats from this run

- Anomalies surfaced: **47** (NOT capped at 10 — Atom 1.1)
- Findings synthesised: **9**
- Motifs surfaced: **8**
- Detected domain template: **enviro**
- run_meta present: **True**

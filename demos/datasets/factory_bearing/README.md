# Demo dataset — `factory_bearing`

**Source:** `data/fixtures/factory_bearing_demo.csv` (ships with Aurora).

**Shape:** 1000 rows × 5 columns.

| Column | Meaning |
|---|---|
| `timestamp_s` | Seconds since start. |
| `vibration_g` | Accelerometer reading (g). |
| `motor_temp_c` | Motor casing temperature (°C). |
| `rpm` | Rotor RPM. |
| `bearing_load_kn` | Bearing load (kN). |

## Known anomaly (the hero finding)

- **Window:** approximately rows 700 – 900 (`timestamp_s` 70 – 90).
- **What happens:** `vibration_g` ramps from baseline (~0.1) into a
  clear amplitude growth (>1.0 by the end), with `motor_temp_c`
  drifting upward in lockstep. Classic bearing-failure precursor.
- **Expected Aurora finding:** a CRITICAL anomaly via Hampel z-score
  on `vibration_g` around the steep ramp, plus an HMM regime
  transition. The Granger panel should also flag
  `vibration_g → motor_temp_c` directional.

## Use in demos

- **Demo 1 — Aurora Alarm:** stream this dataset at 50–100× speed.
  When the contract trips on `findings.crit_count >= 1` the smart
  plug / LED reacts on camera.
- **Demo 3 — The Save:** same dataset, slower speed, narrate the
  "months in 20s" framing. Slack ping is the climax.

## Replay command

```powershell
python -m demos.replay data\fixtures\factory_bearing_demo.csv `
  --to demos\_live\bearing.csv `
  --speed 50 `
  --start-rows 200
```

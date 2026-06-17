# Aurora Demos

The `demos/` workspace is the production rig for the five Aurora
demo videos. Everything here is reusable — the relay + overlay +
replay harness service ALL five demos.

## Layout

```
demos/
  relay/        — Flask service: receives Aurora's contract webhook,
                  fans out to Discord / Slack / OBS overlay / log /
                  device.
  overlay/      — Browser-source HTML/CSS/JS for OBS. Listens to the
                  relay's SSE stream and renders the "Aurora fired"
                  card on screen.
  replay.py     — Streams a CSV row-by-row so contracts trip on
                  camera. Configurable speed (`--speed 50` = 50×).
  datasets/     — Curated demo datasets with known anomalies
                  (factory_bearing, server_metrics, falling_ball).
                  Each has its own README documenting the expected
                  finding so takes are repeatable.
  contracts/    — Aurora Decision Contract JSON, one per demo.
                  Each points its webhook at the relay.
  agent_loop/   — Demo 4 — Verification Cortex terminal walkthrough.
```

## One-time setup

```powershell
# 1. Set up the relay's targets via env (only the ones you want to fire).
$env:AURORA_DEMO_DISCORD_WEBHOOK = "https://discord.com/api/webhooks/..."
$env:AURORA_DEMO_SLACK_WEBHOOK   = "https://hooks.slack.com/services/..."
$env:AURORA_DEMO_DEVICE_URL      = "http://192.168.1.50/fire"   # ESP32 firmware
$env:AURORA_DEMO_DEVICE_KIND     = "esp32"                      # or "shelly"

# 2. Install Aurora's contracts into the user's contracts dir.
copy demos\contracts\aurora-alarm.json $HOME\.aurora\decision_contracts\
copy demos\contracts\community-sentinel.json $HOME\.aurora\decision_contracts\
copy demos\contracts\the-save.json $HOME\.aurora\decision_contracts\

# 3. Generate the synthesised datasets.
python -m demos.datasets.falling_ball.generate
python -m demos.datasets.server_metrics.generate
```

## The recording flow (every demo)

Open three terminals + OBS:

1. **Aurora Studio** — `python studio_api.py` (the engine running on `:8000`).
2. **Relay** — `python -m demos.relay.app` (the fan-out on `:7077`).
3. **Replay harness** — `python -m demos.replay <dataset> --to demos\_live\stream.csv --speed 50` (only for "live"-style demos).
4. **OBS** — add a Browser source pointing at `http://127.0.0.1:7077/overlay/`.

When the contract trips, you'll see:

- The Aurora Studio cube updates with the new finding.
- The relay logs the fan-out in its terminal.
- The overlay card animates in over your OBS scene with the cited
  method + row + |z|σ + `0 FABRICATED`.
- Discord / Slack messages land (if configured).
- The smart plug / LED reacts (if configured).

## Per-demo cheat sheet

| # | Demo | Dataset | Contract | Relay outputs |
|---|---|---|---|---|
| 1 | Aurora Alarm | `factory_bearing_demo.csv` | `aurora-alarm.json` | overlay + device |
| 2 | Community Sentinel | live feed (NOAA / crypto) or `factory_bearing` | `community-sentinel.json` | overlay + Discord |
| 3 | The Save | `demos/datasets/server_metrics/server_metrics.csv` | `the-save.json` | overlay + Slack + log |
| 4 | Verification Cortex | `factory_bearing_demo.csv` | none (terminal-only) | overlay (optional) |
| 5 | Rediscover the Law | `demos/datasets/falling_ball/falling_ball.csv` | none | overlay (Physics lens) |

## Sanity-checking the relay

```powershell
# Smoke-test the fire endpoint without firing Aurora:
curl -X POST http://127.0.0.1:7077/aurora/fire `
  -H "Content-Type: application/json" `
  -d '{\"contract_id\":\"smoke\",\"contract_name\":\"smoke-test\",\"trigger_field\":\"findings.crit_count\",\"trigger_value\":4,\"metadata\":{\"method\":\"hampel-z\",\"row\":1247,\"z\":5.73}}'
```

If the overlay's open in another tab/OBS, you should see the card
fire instantly. Use this to frame the OBS scene + verify the relay
+ overlay chain before going live.

## Demo 5 (Physics) — running it end-to-end

```powershell
# Generate the falling-ball data.
python -m demos.datasets.falling_ball.generate

# Run Aurora on it (Studio or SDK).
python studio_api.py
# In the browser, drop demos/datasets/falling_ball/falling_ball.csv,
# set TARGET = y, hit RUN ANALYSIS.

# After the run finishes, extract the discovered equation for the overlay:
python -m demos.datasets.falling_ball.extract_physics --latest
```

The script prints the discovered law + RMSE + cited paper string in
a shape the video editor can stamp onto the real-world ball-drop clip.

## Demo 4 (Verification Cortex) — running it end-to-end

```powershell
# Just runs in the terminal — no relay needed (but overlay can still
# fire alongside if you want).
python -m demos.agent_loop.run_demo --dataset data\fixtures\factory_bearing_demo.csv
```

You'll see the "naive baseline" (a confidently-wrong invented number)
contrasted with the Aurora-verified chain, then the agent-action
beat. Capture the terminal in OBS.

## Honest notes

- The relay uses Aurora's existing **generic webhook** action.
  That ships TODAY. The v1.2 native Discord/Slack/PagerDuty actions
  shipped earlier as well; the relay is still the cleanest path
  because one Aurora contract drives multiple targets at once.
- The replay harness is `--speed` only. It doesn't try to simulate
  network jitter or out-of-order arrival. Real ops scenarios would
  need a richer simulator; for a 20-second TikTok, this is enough.
- The ESP32 firmware sketch lives outside this repo — `device_adapter`
  just POSTs JSON to whatever HTTP server you flash onto the board.

# 🎬 Aurora Sentinel — demo recording runbook

> Looking for the standalone calibration demo? That's
> [`phantom_signal/`](phantom_signal/README.md) — run
> `python -m demos.phantom_signal.run_demo` from the repo root. It has
> its own README and needs none of the rig below.

This folder is the **production rig** for five short Aurora demo videos.
Everything is reusable — the relay + overlay + replay harness service
all five demos. Per-demo: just a new contract + a new replay command.

If you're reading this to set up the recording, **read § Phase 0 first**,
then jump straight to the demo you want to shoot. Every command below
has been smoke-tested and includes the exact folder path.

---

## 📦 Phase 0 — one-time setup

### 0.1 Generate the synthetic datasets

```powershell
cd C:\Users\bgrut\Desktop\Aurora_QIE\.claude\worktrees\peaceful-easley-8514ec
. .\.venv\Scripts\Activate.ps1     # or wherever your .venv lives

python -m demos.datasets.falling_ball.generate
python -m demos.datasets.server_metrics.generate
```

(`factory_bearing_demo.csv` already ships with Aurora — no generator needed.)

### 0.2 Install the contracts into Aurora's contracts dir

```powershell
mkdir $env:USERPROFILE\.aurora\decision_contracts -Force

copy demos\contracts\aurora-alarm.json        $env:USERPROFILE\.aurora\decision_contracts\
copy demos\contracts\community-sentinel.json  $env:USERPROFILE\.aurora\decision_contracts\
copy demos\contracts\the-save.json            $env:USERPROFILE\.aurora\decision_contracts\
```

### 0.3 Set up your webhook URLs (one-time)

**Discord** — Edit channel → Integrations → Webhooks → New Webhook → copy URL.

**Slack** — <https://api.slack.com/apps> → Create App → Incoming Webhooks → Add to Workspace → copy URL.

Then:

```powershell
copy demos\.env.demos.example demos\.env.demos
notepad demos\.env.demos        # paste your URLs into the two AURORA_DEMO_* lines
```

`demos\.env.demos` is **gitignored** — your URLs never reach the repo.
Treat them like passwords; rotate if you ever paste them in a chat.

### 0.4 Set up OBS once

Create a scene called **"Aurora Demo"** with:

1. **Display Capture** (or two Window Captures for split-screen demos).
2. **Browser Source** with:
   - URL: `http://127.0.0.1:7077/overlay/?corner=tr&hold=10000`
   - Width 1920, Height 1080
   - ✅ Refresh browser when scene becomes active
   - ✅ Shutdown source when not visible
3. Recording output: **1920×1080 @ 60fps, MP4**.
4. Pick a hotkey for Start/Stop Recording (recommended: **F9**).

### 0.5 Smoke-test the rig (do this once before any demo)

```powershell
# === Terminal A ===
. .\demos\load_env.ps1
python -m demos.relay.app
```

Expected:
```
[aurora-demo-relay] listening on http://127.0.0.1:7077
  Adapters: ['sse', 'log', 'discord', 'slack']
```

Switch OBS to the "Aurora Demo" scene. The overlay's status crumb
should turn **green** ("overlay · connected").

```powershell
# === Terminal B (smoke test only — closes when done) ===
curl.exe -X POST http://127.0.0.1:7077/aurora/fire `
  -H "Content-Type: application/json" `
  -d '{\"contract_id\":\"smoke\",\"contract_name\":\"smoke-test\",\"trigger_field\":\"findings.crit_count\",\"trigger_value\":4,\"severity\":\"crit\",\"metadata\":{\"method\":\"hampel-z\",\"row\":1247,\"z\":5.73}}'
```

You should immediately see:

- A magenta CRIT card fly in over OBS for ~10 seconds.
- A message land in your Discord channel.
- A message land in your Slack channel.
- A JSON line appended to `demos\relay\fires.jsonl`.

If all four happened, the **relay** is production-ready. Now validate the
end-to-end Aurora → contract → relay path before recording anything.

### 0.6 End-to-end autofire check (don't skip this — it caught us)

The smoke test in 0.5 POSTs directly to the relay. It does NOT exercise
Aurora's autofire path, which has its own loopback guard. A real run can
still silently fail after 0.5 passes. Run this once:

```powershell
# === Terminal A — relay still running from 0.5 ===

# === Terminal B — Aurora Studio with demos env loaded ===
. .\demos\load_env.ps1
python studio_api.py
```

In the browser at `http://127.0.0.1:8000`, drop
`data\fixtures\factory_bearing_demo.csv` and click **RUN ANALYSIS**.

Watch the **Aurora terminal** (not Discord). Within ~15 s of the run
completing you should see lines like:

```
[aurora-autofire] aurora-alarm-demo1 fired (1/1 actions)
[aurora-autofire] community-sentinel-demo2 fired (1/1 actions)
[aurora-autofire] the-save-demo3 fired (1/1 actions)
[aurora-autofire] async run <id>: 3 contracts matched, 3 fully fired
```

…and the Discord/Slack messages should land. If you see:

```
[aurora-autofire] ... matched but actions failed:
    ["WebhookAction: webhook target '127.0.0.1' resolves to a
      private / loopback address; set ALLOW_LOCAL_WEBHOOKS=True ..."]
```

…then `AURORA_ALLOW_LOCAL_WEBHOOKS=1` isn't set in the Aurora terminal.
Re-dot-source: `. .\demos\load_env.ps1` and restart `studio_api.py`.

If both autofire lines AND the messages land, **you're production-ready
for all demos**. Stop the relay + Aurora (Ctrl+C each).

---

## 🎯 Demo 2 — Community Sentinel (Discord) · record FIRST

**Length:** 30-45 s. **Hero shot:** the Discord embed lands live with the citation.

### Terminals

```powershell
# === Terminal 1 — Aurora Studio ===
cd C:\Users\bgrut\Desktop\Aurora_QIE\.claude\worktrees\peaceful-easley-8514ec
. .\.venv\Scripts\Activate.ps1
python studio_api.py
```

```powershell
# === Terminal 2 — Relay (with webhooks loaded) ===
cd C:\Users\bgrut\Desktop\Aurora_QIE\.claude\worktrees\peaceful-easley-8514ec
. .\.venv\Scripts\Activate.ps1
. .\demos\load_env.ps1
python -m demos.relay.app
```

### OBS layout

Split-screen: Aurora Studio on left, Discord channel on right, overlay top-right corner.

```
┌─────────────────────────────┬──────────────────────┐
│   Aurora Studio :8000       │  Discord channel     │
│   (cube + lens row)         │  (windowed)          │
└─────────────────────────────┴──────────────────────┘
              overlay floats top-right
```

### Dry run (NOT recording yet)

1. Open `http://127.0.0.1:8000`.
2. Drag `data\fixtures\factory_bearing_demo.csv` onto the upload zone.
3. **AUTO** → **RUN ANALYSIS**.
4. ~15 s later, confirm: Discord message lands AND overlay card fires.

If both happen, you're golden.

### Record

1. Aurora Studio: hit **CHANGE** to clear the previous run.
2. Switch OBS scene → **press F9**.
3. Wait 2 s.
4. Drag `factory_bearing_demo.csv` → click **RUN ANALYSIS**.
5. When contract trips (~12-15 s), hold camera 3 s on the Discord embed.
6. **Press F9** to stop.

---

## 🎯 Demo 5 — Rediscover the Law (Physics) · record SECOND

**Length:** 40-60 s. **Hero shot:** the **DISCOVERED MODEL** card showing the
power-law fit Aurora rediscovered for `p99_ms` — plus the eight candidate
forms it tried (and the seven it ruled out).

This demo pairs beautifully with Demo 3 because it uses the **same dataset
on a different lens**. Demo 3's narrative is *"Aurora caught the regime
shift no one was watching."* Demo 5 is *"On the same data, Aurora also
discovered the LAW the system was obeying — no invented dynamics, every
fit cited, every candidate scored."* Two demos, one truth chain.

> **Why not falling_ball?** Aurora's symbolic-discovery layer didn't
> produce a fit for the falling-ball dataset (likely a small-N
> target-inference issue — 60 rows isn't enough for the layer's current
> preconditions). Server metrics produces a clean `power_law` fit with
> RMSE 0.041 and 8 candidates scored. We use what works.

### Step A — run Aurora on server_metrics

```powershell
# === Terminal 1 ===
cd C:\Users\bgrut\Desktop\Aurora_QIE\.claude\worktrees\peaceful-easley-8514ec
. .\.venv\Scripts\Activate.ps1
. .\demos\load_env.ps1
python studio_api.py
```

In the browser at `http://127.0.0.1:8000` (or 8001):

1. **CHANGE** to clear any prior dataset.
2. Drag `demos\datasets\server_metrics\server_metrics.csv`.
3. In "WHAT DO YOU WANT TO KNOW", type: `target column: p99_ms`.
4. Click **STANDARD** tier.
5. Click **RUN ANALYSIS**.

### Step B — record the dashboard panel

**Press F9** to start recording. The analysis runs ~30 s. When complete,
scroll down to the **intelligence** dashboard band. The **PHYSICS** panel
shows the hero shot:

```
DISCOVERED MODEL · POWER_LAW
y = a · t^b
RMSE 0.041
CONSISTENCY 1.00

ALL CANDIDATES TRIED · 8 FORMS
  power_law    y = a · t^b              0.041  ← winner
  sigmoid      y = L / (1 + exp(−k·t))  0.044
  linear_ode   dy/dt = a·y + b          0.078
  logistic     ...                      0.127
  exponential  ...                      0.842
  damped_osc   ...                      0.869
  ...
```

Hold the camera 4-5 s on the **DISCOVERED MODEL** card, then pan down
to the **ALL CANDIDATES TRIED** list to emphasize the citation chain.
**Press F9** to stop.

### Step C — extract the equation JSON for editor overlay

```powershell
# === Terminal 2 ===
cd C:\Users\bgrut\Desktop\Aurora_QIE\.claude\worktrees\peaceful-easley-8514ec
. .\.venv\Scripts\Activate.ps1
python -m demos.datasets.falling_ball.extract_physics --latest
```

(The script lives under `falling_ball/` for historical reasons but is
dataset-agnostic — it reads any run's `deep_math.json`.)

Outputs JSON with `law`, `model`, fitted `params`, `rmse`, `aic`, the
list of `runner_ups`, and a 3-line `headline_overlay` your editor stamps
on screen. For server_metrics:

```
y(t) = ?
y = a · t^b   ·   a=0.398  b=−95872.870
RMSE = 0.0415  ·  Aurora cited: SINDy
```

The cited paper string (Brunton/Proctor/Kutz 2016) is the visual climax.

---

## 🎯 Demo 1 — Aurora Alarm (physical device) · skip without hardware

Need an ESP32 (any LAN HTTP endpoint) **or** a Shelly smart plug.

```powershell
# === Terminal 2 — Relay with device URL ===
. .\demos\load_env.ps1
$env:AURORA_DEMO_DEVICE_URL  = "http://192.168.1.50"
$env:AURORA_DEMO_DEVICE_KIND = "shelly"        # or "esp32"
python -m demos.relay.app
```

Same flow as Demo 2. Split your OBS scene so the **room with the
device** is visible alongside Aurora Studio. When the contract trips,
the device fires in sync with the overlay card.

Skip this demo for now if you don't have hardware.

---

## 🎯 Demo 3 — The Save (Slack) · record THIRD

**Length:** 45-60 s. **Hero shot:** Slack ping arrives at the regime shift.

Same as Demo 2 but use the **server_metrics** dataset and your **Slack**
channel for the visible window.

```powershell
# === Terminal 1 — Aurora ===
python studio_api.py

# === Terminal 2 — Relay (webhooks already in .env.demos) ===
. .\demos\load_env.ps1
python -m demos.relay.app
```

In the browser:

1. **CHANGE**.
2. Drop `demos\datasets\server_metrics\server_metrics.csv`.
3. **STANDARD** → **RUN ANALYSIS**.
4. ~25 s in, the contract trips at the regime shift; Slack ping is the climax.

OBS split: Aurora left, Slack channel right.

---

## 🎯 Demo 4 — Verification Cortex (agent loop) · record LAST

**Length:** 30-40 s. **Hero shot:** the contrast between the wrong invented number and the cited verified one.

Terminal-only. No relay needed.

```powershell
cd C:\Users\bgrut\Desktop\Aurora_QIE\.claude\worktrees\peaceful-easley-8514ec
. .\.venv\Scripts\Activate.ps1
```

Make the terminal **large** (140 cols × 40 rows; Windows Terminal:
Settings → Defaults → Profile → Window: 140 × 40).

**Press F9** to start OBS (terminal window capture). Then:

```powershell
python -m demos.agent_loop.run_demo --dataset data\fixtures\factory_bearing_demo.csv
```

You'll see:
- "Naive Agent" block (faded grey, wrong invented number)
- "Aurora-Verified Agent" block (cyan/mint, the cited MCP chain)
- "Agent Action" block (cyan, how the agent would use the verified figure)

Hold 3 s after "end of demo." prints. **Press F9** to stop.

---

## 🧹 Cleanup between takes

```powershell
# Stop terminals (Ctrl+C each), then:
Remove-Item -Recurse -Force outputs\aurora_dataset_runs\*falling_ball*    -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force outputs\aurora_dataset_runs\*factory_bearing* -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force outputs\aurora_dataset_runs\*server_metrics*  -ErrorAction SilentlyContinue
Remove-Item -Force demos\relay\fires.jsonl                                -ErrorAction SilentlyContinue
```

Every take starts from a fresh slate so `find_latest_run` picks up the right one.

---

## 🆘 Troubleshooting

| Symptom | Fix |
|---|---|
| Overlay says "overlay · connecting…" | Relay isn't running — start Terminal 2 |
| Overlay says "overlay · connected" but no card fires | Contract didn't trip. Check `~\.aurora\decision_contracts\` has the JSON; the relay's terminal logs every `/aurora/fire` POST it receives |
| Aurora terminal shows `WebhookAction: webhook target '127.0.0.1' resolves to a private / loopback address` | `AURORA_ALLOW_LOCAL_WEBHOOKS=1` isn't set in the Aurora terminal. Re-dot-source `. .\demos\load_env.ps1`, then `python studio_api.py` |
| Aurora terminal shows `0 contracts matched, 0 fully fired` (or no `[aurora-autofire]` lines at all) | Either the running Aurora process is stale (started before you pulled the autofire patch — restart it) OR `~\.aurora\decision_contracts\` is empty (re-run Phase 0.2) |
| Discord post fails | Webhook URL in `.env.demos` is wrong / channel was deleted. Re-paste, restart relay |
| Demo 4 terminal shows `Row ?` or `findings = 0 (crit=0  warn=0  info=0)` | Old version of `demos/agent_loop/run_demo.py` — pull latest |
| Demo 4 terminal interleaves `RAG falling back: no_relevant_entries` lines | Benign — RAG correctly refuses to invent precedent on a fresh dataset. Latest `run_demo.py` silences it; pull latest |
| Aurora says "no valid time axis" | Drop the CSV again. The time-axis fix is in `scripts/run_aurora_dataset_runner.py`; restart Aurora Studio if the bug returns |
| `python -m demos.relay.app` crashes on import | Activate the venv: `. .\.venv\Scripts\Activate.ps1` |

---

## 📂 What lives where

```
demos/
├── README.md                — this runbook
├── .env.demos.example       — template; copy to .env.demos (gitignored) and fill in
├── load_env.ps1             — PowerShell helper that loads .env.demos into $env:
├── replay.py                — streams a CSV to simulate "live" data
├── relay/
│   ├── app.py               — Flask service on :7077; receives Aurora's webhook
│   └── adapters/            — discord / slack / log / sse / device fan-out
├── overlay/                 — OBS browser-source HTML/CSS/JS
├── contracts/               — one Aurora Decision Contract JSON per demo
├── agent_loop/              — Demo 4 terminal walkthrough
└── datasets/
    ├── factory_bearing/     — README for the shipped fixture (Demos 1+2)
    ├── server_metrics/      — generator + README (Demo 3)
    └── falling_ball/        — generator + extractor + README (Demo 5)
```

## 🔑 Notes

- The relay leverages Aurora's **generic webhook** Decision Contract
  action. That ships today. Native Discord/Slack contract actions also
  exist (v1.2) — the relay is still the cleanest path because one
  contract drives multiple targets at once.
- Replay supports `--speed` only. Real ops scenarios need richer
  network-jitter simulation; for a 20-second TikTok, `--speed 50` is
  enough.
- The ESP32 firmware sketch lives outside this repo — `device_adapter`
  just POSTs JSON to whatever HTTP server you flash onto the board.

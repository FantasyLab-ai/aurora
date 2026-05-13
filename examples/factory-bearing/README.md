# Example: factory_bearing_demo

This is the canonical Aurora walkthrough — a 1 K-row × 5-column time-series of factory bearing telemetry (vibration, motor temperature, RPM, bearing load) with a planted failure mode you can find without knowing where to look.

## What the dataset looks like

```
timestamp_s,vibration_g,motor_temp_c,rpm,bearing_load_kn
0.0, 0.18, 52.1, 1798, 4.2
5.0, 0.19, 52.3, 1801, 4.3
…
1025.0, 1.52, 71.4, 1722, 5.9    ← anomaly region
1030.0, 1.50, 71.6, 1718, 6.0
…
```

The full dataset ships at `data/fixtures/factory_bearing_demo.csv`. Drop it into the Studio's Start panel, or run it from the SDK.

## Running via the Web Studio

```bash
python studio_api.py
```

Open `http://127.0.0.1:8000`. Click **▶ Try a demo → factory_bearing_demo**. Aurora runs in ~5–10 seconds.

## Running via the SDK

```python
import aurora_sdk as aurora

r = aurora.run("data/fixtures/factory_bearing_demo.csv", depth="standard")

# Headlines
print(f"confidence={r.confidence:.2f}  fabricated={r.fabricated_count}")
print(f"findings: {r.findings.count_by_severity()}")

# The critical anomalies
for f in r.findings.critical():
    print(f"  [{f['method']}] {f['title']}")

# Save for later / share with a collaborator
r.bundle.save("factory_bearing.aurora.json")
```

Expected output (approximate — exact numbers may vary slightly per platform):

```
confidence=0.84  fabricated=0
findings: {'crit': 3, 'warn': 1, 'info': 7}
  [ISO-FOREST + ROBUST-Z] Confirmed anomaly in vibration_g at row 205
  [ISO-FOREST + ROBUST-Z] Confirmed anomaly in vibration_g at row 47
  [ISO-FOREST + ROBUST-Z] Confirmed anomaly in vibration_g at row 138
```

## What to look for in the Studio

After the run completes you should see:

1. **Aurora Pulse** at the top: `🐻 Loaded factory_bearing_demo · 1000 rows over 5s cadence · 3 critical · confidence 84%.`
2. **Run banner** with the **`0 fabricated`** mint chip
3. **Cube** rotating through 6 lenses — click **ANOMALIES** to see the critical rows
4. **Spacetime System Graph** — 4 entities (vibration_g, motor_temp_c, rpm, bearing_load_kn) with worldlines; click any node for the full timeline
5. **Phase Space** view (PHASE SPACE button) — projects onto the two most-variable axes with an attractor centroid
6. **Findings panel** — 11 cards; the 3 critical ones cite ISO-FOREST + ROBUST-Z

## Why this is a good first example

- Small enough to run in seconds
- Real method engagement (anomaly + regime + motif + forecast + physics-prior all fire)
- Cross-sectional fallback is exercised (no template matches "factory_bearing" so Aurora correctly uses the generic numeric-column graph)
- Demonstrates the `0 fabricated` contract in action
- A great target for testing the SDK / MCP / Decision Contracts substrate

## Try it via MCP

If you've configured Aurora MCP in Claude Desktop (see [docs/mcp.md](../../docs/mcp.md)):

> "Run Aurora on `/path/to/factory_bearing_demo.csv` and tell me about the critical anomalies, with citations."

Claude will chain `aurora_analyze → aurora_findings(severity=crit) → aurora_explain(claim_id=...)` and give you a cited response.

## Try it with a Decision Contract

Create `~/.aurora/decision_contracts/bearing-watch.json`:

```json
{
  "id": "bearing-watch",
  "name": "Alert on critical bearing anomalies",
  "trigger": {"field": "findings.crit_count", "op": ">=", "value": 3},
  "actions": [
    {"type": "log", "level": "warn", "message": "bearing critical"},
    {"type": "file", "path": "alerts/bearing.jsonl"}
  ],
  "rate_limit": {"max_per_hour": 12}
}
```

Then:

```python
from aurora_sdk import Bundle
from fantasyai.aurora.decision_contracts import load_contracts, fire_contract

bundle = Bundle.load("factory_bearing.aurora.json").doc
for c in load_contracts():
    rec = fire_contract(c, bundle)
    print(f"{c.id}: {rec.actions_succeeded} actions ran")
```

On this dataset the contract fires (`crit_count == 3`). Check `~/.aurora/contracts_output/alerts/bearing.jsonl` for the audit row.

## Try it as a Research Kit

```python
from fantasyai.aurora.research_kit import write_research_kit
write_research_kit(
    bundle, "./bearing_kit",
    title="Bearing failure precursor detection",
    creators=[{"name": "Your Name", "affiliation": "Your Lab"}],
    keywords=["industrial monitoring", "isolation forest", "bearing failure"],
)
```

Opens up `methods.md`, `references.bib`, `replication.json`, and a `.zenodo.json` ready to upload. See [docs/research-kit.md](../../docs/research-kit.md).

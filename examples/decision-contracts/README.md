# Example: Decision Contracts

Sample contract documents you can copy + adapt. Each is a single JSON file that becomes executable when loaded by the Aurora decision-contracts engine.

## Files

| File | What it fires on | What it does |
|---|---|---|
| [bearing-watch.json](bearing-watch.json) | 3+ critical findings | Webhook + log + file |
| [low-confidence-review.json](low-confidence-review.json) | Confidence below 30% | Webhook for human review queue |
| [forecast-ceiling-breach.json](forecast-ceiling-breach.json) | Forecast peak above operational ceiling | Webhook + log |
| [audit-everything.json](audit-everything.json) | Always (`findings.count >= 0`) | Append JSONL audit row |
| [fabricated-guard.json](fabricated-guard.json) | `fabricated_count > 0` | Critical-level log + webhook |
| [regime-change-detected.json](regime-change-detected.json) | A regime-change method appears in findings | Slack-style webhook |

## How to use

```python
from aurora_sdk import Bundle
from fantasyai.aurora.decision_contracts import (
    Contract, fire_contract, load_contracts
)
import json

# Option A: load all contracts from the default disk location
for c in load_contracts():
    if c.enabled:
        record = fire_contract(c, bundle.doc)
        print(f"{c.id}: actions_run={record.actions_succeeded}, errors={record.errors}")

# Option B: load a specific contract file
with open("examples/decision-contracts/bearing-watch.json") as fh:
    c = Contract.from_dict(json.load(fh))
record = fire_contract(c, Bundle.load("latest.aurora.json").doc)
```

## Where contracts live

The default contracts directory is:

- macOS / Linux: `~/.aurora/decision_contracts/`
- Windows: `%USERPROFILE%\.aurora\decision_contracts\`

Override via `AURORA_CONTRACTS_DIR=/path/to/dir`.

Copy any sample into your contracts directory, edit the URL / threshold / metadata to match your environment, and it's active the next time `load_contracts()` runs.

## Security reminders

- Webhook URLs in these samples point at `https://hooks.example.com/...` — they're placeholders. Replace with your real endpoints before using.
- Private-IP / loopback webhook targets are rejected by default. Set `AURORA_ALLOW_LOCAL_WEBHOOKS=1` only in development.
- `Authorization` headers in samples should be stored in your secrets manager and injected at load time, not committed to source control.
- File actions write under `AURORA_CONTRACTS_OUTPUT` (default `~/.aurora/contracts_output/`). Pre-create this directory with the permissions you want before firing high-volume contracts.

See [docs/decision-contracts.md](../../docs/decision-contracts.md) for the full operator + action reference.

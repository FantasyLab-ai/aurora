# Aurora SDK + MCP + Decision Contracts + Research Kit

**Aurora is the quantitative cortex your code, your agents, and your team can cite without lying.**

Four substrate layers that turn Aurora from a destination into a platform:

| Layer | Where | What |
|------|-------|------|
| **Aurora SDK** | `aurora_sdk/` | Python API + Bundle Format v1 (`.aurora.json`) — a portable, signable, citeable analytical artifact |
| **Aurora MCP** | `aurora_mcp/` | MCP server exposing 7 tools to any LLM agent (Claude Desktop, Claude Code, Cursor, custom agents) |
| **Decision Contracts** | `fantasyai/aurora/decision_contracts/` | Programmable predicates that fire actions (webhook / log / file) when an Aurora bundle satisfies a condition — "Zapier for analytical findings" |
| **Research Kit** | `fantasyai/aurora/research_kit.py` | Publication-ready output: `methods.md` + `references.bib` + `replication.json` + `.zenodo.json` for DOI minting |

## Aurora SDK quick-start

```python
import aurora_sdk as aurora

# Run end-to-end on a CSV
r = aurora.run("data.csv", depth="standard")

# Inspect findings
r.findings.critical()                  # list of crit-severity findings
r.findings.by_method("iso-forest")
r.findings.where(severity=("crit", "warn"))

# Forecast helpers
r.forecast.peak(horizon_hours=24)

# System model
r.system_model.entities()
r.system_model.confidence

# Save the bundle as a portable .aurora.json
r.bundle.save("audit.aurora.json")

# Later — load + verify on any machine with Aurora installed
from aurora_sdk import Bundle
b = Bundle.load("audit.aurora.json")
b.verify()                              # raises if tampered
```

**Integrity:** every bundle includes a SHA-256 content hash. Optional Ed25519 signatures via `b.sign(private_key)` when `cryptography` is installed.

## Aurora MCP quick-start

Install and start the MCP server (stdio transport — Claude Desktop / Claude Code / Cursor compatible):

```bash
pip install mcp
python -m aurora_mcp.server --allow-root ./data --allow-root ./outputs
```

The 7 tools advertised to the client:

| Tool | Description |
|------|-------------|
| `aurora_analyze` | Run Aurora on a dataset; return findings summary or full bundle |
| `aurora_load_bundle` | Load a saved `.aurora.json`; verify integrity hash |
| `aurora_findings` | List findings with severity/method filters |
| `aurora_forecast` | Return forecast points + peak prediction |
| `aurora_explain` | Full evidence + method spec for a specific `claim_id` |
| `aurora_intervene` | Propagate a perturbation through the system model |
| `aurora_simulate` | Forward-step validated dynamics with honest CI pause |

**Security:** path allowlist enforced on every call; output capped at 2 MB; no shell, no eval, no subprocess. All errors wrapped as `{"error", "error_kind"}` — tools never raise across the MCP boundary.

## Decision Contracts quick-start

Define a contract as a JSON document:

```json
{
  "id": "factory-line-3-bearing-watch",
  "name": "Alert on critical bearing anomalies",
  "trigger": {"field": "findings.crit_count", "op": ">=", "value": 3},
  "actions": [
    {"type": "webhook", "url": "https://hooks.example.com/aurora"},
    {"type": "log", "level": "warn", "message": "bearing critical"}
  ],
  "rate_limit": {"max_per_hour": 12}
}
```

Evaluate + fire from Python:

```python
from fantasyai.aurora.decision_contracts import (
    Contract, evaluate_contract, fire_contract, save_contract
)
from aurora_sdk import Bundle

contract = Contract.from_dict(json.loads(open("watch.json").read()))
bundle   = Bundle.load("latest.aurora.json").doc

ok, value = evaluate_contract(contract, bundle)
record    = fire_contract(contract, bundle)
# record.actions_succeeded / record.errors are your audit trail
```

**Security:**
- Webhook URLs validated: `http(s)` only; private / loopback / link-local IPs blocked (set `AURORA_ALLOW_LOCAL_WEBHOOKS=1` to opt in for testing)
- Headers like `Authorization`, `X-API-Key`, `Cookie` redacted in audit records
- File actions sandboxed under `AURORA_CONTRACTS_OUTPUT` (default `~/.aurora/contracts_output/`); traversal blocked
- Rate-limited per contract (`max_per_minute|hour|day`)
- 1 MB webhook body cap, 100 MB file cap, 30 s timeout cap

## Research Kit quick-start

```python
from aurora_sdk import Bundle
from fantasyai.aurora.research_kit import write_research_kit

bundle = Bundle.load("audit.aurora.json").doc
paths = write_research_kit(
    bundle, "./my_paper_kit",
    title="Bearing-failure regime detection at Factory Line 3",
    creators=[{"name": "Smith, Alice", "affiliation": "Acme Labs"}],
    keywords=["industrial monitoring", "isolation forest", "regime change"],
)
# paths.methods    → methods.md (LaTeX-ready Markdown)
# paths.bib        → references.bib (BibTeX, one entry per cited prior)
# paths.replication → replication.json (deterministic re-run config)
# paths.zenodo     → .zenodo.json (mint a DOI on Zenodo)
```

## Testing

```bash
# 103 tests across the 4 new substrate layers
pytest tests/test_aurora_sdk.py tests/test_aurora_mcp.py \
       tests/test_decision_contracts.py tests/test_research_kit.py

# Full sweep (substrate + existing 218 baseline)
pytest tests/
```

## Versioning

- Aurora Bundle Format: **1.0.0** (breaking changes bump the major)
- Aurora SDK: **1.0.0**
- Aurora MCP: **1.0.0**
- Aurora Decision Contracts: schema **v1**

Bundles produced by future major versions will refuse to load in older SDKs by design — bundle format stability is a hard contract.

## Why this matters

Aurora's findings are no longer trapped inside a UI. Indie devs, researchers, small organisations, and AI agents can now embed Aurora's glass-box quantitative reasoning into:

- **Their own code** — `pip install` away from a Jupyter notebook
- **LLM agents** — every agent can call Aurora as a tool, get cited findings back, and never have to hallucinate a number
- **Automation pipelines** — Decision Contracts turn findings into webhooks, logs, files
- **Published papers** — Research Kit produces journal-grade methods + BibTeX + Zenodo metadata in one call

The pipeline that produces the findings is unchanged. The mathematical guarantees are unchanged. What's new is **the surface area for the world to plug in.**

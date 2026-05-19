# Decision Contracts

Decision Contracts turn Aurora findings into automation. A contract is a JSON document that says "when this condition holds in an Aurora bundle, fire these actions." This is **Zapier for analytical findings** — a substrate that lets your existing systems depend on Aurora's outputs.

## Anatomy of a contract

```json
{
  "id": "factory-line-3-bearing-watch",
  "name": "Alert on critical bearing anomalies",
  "description": "Fires when any Aurora run produces 3+ critical anomalies.",
  "enabled": true,
  "trigger": {
    "field": "findings.crit_count",
    "op": ">=",
    "value": 3
  },
  "actions": [
    {"type": "webhook", "url": "https://hooks.example.com/aurora"},
    {"type": "log", "level": "warn", "message": "bearing critical"}
  ],
  "rate_limit": {"max_per_hour": 12},
  "metadata": {
    "owner": "ops@example.com",
    "runbook": "https://wiki.example.com/aurora-bearing-watch"
  }
}
```

| Field | Required | Description |
|---|---|---|
| `id` | yes | Stable identifier; `[a-zA-Z0-9_-.]{1,128}` |
| `name` | yes | Human-readable title |
| `description` | no | Free-text explanation |
| `enabled` | no (default `true`) | Disable without deleting |
| `trigger` | yes | Predicate (see below) |
| `actions` | no (default `[]`) | What to do when the trigger fires |
| `rate_limit` | no | Max firings per window |
| `metadata` | no | Free-form annotations |

## Triggers — the predicate language

A trigger is a single predicate expression:

```json
{ "field": "<dotted path>", "op": "<operator>", "value": <literal> }
```

### Supported operators

| Op | Semantics | Example value type |
|---|---|---|
| `==` | Equal | scalar |
| `!=` | Not equal | scalar |
| `>` | Greater than | number |
| `>=` | Greater than or equal | number |
| `<` | Less than | number |
| `<=` | Less than or equal | number |
| `in` | LHS appears in RHS | list / tuple / string |
| `not_in` | LHS not in RHS | list / tuple / string |
| `contains` | LHS contains RHS | list / string |
| `regex` | LHS matches RHS regex | string |

Incompatible-type comparisons (e.g., `None >= 3`) return `False` rather than raising. A contract that asks an impossible question simply doesn't fire.

### Field paths

Any dotted path through the Aurora Bundle works:

```
dataset.name                 # "factory_bearing_demo.csv"
dataset.rows                 # 1000
system_model.confidence      # 0.84
run.tier                     # "auto"
forecast.target              # "vibration_g"
```

Plus special **aggregate** paths that compute over the bundle:

| Path | What it returns |
|---|---|
| `findings.count` | Total number of findings |
| `findings.crit_count` | Findings with severity ∈ {crit, critical} |
| `findings.warn_count` | Findings with severity ∈ {warn, warning} |
| `findings.info_count` | Findings with severity ∈ {info, informational} |
| `findings.methods` | List of method strings (use with `contains`) |
| `confidence` | Shortcut for `system_model.confidence` |
| `forecast.peak` | Max `value` across all forecast points |
| `fabricated_count` | Should always be 0 |

### Example triggers

Critical-bearing alert:

```json
{"field": "findings.crit_count", "op": ">=", "value": 3}
```

Low-confidence run that needs review:

```json
{"field": "confidence", "op": "<", "value": 0.30}
```

Specific dataset name:

```json
{"field": "dataset.name", "op": "regex", "value": "^prod-traffic-.+\\.csv$"}
```

Aurora found a regime change:

```json
{"field": "findings.methods", "op": "contains", "value": "PELT-RBF"}
```

Forecast peak above operational ceiling:

```json
{"field": "forecast.peak", "op": ">", "value": 3.0}
```

Trust contract violated (should never fire; if it does, file an issue):

```json
{"field": "fabricated_count", "op": ">", "value": 0}
```

## Actions

Three action types ship in v1.1:

### `webhook` — HTTP POST to a URL

```json
{
  "type": "webhook",
  "url": "https://hooks.example.com/aurora",
  "method": "POST",
  "headers": {"X-Project": "aurora", "Authorization": "Bearer <token>"},
  "timeout_s": 10
}
```

Aurora POSTs a JSON body:

```json
{
  "contract_id": "factory-line-3-bearing-watch",
  "contract_name": "Alert on critical bearing anomalies",
  "trigger_field": "findings.crit_count",
  "trigger_value": 5,
  "bundle_run_id": "20260512_150000__data",
  "bundle_hash": "1c8d…",
  "metadata": { "owner": "ops@example.com" },
  "fired_at": "2026-05-12T15:30:22"
}
```

**Security:**

- Only `http` and `https` schemes accepted (no `file://`, `gopher://`, etc.)
- Hostname must resolve to a **public** IP — private (`10.0.0.0/8`, `192.168.0.0/16`, `172.16.0.0/12`), loopback (`127.0.0.0/8`), link-local (`169.254.0.0/16`), multicast, and reserved ranges are rejected with `WebhookSecurityError`
- Override (for development / internal services only): set environment variable `AURORA_ALLOW_LOCAL_WEBHOOKS=1` before starting
- 1 MB body cap; 30 s max timeout
- `Authorization`, `X-API-Key`, and `Cookie` headers are **redacted** in the contract's `to_dict()` audit output (only first 4 chars shown)

### `log` — write to Python `logging`

```json
{
  "type": "log",
  "level": "warn",
  "message": "bearing alert raised by Aurora"
}
```

Levels: `debug` | `info` | `warn` | `error` | `critical`. The log line is formatted with the contract id, name, trigger field, resolved value, and bundle run id. Logger failures never break the contract firing.

### `file` — append a JSON-line audit row

```json
{
  "type": "file",
  "path": "alerts/bearing.jsonl",
  "include_metadata": true
}
```

`path` is **relative** — resolved under `AURORA_CONTRACTS_OUTPUT` (default `~/.aurora/contracts_output/`). Absolute paths and `..` traversal are rejected at build time.

**Security:**

- Path is sandboxed to the output root via `Path.resolve()` + ancestor-check (handles symlinks)
- 100 MB file size cap; appending to a file larger than this raises rather than silently growing forever
- Files are append-only; the contract can never read or overwrite existing content

### `slack` — post a formatted Slack message *(v1.2)*

```json
{
  "type": "slack",
  "webhook_url": "https://hooks.slack.com/services/T000/B000/SECRET_TOKEN",
  "channel":   "#alerts",
  "username":  "Aurora",
  "icon_emoji": ":bar_chart:"
}
```

Posts a Slack Block Kit message with header + section fields covering the contract id, trigger field+value, run id, and bundle hash. The `text` field doubles as a plaintext fallback for clients that don't render blocks.

**Security:**

- `webhook_url` MUST be `https://` (Slack rejects HTTP anyway); the same SSRF guard the `webhook` action uses applies — the resolved hostname can't be private/loopback unless `AURORA_ALLOW_LOCAL_WEBHOOKS=1` is set
- `to_dict()` redacts the URL token segment in audit serialisation — your contract definitions on disk still carry the full URL, but logs + UI show `…[redacted]`
- 1 MB request-body cap (same as `webhook`)

### `discord` — post to a Discord channel webhook *(v1.2)*

```json
{
  "type": "discord",
  "webhook_url": "https://discord.com/api/webhooks/123/SECRET_TOKEN",
  "username": "Aurora"
}
```

Posts a Discord embed with title + 4 fields (contract, trigger, run, bundle hash). Embed colour is amber by default.

**Security:** identical guards to the `slack` action — HTTPS-only, SSRF check, URL redaction in audit output, 1 MB cap.

### `email` — send via SMTP *(v1.2)*

```json
{
  "type": "email",
  "host": "smtp.example.com",
  "port": 587,
  "from_addr": "aurora@example.com",
  "to_addrs":  ["alice@example.com", "bob@example.com"],
  "subject":   "Aurora alert: {contract_name}",
  "body_template": "Trigger {trigger_field}={trigger_value} on run {bundle_run_id}\n",
  "use_tls": true
}
```

Credentials come from environment variables — never from the contract document:

```bash
AURORA_SMTP_USER=...
AURORA_SMTP_PASS=...
```

Subject and body templates support these placeholders: `{contract_id}`, `{contract_name}`, `{trigger_field}`, `{trigger_value}`, `{bundle_run_id}`, `{bundle_hash}`, `{fired_at}`. Unknown placeholders fail silently (template returned verbatim).

**Security guards:**

- TLS required: port 587 → STARTTLS, port 465 → SMTPS. Plain SMTP is refused unless `AURORA_ALLOW_PLAINTEXT_SMTP=1` is set (the deliberate-friction opt-in pattern).
- Recipients capped at 20 per action — refuses to be a spam vector.
- Subject capped at 256 chars; body capped at 16 KB.
- Credentials are never logged or echoed in the action's serialisation.

## Rate limiting

```json
{ "rate_limit": { "max_per_minute": 6 } }
```

Supported windows: `max_per_minute`, `max_per_hour`, `max_per_day`. The engine tracks firings per-contract in-memory and prunes the log past the window. Suppressed firings appear in the audit record as `errors: ["rate_limited"]`.

## End-to-end Python example

```python
import json
from aurora_sdk import Bundle
from fantasyai.aurora.decision_contracts import (
    Contract, fire_contract, save_contract, load_contracts
)

# 1. Build a contract programmatically
contract = Contract.from_dict({
    "id": "bearing-watch",
    "name": "Alert on critical bearing anomalies",
    "trigger": {"field": "findings.crit_count", "op": ">=", "value": 3},
    "actions": [
        {"type": "log", "level": "warn", "message": "bearing critical"},
        {"type": "file", "path": "alerts/bearing.jsonl"},
    ],
    "rate_limit": {"max_per_hour": 12},
})

# 2. Persist it
save_contract(contract)   # → ~/.aurora/decision_contracts/bearing-watch.json

# 3. Evaluate against a bundle
bundle = Bundle.load("latest.aurora.json").doc
record = fire_contract(contract, bundle)
print(record.actions_succeeded, record.errors)

# 4. Load every contract from disk and evaluate on every new run
for c in load_contracts():
    if c.enabled:
        fire_contract(c, bundle)
```

## FiringRecord

Every call to `fire_contract` produces a `FiringRecord` regardless of outcome:

| Field | Description |
|---|---|
| `contract_id` | Which contract |
| `fired_at` | ISO timestamp |
| `bundle_run_id` | Which Aurora run triggered |
| `bundle_hash` | Bundle's content_hash (audit trail) |
| `trigger_value` | What the field actually resolved to |
| `actions_attempted` | How many actions ran |
| `actions_succeeded` | How many completed without raising |
| `errors` | List of per-action error strings (or status markers like `not_satisfied`, `rate_limited`, `dry_run`) |

This is your audit log. Persist these records via a `file` action on a "log everything" contract for compliance.

## Dry-run

```python
record = fire_contract(contract, bundle, dry_run=True)
# record.errors == ["dry_run"]; no actions ran
```

Use this in tests, CI, or to preview which contracts would fire on a given bundle.

## Operational patterns

### "Log everything" baseline audit contract

```json
{
  "id": "audit-everything",
  "name": "Audit log",
  "trigger": {"field": "findings.count", "op": ">=", "value": 0},
  "actions": [{"type": "file", "path": "audit.jsonl", "include_metadata": true}]
}
```

Always fires; appends one JSONL row per Aurora run. Ship to your SIEM.

### Low-confidence run flag

```json
{
  "id": "low-conf-review",
  "name": "Flag low-confidence runs for human review",
  "trigger": {"field": "confidence", "op": "<", "value": 0.30},
  "actions": [
    {"type": "webhook", "url": "https://intake.example.com/aurora-review"},
    {"type": "log", "level": "info"}
  ],
  "rate_limit": {"max_per_day": 50}
}
```

### Tier-2 confirmation gate

Two-stage contract: fire only when tier-2 has completed AND a tier-1 finding survives.

```json
{
  "id": "confirmed-anomaly",
  "name": "Tier-2 confirmed anomaly",
  "trigger": {"field": "run.tier", "op": "==", "value": "standard"},
  "actions": [{"type": "webhook", "url": "https://ops.example.com/anomaly-confirmed"}]
}
```

## Roadmap

- **v1.2**: Action types — Slack, Discord, PagerDuty, email (SMTP), database insert
- **v1.2**: Boolean composition (`all` / `any` of multiple triggers)
- **v1.2**: Web UI in the Studio to author / test / debug contracts
- **v2.0**: Cross-bundle triggers (e.g., "two consecutive bundles show crit_count >= 3")

## Testing

34 tests in `tests/test_decision_contracts.py` cover:

- Every operator on every common shape
- Special aggregates (crit_count, confidence, forecast.peak)
- Action validation (URL scheme, file traversal, header chars)
- SSRF guard (localhost, 127.0.0.1, 10.x.x.x rejected)
- Rate limiting
- Save/load roundtrip
- Audit redaction (auth tokens never leak)
- No raises across the engine boundary

Run with `pytest tests/test_decision_contracts.py`.

## Reference

- [fantasyai/aurora/decision_contracts/engine.py](../fantasyai/aurora/decision_contracts/engine.py) — predicate engine
- [fantasyai/aurora/decision_contracts/actions.py](../fantasyai/aurora/decision_contracts/actions.py) — action runners
- [tests/test_decision_contracts.py](../tests/test_decision_contracts.py) — 34 tests
- [examples/decision-contracts/](../examples/decision-contracts/) — sample contract documents

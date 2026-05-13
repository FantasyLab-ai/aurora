# API Reference

Aurora exposes three programmatic surfaces:

1. **Python SDK** (`aurora_sdk`) — for embedding in notebooks, scripts, pipelines
2. **MCP server** (`aurora_mcp`) — for LLM agents
3. **HTTP API** (`studio_api.py`) — for the Web Studio + ad-hoc curl

This page documents the HTTP API. See [docs/sdk.md](sdk.md) and [docs/mcp.md](mcp.md) for the others.

## Conventions

- **Base URL:** `http://127.0.0.1:8000` by default; configurable via `AURORA_HOST` / `AURORA_PORT`
- **Content type:** `application/json` for requests + responses
- **Errors:** `{"ok": false, "error": "<message>", "error_kind": "<kind>"}` shape consistent across endpoints
- **Run IDs:** ISO-8601 timestamps with dataset-name suffix, e.g., `20260512_143655__factory_bearing_demo`
- **Auth:** none by default for local-only operation. To require an `Authorization: Bearer <token>` header, set `AURORA_ADMIN_TOKEN=<token>` before starting the server (see Security).

## Run lifecycle

### `POST /api/upload`

Upload a dataset file. Validates + caches under `outputs/aurora_dataset_runs/<run_id>/`. Does **not** kick off analysis.

**Request:**

```bash
curl -X POST http://127.0.0.1:8000/api/upload \
  -F file=@data.csv \
  -F auto_run=0
```

**Response:**

```json
{
  "ok": true,
  "saved": true,
  "path": "/absolute/path/to/data.csv",
  "size_mb": 0.04
}
```

### `POST /api/run/profile`

Run Tier 0 profiling: schema detection, time-axis detection, runtime estimates per tier.

**Request:**

```json
{ "path": "/absolute/path/to/data.csv" }
```

**Response:**

```json
{
  "ok": true,
  "size_mb": 0.04,
  "tier0_profile": {
    "rows_total": 1000,
    "cols_total": 5,
    "has_time_axis": true,
    "time_col": "timestamp_s",
    "shape": "oscillatory"
  },
  "estimated_runtime": {
    "quick": "5s",
    "standard": "30s",
    "full": "2m 14s"
  },
  "auto_recommendation": "standard",
  "warnings": []
}
```

### `POST /api/run`

Start an analysis. Body specifies `path`, `tier`, optional `seed_text`. Returns a `run_id` immediately; poll `/api/state` to watch progress.

**Request:**

```json
{
  "path": "/absolute/path/to/data.csv",
  "tier": "auto",
  "seed_text": "any unusual patterns recently?"
}
```

**Response:**

```json
{ "ok": true, "run_id": "20260512_143655__data" }
```

### `GET /api/state?run_id=<id>` (or `/api/state` for the latest run)

Returns the full state dict the frontend consumes. Shape:

```json
{
  "run_id": "20260512_143655__data",
  "run_dir": "/path/to/run",
  "dataset":       { "name": "...", "rows": 1000, "cols": 5, "size_mb": 0.04, "path": "/path/to/data.csv" },
  "structure":     { "available": true, "time_axis": true, "time_col": "...", "cadence": "5s", "columns": [...] },
  "overview":      { ... },
  "anomalies":     [ ... ],
  "forecast":      { ... },
  "causal":        { ... },
  "physics":       { ... },
  "regimes":       [ ... ],
  "motifs":        [ ... ],
  "findings":      [ ... ],
  "system_model":  { ... },
  "synthesis":     { ... },
  "copilot":       { ... },
  "run_meta":      { "state": "complete", "tier": "auto", "started_at": "...", "completed_at": "..." }
}
```

See [aurora_sdk/bundle.py](../aurora_sdk/bundle.py) for the full shape; the SDK's `Bundle.from_state()` is the canonical translator.

### `POST /api/run/cancel`

```json
{ "run_id": "20260512_143655__data" }
```

Sets the cancel flag the runner polls every iteration; the run aborts cleanly and produces a partial state with `run_meta.state = "cancelled"`.

### `GET /api/runs/active`

Returns the run currently in flight (if any) so the frontend can recover on page refresh.

## System Model interaction

### `POST /api/system_model/intervene`

Perturb a node in the system model and propagate the change through discovered relationships.

**Request:**

```json
{
  "run_dir": "/path/to/run",
  "source_entity_id": "vibration_g",
  "perturbation": 1.5,
  "max_depth": 3
}
```

**Response:**

```json
{
  "ok": true,
  "result": {
    "source_entity_id": "vibration_g",
    "perturbation": 1.5,
    "deltas": [
      {
        "entity_id": "motor_temp_c",
        "delta": 0.45,
        "ci_low": 0.30,
        "ci_high": 0.60,
        "confidence": 0.78,
        "contributors": [ { "relationship_id": "...", "kind": "causal", "rationale": "..." } ]
      }
    ],
    "rationale": "Propagation through 2 validated edges; sample size n=940 for the lagged relationship."
  }
}
```

### `POST /api/system_model/simulate`

Forward-step the validated dynamics from the run's last value.

**Request:**

```json
{
  "run_dir": "/path/to/run",
  "n_steps": 50,
  "ci_pause_threshold": 5.0,
  "target_entity_id": "vibration_g"
}
```

**Response:**

```json
{
  "ok": true,
  "result": {
    "ok": true,
    "target_entity_id": "vibration_g",
    "method": "logistic",
    "trajectory": [
      { "step": 1, "t": 1.0, "value": 1.21, "ci_low": 1.10, "ci_high": 1.32, "source_equation": "...", "confidence": 0.95 }
    ],
    "paused": false,
    "paused_reason": "",
    "assumptions": [ "fitted parameters from this run are the truth — re-run if data drifts" ]
  }
}
```

## Knowledge bank

### `GET /knowledge_bank`

Returns an HTML view of the knowledge bank (browse-only).

### `GET /api/knowledge/search?q=<query>&top_k=10`

```json
{
  "ok": true,
  "entries": [
    { "seed_id": "seed:hampel1974_robust_z", "title": "...", "source_citation": "...", "score": 0.87 }
  ]
}
```

## Export

### `POST /api/export`

Generate an export bundle.

**Request:**

```json
{ "run_id": "...", "format": "bundle_json" }
```

`format` is one of: `findings_md`, `methods_md`, `bundle_json`, `research_kit`.

**Response:**

```json
{ "ok": true, "path": "/path/to/export/aurora.bundle.json", "size_bytes": 145729 }
```

## Security

By default the API is bound to `127.0.0.1` only — not reachable from other machines on the network. For multi-user or remote scenarios:

- Set `AURORA_HOST=0.0.0.0` to bind to all interfaces (use carefully)
- Set `AURORA_ADMIN_TOKEN=<random-256-bit-token>` to require `Authorization: Bearer <token>` on every write endpoint
- Run behind a reverse proxy with TLS

Aurora does NOT include built-in user management. For multi-tenant deployment, use a proxy that handles auth (Caddy, Nginx, Traefik, oauth2-proxy, etc.) and pass user identity via a header — Aurora will log it but doesn't enforce identity beyond the bearer-token gate.

See [docs/deployment.md](deployment.md) for production-deployment guidance.

## Versioning

API endpoints under `/api/*` are stable within a major Aurora version. Breaking changes will bump to `/api/v2/*` etc. New optional response fields can land in minor versions without notice.

## Programmatic alternatives

For most use cases, the **Python SDK** (`aurora_sdk`) is a better integration surface than direct HTTP — it wraps the same pipeline without the network hop. See [docs/sdk.md](sdk.md).

For **LLM agent integration**, use the **MCP server** (`aurora_mcp`) instead. See [docs/mcp.md](mcp.md).

The HTTP API exists primarily for the Web Studio + ad-hoc tooling.

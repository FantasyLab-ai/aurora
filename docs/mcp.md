# Aurora MCP Server

The Aurora MCP (Model Context Protocol) server exposes Aurora's quantitative-reasoning tools to LLM agents. Any MCP-capable client — Claude Desktop, Claude Code, Cursor, custom agents using the MCP Python SDK — can call Aurora as a first-class tool.

This is what makes Aurora **the quantitative cortex** LLM agents plug into.

## Why MCP matters now

LLM agents need quantitative reasoning grounded in citations. Right now they don't have it — they invent numbers, make up methods, and can't be defended legally. The first quantitative MCP server with a glass-box angle wins the category. Aurora is purpose-built for this slot.

## Install

```bash
pip install mcp                                # MCP Python SDK (optional dep)
pip install -r requirements.txt               # Aurora itself
```

## Run the server

Default (stdio transport, current working directory as the only allow-root):

```bash
python -m aurora_mcp.server
```

Recommended (explicit allow-roots, scoped to a project):

```bash
python -m aurora_mcp.server \
  --allow-root /Users/you/data \
  --allow-root /Users/you/aurora-outputs
```

Debugging (list tools without running the protocol):

```bash
python -m aurora_mcp.server --list-tools
```

## Claude Desktop configuration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "aurora": {
      "command": "python",
      "args": [
        "-m", "aurora_mcp.server",
        "--allow-root", "/Users/you/data",
        "--allow-root", "/Users/you/aurora-outputs"
      ]
    }
  }
}
```

If you're using a venv, point `command` at the venv's `python`:

```json
{
  "mcpServers": {
    "aurora": {
      "command": "/Users/you/Code/aurora/.venv/bin/python",
      "args": ["-m", "aurora_mcp.server", "--allow-root", "/Users/you/data"]
    }
  }
}
```

Restart Claude Desktop. You should see "Aurora" in the tool menu with 7 tools available.

## Cursor configuration

Cursor uses an analogous config — see Cursor's MCP docs for the exact location. Same `command` / `args` shape works.

## Tool catalog

| Tool | Description |
|---|---|
| `aurora_analyze` | Run Aurora on a dataset; return a glass-box summary (or full bundle) |
| `aurora_load_bundle` | Load a saved `.aurora.json`; verify the integrity hash |
| `aurora_findings` | List findings with severity / method filters |
| `aurora_forecast` | Forecast points + peak prediction |
| `aurora_explain` | Full evidence + method spec for a specific `claim_id` |
| `aurora_intervene` | Perturb a node in the system model; propagate Δ |
| `aurora_simulate` | Forward-step validated dynamics with honest CI pause |

Each tool's full JSON schema is in `aurora_mcp/tools.py` (`TOOL_SCHEMAS`).

### Example: `aurora_analyze`

**Input:**
```json
{
  "path": "/Users/you/data/factory_bearing.csv",
  "depth": "standard",
  "full_bundle": false
}
```

**Output (truncated):**
```json
{
  "ok": true,
  "summary": {
    "run_id": "20260512_150000__factory_bearing",
    "confidence": 0.84,
    "fabricated_count": 0,
    "findings_count_by_severity": {"crit": 3, "warn": 2, "info": 6},
    "methods_used": ["ISO-FOREST + ROBUST-Z", "Granger", "matrix_profile", "AR(1)", "VIETORIS_RIPS_H0_H1"],
    "bundle_version": "1.0.0",
    "content_hash": "1c8d…"
  }
}
```

Set `full_bundle: true` to get the entire Aurora Bundle in the response (still subject to the 2 MB cap; bigger bundles are truncated at top-level lists with a marker entry).

### Example: `aurora_findings` with filter

**Input:**
```json
{
  "path": "/Users/you/data/factory_bearing.csv",
  "severity": "crit",
  "method": "iso-forest",
  "limit": 10
}
```

**Output:**
```json
{
  "ok": true,
  "count": 3,
  "returned": 3,
  "findings": [
    {
      "claim_id": "anom-0000",
      "rank": 1,
      "severity": "crit",
      "method": "ISO-FOREST + ROBUST-Z",
      "title": "Confirmed anomaly in vibration_g at row 205",
      "confidence": 1.0
    }
  ]
}
```

### Example: `aurora_explain`

**Input:**
```json
{
  "path": "/Users/you/data/factory_bearing.csv",
  "claim_id": "anom-0000"
}
```

**Output:**
```json
{
  "ok": true,
  "claim_id": "anom-0000",
  "finding": { "method": "ISO-FOREST + ROBUST-Z", "rank": 1, "severity": "crit", … },
  "method_registry": { "count": 5, "severities": {"crit": 3, "warn": 2} }
}
```

## Security model

The MCP server enforces a security policy on every tool call:

### Path allowlist

Every input `path` is resolved via `Path.resolve()` (which normalises `..` and follows symlinks) then checked against the configured allow-roots. Paths outside any allow-root are rejected with `error_kind: "path_outside_allowlist"`.

Default allow-root: `Path.cwd()` at server-start time. Always pass `--allow-root` explicitly in production.

### Response budget

Every response is JSON-serialised; if the result exceeds `MAX_RESPONSE_BYTES` (2 MB), the largest top-level list is truncated and a marker entry inserted. This prevents a runaway agent from consuming unbounded client memory.

### No code execution

Tools never call `eval`, `exec`, `open`-for-write outside the run dir, or spawn subprocesses. The MCP layer is pure declarative dispatch.

### Errors as data

Tools never raise across the MCP boundary. Any failure becomes `{"error": "...", "error_kind": "..."}`. Common `error_kind` values:

| Kind | Meaning |
|---|---|
| `bad_input` | Missing or wrong-typed required field |
| `not_found` | Path doesn't exist |
| `path_outside_allowlist` | Path is outside the configured allow-roots |
| `load_error` | Couldn't load the run / bundle |
| `load_or_verify_error` | Bundle integrity check failed |
| `pipeline_error` | The analysis pipeline raised |
| `feature_unavailable` | Optional feature (e.g., `simulate_intervention`) isn't installed |
| `unknown_tool` | Tool name not registered |
| `tool_crash` | A tool raised (should never happen; framework wrapping) |

## What the agent sees

When Claude (or Cursor, or your custom agent) connects to the Aurora MCP server, it discovers the tool catalog automatically. The tool *descriptions* (in `TOOL_SCHEMAS`) are written to advertise Aurora's glass-box angle:

> "Run Aurora on a dataset and return a glass-box summary (findings count by severity, methods used, confidence, fabricated_count). Set full_bundle=true to get the entire Aurora Bundle. Read-only on the dataset."

The agent uses these descriptions to decide *when* to call Aurora. A well-designed prompt like "analyse this CSV and tell me about critical anomalies; cite your sources" will produce a tool-call chain like `aurora_analyze → aurora_findings(severity=crit) → aurora_explain(claim_id=...)`.

## Customising

### Limit which tools are exposed

By default all 7 tools are exposed. To expose a subset, edit `aurora_mcp/__init__.py` `TOOLS` dict before starting the server (or fork + customise).

### Custom allow-root logic

`aurora_mcp.tools.set_allowed_roots([...])` lets you programmatically set roots if you embed the server in a larger Python process. The check is in `_is_within_allowed_roots`.

### Output budget

Override `MAX_RESPONSE_BYTES`:

```python
from aurora_mcp.tools import MAX_RESPONSE_BYTES   # default 2 MB
# Higher / lower via your own wrapper module — not a runtime env var by design
```

## Testing

19 tests in `tests/test_aurora_mcp.py` cover:

- Schema sanity (every tool has a complete schema)
- Path allowlist (traversal rejected, nonexistent rejected, missing arg rejected)
- Bundle load + verify happy path + tamper detection
- Findings filters (severity, method, limit)
- Error wrapping (no raises across boundary)

Run with `pytest tests/test_aurora_mcp.py`.

## Roadmap

- **v1.2**: HTTP transport (optional, alongside stdio) for remote agents that can't subprocess locally
- **v1.2**: Tool catalog plugin system — third parties register their own MCP tools alongside Aurora's
- **v2.0**: First-class auth on HTTP transport (bearer tokens, OAuth)

## Reference

- [aurora_mcp/__init__.py](../aurora_mcp/__init__.py) — Public surface
- [aurora_mcp/tools.py](../aurora_mcp/tools.py) — Tool implementations + JSON schemas
- [aurora_mcp/server.py](../aurora_mcp/server.py) — MCP stdio server
- [tests/test_aurora_mcp.py](../tests/test_aurora_mcp.py) — 19 tests
- [Model Context Protocol spec](https://modelcontextprotocol.io/) — Anthropic's MCP documentation

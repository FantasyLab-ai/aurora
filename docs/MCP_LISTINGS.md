# Aurora on the MCP directories — the metadata pack

One source of truth for every registry and directory listing. Write once,
paste everywhere, keep consistent. Update this file first when the pitch or
install command changes.

## Canonical identity

| Field | Value |
|---|---|
| Registry name | `io.github.FantasyLab-ai/aurora` |
| PyPI package | [`aurora-mcp`](https://pypi.org/project/aurora-mcp/) |
| Run command | `uvx aurora-mcp --allow-root /path/to/data` |
| Repo | https://github.com/FantasyLab-ai/aurora |
| License | Apache-2.0 |
| Transport | stdio (default) · HTTP shim (`aurora_mcp.http_server`, `pip install aurora-mcp[http]`) |
| Tools | 7 — analyze, findings, explain, forecast, load_bundle, intervene, simulate |

## The one-paragraph blurb (short listings)

> Glass-box statistical analysis for AI agents. Aurora runs 19 research-grade
> methods (anomaly detection, change-points, forecasting, causal system
> models) locally on a dataset and returns cited findings — every claim
> carries its method, threshold, and evidence, and the fabricated-number
> count is contractually zero. Agents stop guessing statistics and start
> citing them.

## The long blurb (registry description / Smithery page)

> LLMs invent numbers. Aurora is the structurally different fix: an MCP
> server that computes instead of predicting. Point `aurora_analyze` at a
> CSV/Parquet/XLSX and it runs a battery of 19 research-grade statistical
> methods on-device — isolation-forest + robust-z anomaly detection,
> change-point detection, trend/seasonality, FDR-controlled correlation
> screening, forecasting, causal system-model discovery. Every finding is
> cited (method, threshold, claim_id), drillable to full evidence via
> `aurora_explain`, and packaged in an integrity-hashed `.aurora.json`
> bundle that `aurora_load_bundle` verifies before anyone trusts it.
> What-if questions run through `aurora_intervene` (shock propagation with
> confidence intervals) and `aurora_simulate` (which pauses honestly when
> CIs grow too wide). Local-first: your data never leaves the machine, the
> server enforces a path allowlist, and output is capped. Apache-2.0.

## Tags (pick per directory's limit, in this order)

`statistics` `data-analysis` `anomaly-detection` `forecasting`
`causal-inference` `change-point-detection` `verification` `glass-box`
`local-first` `python`

## Client config snippet (paste into listing "installation" fields)

```json
{
  "mcpServers": {
    "aurora": {
      "command": "uvx",
      "args": ["aurora-mcp", "--allow-root", "/path/to/your/data"]
    }
  }
}
```

## Where to list, in order

1. **PyPI** (prerequisite) — `aurora-mcp`; published by the
   `publish-pypi.yml` workflow on a `pypi-v*` tag after one-time trusted-
   publisher setup on pypi.org.
2. **Official MCP Registry** (registry.modelcontextprotocol.io) — the
   legitimacy signal. `server.json` lives at the repo root; the README
   carries the `mcp-name` marker the registry checks. Publish with the
   `mcp-publisher` CLI: `login github`, then `publish`.
3. **Glama** (glama.ai) — crawls GitHub; search for an existing unclaimed
   stub first and claim it. The in-browser inspector doubles as a free
   hosted demo of the tools.
4. **Smithery** (smithery.ai) — sign in with GitHub, add the server by
   repo; the hosted option can point at the HTTP shim later.
5. **mcp.so** — submit via the site form; same blurb + tags.
6. **awesome-mcp-servers** (punkpeye) — PR one line under *Data Science*:
   `- [FantasyLab-ai/aurora](https://github.com/FantasyLab-ai/aurora) 🐍 🏠 - Glass-box statistical analysis: 19 research-grade methods, cited findings, integrity-hashed bundles. Agents cite real math instead of inventing it.`

## The discipline

The listing gets Aurora *found*; the seven tool descriptions in
`aurora_mcp/tools.py` get it *chosen* — agents pick tools by reading
descriptions. Treat those descriptions as product copy under test: keep
them capability-first, honest, and specific. Never claim a method count
the Advanced Methods panel doesn't show.

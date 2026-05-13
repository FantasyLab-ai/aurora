# Getting Started with Aurora

This guide walks you from a fresh clone to your first cited finding in about 10 minutes.

## Prerequisites

- **Python 3.10+** (`python --version` to check)
- **8 GB RAM** minimum, 16 GB recommended for medium datasets
- **~3 GB disk space** (500 MB application + 2 GB optional knowledge bank)
- *(Optional)* **Ollama** running locally with `gemma3:12b` for LLM narrative synthesis. Aurora falls back to a deterministic template synthesis if Ollama is absent — nothing is *required* to be online.

## Step 1 — Clone and install

```bash
git clone https://github.com/fantasylab/aurora.git
cd aurora
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Optional but recommended:

```bash
pip install cryptography              # Ed25519 signing for Aurora Bundles
pip install mcp                       # for Aurora MCP server
```

## Step 2 — Start the Studio

```bash
python studio_api.py
```

The console will print something like:

```
 * Serving Flask app 'studio_api'
 * Running on http://127.0.0.1:8000
```

Open **http://127.0.0.1:8000** in your browser. You should see Aurora's Web Studio with the walking polar-bear mascot in the top-left and an empty Start panel in the middle.

## Step 3 — Run the demo dataset

Click **▶ Try a demo** in the Start panel. Pick `factory_bearing_demo` — it's small (1 K rows × 5 columns), runs in seconds, and shows every Aurora surface at once.

Wait ~5–15 seconds. The cube spins up, the worldlines fill in, the findings panel populates with 11 cards (3 critical, 1 warning, 7 informational), and the **0 fabricated** chip in the run banner glows mint.

## Step 4 — Read the output

Look at the page top-to-bottom:

1. **Aurora Pulse** (bear-voiced banner) — "Loaded factory_bearing_demo · …; 3 critical anomalies; confidence X%."
2. **Run banner** — current-run id, dataset stats, `0 fabricated` glass-box chip
3. **Assumptions strip** — what Aurora assumed when running these methods
4. **Dataset Lens** — Structure (time axis, cadence, gaps, dupes) + Columns
5. **"What This Means"** — Aurora's plain-English synthesis with `seed:*` citations
6. **Cube + intelligence tiles** — Top Anomalies, Forecast, What-If, Physics
7. **Advanced Methods** (collapsed) — click `Σ ADVANCED METHODS` to expand
8. **System Graph** with GRAPH / SPACETIME / PHASE SPACE views
9. **Findings** — 11 cards with method tag, citation, view-evidence button
10. **Research Diary** — morning summary + prior-library changelog

Hover any finding's METHOD chip to see a plain-English description (e.g., `ISO-FOREST + ROBUST-Z` → "Isolation Forest + robust z-score (Hampel 1974, |z| ≥ 3 warns, ≥ 5 critical)").

## Step 5 — Drop your own dataset

Drag a CSV, TSV, JSON, JSONL, Parquet, or XLSX file onto the Start panel. Aurora will:

1. Profile the dataset (rows / cols / size / time axis detection)
2. Recommend a tier (AUTO / QUICK / STANDARD / FULL) based on size
3. Enable the **▶ RUN ANALYSIS** button with the ETA appended (e.g., `▶ RUN ANALYSIS · ~2m 14s`)

Optionally type a question in the **WHAT DO YOU WANT TO KNOW?** box. Aurora will sort findings around that focus.

Click **▶ RUN ANALYSIS** and wait. Tier 1 results appear first; if you're on STANDARD or FULL, tier 2/3 results populate progressively.

## Step 6 — Save the run as a portable bundle

Click **EXPORT REPORT ▾ → aurora.bundle.json** to save the run as a single `.aurora.json` file. This is the Aurora Bundle Format v1 — a citeable, signable analytical artifact you can:

- Hand to a teammate (they `Bundle.load()` it without re-running)
- Attach to a paper appendix
- Verify with `bundle.verify()` on any machine with Aurora installed
- Sign with `bundle.sign(private_key)` for tamper-evident distribution

See [docs/sdk.md](sdk.md) for the full SDK reference.

## Step 7 — (Optional) Hook into an AI agent via MCP

If you use Claude Desktop, Claude Code, or another MCP-capable agent, you can give it Aurora as a tool:

```bash
pip install mcp
python -m aurora_mcp.server --allow-root ./data --allow-root ./outputs
```

Then in Claude Desktop's `claude_desktop_config.json`:

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

Restart Claude Desktop and the 7 Aurora tools are available. Ask it: "Analyse `/Users/you/data/factory_bearing.csv` and tell me about any critical anomalies." Claude will call `aurora_analyze` + `aurora_findings` and cite the findings back.

See [docs/mcp.md](mcp.md) for the full guide.

## Step 8 — (Optional) Wire findings into automation

Define a Decision Contract that fires when something critical happens:

```json
{
  "id": "bearing-watch",
  "name": "Alert on critical bearing anomalies",
  "trigger": {"field": "findings.crit_count", "op": ">=", "value": 3},
  "actions": [
    {"type": "webhook", "url": "https://hooks.example.com/aurora"},
    {"type": "log", "level": "warn", "message": "bearing alert"}
  ],
  "rate_limit": {"max_per_hour": 12}
}
```

Save it as `~/.aurora/decision_contracts/bearing-watch.json` and call `fire_contract(contract, bundle)` whenever a new bundle is produced. See [docs/decision-contracts.md](decision-contracts.md).

## What if Aurora doesn't run?

- **`ModuleNotFoundError`** — re-run `pip install -r requirements.txt` inside your venv
- **Port 8000 already in use** — kill the stale process (`lsof -i :8000` then `kill -9 <PID>`) or set `AURORA_PORT=8001`
- **Synthesis returns "template" instead of LLM narrative** — Ollama isn't reachable. Either start it (`ollama serve` + `ollama pull gemma3:12b`) or ignore — template synthesis is deterministic and grounded
- **A method shows as "deferred"** — the 90-second per-method timeout fired. This is honest disclosure; the rest of the pipeline still ran

For more: see [docs/faq.md](faq.md).

## Next steps

- [docs/concepts.md](concepts.md) — Glass-box, RAG, knowledge bank, tiers
- [docs/methods.md](methods.md) — Every analytical method explained with citations
- [docs/sdk.md](sdk.md) — Python SDK reference
- [docs/mcp.md](mcp.md) — MCP server setup + Claude Desktop config
- [docs/decision-contracts.md](decision-contracts.md) — Contracts schema + security
- [docs/research-kit.md](research-kit.md) — Publication-ready output + Zenodo

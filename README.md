<div align="center">
  <img src="frontend/assets/brand/aurora-mascot-standing.svg" alt="Aurora" width="180"/>

  # Aurora

  ### Glass-box quantitative intelligence. Local. Open. Cited.

  Aurora is the **verification cortex** for serious quantitative work — for humans analyzing hard data, and for AI systems that can't afford to hallucinate.

  > **Cloud LLMs guess. Aurora computes.**

  [![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
  [![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
  [![Status](https://img.shields.io/badge/status-v1.1%20launch-purple.svg)](#)
  [![Tests](https://img.shields.io/badge/tests-320%20passing-brightgreen.svg)](#)
  [![Patreon](https://img.shields.io/badge/support-Patreon-f96854.svg)](https://patreon.com/FantasyLabAI)

  [Install](#install) · [Aurora Copilot](#-aurora-copilot--for-humans) · [Aurora Cortex](#%EF%B8%8F-aurora-cortex--for-ai-systems) · [Roadmap](ROADMAP.md) · [FantasyLab.ai](https://fantasylab.ai)
</div>

---

## Install

```bash
git clone https://github.com/fantasylab/aurora.git
cd aurora
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional substrate-layer extras
pip install cryptography             # Ed25519 bundle signing
pip install mcp                      # MCP server for LLM agents

# Run the Studio
python studio_api.py
```

Open <http://127.0.0.1:8000>. Click **▶ Try a demo** for a 10-second smoke test, or drop your own CSV / Parquet / JSON / XLSX.

> **Status:** v1.1, public launch, 320 tests passing, real users running it on real data.

---

## One engine. Two surfaces. Same glass-box.

Aurora has two faces sharing one analytical engine. Same code, same principles, two integration shapes — one for humans clicking through findings, one for AI systems calling APIs.

### 🧠 Aurora Copilot — for humans

*For analysts, quants, scientists, engineers.*

A local quantitative copilot for the work that matters too much to trust to a model that hallucinates. Drop in a dataset and get rigorous findings — anomalies surfaced, causal relationships tested, forecasts with confidence bounds, every claim cited to the underlying computation. No cloud LLM guessing. No black-box math.

- **Glass-box studio** — six analytical lenses (Overview, Anomalies, Regimes, Motifs, Forecast, Physics), spacetime system graph, phase-space projection
- **17+ research-grade methods** — Isolation Forest, robust z-score (Hampel), Granger, HMM Baum-Welch, persistent homology, SINDy, Gaussian processes, mutual information, and more
- **Knowledge-grounded synthesis** — every "What This Means" sentence cites a `seed:*` entry in a public, licensed knowledge bank
- **Honest disclosure** — sampling, timeouts, and skipped methods are surfaced; never silently faked

→ See it in action: [`examples/factory-bearing/`](examples/factory-bearing/)
→ Conceptual overview: [docs/concepts.md](docs/concepts.md)

### 🛡️ Aurora Cortex — for AI systems

*For AI builders, agent developers, AI product teams.*

The verification layer your AI agents and AI products call when they can't afford to hallucinate quantitative claims. Every LLM today invents numbers; **Aurora is the structurally different fix** — it computes and verifies rather than predicts. Connect via MCP, the Python SDK, or Decision Contracts.

Four programmable surfaces, all consuming the same `.aurora.json` bundle format:

```python
import aurora_sdk as aurora

r = aurora.run("data.csv", depth="standard")
r.findings.critical().by_method("iso-forest")
r.forecast.peak(horizon_hours=24)
r.bundle.save("audit.aurora.json")        # SHA-256 integrity + optional Ed25519 signing

# Verify on any machine with Aurora installed
b = aurora.Bundle.load("audit.aurora.json")
b.verify()                                  # raises if tampered
```

| Layer | Audience | What it does |
|---|---|---|
| **Aurora SDK** ([docs](docs/sdk.md)) | Python devs, notebooks, pipelines | `pip install` away from cited, glass-box quantitative reasoning |
| **Aurora MCP** ([docs](docs/mcp.md)) | LLM agents (Claude Desktop, Claude Code, Cursor, custom) | 7 MCP tools your agent calls; path-allowlisted, output-capped, JSON-only |
| **Decision Contracts** ([docs](docs/decision-contracts.md)) | Automation pipelines | Programmable predicates → webhook / log / file when findings match. SSRF-guarded |
| **Research Kit** ([docs](docs/research-kit.md)) | Researchers, academics | `methods.md` + `references.bib` + `replication.json` + `.zenodo.json` for DOI minting |

→ Wire Aurora into Claude Desktop in 5 minutes: [`examples/mcp-claude-desktop/`](examples/mcp-claude-desktop/)
→ Build a "fire when 3+ critical anomalies appear" automation: [`examples/decision-contracts/`](examples/decision-contracts/)

---

## Why Aurora exists

Every AI today — including the best cloud LLMs — hallucinates on quantitative claims. Bigger models don't fix it. RAG alone doesn't fix it. Chain-of-thought just produces longer confident lies.

Aurora is the rarest kind of fix — **structurally different**. It computes and verifies rather than predicts. It runs locally on your machine. It shows its math. Every relationship Aurora reports is computed, not guessed. Every claim cites its source. Every uncertain finding is rendered as uncertain — never confident-looking math over shaky ground.

### Core principles

- 🔍 **Glass-box at every layer.** Every node, edge, finding, and recommendation traces to its source. Bundles carry a SHA-256 content hash and can be Ed25519-signed.
- 💻 **Local-first, always.** Your data stays on your machine. No telemetry. No phone-home. The MCP server enforces a per-call path allowlist; Decision Contracts block private-IP webhook targets by default.
- 🎯 **Honesty rule.** Uncertain relationships render as uncertain. When methods sample or time out, the user is told. Aurora's `fabricated_count` chip is a contractual `0` — and it's audited live.
- 📖 **Open source.** Apache 2.0. Inspectable. Forkable. Yours to audit, vendor, embed, redistribute.

---

## How it connects

Aurora is built to be infrastructure both humans and AI systems can call:

- **MCP Server** — connect any MCP-compatible AI client (Claude Desktop, Claude Code, Cursor, custom agents). 7 tools advertised; path-allowlisted; 2 MB output cap; JSON-only error wrapping
- **Python SDK** — `import aurora_sdk` in notebooks, scripts, pipelines, agent frameworks
- **Decision Contracts** — programmable predicates fire actions (webhook / log / append-only file). SSRF-guarded, rate-limited, audited
- **Webhooks** — deliver verified findings to Slack / Discord / PagerDuty / your own intake API (Slack/Discord/PagerDuty action types coming in v1.2; generic webhook ships now)
- **Local Studio** — full glass-box exploration of findings, intelligence panels, system graph, cube navigator
- **Aurora Bundle Format v1** — a portable, signable, citeable `.aurora.json` artifact that every layer above produces and consumes

---

## Quick examples

### As a Copilot — drop a CSV, read what Aurora found

```bash
python studio_api.py
# Open http://127.0.0.1:8000 → click "Try a demo → factory_bearing_demo"
# 10 seconds later: 11 cited findings, 3 critical, confidence 84%, 0 fabricated.
```

### As a Cortex — give Claude Desktop the ability to cite real math

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aurora": {
      "command": "/path/to/aurora/.venv/bin/python",
      "args": ["-m", "aurora_mcp.server", "--allow-root", "/Users/you/data"]
    }
  }
}
```

Then ask Claude:

> Run Aurora on `/Users/you/data/factory_bearing.csv` at standard depth, then drill into the most critical anomaly with full evidence.

Claude chains `aurora_analyze → aurora_findings(severity=crit) → aurora_explain(claim_id=…)` and writes you back a response **citing every method** — never inventing a row number or a z-score.

Full walkthrough: [`examples/mcp-claude-desktop/`](examples/mcp-claude-desktop/)

### As a Cortex — wire findings into automation

`~/.aurora/decision_contracts/bearing-watch.json`:

```json
{
  "id": "bearing-watch",
  "trigger": {"field": "findings.crit_count", "op": ">=", "value": 3},
  "actions": [
    {"type": "webhook", "url": "https://hooks.example.com/aurora"},
    {"type": "file", "path": "alerts.jsonl"}
  ],
  "rate_limit": {"max_per_hour": 12}
}
```

```python
from aurora_sdk import Bundle
from fantasyai.aurora.decision_contracts import load_contracts, fire_contract

bundle = Bundle.load("latest.aurora.json").doc
for c in load_contracts():
    fire_contract(c, bundle)               # webhook fires, audit row appended
```

---

## What's working today (v1.1)

- Six analytical lenses (Overview, Anomalies, Regimes, Motifs, Forecast, Physics) with the spacetime system graph
- 17+ advanced research-grade methods with honest sampling + timeout disclosure
- Local Gemma 3 12B synthesis with strict RAG grounding + post-hoc verification
- Tiered analysis (AUTO / QUICK / STANDARD / FULL) — auto-scales to dataset size
- **Aurora Bundle Format v1** — SHA-256 integrity + optional Ed25519 signing
- **Aurora SDK** — 28 tests, full bundle roundtrip + tamper detection
- **Aurora MCP server** — 7 tools, 19 tests, path allowlist + output cap
- **Decision Contracts** — predicate engine + webhook/log/file actions, 34 tests, SSRF guard
- **Research Kit** — methods.md + references.bib + replication.json + .zenodo.json, 22 tests
- **320 tests passing** across backend, frontend audit, SDK, MCP, contracts, research kit
- Local execution end-to-end — no cloud dependencies after initial knowledge bank download

## What's rough

This is v1.1, shipped in public, with rough edges. Named honestly:

- The knowledge bank ships with a seed set; the full pack is downloaded separately and is still expanding
- Some advanced methods skip on datasets that lack required structure (no time axis, no entity column) — these skips are correct glass-box behavior; can be confusing without reading the skip reason
- Per-method timeouts (90 s default) defer some methods on very large datasets; disclosed honestly
- Mobile / tablet responsiveness is on the v1.2 roadmap
- Decision Contracts ship with webhook / log / file action types; Slack / Discord / PagerDuty actions are v1.2

Build-in-public log: [CHANGELOG.md](CHANGELOG.md).

---

## Built in the open. By a real person. For real work.

Aurora is fully open source under Apache 2.0. The engine, the schema, the baseline templates, the MCP server, the SDK, the webhook layer — all of it. No black box at any layer, including the codebase itself.

The roadmap is public. The build is documented on YouTube. Domain experts who contribute knowledge bank entries or templates will be able to earn from the upcoming marketplace (v2.0). Aurora gets smarter as the community grows.

- ⭐ [Star on GitHub](https://github.com/fantasylab/aurora) — visibility
- 💜 [Back on Patreon](https://patreon.com/FantasyLabAI) — recurring support funds the build
- 📺 [Follow the build on YouTube](https://youtube.com/@Fantasy_lab_ai)
- 🐦 [Daily progress on X](https://twitter.com/Fantasylab_ai)

---

## Roadmap

See [ROADMAP.md](ROADMAP.md). Highlights:

- **v1.2 (next 4–8 weeks):** Slack/Discord/PagerDuty Decision Contract actions, file-watcher streaming mode, runs library, knowledge bank expansion, Jupyter integration
- **v2.0 (3–6 months):** Domain knowledge bank packs (Climate / Finance / Biomed / Industrial), customer-hosted enterprise deployment, Postgres CDC + Kafka streaming, signed-bundle attestation service
- **v2.0+ (6–12 months):** Federated knowledge contribution, plugin SDK for custom methods, template marketplace with creator revenue share

---

## Project Family

Aurora is part of [FantasyLab.ai](https://fantasylab.ai) — local-first AI tools for serious work. Sister project:

- **[Fantasy Studio](https://github.com/fantasylab/studio)** — AI-directed cinematic 3D rendering using real path-traced light simulation. Same philosophy applied to creative work instead of analytical work.

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, code style, testing requirements, and the development workflow.

Good places to start:
- Issues labeled [`good first issue`](https://github.com/fantasylab/aurora/labels/good%20first%20issue)
- Issues labeled [`help wanted`](https://github.com/fantasylab/aurora/labels/help%20wanted)
- Knowledge bank contributions — adding cited entries for new domains. See [docs/knowledge-bank.md](docs/knowledge-bank.md)
- Aurora MCP integrations — example notebooks, demo agents

## Security

Aurora processes data locally. The SDK and MCP server have no telemetry, no phone-home, no analytics. The verification cortex is the moat — and it's audited. See [SECURITY.md](SECURITY.md) for the full security model and how to report a vulnerability.

## License

Aurora is licensed under [Apache License 2.0](LICENSE). Use it commercially, modify it, redistribute it. Just keep the copyright notice and don't claim we endorse your derivative.

For deployment in customer-managed cloud environments with enterprise support, contact [enterprise@fantasylab.ai](mailto:enterprise@fantasylab.ai).

## Acknowledgments

Aurora stands on the shoulders of decades of statistical and analytical research. Citations are baked into every Aurora output. Foundational methods come from researchers including:

- Vipin Chandola, Arindam Banerjee, Vipin Kumar (anomaly detection)
- Judea Pearl (causal inference)
- Rob Hyndman, George Athanasopoulos (forecasting)
- Steven Brunton, Joshua Proctor, Nathan Kutz (SINDy / sparse identification)
- Frank Hampel (robust statistics, 1974)
- Fei Tony Liu, Kai Ming Ting, Zhi-Hua Zhou (Isolation Forest)
- Carl Edward Rasmussen, Christopher K. I. Williams (Gaussian processes)
- C. W. J. Granger (causality)
- L. E. Baum, T. Petrie, G. Soules, N. Weiss (Baum–Welch HMM)
- Thomas Malthus (exponential dynamics, 1798)
- Reverend Thomas Bayes (Bayesian inference)
- …and the broader open scientific community whose work makes Aurora possible

The Aurora project itself is built by **Brandon Grutkowski** as part of FantasyLab.ai.

---

<div align="center">

  **Aurora is part of [FantasyLab.ai](https://fantasylab.ai)** · Glass-box, local-first, source-available AI tools for serious work

</div>

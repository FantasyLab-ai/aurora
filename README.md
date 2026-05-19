<div align="center">
  <img src="docs/screenshots/aurora-mascot-wizard.png" alt="Aurora" width="200"/>

  # Aurora

  ### Glass-box quantitative intelligence. Local. Open. Cited.

  Aurora is the **verification cortex** for serious quantitative work — for humans analyzing hard data, and for AI systems that can't afford to hallucinate.

  > **Cloud LLMs guess. Aurora computes.**

  [![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
  [![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
  [![Status](https://img.shields.io/badge/status-v2.0%20active-purple.svg)](#)
  [![Tests](https://img.shields.io/badge/tests-599%20passing-brightgreen.svg)](#)
  [![Patreon](https://img.shields.io/badge/support-Patreon-f96854.svg)](https://www.patreon.com/c/FantasyLab3DStudio)

  [Install](#install) · [See it in action](#see-aurora-in-action) · [Aurora Copilot](#-aurora-copilot--for-humans) · [Aurora Cortex](#%EF%B8%8F-aurora-cortex--for-ai-systems) · [Roadmap](ROADMAP.md) · [FantasyLab.ai](https://fantasylab.ai)
</div>

---

## See Aurora in action

A real run on an environmental air-quality dataset — captured straight from a Studio session.

<div align="center">
  <img src="docs/screenshots/07-summary-run.png" alt="Aurora run summary banner — domain selector, run status, fabricated chip, and confidence"/>
  <p><em><strong>The run banner.</strong> Domain selector across the top (Research / Ops / Industrial / Finance / Medical / Economics / Sports / Logistics / Custom+) sets context. The Aurora Pulse line below states the run's status in plain English. The <code>0 fabricated</code> chip is the contractual signal that every finding traces to a method.</em></p>
</div>

<div align="center">
  <img src="docs/screenshots/01-hero-studio.png" alt="Aurora Studio — Overview cube and Intelligence tiles after a real analytical run"/>
  <p><em><strong>The Studio.</strong> The Overview cube rotates through six analytical lenses (Overview, Anomalies, Regimes, Motifs, Forecast, Physics). Below it, the Intelligence row surfaces the top anomalies (20 critical), the forecast peak prediction, a what-if causal answer, and the discovered physics law — <code>y = a·t² + b·t + c</code> at RMSE 126.720 — all live and grounded in artifacts, not LLM guesses.</em></p>
</div>

### The moat: every sentence cites a source

<div align="center">
  <img src="docs/screenshots/02-cited-synthesis.png" alt="What This Means — cited synthesis with Grounded in 12 knowledge entries panel below"/>
  <p><em><strong>"What This Means" reads like a research paragraph because it is one.</strong> Each claim is tagged with a <code>seed:*</code> citation — <code>seed:diurnal_cycle</code>, <code>seed:mutual_information_kg</code>, <code>seed:causal_chain</code>, <code>seed:physics_match</code>, <code>seed:wavelet_morlet</code>, <code>seed:sindy</code> — that links to the exact knowledge-bank entry backing it. The panel below lists all 12 entries Aurora actually retrieved, each with its real source: Newton (1701), French AP (1971), Pierson &amp; Moskowitz (1964), NIST, NOAA NDBC, Torrence &amp; Compo (1998). <strong>No invented citations. No invented numbers. No invented papers.</strong></em></p>
</div>

<table>
  <tr>
    <td width="50%" align="center" valign="top">
      <img src="docs/screenshots/06-findings.png" alt="Findings cards with method chip, severity, threshold and seed citation"/>
      <br/><sub><b>Findings as structured atoms.</b> Each card is a typed object — method, severity (crit / high / med), threshold, evidence, citation — not a paragraph of LLM prose. <code>+448.6σ</code> with <code>p &lt; 0E+0</code> isn't a vibes-level "anomaly"; it's a Hampel z-score on row 6715 you can re-run.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <img src="docs/screenshots/03-advanced-methods.png" alt="Advanced methods panel showing 19 research-grade methods with honest skip reasons"/>
      <br/><sub><b>19 advanced methods, honestly disclosed.</b> HMM (3 latent regimes), mutual info (13-feature matrix), Granger (5 causal pairs), Wavelet Morlet CWT, Gaussian process, persistent topology, multivariate outliers (325 of 5000 flagged by ≥2 detectors). Methods that couldn't run are explicitly <code>skipped</code> with the reason — <code>no_time_axis</code>, <code>negative_values_present</code>, <code>cross_sectional_no_time_axis</code>. No silent failure.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center" valign="top">
      <img src="docs/screenshots/04-spacetime.png" alt="Spacetime view — entity worldlines with a threshold cross marker in the forecast cone"/>
      <br/><sub><b>Spacetime worldlines.</b> Eight entity worldlines (Wind, Atmospheric pressure, Solar input, Humidity, Air temperature, Sea-surface temp, Wave height, Precipitation) plotted against time. The vertical "NOW" line separates past from the forecast cone. The orange marker is a <strong>predicted threshold cross at +2.0h</strong> — fired by Aurora, not a human.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <img src="docs/screenshots/05-phase-space.png" alt="Phase space view — the system's state trajectory through its attractor basin"/>
      <br/><sub><b>Phase Space.</b> The system reduced to a 2D state projection. The cyan <strong>NOW</strong> marker is current position; the trail behind it is the trajectory it took to get there. Pressure ↔ solar, pressure ↔ humidity, pressure ↔ air_temp resonances (35.71m, coh 1.0) drive the geometry.</sub>
    </td>
  </tr>
</table>

<sub><em>Captured on a 9,357-row cross-sectional air-quality dataset at AUTO tier. Run took ≈14 seconds local on consumer hardware. <code>0 fabricated</code>. 12 cited knowledge entries. Three methods deferred with reason.</em></sub>

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

> **Status:** v1.1 shipped (May 2026). v1.2 substantially shipped (streaming, cloud, Jupyter, contracts actions, runs library, MCP HTTP). v2.0 actively shipping on `main` (causal do-calculus, multi-dataset joins, Plugin SDK, custom KB ingestion, bundle attestation, KB marketplace, Kafka + Postgres CDC connectors, GPU embeddings). 599 tests passing locally; ~625 in CI. Real users running it on real data.

### Run it in Docker (BYO-LLM)

```bash
cp .env.example .env           # pick a provider + paste your key
docker compose up              # → Aurora Studio at http://localhost:8000
```

One command, persistent state on the host (`./aurora-data/`), no cloud
dependency. See [docs/cloud-deploy.md](docs/cloud-deploy.md) for Fly /
Railway / Render / VPS recipes + the new multi-tenant auth model.

---

## One engine. Two surfaces. Same glass-box.

Aurora has two faces sharing one analytical engine. Same code, same principles, two integration shapes — one for humans clicking through findings, one for AI systems calling APIs.

### 🧠 Aurora Copilot — for humans

*For analysts, quants, scientists, engineers.*

A local quantitative copilot for the work that matters too much to trust to a model that hallucinates. Drop in a dataset and get rigorous findings — anomalies surfaced, causal relationships tested, forecasts with confidence bounds, every claim cited to the underlying computation. No cloud LLM guessing. No black-box math.

- **Glass-box studio** — six analytical lenses (Overview, Anomalies, Regimes, Motifs, Forecast, Physics), spacetime system graph, phase-space projection
- **24+ research-grade methods** — Isolation Forest, robust z-score (Hampel), Granger, HMM Baum-Welch, persistent homology, SINDy, Gaussian processes, mutual information, **VAR**, **DTW**, **BOCPD**, **Robust PCA**, **EMD**, **Kalman**, **Spectral entropy**, and more
- **Knowledge-grounded synthesis** — every "What This Means" sentence cites a `seed:*` entry in a public, licensed knowledge bank
- **Preflight data-quality** — schema validation, missingness pattern detection, irregular-sampling check before any analysis runs (the **`data ok / N issues`** chip next to the fabricated counter)
- **Causal inference (do-calculus)** — Pearl-style backdoor identification + adjustment-OLS estimation. The legacy WHAT-IF panel now ships a "do() causal verdict" beneath every simulator output; the v2.0 LAB has a dedicated full-screen Causal tab for explicit treatment/outcome/intervention queries + counterfactuals
- **Streaming / continuous mode** — point Aurora at a directory; findings refresh as new data lands. Per-finding dedupe so the bus only fires on genuinely new findings; opt-in Decision Contracts auto-fire bridge. **Kafka + Postgres CDC connectors** for non-file sources (deps gated, listed in the v2.0 LAB → Connectors tab)
- **Composable findings** — a finished run's fitted physics / regime / baseline becomes a prior for the next run; aligned findings get a **PRIOR** badge
- **Multi-dataset joins** — pair two finished runs to see shared keys, schema compatibility, and inheritance candidates
- **Runs Library** — pin runs across sessions, A/B compare any two runs, share runs as portable `.aurora.json` bundles
- **Custom KB ingestion** — drop a folder of PDFs / TXT / MD; Aurora walks it, extracts text, folds chunks into your workspace KB. Browse-folder UI in the v2.0 LAB
- **KB pack marketplace** — install curated domain KB packs (Climate / Finance / Biomed / Industrial); preview-state packs labelled honestly
- **Bundle attestation** — three-check rollup (integrity + Ed25519 signature + trusted-signer registry) on any `.aurora.json`
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
| **Aurora Jupyter** ([docs](docs/jupyter.md)) | Notebook users | `aurora.run(df)` with rich HTML reprs, `to_html_report()` exports |
| **Aurora MCP** ([docs](docs/mcp.md)) | LLM agents (Claude Desktop, Claude Code, Cursor, custom) | 7 MCP tools via stdio or HTTP transport; path-allowlisted, output-capped, JSON-only |
| **Decision Contracts** ([docs](docs/decision-contracts.md)) | Automation pipelines | Programmable predicates → webhook / log / file / **Slack / Discord / email** when findings match. SSRF + recipient-cap guards. Streaming bridge fires contracts on live findings |
| **Aurora Streaming** ([docs](docs/streaming.md)) | Live data feeds | File-watcher + rolling window + SSE event bus; per-finding dedupe; optional contracts auto-fire. **Kafka + Postgres CDC connectors** (deps gated) |
| **Aurora Causal** ([docs](docs/causal-inference.md)) | Analysts asking "what if X?" | Pearl do-calculus: backdoor identification + adjustment OLS + counterfactual queries on the run's system_model DAG |
| **Runs Library** ([docs](docs/runs-library.md)) | Anyone iterating on a dataset | Pin runs across sessions, A/B compare two runs, export portable `.aurora.json` |
| **Plugin SDK** ([docs](docs/plugins.md)) | Domain specialists | Register third-party methods via the `aurora_plugins` entry-point group; same finding contract as built-ins |
| **Custom KB ingestion** | Researchers with private libraries | Drop a folder of PDFs / TXT / MD into your workspace KB |
| **Bundle Attestation** | Anyone consuming a shared `.aurora.json` | Verify integrity + Ed25519 signature + trusted-signer registry in one call |
| **Aurora Cloud** ([docs](docs/cloud-deploy.md)) | Self-hosted deployments | Docker image, BYO-LLM, multi-tenant auth, per-workspace data isolation, usage logging |
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

## What's working today

The v1.1 substrate — Bundle Format, SDK, MCP, Decision Contracts, Research Kit, six analytical lenses — is **shipped and stable**. The v1.2 sprint is substantially complete; v2.0 work is landing continuously on `main`.

**Analytical methods (17 → 24+):**
- v1.1: Isolation Forest, Hampel z-score, HMM, mutual information, Granger, wavelet, Lomb-Scargle, Gaussian process, persistent topology, SINDy, multivariate outliers, cluster stability, Bayesian, panel, survival, spatial, network, DiD
- v1.2 added: **VAR**, **DTW**, **BOCPD**, **Robust PCA**, **EMD**, **Kalman**, **Spectral entropy** — each with skip reasons + glass-box compliance

**v1.2 surfaces (mostly shipped):**
- **Streaming / continuous mode** — `/api/stream/start|stop|status|events` with SSE event bus, per-finding dedupe, opt-in Decision Contracts bridge, Live Findings strip in the Studio
- **Aurora Cloud** (Phase 1 + 2) — Docker + docker-compose + cloud-deploy guide. Multi-tenant auth + per-workspace data isolation + usage logging behind `AURORA_AUTH_REQUIRED=1`
- **BYO-LLM** — pluggable provider abstraction with 5 backends (Anthropic / OpenAI / Gemini / Ollama / OpenAI-compatible)
- **Decision Contracts: Slack / Discord / Email actions** — extend the v1.1 webhook / log / file types. SSRF + recipient caps + URL token redaction
- **Runs Library UI** — pin / A/B compare (via the joins endpoint) / share-as-bundle, all from a top-toolbar chip
- **MCP HTTP transport** — `/mcp/v1/*` endpoints for remote agents that can't subprocess locally; standalone server mode with optional token gate
- **Preflight data-quality** — schema + missingness + irregular-sampling checks. `data ok / N issues` pill alongside the fabricated chip
- **Jupyter integration** — `aurora.run(df)` with rich HTML reprs, `to_html_report()`, sample notebook
- **KB pack distribution** — manifest + downloader + 4 starter packs + registry auto-detection

**v2.0 surfaces (shipping, exposed in the ⚡ v2.0 LAB modal):**
- **Causal inference (do-calculus)** — `fantasyai/aurora/causal/`. Backdoor identification + adjustment-OLS estimation + counterfactual queries. The legacy WHAT-IF panel now also returns a "do() causal verdict" beneath every simulator output
- **Multi-dataset joins** — `/api/joins/analyze` produces shared keys + schema compatibility + cross-correlation hints + inheritance candidates between two finished runs
- **Composable findings** — extract priors from a finished run, inherit them into the next; aligned findings get a `PRIOR · matches / drifts / novel` badge
- **Plugin SDK** — third-party methods register via `aurora_plugins` entry-point group; same contract as built-ins; failures are isolated
- **Custom KB ingestion** — drop a folder of PDFs / TXT / MD; Aurora walks it and folds chunks into the workspace KB. Folder picker + drag-and-drop in the v2.0 LAB
- **Bundle attestation** — three-check rollup (integrity + Ed25519 signature + trusted-signer registry) on any `.aurora.json`
- **KB pack marketplace** — install curated domain KB packs (Climate / Finance / Biomed / Industrial). Preview-state packs honestly labelled "not yet released"
- **Streaming connectors** — Kafka + Postgres CDC connectors for non-file sources. Deps are gated; the v2.0 LAB → Connectors tab lists install hints
- **GPU embeddings** — `AURORA_EMBEDDINGS_DEVICE=cuda|mps|auto` env-var device selection with graceful CPU fallback

**Counts:**
- **599 tests passing** locally; ~625 in CI (scipy + flask-dependent tests run there)
- 16 backend modules import cleanly
- 0 fabricated findings — contractual

Local execution end-to-end. No cloud dependency after initial knowledge bank download.

## What's rough

Named honestly:

- The knowledge bank ships with a seed set; the full pack is downloaded separately and is still expanding
- The 4 v2.0 marketplace packs (Climate / Finance / Biomed / Industrial) are **reserved in the manifest but not yet hosted** — SHAs are `PENDING_FIRST_BUILD`. The Studio labels them `PREVIEW` and disables install until the next manifest revision
- Some advanced methods skip on datasets that lack required structure (no time axis, no entity column) — these skips are correct glass-box behavior; can be confusing without reading the skip reason
- Per-method timeouts (90 s default) defer some methods on very large datasets; disclosed honestly
- Mobile / tablet responsiveness still pending
- Browser security prevents Aurora from reading absolute folder paths from a `<input type="file">` picker; KB ingest seeds the folder name + asks the user to type the absolute path

Build-in-public log: [CHANGELOG.md](CHANGELOG.md).

---

## Built in the open. By a real person. For real work.

Aurora is fully open source under Apache 2.0. The engine, the schema, the baseline templates, the MCP server, the SDK, the webhook layer — all of it. No black box at any layer, including the codebase itself.

The roadmap is public. The build is documented on YouTube. Domain experts who contribute knowledge bank entries or templates will be able to earn from the upcoming marketplace (v2.0). Aurora gets smarter as the community grows.

- ⭐ [Star on GitHub](https://github.com/FantasyLab-ai/aurora) — visibility
- 💜 [Back on Patreon](https://www.patreon.com/c/FantasyLab3DStudio) — recurring support funds the build
- 📺 [Follow the build on YouTube](https://www.youtube.com/channel/UCUtqOJYK9qBIXmpNaeJRjfQ)
- 🎬 [Daily clips on TikTok](https://www.tiktok.com/@fantasylab.ai)
- 🐦 [Daily progress on X](https://twitter.com/Fantasylab_ai)

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full picture. Recent progress:

- **v1.2 — substantially shipped:** Streaming Phase 1+2 · Aurora Cloud Phase 1+2 (Docker + BYO-LLM + multi-tenant auth) · 7 new analytical methods · Jupyter integration · KB pack distribution · Preflight data-quality · **Slack/Discord/Email contract actions** · **Runs Library UI** · **MCP HTTP transport**
- **v2.0 — actively shipping on `main`:** Causal inference (do-calculus) · Multi-dataset joins · Composable findings · Plugin SDK · **Custom KB ingestion (PDFs → KB)** · **Bundle attestation service** · **KB pack marketplace (preview)** · **Kafka + Postgres CDC streaming connectors** · **GPU embedding device gate**
- **v1.2 tail (still open):** Bundle Format v1.1 (signed delta updates) · conversational copilot polish · KB community contribution flow · domain template packs · DuckDB ingest · mobile/tablet responsiveness pass
- **v2.0+ (6–12 months):** Federated knowledge contribution · marketplace with creator revenue share · Aurora kernels · Aurora-as-CI · Web Component embeds · mobile companion

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

The Aurora project itself is built as part of FantasyLab.ai.

---

<div align="center">

  **Aurora is part of [FantasyLab.ai](https://fantasylab.ai)** · Glass-box, local-first, source-available AI tools for serious work

</div>

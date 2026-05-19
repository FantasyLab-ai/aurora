# Aurora Architecture

This document describes Aurora's system design — the pipeline, the data flow, the subsystems, and the key engineering decisions. If you want to understand how Aurora actually works under the hood, this is the place to start.

## One engine. Two surfaces.

Aurora is one analytical engine exposed through two surfaces with equal billing:

- **🧠 Aurora Copilot** — the Web Studio. For humans: analysts, quants, scientists, engineers clicking through findings, intervening on the system graph, reading the cited synthesis.
- **🛡️ Aurora Cortex** — the verification layer for AI systems. For agents and pipelines: SDK, MCP server, Decision Contracts, Research Kit — all consuming the same Aurora Bundle Format v1.

Same code, same principles, two integration shapes. The Copilot is what you see; the Cortex is what your code, your agents, and your automation call. **Cloud LLMs guess. Aurora computes.**

## Design Principles

Aurora is built on six non-negotiable principles. Every architectural decision serves these:

1. **Glass-box at every layer.** Every node, edge, finding, and recommendation traces to its source. Bundles carry a SHA-256 content hash and can be Ed25519-signed. No mystery layers.

2. **Local-first, always.** Your data, your CPU, your runs. No cloud dependencies after initial knowledge bank download. No telemetry. No phone-home.

3. **Honesty rule (anti-hallucination by construction).** Deterministic math runs first. The LLM only translates pre-computed findings via strict RAG grounding with post-hoc verification. The LLM never makes analytical decisions. Uncertain relationships render as uncertain. When methods sample or time out, the user is told.

4. **Reproducibility.** The same input + seed produces the same output. Methods don't drift across runs. The Aurora Bundle's SHA-256 content hash is the proof.

5. **Open source.** Apache 2.0. Inspectable. Forkable. The codebase itself is part of the glass-box promise.

6. **Substrate-shaped.** Aurora is a tool *other* tools depend on. Every output is a stable, citeable, signable artifact. SDK + MCP + Decision Contracts + Research Kit are the surface area for the world to plug in.

## High-Level Pipeline

```
                  ┌─────────────────────────────────────────────────────┐
                  │                  User's Machine                      │
                  │                                                       │
   CSV / Parquet  │     ┌──────────────┐                                  │
   / JSON / XLSX ─┼───► │   Ingest +    │                                  │
                  │     │ Shape Detect  │                                  │
                  │     └──────┬───────┘                                  │
                  │            ▼                                          │
                  │     ┌──────────────┐    ┌──────────────────────┐      │
                  │     │ Tier Selector│ ──►│   Sample (stratified) │      │
                  │     └──────┬───────┘    └──────────┬───────────┘      │
                  │            ▼                       ▼                  │
                  │     ┌─────────────────────────────────────┐          │
                  │     │   Deep Math + Advanced Pass         │          │
                  │     │   17+ methods, 90 s/method budget   │          │
                  │     │   Per-method timeouts + sampling    │          │
                  │     └────────────────┬────────────────────┘          │
                  │                      ▼                                │
                  │     ┌─────────────────────────────────────┐          │
                  │     │   RAG Retrieval (Knowledge Bank)    │          │
                  │     │   Sentence-transformer embeddings   │          │
                  │     └────────────────┬────────────────────┘          │
                  │                      ▼                                │
                  │     ┌─────────────────────────────────────┐          │
                  │     │   Synthesis (local LLM via Ollama)  │          │
                  │     │   Strict RAG: only retrieved facts  │          │
                  │     └────────────────┬────────────────────┘          │
                  │                      ▼                                │
                  │     ┌─────────────────────────────────────┐          │
                  │     │   Verification + Disclosure         │          │
                  │     │   "0 fabricated" contract enforced  │          │
                  │     └────────────────┬────────────────────┘          │
                  │                      ▼                                │
                  │     ┌─────────────────────────────────────┐          │
                  │     │   State + Aurora Bundle Format v1   │          │
                  │     │   SHA-256 integrity hash + signing  │          │
                  │     └────────┬───────────────────┬────────┘          │
                  │              ▼                   ▼                    │
                  │    ┌──────────────┐    ┌────────────────────┐         │
                  │    │ Web Studio   │    │  Substrate layer:  │         │
                  │    │ (UI)         │    │  SDK / MCP /       │         │
                  │    │              │    │  Contracts / Kit   │         │
                  │    └──────────────┘    └────────────────────┘         │
                  │                                                       │
                  └─────────────────────────────────────────────────────┘
                            ▲                              ▲
                            │                              │
                       Browser at                   pip install / MCP /
                       127.0.0.1:8000               webhook from any LLM
```

The pipeline runs in sequence:

1. **Ingest** — Read the input file (CSV, TSV, JSON, JSONL, Parquet, XLSX). Detect schema, time axis, gaps, duplicates, cadence.

2. **Shape Detection** — Classify the dataset's shape (linear-monotonic, oscillatory, regime-switching, cross-sectional, etc.) and select appropriate domain priors.

3. **Tier Selection** — Based on data size and selected tier (AUTO / QUICK / STANDARD / FULL), decide which methods to run on full data versus sampled subsets.

4. **Sample** — If needed, create a stratified time-preserving sample. Extreme outliers (|z| ≥ 4) are always retained.

5. **Deep Math** — Run the core analytical methods in `compute_deep_math_v3`: forecasting, regime detection, bootstrap CI, monte carlo risk surface, physics discovery, physics invariants. Each method is wrapped in a 90-second timeout.

6. **Advanced Pass** — Run the 17 advanced research-grade methods in `apply_advanced_pass`: HMM, wavelet, mutual info, Granger, SINDy, topology, GP, etc. Each is also wrapped in a 90-second timeout.

7. **RAG Retrieval** — Query the knowledge bank for entries relevant to the findings produced by steps 5-6. Score by semantic similarity using sentence-transformer embeddings.

8. **Synthesis** — Local LLM (Gemma 3 12B by default) writes the "What This Means" narrative, using only the retrieved knowledge entries as factual grounding. Strict RAG.

9. **Verification** — Post-hoc verifier checks every claim in the narrative against the retrieved entries. Flag or rewrite anything that doesn't trace back. The `fabricated_count` chip in the Studio is the live audit signal.

10. **Disclosure** — Append pipeline notes: which methods were sampled, which timed out, what fraction of data was analyzed. Deterministic, not LLM-generated.

11. **Render + Bundle** — Build the state object the frontend consumes. Spacetime graph constructed. Findings rendered. Aurora Bundle (v1) generated with integrity hash.

## Subsystems

### Quantitative Engine

Lives in `fantasyai/aurora/math/` and `fantasyai/aurora/stats/`. Each method is a standalone function with:
- Clear input shape requirements
- Explicit skip conditions if preconditions aren't met
- Structured output with confidence/uncertainty fields
- Optional sampling and timeout wrappers

Methods are run in parallel where possible. No method blocks the pipeline more than 90 seconds (configurable). Methods that exceed the budget are honestly reported as deferred.

### Knowledge Bank

A SQLite database living in the user-data directory (`~/.aurora/knowledge_bank/aurora_kb.db` on macOS/Linux; `%APPDATA%\Aurora\knowledge_bank\` on Windows). Schema in [docs/knowledge-bank.md](docs/knowledge-bank.md).

Entries come from public, licensed sources:
- Peer-reviewed papers (foundational methods)
- FRED economic releases (US Federal Reserve data)
- NOAA observations (climate, oceanographic)
- NIST reference databases (physical constants, materials)
- IPCC climate reports
- Wikidata structured knowledge
- arXiv preprints (selective ingest)

Each entry has: a unique seed identifier, source citation, content, embedding vector, version, ingestion timestamp.

The bank ships in two forms:
- Seed bank (~50 MB, committed to the repo) — covers the demo datasets and foundational methods
- Full bank (hosted on Hugging Face, ~2 GB) — comprehensive coverage across domains

### Synthesis Engine (RAG)

Lives in `fantasyai/aurora/synthesis/`. Two-stage pipeline:

1. **Retrieval.** Embed the structured findings using sentence-transformers (`all-mpnet-base-v2` or similar). Query the knowledge bank for top-K most similar entries. Re-rank by relevance to specific finding categories.

2. **Generation.** Pass the findings + retrieved entries to the local LLM (Gemma 3 12B via Ollama by default). Strict prompt: "Use only the facts below. Cite each statement with the `seed:*` identifier. Do not invent claims."

The synthesis falls back to template mode (deterministic, no LLM) if:
- The LLM is unavailable
- Retrieval produces no relevant entries
- The verifier rejects too many claims

This guarantees Aurora always produces *some* output, even in degraded mode.

### Verification Layer

After synthesis, each claim in the narrative is checked against retrieved entries. Implemented as:
- Sentence-level segmentation of the narrative
- Per-sentence entailment scoring (does the retrieved entry support this claim?)
- Flagging of unsupported sentences
- Optional rewrite or removal

This is what makes Aurora glass-box even with an LLM in the loop. The LLM can be wrong; the verifier catches it. The Studio's **0 fabricated** chip is the live signal that the contract held for this run.

### Spacetime System Graph

Lives in `fantasyai/aurora/system_model/`. Per-run construction:
- Nodes for variables (input columns) and processes (derived stages)
- Edges for discovered relationships (causal, correlative, lagged)
- Worldlines: temporal projections of each variable forward and backward
- Threshold-cross events: where the math predicts something is about to break
- Phase-space projection onto the two most-variable axes, with attractor centroids + bifurcation manifold

The graph is rendered in the frontend as the SPACETIME view, with interactive scrubbing, counterfactual simulation (`INTERVENE`), and forward-stepping validated dynamics (`SIMULATE`).

## Aurora Cortex — the substrate layer

Aurora's outputs are first-class artifacts that *other* code can consume. This is the verification cortex AI agents and automation pipelines plug into — four surfaces, all consuming and producing the same `.aurora.json` bundle:

### Aurora SDK (`aurora_sdk/`)

A pure-Python SDK that wraps the pipeline and emits a **stable, versioned Aurora Bundle** (`.aurora.json`). The bundle is a single JSON document with:

- All findings, normalised with `claim_id` per finding
- Dataset identity (basename + SHA-256)
- Run identity (id, tier, timestamps)
- Method registry (which methods produced findings, by severity)
- Assumptions Aurora made
- Confidence
- `fabricated_count` (Aurora's contract guarantee)
- **Integrity block** with SHA-256 content hash over a canonical JSON serialisation

Bundles are tamper-evident. Optionally signable with Ed25519 (`pip install cryptography`). See `aurora_sdk/bundle.py`.

The SDK exposes `Findings`, `ForecastView`, `SystemModelView` helpers so notebook code stays readable:

```python
r = aurora.run("data.csv", depth="standard")
r.findings.critical().by_method("iso-forest")
r.forecast.peak(horizon_hours=24)
r.bundle.save("audit.aurora.json")
```

### Aurora MCP (`aurora_mcp/`)

A Model Context Protocol server that exposes Aurora's tools to any MCP client (Claude Desktop, Claude Code, Cursor, custom agents). Seven tools:

- `aurora_analyze` — run Aurora on a dataset
- `aurora_load_bundle` — load + verify a `.aurora.json`
- `aurora_findings` — list with severity/method filters
- `aurora_forecast` — points + peak
- `aurora_explain` — evidence + method spec for a `claim_id`
- `aurora_intervene` — propagate Δ through the system graph
- `aurora_simulate` — forward-step validated dynamics

Transport: stdio (default). Security: path allowlist enforced per call; 2 MB response cap; tools never raise across the boundary; no shell, no eval, no subprocess. See `aurora_mcp/tools.py`.

### Decision Contracts (`fantasyai/aurora/decision_contracts/`)

Programmable predicates that fire actions when an Aurora bundle satisfies a condition. A contract is a JSON document with:

- `trigger` — a single predicate expression (e.g., `findings.crit_count >= 3`)
- `actions` — webhook / log / file actions to run when satisfied
- `rate_limit` — per-contract cap (`max_per_minute|hour|day`)
- `metadata` — owner, description

Engine: pure, declarative, no eval. Operators: `==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not_in`, `contains`, `regex`. Field-path resolver supports special aggregates (`findings.crit_count`, `confidence`, `forecast.peak`).

Security: webhook URLs validated (`http(s)` only; private/loopback IPs blocked unless `AURORA_ALLOW_LOCAL_WEBHOOKS=1`); 1 MB request body cap; 30 s timeout cap; Authorization/X-API-Key/Cookie headers redacted in audit records; file actions sandboxed to `AURORA_CONTRACTS_OUTPUT`; rate-limited.

Every firing produces a `FiringRecord` audit row.

### Research Kit (`fantasyai/aurora/research_kit.py`)

Generates a publication-ready directory from any Aurora Bundle:

- `methods.md` — full methods section, LaTeX-ready Markdown
- `references.bib` — BibTeX, one entry per cited prior (with a built-in library of canonical citations; unknown methods get a placeholder `@misc`)
- `replication.json` — deterministic re-run config + bundle hash
- `.zenodo.json` — Zenodo deposit metadata for DOI minting
- `README.md` — orientation for reviewers
- `bundle.aurora.json` — the source bundle

Designed for academic users: every method gets a plain-English description and a canonical citation. The kit's `replication.json` carries the dataset SHA-256 + Aurora version, so reviewers can verify the analysis byte-for-byte.

## Post-launch additions (v1.2 work landing on `main`)

The v1.1 substrate above is stable. The v1.2 sprint added five new subsystems that share the same glass-box principles + bundle format.

### Streaming / continuous mode (`fantasyai/aurora/streaming/`)

Watches a directory for new files (or accepts programmatic DataFrame ingestion), maintains a rolling window over the most-recent N rows, re-runs the analytical pipeline as data arrives, and publishes events through an in-process event bus that the Studio + any SSE client consumes.

Phase 2 adds a **per-finding dedupe store** (bounded LRU of finding-identity hashes — method + title + severity + key evidence fields) so the bus only fires `new_finding` when something genuinely changed across polls. An opt-in **Decision Contracts bridge** evaluates loaded contracts against streaming findings; matching contracts fire their actions in real time. Both surface in the Studio's STREAMING popover.

### Aurora Cloud (`fantasyai/aurora/llm/` + `fantasyai/aurora/auth/`)

Phase 1 packages Aurora into a Docker image with bind-mounted persistence + an **`LLM` provider abstraction** (Anthropic / OpenAI / Gemini / Ollama / OpenAI-compatible) so a single image runs anywhere with BYO credentials.

Phase 2 layers **multi-tenant auth** on top:
- Bearer-token resolution from env vars (`AURORA_TOKEN_<workspace>`) or a JSON token file
- A `before_request` hook stamps every request with a `WorkspaceContext` and records usage to per-workspace `usage.jsonl`
- Per-workspace data isolation under `$AURORA_DATA_ROOT/workspaces/<id>/{runs,kb,contracts,uploads}/` with strict path-injection guards
- Workspace identity chip in the Studio toolbar (hidden in single-tenant deploys; preserves Phase 1 UX)

Set `AURORA_AUTH_REQUIRED=1` to enforce; default is unchanged (no auth, single workspace).

### Composable findings (`fantasyai/aurora/composable/`)

After a run finishes, the **extractor** pulls a compact prior pack (physics best-fit law + regime structure + baseline range + anomaly z-floors). The **applier** consumes that pack on the next run and tags aligned findings with a `prior_source` decoration (`matches` / `drifts` / `novel`).

The state_builder exposes `state["composed_from"]`; the frontend renders a colour-coded PRIOR badge on tagged findings + an INHERIT picker chip in the toolbar. `/api/run` accepts `inherit_from` and the runner stages the source pack into the new run dir.

### Multi-dataset joins (`fantasyai/aurora/joins/`)

Pure read computation over two finished runs. Surfaces shared keys (with overlap-quality %), schema compatibility (cadence, row-count ratio), cross-correlation hints (mined from per-run anomaly attribution), and **inheritance candidates** — A's physics law or regime K suggested for B and vice versa.

Endpoint: `/api/joins/analyze`. Frontend: `JOIN RUNS` popover with two run selectors.

### Plugin SDK (`fantasyai/aurora/plugins/`)

Third-party Python packages register methods via the standard `aurora_plugins` entry-point group. The registry validates each emitted finding against the same contract built-ins use (`fabricated=True` is hard-refused), catches plugin crashes, and emits synthetic "plugin crashed" findings on failure — so a buggy plugin can never silently corrupt results.

Plugin findings flow into the bundle alongside the built-in 24+ methods. The Studio's PLUGINS chip shows the loaded count + per-plugin status (loaded / import_error / contract_error) + last-run telemetry.

See [docs/plugins.md](docs/plugins.md) for the authoring guide.

### 7 new analytical methods

VAR, DTW, BOCPD, Robust PCA, EMD, Kalman, and Spectral Entropy each live under `fantasyai/aurora/math/methods/`. They share a common shape (function takes a DataFrame, returns an Aurora-shaped finding with `evidence.status in {fit, skipped, failed}`) so the same `extended_runner` orchestrates them. Findings flow through state_builder into a dedicated 7-tile section of the Studio's ADVANCED METHODS grid.

### Preflight data-quality (`fantasyai/aurora/preflight/`)

Schema validation + missingness pattern detection (MCAR vs MAR vs blocky) + irregular-sampling check. Runs before any analysis can be queried. The Studio surfaces the result as a **`data ok / N issues` pill** next to the `0 fabricated` chip — the seventh-lens-by-spirit signal.

## Performance

Aurora is optimised for the workstation, not the datacenter:

- Memory: typically 200–500 MB peak; 1 GB worst case on very wide datasets
- CPU: scales with method count; benefits from multi-core for parallel method execution
- Disk: ~500 MB for the application + ~2 GB for the full knowledge bank
- GPU: optional; speeds up the LLM synthesis 3–10×

Typical runtimes (AUTO depth):

- Small datasets (≤10 K rows): 3–10 seconds
- Medium datasets (10 K–100 K rows): 30 s – 3 minutes
- Large datasets (100 K–1 M rows): 2–5 minutes (with sampling)
- Very large (>1 M rows): 5–15 minutes (with aggressive sampling)

Sampling is honest — the user sees exactly what fraction of their data was analysed.

## Determinism and Reproducibility

Every Aurora run is deterministic given:

- Same input data
- Same seed (default: 42)
- Same Aurora version
- Same knowledge bank version

The Aurora Bundle's **content hash** is the proof: SHA-256 over a canonical JSON serialisation of all bundle fields except `generated_at` and `integrity` itself. The same logical content always produces the same hash regardless of which day it was generated. Two runs from the same state on the same Aurora version produce byte-identical content hashes.

Floating-point non-determinism in some advanced methods (e.g., Gaussian Process MCMC sampling, when enabled) may produce numerically close but not bit-identical results; tier-1 statistics are fully deterministic.

## Security and Privacy

- All data processing is local. Your data never leaves your machine.
- The only network activity is the optional initial knowledge bank download from Hugging Face Hub.
- No telemetry. No analytics. No user tracking.
- The local LLM (Gemma 3 12B via Ollama) runs entirely on your hardware.
- Aurora does not request or require any API keys.
- The MCP server enforces a path allowlist on every tool call; output capped at 2 MB.
- Decision Contracts validate webhook URLs (SSRF guard); secrets redacted in audit logs.
- Aurora Bundles can be Ed25519-signed for tamper-evident distribution.

For deployment in regulated environments (healthcare, finance, defense), see [docs/deployment.md](docs/deployment.md).

## Engineering Decisions and Tradeoffs

**Why local-first?** The audience (indie devs, researchers, small organisations, regulated industries) cannot reliably send proprietary data to a third-party cloud. A local-first architecture is also the only honest answer to the question "can we audit this?" — every byte of compute and storage is under the user's control.

**Why Gemma 3 12B as the default LLM?** Strong instruction-following at a size that fits on a single workstation GPU (≥12 GB VRAM). Apache 2.0–compatible licensing. Quantized variants run on CPU only for users without a GPU. The synthesis layer is model-agnostic — swap in any Ollama-served model via `OLLAMA_MODEL=...`.

**Why SQLite for the knowledge bank?** Single-file, no server, replicable, portable. Sentence-transformer embeddings are stored alongside content so retrieval is one query. Supports up to several million entries comfortably on a workstation.

**Why no orchestration framework?** Aurora is a pipeline, not a DAG of microservices. Direct Python function calls are observable, deterministic, and don't introduce a dependency on Airflow/Prefect/Dagster. If a user wants to embed Aurora in such a framework, the SDK is the integration point.

**Why deterministic seeds?** Reproducibility is the foundation of scientific trust. Random methods (bootstrap, MCMC) all consume seeds derived from a single user-supplied root. Two runs at the same seed produce identical output (modulo floating-point determinism on the specific platform).

**Why stdio for MCP (not HTTP)?** Stdio is the simplest, most isolation-friendly transport. The MCP server runs as a subprocess of the user's LLM client; no ports to open, no firewall config, no auth dance. HTTP transport will land when the use case demands it (remote inference, multi-user).

**Why MIT-style permissive licensing (Apache 2.0)?** Maximum reach for the indie/academic audience. Apache 2.0 specifically grants patent rights, which matters for enterprise adopters wanting legal certainty.

## Further Reading

- [README.md](README.md) — Project overview
- [docs/concepts.md](docs/concepts.md) — Conceptual foundations (glass-box, RAG, tiers)
- [docs/methods.md](docs/methods.md) — Each analytical method explained
- [docs/new-methods.md](docs/new-methods.md) — The 7 v1.2 extended methods
- [docs/knowledge-bank.md](docs/knowledge-bank.md) — Knowledge bank schema and contribution
- [docs/kb-packs.md](docs/kb-packs.md) — Knowledge-bank pack format + distribution
- [docs/sdk.md](docs/sdk.md) — Python SDK reference
- [docs/jupyter.md](docs/jupyter.md) — Notebook SDK surface
- [docs/mcp.md](docs/mcp.md) — MCP server setup for Claude / Cursor / agents
- [docs/decision-contracts.md](docs/decision-contracts.md) — Contracts schema + actions
- [docs/research-kit.md](docs/research-kit.md) — Research Kit + Zenodo workflow
- [docs/streaming.md](docs/streaming.md) — Streaming / continuous mode (Phase 1 + 2)
- [docs/cloud-deploy.md](docs/cloud-deploy.md) — Aurora Cloud self-host + multi-tenant auth
- [docs/plugins.md](docs/plugins.md) — Plugin SDK authoring + contract + lifecycle
- [docs/preflight.md](docs/preflight.md) — Data-quality preflight lens
- [ROADMAP.md](ROADMAP.md) — What's coming next

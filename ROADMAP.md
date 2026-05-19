# Aurora Roadmap

Timelines are aspirational, dependent on traction and feedback. Priorities shift based on user data and what the community needs most.

Legend: ✅ shipped · 🟡 in flight · ⏳ planned · 🔬 research

## Now — v1.1 (shipped May 2026)

The substrate layer + glass-box studio:

- ✅ 6 analytical lenses: Overview, Anomalies, Regimes, Motifs, Forecast, Physics
- ✅ 17+ advanced research-grade methods with honest sampling + timeout disclosure
- ✅ Local Gemma 3 12B synthesis with strict RAG grounding + post-hoc verification
- ✅ Spacetime System Graph with cube + scroll-docked navigator, INTERVENE, SIMULATE
- ✅ Tiered analysis (AUTO / QUICK / STANDARD / FULL)
- ✅ Knowledge-grounded synthesis with cited `seed:*` references
- ✅ Navigator bear (state-aware narrator with mood-tiered copy)
- ✅ **Aurora SDK + Bundle Format v1** (`.aurora.json` — SHA-256 integrity + optional Ed25519 signing)
- ✅ **Aurora MCP server** with 7 tools (path allowlist, SSRF guards, 2 MB output cap)
- ✅ **Decision Contracts** engine (predicates → webhooks/logs/files, rate-limited, audited)
- ✅ **Research Kit** (methods.md + references.bib + replication.json + .zenodo.json)
- ✅ 320 tests passing across backend, frontend audit, SDK, MCP, contracts, research kit
- ✅ Apache 2.0 open-source release

## v1.2 — partially shipped, partially in flight

**Already on `main`:**

- ✅ **Streaming Phase 1** — file-watcher + rolling window + SSE event bus + `/api/stream/*` endpoints + Studio popover. Watch a directory, get findings refreshed as new data lands.
- ✅ **Streaming Phase 2** — per-finding dedupe (only fire on genuinely new findings), opt-in Decision Contracts auto-fire bridge, Live Findings strip in the Studio
- ✅ **Notebook / Jupyter integration** — `aurora.run(df)`, HTML reprs, `to_html_report()`, sample notebook (23 tests)
- ✅ **Preflight data-quality** — schema validation + missingness pattern detection + irregular-sampling check + `data ok / N issues` chip in the Studio (34 tests)
- ✅ **7 new analytical methods** — VAR, DTW, BOCPD, Robust PCA, EMD, Kalman, Spectral Entropy. Catalogue grows to 24+.
- ✅ **KB pack distribution** — manifest format + downloader + installer + registry + auto-detect + 4 starter packs (25 tests)
- ✅ **Aurora Cloud Phase 1** — Dockerfile + docker-compose + `cloud-deploy.md` for Fly / Railway / Render / VPS
- ✅ **BYO-LLM** — pluggable abstraction with 5 backends (Anthropic / OpenAI / Gemini / Ollama / OpenAI-compatible) — zero new runtime deps

**Still in v1.2 queue:**

- ⏳ **Aurora Bundle Format v1.1** — backward-compatible additions: signed delta updates, embedded media references
- ⏳ **Decision Contracts: more action types** — Slack, Discord, PagerDuty, email (SMTP), database insert
- ⏳ **Runs library UI** — pinned runs, A/B compare across runs, share a run as a public bundle URL (backend `/api/runs` exists; Studio panel still placeholder)
- ⏳ **Conversational copilot polish** — Ask Aurora answers grounded in the current run's bundle, with `seed:*` citations
- ⏳ **Knowledge bank expansion** — incremental additions covering more domains, with a community contribution flow
- ⏳ **Domain template packs** — industrial, finance, medical scaffolding (full packs land in v2.0)
- ⏳ **DuckDB ingest layer** — faster Parquet/CSV reads for very large files
- ⏳ **MCP HTTP transport** (optional, for remote agents that can't subprocess locally)
- ⏳ **Mobile / tablet responsiveness pass**

## v2.0 — partially shipped, mostly planned (3–6 months)

The platform play.

**Already on `main` (shipped ahead of schedule):**

- ✅ **Composable findings** — extract a prior pack from a finished run (physics law, regime K, baseline range, anomaly z-floors); inherit into the next run; aligned findings get a `PRIOR · matches / drifts / novel` badge. INHERIT chip in the Studio toolbar.
- ✅ **Multi-dataset joins** — `compute_join_report(a, b)` returns shared keys + schema compatibility + cross-correlation hints + inheritance candidates. `JOIN RUNS` popover in the Studio.
- ✅ **Plugin SDK** — third-party methods register via the `aurora_plugins` entry-point group with full glass-box compliance (`fabricated=True` is hard-refused). Plugin findings flow into bundles alongside built-ins. `plugins: N` chip in the Studio.
- ✅ **Customer-hosted enterprise deployment — Phase 1+2** — Docker + cloud-deploy guide. Phase 2 adds multi-tenant auth (token-based, env or file), per-workspace data isolation under `$AURORA_DATA_ROOT/workspaces/<id>/`, append-only `usage.jsonl` for billing aggregation, workspace identity chip in the Studio.

**Still planned:**

- ⏳ **Domain knowledge bank packs marketplace** (Climate / Finance / Biomed / Industrial / Ops) — pack format ships; marketplace listing flow pending
- ⏳ **Streaming Phase 3** — Postgres CDC + Kafka connectors
- 🔬 **Causal inference (do-calculus)** — DAG editor, intervention reasoning, counterfactual queries
- ⏳ **Custom domain knowledge ingestion tooling** — drop a folder of PDFs / papers → Aurora ingests them into your private knowledge bank
- ⏳ **Signed-bundle attestation service** — third-party verification of Aurora bundle signatures
- ⏳ **GPU acceleration for embeddings** — sentence-transformer inference on GPU when available

## Future — v2.0+ (6–12 months)

The ecosystem:

- ⏳ **Federated knowledge contribution** — community-curated entries flow back into the canonical knowledge bank with provenance preserved
- ⏳ **Marketplace** for community domain packs + plugins, with revenue share for contributors
- ⏳ **Aurora kernels** — pre-fitted models for common phenomena (e.g., bearing-failure model, BTC-vol regime model), installable as `aurora-kernel-*`
- ⏳ **Aurora-as-CI** — run Aurora on every dataset change in a repo, fail CI when a Decision Contract trips
- ⏳ **Mobile companion** — remote run monitoring + alert acknowledgement (web app first; native later if demand)
- ⏳ **Web Component embeds** — drop `<aurora-graph>`, `<aurora-cube>`, `<aurora-findings>` into any web app

## Vision

Aurora becomes **the** reference for glass-box quantitative AI — the substrate every analytical workflow, every AI agent, and every regulated decision system depends on. The same engineering philosophy will extend to adjacent domains as the FantasyLab.ai brand grows.

## What we are NOT building (intentionally)

These are seductive but distracting. We'd rather be excellent at the core than mediocre at everything:

- **A generic dashboarding tool.** Tableau / Looker / Metabase exist. Aurora stays analytical, not visualisation-first.
- **Custom LLM training.** We don't compete with frontier labs. Aurora is the tool an LLM *calls*.
- **A cloud-hosted SaaS as the default.** Local-first is the moat. Cloud is the enterprise option (now shippable in Docker), not the product.
- **Real-time collaboration (Google Docs-style).** Cool but expensive; the SDK / MCP unlock most of the same value.
- **A "build your own template" GUI.** Power users want JSON/YAML; ship the format, not a builder.

## How to influence the roadmap

- **File a feature request** issue describing the use case
- **Back on [Patreon](https://www.patreon.com/c/FantasyLab3DStudio)** — backers get input on priorities
- **Submit a Decision Contract** for a use case we haven't covered — concrete usage shapes the action backlog
- **Ship an Aurora plugin** — see [docs/plugins.md](docs/plugins.md). Third-party methods are first-class citizens.
- **Contribute a method or knowledge bank entry** — see [CONTRIBUTING.md](CONTRIBUTING.md)
- **Join the conversation** in [GitHub Discussions](https://github.com/fantasylab/aurora/discussions)

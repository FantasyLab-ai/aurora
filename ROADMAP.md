# Aurora Roadmap

Timelines are aspirational, dependent on traction and feedback. Priorities shift based on user data and what the community needs most.

## Now — v1.1 (shipped)

The substrate layer + glass-box studio:

- 6 analytical lenses: Overview, Anomalies, Regimes, Motifs, Forecast, Physics
- 17+ advanced research-grade methods with honest sampling + timeout disclosure
- Local Gemma 3 12B synthesis with strict RAG grounding + post-hoc verification
- Spacetime System Graph with cube + scroll-docked navigator, INTERVENE, SIMULATE
- Tiered analysis (AUTO / QUICK / STANDARD / FULL)
- Knowledge-grounded synthesis with cited `seed:*` references
- Navigator bear (state-aware narrator with mood-tiered copy)
- **Aurora SDK + Bundle Format v1** (`.aurora.json` — SHA-256 integrity + optional Ed25519 signing)
- **Aurora MCP server** with 7 tools (path allowlist, SSRF guards, 2 MB output cap)
- **Decision Contracts** engine (predicates → webhooks/logs/files, rate-limited, audited)
- **Research Kit** (methods.md + references.bib + replication.json + .zenodo.json)
- 320 tests passing across backend, frontend audit, SDK, MCP, contracts, research kit
- Apache 2.0 open-source release

## Next — v1.2 (4–8 weeks)

The "make it widely useful" sprint:

- **Aurora Bundle Format v1.1** — backward-compatible additions: signed delta updates, embedded media references
- **Decision Contracts: more action types** — Slack, Discord, PagerDuty, email (SMTP), database insert
- **Streaming mode (phase 1)**: file-watcher connector that re-runs Aurora when a CSV changes on disk and fires contracts on the new bundle
- **Runs library** (wires up the previously placeholder Sessions panel) — pinned runs, A/B compare across runs, share a run as a public bundle URL
- **Conversational copilot polish** — Ask Aurora answers grounded in the current run's bundle, with `seed:*` citations
- **Knowledge bank expansion** — incremental additions covering more domains, with a community contribution flow
- **Notebook / Jupyter integration** — `%aurora` magic, `df.aurora.analyze()` Pandas extension
- **Domain template packs** (early) — industrial, finance, medical scaffolding (full packs land in v2.0)
- **DuckDB ingest layer** — faster Parquet/CSV reads for very large files
- **MCP HTTP transport** (optional, for remote agents that can't subprocess locally)

## Soon — v2.0 (3–6 months)

The platform play:

- **Domain knowledge bank packs** as a marketplace (Climate / Finance / Biomed / Industrial / Ops)
- **Streaming mode (phase 2)** — Postgres CDC connector, Kafka connector
- **Multi-dataset joins** — analyse two related datasets together; pull discovered relationships from dataset A into dataset B's priors
- **Composable findings** — last run's fitted physics model becomes a prior for the next run on the same line
- **Causal inference (do-calculus)** — DAG editor, intervention reasoning, counterfactual queries
- **Custom domain knowledge ingestion tooling** — drop a folder of PDFs / papers → Aurora ingests them into your private knowledge bank
- **Customer-hosted enterprise deployment** — Docker + Helm + audit-log streaming, signed-bundle attestation service
- **GPU acceleration for embeddings** — sentence-transformer inference on GPU when available
- **Plugin SDK** — third-party methods register as Aurora methods with full glass-box compliance

## Future — v2.0+ (6–12 months)

The ecosystem:

- **Federated knowledge contribution** — community-curated entries flow back into the canonical knowledge bank with provenance preserved
- **Marketplace** for community domain packs, with revenue share for contributors
- **Aurora kernels** — pre-fitted models for common phenomena (e.g., bearing-failure model, BTC-vol regime model), installable as `aurora-kernel-*`
- **Aurora-as-CI** — run Aurora on every dataset change in a repo, fail CI when a Decision Contract trips
- **Mobile companion** — remote run monitoring + alert acknowledgement (web app first; native later if demand)
- **Web Component embeds** — drop `<aurora-graph>`, `<aurora-cube>`, `<aurora-findings>` into any web app

## Vision

Aurora becomes **the** reference for glass-box quantitative AI — the substrate every analytical workflow, every AI agent, and every regulated decision system depends on. The same engineering philosophy will extend to adjacent domains as the FantasyLab.ai brand grows.

## What we are NOT building (intentionally)

These are seductive but distracting. We'd rather be excellent at the core than mediocre at everything:

- **A generic dashboarding tool.** Tableau / Looker / Metabase exist. Aurora stays analytical, not visualisation-first.
- **Custom LLM training.** We don't compete with frontier labs. Aurora is the tool an LLM *calls*.
- **A cloud-hosted SaaS as the default.** Local-first is the moat. Cloud is the enterprise option, not the product.
- **Real-time collaboration (Google Docs-style).** Cool but expensive; the SDK / MCP unlock most of the same value.
- **A "build your own template" GUI.** Power users want JSON/YAML; ship the format, not a builder.

## How to influence the roadmap

- **File a feature request** issue describing the use case
- **Back on [Patreon](https://www.patreon.com/c/FantasyLab3DStudio)** — backers get input on priorities
- **Submit a Decision Contract** for a use case we haven't covered — concrete usage shapes the action backlog
- **Contribute a method or knowledge bank entry** — see [CONTRIBUTING.md](CONTRIBUTING.md)
- **Join the conversation** in [GitHub Discussions](https://github.com/fantasylab/aurora/discussions)

# FAQ

Common questions about Aurora. If your question isn't here, [open a Discussion](https://github.com/fantasylab/aurora/discussions).

## What is Aurora, in one sentence?

Aurora is the **quantitative cortex** your code, your agents, and your team can cite without lying — a glass-box, local-first quantitative AI tool that runs real math, grounds every claim in a public citation, and never invents.

## Who is Aurora for?

- **Indie developers** building apps that need real analytical reasoning
- **Researchers & scientists** wanting reproducible, citeable analyses
- **Small organisations** that can't or won't send data to a cloud
- **Analysts & data scientists** who want a glass-box copilot in their notebook
- **AI agent builders** wiring quantitative tools into LLM workflows (via MCP)

Aurora is explicitly NOT optimised for: hyperscaler cloud SaaS shops, real-time tick trading, or use cases where you genuinely want LLM creativity over deterministic math.

## Is Aurora free?

**Yes.** The SDK + MCP + Decision Contracts + Research Kit are Apache 2.0 open source. You can use it commercially, modify it, redistribute it. You owe nothing to the project for any number of runs at any scale.

Future Pro / Enterprise tiers will add hosted knowledge-bank sync, signed-bundle attestation, team features, and SLA support. The core Aurora — what's in this repo — stays free forever.

## Does my data leave my machine?

**No.** Aurora is local-first. After the optional one-time knowledge bank download, Aurora has zero network dependencies. The SDK has no telemetry, no analytics, no phone-home. The MCP server reads only files in the path-allowlist. Decision Contracts can fire webhooks, but only to URLs you explicitly configure.

The only way your data leaves your machine is if you choose to share a bundle (e.g., post it to GitHub) or send results through your LLM client to a cloud LLM provider (your existing relationship with them, not Aurora's).

## Does Aurora send my prompts / questions to a third party?

**No.** Aurora ships with [Ollama](https://ollama.ai) integration for local LLM synthesis. The default model is Gemma 3 12B, which runs on your hardware. If Ollama isn't running, Aurora falls back to a deterministic template synthesis — no LLM, no network.

If you wire Aurora into Claude Desktop / Cursor / another cloud-LLM client via MCP, then your prompts to *that* client go to *that* vendor. Aurora itself never makes outbound LLM calls.

## How is Aurora different from ChatGPT / Claude / Gemini reading my CSV?

- **They invent numbers.** Aurora computes them.
- **They paraphrase plausibly.** Aurora cites.
- **They can't be audited.** Aurora's every output traces to a method tag + threshold + citation.
- **They send your data to a cloud.** Aurora doesn't.
- **They're not reproducible.** Aurora's bundle content hash is byte-for-byte stable.

Aurora is what you call from inside an agent like Claude when you need *cited quantitative reasoning the LLM can defend in court*. Aurora doesn't compete with the LLM — it's the math layer the LLM lacks.

## What about hallucinations?

Aurora is built specifically to prevent them. See the **Anti-hallucination architecture** section in [docs/concepts.md](concepts.md). Short version:

- Deterministic math runs first
- The LLM only paraphrases pre-computed findings
- Strict RAG: only retrieved knowledge entries are factual inputs
- Post-hoc verifier catches any unsupported sentence
- The `0 fabricated` chip is the live audit signal

If the chip ever turns red on your run, something's wrong with the pipeline (or, very rarely, a finding genuinely lacks provenance) — file an issue.

## What's the difference between Aurora and PyCaret / scikit-learn / statsmodels?

Those are *libraries*. You call them; you write the analysis; you write the report.

Aurora is a *system*. You drop a CSV; Aurora picks which methods to run, runs them, retrieves grounding from a knowledge bank, writes the narrative, and produces a citeable bundle. It uses scikit-learn / statsmodels / scipy underneath where appropriate.

If you want lower-level control, drop down to the library directly. If you want a system that makes glass-box decisions and produces an auditable artifact, use Aurora.

## What about Tableau / Looker / Power BI?

Those are dashboarding tools. Aurora is an analytical reasoning tool. They visualise data; Aurora explains it.

You can wire Aurora's output (the bundle) into any dashboard — but the dashboards don't replace Aurora and Aurora doesn't replace the dashboards.

## How big a dataset can Aurora handle?

Comfortable sizes:

- **Small** (≤ 10 K rows): 3–10 seconds, full-data on every method
- **Medium** (10 K–100 K rows): 30 s – 3 minutes, full-data on tier-1, sampled for some tier-2
- **Large** (100 K–1 M rows): 2–5 minutes, aggressive sampling on tier-2/3 with honest disclosure
- **Very large** (> 1 M rows): 5–15 minutes; we recommend QUICK tier first

Sampling is *always disclosed* in the pipeline notes. Aurora never silently pretends to analyse what it didn't.

For genuinely huge datasets (10 M+ rows), use the SDK to chunk + summarise; we're adding DuckDB ingest in v1.2 for faster reads.

## Does Aurora support streaming / live data?

Not yet in v1.1. Streaming-mode is the v1.2 / v2.0 plan:

- v1.2 (4–8 weeks): file-watcher connector — re-run Aurora when a CSV on disk changes
- v2.0 (3–6 months): Postgres CDC, Kafka, webhook ingestion

In the meantime, you can poll Aurora via the SDK on a cron and fire Decision Contracts on each fresh bundle. That covers most "near-real-time" use cases.

## I'm a researcher / I want to publish a paper. How does Aurora help?

Use the **Research Kit**:

```python
from aurora_sdk import Bundle
from fantasyai.aurora.research_kit import write_research_kit

bundle = Bundle.load("audit.aurora.json").doc
paths = write_research_kit(
    bundle, "./my_paper_kit",
    title="My analysis",
    creators=[{"name": "Smith, Alice", "affiliation": "Acme"}],
)
```

You get:
- `methods.md` — LaTeX-ready Markdown methods section
- `references.bib` — BibTeX with canonical citations for every method
- `replication.json` — deterministic re-run config + bundle SHA-256
- `.zenodo.json` — Zenodo deposit metadata for DOI minting

Upload the directory to Zenodo → get a DOI → cite it in your paper. Reviewers can verify by re-running Aurora with the supplied config and comparing content hashes.

See [docs/research-kit.md](research-kit.md).

## I'm building an AI agent. How do I use Aurora?

Install the MCP server:

```bash
pip install mcp
python -m aurora_mcp.server --allow-root /path/to/data
```

Configure your agent (Claude Desktop, Cursor, custom). The 7 Aurora tools become callable. The agent can:

- Analyse a dataset → cited findings back
- Look up evidence for a specific claim_id
- Run an intervention or simulation
- Verify a saved bundle's integrity

See [docs/mcp.md](mcp.md) for the Claude Desktop / Cursor configuration.

## I want to react to Aurora findings automatically. How?

Use **Decision Contracts**. Define a JSON document with a trigger and actions; the engine evaluates it against any Aurora bundle:

```json
{
  "id": "factory-line-3-bearing-watch",
  "trigger": {"field": "findings.crit_count", "op": ">=", "value": 3},
  "actions": [
    {"type": "webhook", "url": "https://hooks.example.com/aurora"},
    {"type": "file",    "path": "alerts.jsonl"}
  ],
  "rate_limit": {"max_per_hour": 12}
}
```

See [docs/decision-contracts.md](decision-contracts.md) for the full operator + action reference.

## What's the difference between AUTO / QUICK / STANDARD / FULL?

| Tier | What runs | Time on 100K rows |
|---|---|---|
| QUICK | Tier 1 only (anomaly + regime + motif + AR baseline) | ~30 s |
| STANDARD | Tier 1 + Tier 2 (Bayesian, Granger, GP, wavelet, topology) | 1–2 min |
| FULL | All three tiers, with full-data replication | 3–5 min |
| AUTO | Aurora picks based on dataset size and shape | varies |

AUTO is the default and the right answer 95% of the time. Use QUICK for rapid scans, FULL for publication-grade reproducibility.

## A method shows as "skipped" / "deferred" / "no_evidence" — what does that mean?

These are **honest disclosure**, not failures. Examples:

- `no_time_axis` — the method needs temporal structure; your dataset doesn't have one
- `cross_sectional_no_time_axis` — explicit signal this is i.i.d. rows
- `too_few_observations` — the method needs N≥30 (or 200, or 1000); you have fewer
- `timeout` — the method exceeded its 90 s budget; Aurora moved on rather than hang
- `no_evidence` — the method ran but didn't find anything above its significance threshold (e.g., Granger found no causality; that's a real result, not a failure)

If a skip surprises you, hover the card — Aurora always explains why.

## Does Aurora work on Windows / Mac / Linux?

Yes, all three. CI runs on Linux + Python 3.10/3.11/3.12. Most development is done on Windows + Mac. macOS Apple Silicon (M1/M2/M3/M4) is well-supported; Ollama + Gemma 3 run well on the Metal-accelerated runtime.

## Can I run Aurora on a Raspberry Pi?

Tier-1 methods: yes (~10 K rows in a few minutes). The LLM synthesis is the bottleneck — Gemma 3 12B is too big for a Pi. Use template synthesis instead (set `OLLAMA_MODEL=` to empty or stop Ollama), and you have a fully-functional local-only Aurora at low power.

## I want to contribute. Where do I start?

See [CONTRIBUTING.md](../CONTRIBUTING.md). Good first contributions:

- **Documentation fixes** — typos, clarifications, new examples
- **Knowledge bank entries** — adding cited entries for new domains
- **Test datasets** — interesting datasets that exercise edge cases
- **MCP demos** — example notebooks showing Aurora-as-tool

Larger contributions (new methods, new action types for Decision Contracts) — please open an issue first to discuss the design.

## I found a bug. Where do I report it?

[Open an issue](https://github.com/fantasylab/aurora/issues/new/choose). For security vulnerabilities, please email **security@fantasylab.ai** privately first (see [SECURITY.md](../SECURITY.md)).

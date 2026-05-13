# Aurora Concepts

This document explains the ideas that make Aurora different: glass-box, RAG-grounded synthesis, the knowledge bank, tiers, deterministic outputs, and the Aurora Bundle as a substrate.

## Glass-box, not black-box

Most "AI for analytics" tools are an LLM with file-upload access. The LLM reads your data, pattern-matches against its training, and writes a plausible-sounding summary. There's no math; there's no method tag; there's no citation. If it's wrong, you can't tell.

Aurora inverts that:

1. **Deterministic math runs first.** Anomaly detection, regime fitting, ODE discovery, causal tests — all by named methods with thresholds, on the actual data, in code you can read.
2. **Findings are structured.** Each is a typed object: `{method, severity, claim_id, threshold, citation, evidence_ref}`. Not paragraphs of prose — atoms.
3. **The LLM only translates.** Synthesis uses strict RAG grounding: only retrieved knowledge entries are factual inputs. The verifier rejects any sentence the retrieval didn't support.

The result: every claim in Aurora's output traces back to (a) a specific method that produced it and (b) a public, licensed citation explaining what that method means. The `0 fabricated` chip in the run banner is the live audit signal.

## RAG = Retrieval-Augmented Generation

When Aurora's synthesis engine writes the "What This Means" narrative, it doesn't ask the LLM to think freely. The pipeline:

1. **Structured findings** flow out of the math layer
2. The synthesis engine **queries the knowledge bank** for entries relevant to those findings (using sentence-transformer semantic similarity)
3. The retrieved entries become the LLM's **only factual input**: "Here are the findings. Here are the cited facts. Write a paragraph explaining what this means, using only the facts below, and cite each statement with the `seed:*` identifier."
4. The verifier **post-checks** every claim against the retrieved facts

The LLM is constrained to a tight context window of *attributable* knowledge. It can paraphrase. It can connect related entries. It cannot invent.

## Knowledge bank

A SQLite database of curated entries from public, licensed sources: peer-reviewed papers, FRED economic releases, NOAA observations, NIST reference databases, IPCC reports, Wikidata, arXiv preprints. Each entry has:

- A unique **seed identifier** (e.g., `seed:hampel1974_robust_z`)
- **Source citation** (DOI, URL, ISBN as applicable)
- **Content** (the text Aurora retrieves and the LLM cites)
- **Embedding vector** for semantic search
- **Version** and **ingestion timestamp**

The bank lives in your user-data directory:

- macOS / Linux: `~/.aurora/knowledge_bank/aurora_kb.db`
- Windows: `%APPDATA%\Aurora\knowledge_bank\aurora_kb.db`

A seed set ships in the repo (~50 MB); the full set downloads on first run via `scripts/download_knowledge_bank.py` (~2 GB). After download, **nothing leaves your machine**.

You can contribute knowledge entries — see [docs/knowledge-bank.md](knowledge-bank.md).

## Tiers — honest about how much was analysed

Aurora runs at one of four depths. The tier you pick determines which methods run on full data versus a stratified sample:

| Tier | What runs | Best for |
|---|---|---|
| **AUTO** | Aurora picks based on dataset size | Default; almost always correct |
| **QUICK** | Tier 1 only (anomaly + regime + motif + AR baseline forecast) | Fast scans, < 30 s typical |
| **STANDARD** | Tier 1 + Tier 2 (Bayesian, Granger, GP, wavelet, topology) | Recommended for serious analysis |
| **FULL** | Tier 1 + 2 + 3 (full-data replication) | Reproducible publication-grade |

Sampling is **always disclosed.** The pipeline-notes panel tells you exactly what fraction of your data was analysed and which methods ran on samples. Aurora never silently pretends to analyse what it didn't.

When the dataset is huge (>1 M rows), the AUTO tier will stratify-sample for the longest-running methods while keeping fast ones on full data. Extreme outliers (|z| ≥ 4) are always retained.

## Determinism and reproducibility

Two runs of the same Aurora version, on the same dataset, with the same seed (default: 42), produce **bit-identical** tier-1 results. The Aurora Bundle's `integrity.content_hash` is the proof — it's SHA-256 over a canonical JSON serialisation, excluding only `generated_at` and the integrity block itself.

This is the reproducibility property most "AI for analytics" tools cannot offer. It's central to Aurora's glass-box promise — and it's what makes the Research Kit useful for academic publication.

Tier-2/3 methods (Gaussian process MCMC sampling, when enabled) may produce numerically close but not bit-identical results on different platforms; tier-1 statistics are fully deterministic everywhere.

## The Aurora Bundle (`.aurora.json`)

Aurora's outputs aren't trapped in the UI. Every run produces a single JSON document called an **Aurora Bundle** with everything needed to:

- Re-render the analysis later (UI consumes it directly)
- Hand to a teammate or paper reviewer (portable, complete)
- Embed in your own application (`Bundle.load(path)` from the SDK)
- Verify hasn't been tampered with (`bundle.verify()`)
- Sign with Ed25519 for tamper-evident distribution (`bundle.sign(private_key)`)
- Feed into a Decision Contract for automated reaction

The bundle is the substrate. SDK, MCP, Decision Contracts, Research Kit — they all consume bundles and produce more bundles.

Schema version is part of the document (`bundle_version: "1.0.0"`). Breaking changes bump the major; minor versions are backward-compatible.

## One engine. Two surfaces.

Aurora is one analytical engine exposed through two surfaces with equal billing.

### 🧠 Aurora Copilot — for humans

The Web Studio. For analysts, quants, scientists, engineers. You point Aurora at your data and click through findings, intervene on the system graph, read the cited synthesis. The Copilot is what most people see first — but it's only half the story.

### 🛡️ Aurora Cortex — for AI systems

The verification layer your AI agents and AI products call when they can't afford to hallucinate. Four programmable surfaces, all consuming and producing the same `.aurora.json` bundle:

- **Aurora SDK** — `import aurora_sdk` in your Python code, notebook, or pipeline
- **Aurora MCP** — every LLM agent (Claude Desktop, Claude Code, Cursor, custom) can call Aurora's 7 tools
- **Decision Contracts** — automation systems wire `findings.crit_count >= 3 → webhook` and stop thinking about Aurora
- **Research Kit** — academic users mint a DOI for their next paper from any Aurora run

Aurora is the **verification cortex** these tools plug into. They cite Aurora; Aurora cites the underlying methods; everything traces back. **Cloud LLMs guess. Aurora computes.**

Same engine. Same glass-box principles. Two integration shapes — one for humans, one for AI.

## Anti-hallucination architecture

How Aurora avoids the standard failure mode of AI tools:

| Risk | Aurora's mitigation |
|---|---|
| LLM invents a number not in the data | Synthesis prompt forbids it; verifier rejects sentences with unsupported numbers |
| LLM cites a paper that doesn't exist | Citations are restricted to retrieved `seed:*` ids; the LLM cannot mint new ones |
| Wrong method chosen for the data shape | Methods declare their own preconditions; skip with reason when violated |
| Method silently fails | Per-method 90 s timeout; result is "deferred" not "made up" |
| Bundle content tampered after generation | SHA-256 content hash + optional Ed25519 signature |
| Agent uses Aurora to read files it shouldn't | MCP path allowlist enforced per call |
| Webhook fires to a private internal IP | Decision Contracts SSRF guard rejects private / loopback unless opted in |

This is what "glass-box" actually means in production: every potential trust violation has a named, code-level guard.

## Confidence — what it actually measures

Aurora's `confidence` value (visible in the run banner, color-tiered red <30% / amber 30-60% / green ≥60%) is the **system model's** confidence, not the LLM's. It combines:

- **Slot-fill rate** of the matched domain template — how many of the template's expected entities mapped to real columns
- **Strength of discovered relationships** — confidence-weighted average over edges
- **Constraint satisfaction** — whether the template's known invariants hold

Low confidence is *information*, not a failure. A 22% confidence on a cross-sectional dataset that didn't match any template just means "I don't have a strong prior here; the findings are real but I can't anchor them to a known domain." The findings themselves still carry their own per-method confidence.

## Further reading

- [docs/methods.md](methods.md) — Each analytical method explained, with citations
- [docs/knowledge-bank.md](knowledge-bank.md) — Knowledge bank schema and contribution
- [docs/sdk.md](sdk.md) — Python SDK reference
- [docs/mcp.md](mcp.md) — MCP server for LLM agents
- [docs/decision-contracts.md](decision-contracts.md) — Programmable predicates → automation
- [docs/research-kit.md](research-kit.md) — Publication-ready output for academic users
- [ARCHITECTURE.md](../ARCHITECTURE.md) — Full system design

# Aurora Knowledge Packs

Aurora's knowledge bank ships with a small curated seed set (~59-500
hand-authored entries covering every analytical method). For broader
domain coverage — financial markets, industrial sensors, climate,
biomedical signals — Aurora downloads **knowledge packs** on demand.

This page explains how packs work, how to use them, and how to author
your own.

---

## Why packs exist

Aurora's local-first promise breaks if every user has to download
50 GB on first run. It also breaks if we commit megabytes of binary
data to git. Packs solve both:

- **Seed (in repo):** ~5-10 MB of hand-curated cited entries covering
  every method. Always available offline, instant on first run.
- **Domain packs (downloaded lazily):** 50-500 MB each, fetched the
  first time Aurora analyses data in that domain. A finance analyst
  pulls only the finance pack; a climate researcher pulls only the
  climate pack.

Packs are deduplicated and SHA-256 verified. The manifest (the index
of which packs exist + where to get them) lives in the repo, so the
client always knows what's available even if a CDN goes down.

---

## Where packs come from

Two redundant CDNs serve byte-identical content. Clients try the
primary first and fall back to the mirror on failure.

| Channel | URL pattern | Why |
|---|---|---|
| **Primary** | `https://kb.aurora.fantasylab.ai/packs/{id}-v{n}.tar.gz` | Cloudflare R2 — free egress, fast, our control |
| **Mirror** | `https://huggingface.co/datasets/fantasylab-ai/aurora-kb/resolve/main/{id}-v{n}.tar.gz` | HuggingFace — discovery + failover |

Both serve the same `.tar.gz`. The SHA-256 in the manifest catches any
tampering regardless of which CDN serves it.

---

## How a pack gets onto your machine

1. You drop a CSV into Aurora.
2. Aurora's domain classifier identifies the dataset as e.g. "finance".
3. Aurora consults the in-repo manifest:
   `fantasyai/aurora/knowledge_bank/packs/manifest.json`
4. If the finance pack isn't already installed (checked via
   `~/.aurora/knowledge_bank/packs/_installed.json`), Aurora starts
   a background download.
5. Download → SHA-256 verify → extract into
   `~/.aurora/knowledge_bank/packs/finance-v1/` → register.
6. Future analyses in the finance domain retrieve from both the seed
   and the installed pack.

The whole flow is **idempotent**. Re-running Aurora doesn't re-download;
the registry knows what's already there.

---

## Manual pack management

You can also install / remove packs explicitly without going through
the auto-detector.

```bash
# List available packs (from the manifest)
python -m fantasyai.aurora.knowledge_bank.packs.list

# Install the finance pack
python -m fantasyai.aurora.knowledge_bank.packs.install finance

# Install everything
python -m fantasyai.aurora.knowledge_bank.packs.install --all

# Remove a pack
python -m fantasyai.aurora.knowledge_bank.packs.uninstall finance

# Disable auto-download for the current Aurora instance
AURORA_NO_PACK_AUTODOWNLOAD=1 python studio_api.py
```

---

## Pack file format

A pack is a gzipped tar of exactly three files:

```
finance-v1.tar.gz
├── pack_meta.json    # id, version, entry count, embedder info
├── entries.jsonl     # one KnowledgeEntry per line (matches schema.py)
└── embeddings.npy    # pre-computed (N, D) float32 vectors, aligned with entries
```

**Why JSONL + .npy instead of parquet:** gzipped JSONL is readable by
any language with stdlib, streamable, easy to inspect by hand (`gunzip
| head`), and within ~10% of parquet's size at the 50-500 MB scale we
target. We can migrate to parquet via a schema_version bump later if
needed.

**Embedder lock-in:** the embedder name and dim live in `pack_meta.json`
*and* in the manifest entry. Clients reject packs whose embedder doesn't
match their active retrieval embedder — mixing embedders gives garbage
results.

---

## Authoring a pack (for maintainers + contributors)

```bash
python tools/build_kb_pack.py \
    --id finance \
    --name "Financial markets & quantitative finance" \
    --description "Cited entries covering volatility regimes, ..." \
    --version 1 \
    --source path/to/finance_entries.jsonl \
    --covers-domains finance economics \
    --covers-methods granger hmm_baum_welch iso-forest+robust-Z \
    --output-dir dist/packs/
```

This produces `dist/packs/finance-v1.tar.gz` and prints a manifest-entry
snippet (with the actual SHA-256 + size) to stdout. Paste that snippet
into `fantasyai/aurora/knowledge_bank/packs/manifest.json` and you're
done — next Aurora restart picks it up.

### What entries.jsonl should contain

Each line is a JSON object matching the `KnowledgeEntry` schema from
`fantasyai/aurora/knowledge_bank/schema.py`. Minimum:

```json
{
  "id": "seed:granger_1969",
  "domain": ["finance", "research"],
  "concept_type": "method",
  "name": "Granger causality",
  "definition": "A statistical test for whether one time series ...",
  "references": [
    {
      "citation": "Granger, C.W.J. (1969). Investigating causal relations by econometric models and cross-spectral methods. Econometrica 37(3), 424-438.",
      "doi": "10.2307/1912791"
    }
  ],
  "pattern_signatures": [
    {
      "finding_method": "granger",
      "finding_kind_hint": "causal_direction"
    }
  ]
}
```

Per-entry licenses are preserved — the collection license (default
CC-BY-4.0) covers the curation work, but each entry retains its
underlying license (the paper / textbook / dataset it came from).

### Quality bar for entries

A good pack has entries that:

- Cite a **real, accessible** published source (DOI, ISBN, or stable URL)
- Cover **one concept** per entry (not "everything about regression")
- Include **pattern signatures** so Aurora's retrieval can route findings
  to them deterministically (e.g., `finding_method = "granger"`)
- Are reviewed by a domain expert (`review_status: "human_approved"`)

5,000 entries that meet this bar are more useful than 500,000 scraped
entries that don't. **Quality > quantity for citation retrieval.**

---

## Operational notes

### Storage paths

| What | Path |
|---|---|
| The seed entries (always present) | `fantasyai/aurora/knowledge_bank/ingest/sources/seed.py` |
| The pack manifest (always present) | `fantasyai/aurora/knowledge_bank/packs/manifest.json` |
| The user's main KB SQLite | `~/.aurora/knowledge_bank/main.db` |
| The user's pack install root | `~/.aurora/knowledge_bank/packs/` |
| The user's installed-pack registry | `~/.aurora/knowledge_bank/packs/_installed.json` |

### Environment variables

| Var | Effect |
|---|---|
| `AURORA_NO_KB_BOOTSTRAP=1` | Skip seed ingest on first server start (CI / scripted use) |
| `AURORA_NO_PACK_AUTODOWNLOAD=1` | Skip automatic pack download even when domain detected |
| `AURORA_PACK_TIMEOUT_S=300` | Per-HTTP-request timeout for pack downloads |

### Privacy

Pack downloads make outbound HTTP requests to the configured CDN URLs.
That's the **only** network call Aurora makes by default (apart from
the optional Ollama LLM connection on `localhost`). To run Aurora 100%
offline, set `AURORA_NO_PACK_AUTODOWNLOAD=1` and rely on the in-repo
seed entries. Aurora's anomaly detection, regime detection, Granger
causality, physics matching, and forecast all work offline — only the
*citation retrieval* benefits from the bigger packs.

---

## Roadmap

| Version | Status | Plan |
|---|---|---|
| 1.0 | Shipped | Hybrid: in-repo seed (59-500 entries) + on-demand domain packs |
| 1.1 | Planned | Pro packs (Bloomberg-curated financial, FDA-cited biomedical) behind license-key check |
| 2.0 | Planned | Federated KB: opt-in user contributions flow back into the shared bank |
| 2.0+ | Planned | DuckDB-over-HTTP zero-download mode for ultra-light clients |

---

## See also

- [docs/knowledge-bank.md](knowledge-bank.md) — overall KB architecture
- [docs/concepts.md](concepts.md) — why citation-grounded synthesis matters
- [ROADMAP.md](../ROADMAP.md) — v1.2+ plan including domain packs

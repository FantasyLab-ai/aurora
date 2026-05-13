# Knowledge Bank

Aurora's knowledge bank is a local SQLite database of curated, citable entries that ground the synthesis layer. This document describes its schema, where it lives, how to populate it, and how to contribute new entries.

## Where it lives

The knowledge bank is per-user state, stored in:

- **macOS / Linux:** `~/.aurora/knowledge_bank/aurora_kb.db`
- **Windows:** `%APPDATA%\Aurora\knowledge_bank\aurora_kb.db`

If you need to relocate it (e.g., shared network drive in a regulated environment), set `AURORA_KB_PATH` to an absolute path before starting Aurora.

## Two distribution forms

| Form | Size | How to get it |
|---|---|---|
| **Seed bank** | ~50 MB | Ships in `fantasyai/aurora/knowledge_bank/seed/` in the repo; covers demo datasets + foundational methods |
| **Full bank** | ~2 GB | Hosted on Hugging Face; download via `python scripts/download_knowledge_bank.py` |

After the full bank is downloaded once, Aurora has zero network dependencies.

## Schema

Each entry is a row in the `entries` table:

| Column | Type | Description |
|---|---|---|
| `seed_id` | TEXT PRIMARY KEY | Unique stable identifier (e.g., `seed:hampel1974_robust_z`) |
| `title` | TEXT | Human-readable short title |
| `content` | TEXT | The body of text Aurora retrieves and the LLM cites |
| `source_type` | TEXT | One of: `paper`, `book`, `database`, `report`, `wikidata`, `dataset`, `software` |
| `source_citation` | TEXT | Full citation (BibTeX-style) |
| `source_url` | TEXT | DOI, ISBN, arXiv id, or canonical URL |
| `domain` | TEXT | `general` / `climate` / `finance` / `biomed` / `industrial` / `ops` / etc. |
| `embedding` | BLOB | Sentence-transformer vector (float32, dim = embedder-dependent) |
| `embedder` | TEXT | Embedder identifier (e.g., `all-mpnet-base-v2`) |
| `version` | INTEGER | Entry version; bumps when content is corrected |
| `ingested_at` | TEXT | ISO-8601 timestamp |
| `license` | TEXT | The source's license (must permit redistribution) |

Indices on `seed_id`, `domain`, and `embedder`. A separate vector index supports approximate-nearest-neighbour queries; on rebuild it's regenerated from the `embedding` blobs.

## Provenance contract

Every entry must:

1. **Cite a public, licensed source** — peer-reviewed paper, government data release, established reference work, or properly-licensed dataset. No copy-paste from random web pages without verifiable provenance.
2. **Be redistributable** — the entry's `license` must permit the bank to ship it. CC-BY, CC-BY-SA, public domain, MIT/BSD/Apache for software/data are all fine.
3. **Be inspectable** — anyone with the bank should be able to read the source citation, click the URL, and verify the content matches.
4. **Be deterministically retrievable** — `seed_id` must be stable across versions; if you correct an entry, bump `version` and keep the id.

Entries that fail any of these checks are rejected at ingest time.

## Querying programmatically

```python
from fantasyai.aurora.knowledge_bank import open_bank

bank = open_bank()                          # opens the default db
entries = bank.search("robust z-score Hampel", top_k=5)
for e in entries:
    print(e.seed_id, e.title, e.source_citation)
```

The synthesis engine uses the same API — there's no privileged access path.

## Contributing entries

Knowledge bank contributions are one of the most impactful ways to help Aurora improve. A good entry covers a method, fact, or reference Aurora can cite when explaining findings.

### Workflow

1. **Pick a domain or method.** Browse the [open issues labelled `knowledge-bank`](https://github.com/fantasylab/aurora/issues?q=is%3Aissue+label%3Aknowledge-bank) for ones flagged as needed. Climate, biomed, finance, industrial diagnostics, energy, and materials are all underrepresented.

2. **Write the entry.** Use the YAML form below. Keep content focused — one method or one fact per entry. The synthesis layer chains entries; you don't need to bundle everything into one giant entry.

3. **Open a PR** with the entry added to `fantasyai/aurora/knowledge_bank/contrib/<domain>/<entry-id>.yaml`. The ingest script reads YAML and writes to SQLite.

4. **Reviewer checks:** citation valid, license OK, content accurate, no duplicate of existing entries.

### Entry YAML format

```yaml
seed_id: seed:liu2008_isolation_forest
title: "Isolation Forest (Liu, Ting, Zhou 2008)"
domain: anomaly_detection
source_type: paper
source_url: https://doi.org/10.1109/ICDM.2008.17
source_citation: |
  Liu, F. T., Ting, K. M., & Zhou, Z.-H. (2008).
  Isolation Forest. In Eighth IEEE International Conference on
  Data Mining (ICDM 2008), pp. 413-422.
  doi:10.1109/ICDM.2008.17.
license: scholarly-fair-use   # or CC-BY-4.0, etc.
content: |
  Isolation Forest is an anomaly-detection method that isolates
  observations by randomly selecting a feature and a split value
  between the min and max of that feature. Anomalous points
  require fewer splits to isolate; the average path length across
  an ensemble of isolation trees yields the anomaly score. The
  method has linear time complexity and a low memory footprint,
  making it well-suited to large datasets.
```

### Ingest

```bash
python scripts/ingest_knowledge_bank.py \
  --source fantasyai/aurora/knowledge_bank/contrib/ \
  --embedder all-mpnet-base-v2
```

The script will:
- Validate each YAML entry
- Embed the content via sentence-transformers
- Upsert into the SQLite db
- Refuse duplicates (`seed_id` already exists with different content → version bump required)

## Domain pack roadmap

In v2.0 we plan to release curated **domain knowledge packs**: opt-in bundles of entries for specific verticals. Each pack will ship with its own README, citation list, and license summary. Likely first packs:

- **Climate** — IPCC, NOAA, NASA-GISS, peer-reviewed climate science
- **Finance** — FRED, BIS, peer-reviewed econometrics
- **Biomed** — clinical trial methodology, NIH datasets, peer-reviewed biostatistics
- **Industrial** — failure-mode references, sensor primer, predictive-maintenance methods

If you're an expert in one of these areas and want to be a domain-pack maintainer, [open a Discussion](https://github.com/fantasylab/aurora/discussions).

## Privacy

Your local knowledge bank is private to your machine. Aurora never uploads it; the SDK never reads it across process boundaries; the MCP server never exposes the bank's contents directly (only retrieval results that flow through synthesis).

In v2.0+ we plan to support **federated knowledge contribution** — an opt-in flow where you can submit specific entries upstream for inclusion in the canonical bank. The opt-in is per-entry and reversible. We will never auto-upload.

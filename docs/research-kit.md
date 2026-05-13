# Research Kit

The Aurora Research Kit turns any Aurora Bundle into a publication-ready directory: a methods section ready to paste into your paper, a BibTeX file with every cited prior, a reproducibility manifest, and Zenodo deposit metadata so you can mint a DOI.

This is built for academic users — but anyone who needs an auditable, citeable analytical artifact will find it useful.

## Quick example

```python
from aurora_sdk import Bundle
from fantasyai.aurora.research_kit import write_research_kit

bundle = Bundle.load("audit.aurora.json").doc
paths = write_research_kit(
    bundle,
    "./my_paper_kit",
    title="Bearing-failure regime detection at Factory Line 3",
    creators=[
        {"name": "Smith, Alice", "affiliation": "Acme Labs"},
        {"name": "Patel, Raj", "affiliation": "Acme Labs"},
    ],
    keywords=["industrial monitoring", "isolation forest", "regime change"],
    description="Aurora analysis of bearing vibration data, March 2026.",
)

print(paths.methods)        # → ./my_paper_kit/methods.md
print(paths.bib)            # → ./my_paper_kit/references.bib
print(paths.replication)    # → ./my_paper_kit/replication.json
print(paths.zenodo)         # → ./my_paper_kit/.zenodo.json
print(paths.bundle)         # → ./my_paper_kit/bundle.aurora.json
```

## What's in the kit

```
my_paper_kit/
├── README.md             # Orientation: what each file is, how to cite, how to verify
├── methods.md            # Full methods section (LaTeX-ready Markdown)
├── references.bib        # BibTeX for every method's canonical citation
├── replication.json      # Deterministic re-run config + bundle SHA-256
├── .zenodo.json          # Zenodo deposit metadata for DOI minting
└── bundle.aurora.json    # The source Aurora Bundle this kit was built from
```

### `methods.md`

A complete Methods section with these subsections:

- **Data** — dataset filename, row/column counts, time-axis status, cadence, dataset SHA-256 (when known)
- **Pipeline overview** — Aurora version, tier, contract guarantees
- **Methods used** — one subsection per distinct method that produced findings, with inline citation tag, count of findings, severity breakdown, and a plain-English description of the method (LaTeX-formatted math where appropriate)
- **Assumptions** — explicit list of what Aurora assumed (Hampel thresholds, AR σ propagation, etc.)
- **Confidence** — the system-model confidence + tier label (strong / moderate / low)
- **Reproducibility** — bundle hash + instructions for re-running

The Markdown is paste-ready into a paper. Math is delimited with `$...$` and `$$...$$` so most LaTeX → Markdown → DOCX converters handle it correctly.

### `references.bib`

One BibTeX entry per method that produced findings. The Research Kit ships with a curated library of canonical citations:

| Method tag | Citation |
|---|---|
| `robust-z` | Hampel (1974) |
| `iso-forest` | Liu, Ting, Zhou (2008) |
| `ar(1)` / `ar(2)` | Hyndman & Athanasopoulos (2021) |
| `granger` | Granger (1969) |
| `hmm_baum_welch` | Baum, Petrie, Soules, Weiss (1970) |
| `matrix_profile` | Yeh et al. (2016) |
| `morlet_wavelet` | Torrence & Compo (1998) |
| `vietoris_rips` | Edelsbrunner & Harer (2010) |
| `lof` | Breunig et al. (2000) |
| `mahalanobis_robust` | Rousseeuw & Van Driessen (1999) |
| `gaussian_process` | Rasmussen & Williams (2006) |
| `mutual_info_ksg` | Kraskov, Stögbauer, Grassberger (2004) |

Combined method tags (e.g., `ISO-FOREST + ROBUST-Z`) emit BOTH contributing citations. Unknown method tags emit a `@misc{aurora-<slug>, ...}` placeholder you can fill in — never silently dropped.

Aurora's own self-citation is always included as `@software{aurora-qie, ...}` so reviewers know which version produced the analysis.

### `replication.json`

The deterministic re-run manifest:

```json
{
  "schema": "aurora.replication/v1",
  "aurora_version": "1.1.0",
  "bundle_hash": "1c8d…",
  "dataset": {
    "basename": "factory_bearing_demo.csv",
    "sha256": "ab12cd34…",
    "rows": 1000,
    "cols": 5,
    "size_mb": 0.04
  },
  "run": {
    "id": "20260512_150000__factory_bearing",
    "tier": "standard",
    "started_at": "...",
    "completed_at": "..."
  },
  "instructions": [
    "1. Install Aurora-QIE at the version listed above.",
    "2. Obtain the dataset file matching the listed SHA-256.",
    "3. Run: `aurora analyze <dataset> --depth <tier>`",
    "4. Verify the new bundle's content_hash matches bundle_hash above.",
    "   Bit-identical for tier-1; numerically close for tier-2/3 advanced methods."
  ]
}
```

Anyone with the dataset file (matching SHA-256) + Aurora at the listed version can reproduce the analysis. Tier-1 methods are bit-identical. Tier-2/3 methods using MCMC sampling (e.g., Gaussian process posteriors with non-default sampler) may be numerically close but not bit-identical across platforms.

### `.zenodo.json`

Zenodo deposit metadata. Zenodo reads this file when you upload the kit directory and mints a DOI based on its contents.

```json
{
  "metadata": {
    "title": "Bearing-failure regime detection at Factory Line 3",
    "description": "Aurora-QIE quantitative analysis bundle, including findings, methods, assumptions, and a cryptographically-hashed reproducibility manifest. ...",
    "creators": [{"name": "Smith, Alice", "affiliation": "Acme Labs"}],
    "keywords": ["industrial monitoring", "isolation forest", "regime change"],
    "upload_type": "dataset",
    "publication_date": "2026-05-12",
    "access_right": "open",
    "license": "CC-BY-4.0",
    "notes": "Generated by aurora_sdk research_kit. ...",
    "related_identifiers": [
      {
        "scheme": "url",
        "identifier": "https://aurora-qie.dev",
        "relation": "isDerivedFrom",
        "resource_type": "software"
      }
    ]
  }
}
```

Edit the file directly if you need a different license, want to add ORCIDs to creators, or want to link related resources.

## Workflow: from CSV to DOI

1. **Run the analysis**

   ```python
   r = aurora.run("data.csv", depth="standard")
   r.bundle.save("audit.aurora.json")
   ```

2. **Build the kit**

   ```python
   from fantasyai.aurora.research_kit import write_research_kit
   paths = write_research_kit(
       r.bundle.doc, "./paper_kit",
       title="...", creators=[...],
   )
   ```

3. **Review `methods.md`** — it's a draft. Read it; tighten the prose. Method descriptions are starter text — refine for your paper's voice.

4. **Sign the bundle (optional)**

   ```python
   r.bundle.sign(private_key_bytes)
   r.bundle.save("./paper_kit/bundle.aurora.json")
   ```

   Now reviewers can run `Bundle.load(...).verify(require_signature=True)` to confirm the artifact wasn't modified between your run and their review.

5. **Zip and upload to Zenodo**

   ```bash
   cd paper_kit
   zip -r ../paper_kit.zip .
   ```

   Upload `paper_kit.zip` at [zenodo.org/deposit/new](https://zenodo.org/deposit/new). Zenodo reads `.zenodo.json` and pre-fills the metadata. Publish → DOI minted.

6. **Cite in your paper**

   In the paper's methods section: "All analyses were performed with Aurora-QIE v1.1.0; the full reproducibility kit is deposited at [DOI]."

   In the bibliography: copy the entries from `references.bib` into your paper's bib file.

## Customisation

### Add your own method citations

If your run uses a method Aurora doesn't have a built-in citation for, the kit emits an `@misc{aurora-<slug>, ...}` placeholder. To make those automatic:

1. Open `fantasyai/aurora/research_kit.py`
2. Add an entry to `_BUILTIN_CITATIONS` with the method tag and BibTeX fields
3. Optionally add a plain-English description to `_METHOD_DESCRIPTIONS`
4. Open a PR — your citation becomes everyone's

### Different license

Edit `.zenodo.json` before uploading. Common alternatives:

- `CC-BY-4.0` (default; attribution required)
- `CC-BY-SA-4.0` (attribution + share-alike)
- `CC0-1.0` (public domain dedication)
- `MIT` / `Apache-2.0` (if depositing code along with the kit)

### Larger reproducibility set

The kit doesn't include the source dataset by default (datasets are often private or large). If you can publish your dataset, include it in the zip:

```
paper_kit/
├── ... (kit files)
└── data/
    └── factory_bearing.csv          ← your dataset (verifies vs replication.json.dataset.sha256)
```

Zenodo will deposit the full directory; reviewers don't need to find the dataset elsewhere.

## What this is NOT

- It's not a substitute for writing the paper. The Methods section is a draft; you still write the rest.
- It's not a journal-pre-print server. Use Zenodo for data deposit; use arXiv / journal submission for the paper itself.
- It's not a workflow tool. It's a one-shot generator: run it once per analysis you want to publish.

## Testing

22 tests in `tests/test_research_kit.py` cover:

- Methods.md contains every required section
- References.bib cites known methods + handles unknown with placeholder
- Combined method tags (e.g., `ISO-FOREST + ROBUST-Z`) emit both citations
- Replication.json carries the right fields
- .zenodo.json metadata shape conforms to Zenodo's API
- `write_research_kit` produces all six files
- Overwrite guard prevents accidental clobber

Run with `pytest tests/test_research_kit.py`.

## Reference

- [fantasyai/aurora/research_kit.py](../fantasyai/aurora/research_kit.py) — Implementation + built-in citations
- [tests/test_research_kit.py](../tests/test_research_kit.py) — 22 tests
- [Zenodo REST API: metadata representation](https://developers.zenodo.org/#representation)
- [Aurora SDK / Bundle reference](sdk.md)

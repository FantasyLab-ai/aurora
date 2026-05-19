# Aurora in Jupyter

Aurora's Python SDK is designed to feel native in a Jupyter notebook.
Drop in a DataFrame, get findings out, render them inline, save the
bundle, export the whole notebook + bundle as a single artifact.

This page explains the four notebook-specific features added in v1.2:

1. **DataFrame input** — `aurora.run(df)` instead of file paths
2. **Rich HTML reprs** — `RunResult`, `Findings`, `Bundle` render as
   readable cards/tables in any notebook environment
3. **Notebook export** — bundle the .ipynb + the .aurora.json into one
   shareable tarball
4. **Sample notebook** at `examples/jupyter/aurora_in_jupyter.ipynb`

## Setup

```bash
# Aurora's runtime deps (you have these if you ran the install)
pip install -r requirements.txt

# JupyterLab itself, if you don't already have it
pip install jupyterlab

# Optional: pandas + numpy explicitly (already in Aurora's deps)
pip install pandas numpy
```

Then `jupyter lab` and open `examples/jupyter/aurora_in_jupyter.ipynb`
to walk through the demo.

## 1. DataFrame input

The traditional Aurora flow is `aurora.run("file.csv")`. In a notebook,
you usually already have a DataFrame from `pd.read_csv()`, a database
query, or an API call — writing it to disk first feels clunky. So:

```python
import pandas as pd
import aurora_sdk as aurora

df = pd.read_csv("my_data.csv")  # or any DataFrame, anywhere

r = aurora.run(df, depth="quick", dataset_name="my_demo")
```

**What happens under the hood:** Aurora writes the DataFrame to
`outputs/aurora_dataset_runs/_uploads/my_demo.csv` and runs its
standard pipeline on that file. The CSV is preserved alongside the
run_dir for the audit trail — **Aurora's bundle hashes the CSV bytes**,
not the in-memory DataFrame, so reproducibility is intact.

If you omit `dataset_name`, Aurora generates a timestamped name like
`aurora_df_20260519_163000.csv`.

You can also still pass file paths, run_dirs, or `.aurora.json` bundles
exactly as before — the input type is auto-detected.

## 2. Rich HTML reprs

Every Aurora SDK object now has a `_repr_html_` method. When you
evaluate the object as the last expression in a notebook cell, Jupyter
renders the HTML instead of the default `<aurora_sdk.RunResult ...>`
repr:

```python
r = aurora.run(df, depth="quick")
r                # → summary card with severity pills, methods, bundle hash
r.findings       # → sortable-looking findings table with severity colours
r.bundle         # → bundle metadata + integrity card
```

The HTML is **self-contained** (no JS, no external assets) so it
renders identically in:

- JupyterLab
- Classic Jupyter Notebook
- VS Code's notebook view
- nbviewer
- GitHub's notebook preview
- Anywhere else that respects `_repr_html_`

Dark-mode-friendly. Long finding lists truncate at 25 rows with a
"+N more" footer; the full list is still accessible via the Python API.

## 3. Notebook export

When you want to send a teammate the **whole thing** — your narrative
*plus* the analytical evidence — use `export_notebook`:

```python
info = aurora.export_notebook(
    notebook_path="my_analysis.ipynb",
    bundle=r.bundle,                # or path to a .aurora.json
    output_path="my_report",        # extension auto-appended
)
print(info.output_path)
# → PosixPath('/.../my_report.aurora-notebook.tar.gz')
```

The output is a single tarball containing:

```
my_report.aurora-notebook.tar.gz
├── notebook.ipynb        # your notebook (UTF-8 JSON, verbatim)
├── bundle.aurora.json    # the Aurora bundle the notebook produced
└── manifest.json         # provenance + integrity hashes
```

`manifest.json` carries three hashes:

- `notebook_sha256` — file-level hash of the .ipynb bytes
- `bundle_sha256` — file-level hash of the bundle JSON
- `bundle_content_hash` — Aurora's *canonical* hash from inside the
  bundle. The reviewer can call `Bundle.load(...)` on the inner bundle
  and run `.verify()` to confirm the analytical claims haven't been
  tampered with.

### Reading an export

```python
extracted = aurora.read_export(
    "my_report.aurora-notebook.tar.gz",
    output_dir="./review_unpack",
)
# {'notebook': Path('...'), 'bundle': Path('...'), 'manifest': Path('...')}

# Verify the analytical content
b = aurora.Bundle.load(extracted["bundle"])
assert b.verify()  # True if untampered
```

The extractor refuses unknown file names, path-traversal entries, and
non-regular files. Same defensive posture as the KB pack installer.

## 4. The sample notebook

Open `examples/jupyter/aurora_in_jupyter.ipynb` for a runnable
end-to-end walkthrough:

- Loads a CSV into a DataFrame
- Calls `aurora.run(df)`
- Renders `RunResult`, `Findings`, `Bundle` inline
- Filters findings with `.critical()`, `.by_method(...)`
- Drops to a plain DataFrame for further analysis
- Saves + verifies the bundle
- Exports notebook + bundle as a single artifact

It's the cleanest reference for what Aurora-in-Jupyter feels like in
practice.

## Optional: install Aurora as `pip install aurora-qie[jupyter]`

Aurora doesn't require Jupyter — the notebook helpers degrade to plain
Python when Jupyter isn't present (the `_repr_html_` methods just sit
there unused). If you want a one-shot install that pulls in JupyterLab
as well, future packaging will support `pip install aurora-qie[jupyter]`
once Aurora ships to PyPI.

For now, the `requirements.txt` + `pip install jupyterlab` flow above
is sufficient.

## What's intentionally NOT in this surface yet

| Feature | Status | Why |
|---|---|---|
| `ipywidgets`-based interactive finding browser | v1.2.1 | Adds an optional dep (`ipywidgets`) and requires JupyterLab widget setup; defer until base notebook UX is proven |
| Inline plot rendering (matplotlib / plotly) | v1.2 | Pending design — Aurora doesn't ship plots, so we'd need to coordinate with the Studio's plot generation path |
| `aurora.run(parquet_url=...)` for remote-only DataFrames | v1.2+ | Tied to the KB pack DuckDB-over-HTTP work |

If any of these matter for your workflow, file an issue with the use
case and we'll prioritise.

## See also

- [aurora_sdk/jupyter.py](../aurora_sdk/jupyter.py) — HTML renderer
  source if you want to customise styles
- [aurora_sdk/notebook_export.py](../aurora_sdk/notebook_export.py) —
  export / read implementation
- [docs/sdk.md](sdk.md) — full Python SDK reference
- [docs/concepts.md](concepts.md) — Aurora's glass-box principles
- `examples/jupyter/aurora_in_jupyter.ipynb` — runnable demo notebook

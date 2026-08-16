"""Write the human-readable MANIFEST.md next to a corpus artifact."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict


def write_manifest(corpus: Dict[str, Any], corpus_path: Path) -> Path:
    version = corpus.get("corpus_version", "v1")
    entries = corpus.get("entries") or []
    methods = corpus.get("methods") or []
    by_method: Dict[str, int] = {}
    for e in entries:
        by_method[e["method"]] = by_method.get(e["method"], 0) + 1

    lines = [
        f"# Calibration corpus {version} — manifest",
        "",
        "What this table is: for each (method, null regime) cell, the",
        "empirically measured rate at which Aurora's PRODUCTION detector",
        "fires on data generated to contain nothing to find.",
        "",
        f"* built: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"* build compute: {corpus.get('build_seconds', '?')}s single-threaded",
        f"* base seed: {corpus.get('base_seed')}",
        f"* trials per cell: {corpus.get('trials_per_cell')}",
        f"* cells: {corpus.get('n_cells')}  entries: {corpus.get('n_entries')}",
        f"* corpus file: {corpus_path.name} (sha256 in the sidecar)",
        "",
        "## Methods covered",
        "",
    ]
    mv = corpus.get("method_versions") or {}
    md = corpus.get("method_defaults") or {}
    for m in methods:
        lines.append(
            f"* `{m}` v{mv.get(m, '?')} — {by_method.get(m, 0)} entries; "
            f"production defaults: {md.get(m, {})}"
        )
    lines += [
        "",
        "## Generators",
        "",
    ]
    for name, ver in sorted((corpus.get("generator_versions") or {}).items()):
        lines.append(f"* `{name}` v{ver}")
    lines += [
        "",
        "## Seeding",
        "",
        "Every trial is drawn via `generate_cell_trial(generator, params, n,",
        "trial)`: NumPy `SeedSequence` with fixed entropy (the base seed",
        "above) and a spawn key from a SHA-256 of the cell descriptor.",
        "Same cell, same trial, same series — on any machine.",
        "",
        "## Invalidation rules",
        "",
        "A corpus is only valid for the generator versions and production",
        "detector defaults it records. Changing either — a generator's",
        "output for a given seed, a detector's algorithm, or a detector's",
        "default thresholds — requires a version bump and a rebuild. The",
        "guard tests in `tests/test_calibration.py` pin the production",
        "defaults so a silent drift fails CI.",
        "",
        "## What this corpus does NOT claim",
        "",
        "* No power / sensitivity numbers. This table measures false fires",
        "  on nulls only; false negatives are a separate, harder problem",
        "  and are deliberately out of scope.",
        "* No real-data ground truth. There is no honest label for 'was",
        "  there really a regime change in this production series', so",
        "  synthetic nulls are the only defensible basis. Rates on real",
        "  data with real structure may differ.",
        "* No nominal alpha. BOCPD, CUSUM and PELT (as configured in",
        "  production) make no alpha-level claim, so 'inflation vs nominal",
        "  5%' would be a comparison against a number the methods never",
        "  promised. Inflation is reported against the measured IID",
        "  baseline instead.",
        "",
    ]
    out = corpus_path.parent / "MANIFEST.md"
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return out

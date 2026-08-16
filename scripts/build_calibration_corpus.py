"""
Build the calibration corpus — a batch job, not a runtime path.

Runs Aurora's production changepoint detectors over the null grid and
writes the versioned corpus artifact (JSON + sha256 sidecar + manifest)
into fantasyai/aurora/calibration/, where it ships inside the wheel.

Usage:
    python scripts/build_calibration_corpus.py                # parallel build
    python scripts/build_calibration_corpus.py --workers 1    # sequential
    python scripts/build_calibration_corpus.py --trials 200   # quick pass
    python scripts/build_calibration_corpus.py --out DIR      # elsewhere

The full build (4000 trials/cell) takes on the order of an hour
single-threaded, minutes with workers. Either way the output is
byte-identical — seeding is per (cell, trial), so parallelism can only
move the wall clock, never a measured number. The corpus is built once
per method/generator version and shipped; runtime lookups just read the
table.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trials", type=int, default=None,
                    help="trials per cell (default: harness DEFAULT_TRIALS)")
    ap.add_argument("--version", default="v1", help="corpus version tag")
    ap.add_argument("--out", default=None,
                    help="output directory (default: the calibration package)")
    ap.add_argument("--workers", type=int, default=None,
                    help="parallel cell workers (default: cpu_count - 2; "
                         "output is identical at any worker count)")
    args = ap.parse_args(argv)

    from fantasyai.aurora.calibration import (
        DEFAULT_GRID, DEFAULT_TRIALS, build_corpus, save_corpus,
    )
    from fantasyai.aurora.calibration.manifest import write_manifest

    import os
    trials = args.trials or DEFAULT_TRIALS
    workers = args.workers if args.workers is not None else max(
        1, (os.cpu_count() or 2) - 2
    )
    print(f"building corpus {args.version}: {len(DEFAULT_GRID)} cells x "
          f"{trials} trials, {workers} worker(s)", flush=True)

    corpus = build_corpus(
        DEFAULT_GRID, trials=trials, corpus_version=args.version,
        progress=lambda msg: print(msg, flush=True),
        workers=workers,
    )
    path = save_corpus(corpus, directory=args.out)
    manifest = write_manifest(corpus, path)
    print(f"\ncorpus written: {path}")
    print(f"sha256 sidecar: {path.with_suffix('.sha256')}")
    print(f"manifest:       {manifest}")
    print(f"entries:        {corpus['n_entries']}  "
          f"build time: {corpus['build_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
CLI entry point for the bulk-ingest pipeline.

Examples:
  python -m fantasyai.aurora.knowledge_bank.ingest --source seed
  python -m fantasyai.aurora.knowledge_bank.ingest --source fred_metadata
  python -m fantasyai.aurora.knowledge_bank.ingest --stage stage_1_fred
  python -m fantasyai.aurora.knowledge_bank.ingest --all
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .pipeline import (
    DEFAULT_SOURCES, INGEST_STAGES, run_ingest, run_seed_ingest, run_stage,
)


def _progress_cb(ev):
    line = " · ".join(f"{k}={v}" for k, v in ev.items())
    print(f"  [{time.strftime('%H:%M:%S')}] {line}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(prog="aurora-kb-ingest")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--source", nargs="+",
                    help="Run one or more named sources")
    g.add_argument("--stage", choices=list(INGEST_STAGES.keys()),
                    help="Run a named stage (group of sources)")
    g.add_argument("--all", action="store_true",
                    help="Run every source in DEFAULT_SOURCES")
    g.add_argument("--seed-only", action="store_true",
                    help="Re-ingest the curated seed entries")
    g.add_argument("--list", action="store_true",
                    help="List sources + stages and exit")
    args = p.parse_args()

    if args.list:
        print("Sources:")
        for s in DEFAULT_SOURCES:
            print(f"  · {s}")
        print("\nStages:")
        for sid, info in INGEST_STAGES.items():
            print(f"  · {sid:30s} {info['label']}  "
                   f"(expected {info['expected_entries']})")
        return 0

    if args.seed_only:
        res = run_seed_ingest()
    elif args.source:
        res = run_ingest(args.source, progress_cb=_progress_cb)
    elif args.stage:
        res = run_stage(args.stage, progress_cb=_progress_cb)
    else:  # --all
        res = run_ingest(progress_cb=_progress_cb)

    print("\n" + json.dumps(res, indent=2, default=str))
    return 0 if res.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())

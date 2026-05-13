"""
Diagnostic script — run RAG synthesis directly against a real run's
state and print the full result + traceback if anything fails.

Use this whenever the synthesis badge shows TEMPLATE on a run where
KNOWLEDGE-GROUNDED was expected. The script bypasses the API layer,
so any exception that the engine's wrapping ``try/except`` would have
swallowed surfaces here verbatim.

Usage:
  python scripts/debug_rag.py
  python scripts/debug_rag.py --run <run_dir>
  python scripts/debug_rag.py --run <run_dir> --json

Default run-dir search order:
  1. The path explicitly passed via --run.
  2. ``$AURORA_LATEST_RUN`` env var.
  3. The latest mtime under ``outputs/aurora_dataset_runs/``.
  4. The synthetic capture at ``docs/captures/2026-05-05/after/run``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from pathlib import Path

# Make the repo root importable when running this script standalone
# (i.e. ``python scripts/debug_rag.py`` from the repo root).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _find_run_dir(arg: str | None) -> Path | None:
    if arg:
        p = Path(arg)
        return p if p.exists() else None
    env = os.environ.get("AURORA_LATEST_RUN")
    if env and Path(env).exists():
        return Path(env)
    runs = Path("outputs/aurora_dataset_runs")
    if runs.exists():
        candidates = sorted(
            (p for p in runs.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if candidates:
            return candidates[0]
    fallback = Path("docs/captures/2026-05-05/after/run")
    return fallback if fallback.exists() else None


def main() -> int:
    p = argparse.ArgumentParser(prog="debug-rag",
                                  description="Direct-call RAG synthesis "
                                              "for diagnosis.")
    p.add_argument("--run", help="Path to a run dir")
    p.add_argument("--json", action="store_true",
                    help="Dump the full meta block as JSON at the end")
    p.add_argument("--quiet", action="store_true",
                    help="Suppress aurora.rag INFO log spam")
    args = p.parse_args()

    if not args.quiet:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        )

    run_dir = _find_run_dir(args.run)
    if run_dir is None:
        print("ERROR: could not locate a run dir. Pass --run <path> "
                "or run a dataset analysis first.")
        return 2
    print(f"Using run dir: {run_dir}")

    try:
        from fantasyai.aurora.state_builder import build_state
        state = build_state(run_dir)
    except Exception:
        print("ERROR: could not build state for that run dir:")
        traceback.print_exc()
        return 3

    print(f"Built state — {len(state.get('findings') or [])} findings, "
            f"{len((state.get('system_model') or {}).get('domain_csv', '').split(','))} "
            "domain hints")

    # Bank diagnostics.
    try:
        from fantasyai.aurora.knowledge_bank import get_bank
        bank = get_bank()
        s = bank.summary()
        print(f"Bank: {s['total_entries']} entries, "
                f"embedder dim={s['embedding_dim']} "
                f"({s['embedding_model']})")
        if getattr(bank, "needs_vector_rebuild", False):
            print("[!] bank.needs_vector_rebuild=True - vector index dim "
                    "does not match the active embedder.")
            print("    Run this first:")
            print("      python -m fantasyai.aurora.knowledge_bank.ingest "
                    "--seed-only")
            print("    Then re-run this debug script.")
            return 6
    except Exception:
        print("ERROR: could not open knowledge bank:")
        traceback.print_exc()
        return 4

    print()
    print("=" * 70)
    print("Calling synthesize_rag directly")
    print("=" * 70)

    try:
        from fantasyai.aurora.synthesis.rag import synthesize_rag
        res = synthesize_rag(state)
    except Exception:
        print("RAG raised an unhandled exception:")
        traceback.print_exc()
        return 5

    meta = res.get("synthesis_meta") or {}
    print()
    print(f"  ok:              {res.get('ok')}")
    print(f"  mode:            {res.get('mode')}")
    print(f"  reason:          {res.get('reason')}")
    print(f"  elapsed_ms:      {meta.get('elapsed_ms')}")
    print(f"  n_retrieved:     {meta.get('n_retrieved')}")
    print(f"  n_claims_total:  {meta.get('n_claims_total')}")
    print(f"  n_verified:      {meta.get('n_claims_verified')} "
            f"(recovered={meta.get('n_claims_recovered')})")
    print(f"  verified_frac:   {meta.get('verified_fraction')}")
    if meta.get("retrieved_entries"):
        print(f"  top-3 retrieved:")
        for e in meta["retrieved_entries"][:3]:
            print(f"    · {e['id']}  rel={e['relevance']:.2f}")

    if res.get("ok"):
        print()
        print("=" * 70)
        print("INTERPRETIVE SUMMARY")
        print("=" * 70)
        print(res["interpretive_summary"])
    else:
        print()
        print("RAG REJECTED. Inspect first claims for citation-format issues:")
        for i, c in enumerate(meta.get("claims", [])[:6]):
            print(f"  [{i}] verified={c['verified']} "
                    f"raw_tokens={c.get('raw_tokens', [])} "
                    f"resolved={c['cited_entries']}")
            print(f"      text: {c['text'][:140]}…")

    if args.json:
        print()
        print("=" * 70)
        print("FULL META JSON")
        print("=" * 70)
        print(json.dumps(meta, indent=2, default=str))

    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

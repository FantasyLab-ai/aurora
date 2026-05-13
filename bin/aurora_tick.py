"""
aurora_tick — single-call scheduler entry point.

Runs the full agent chain once and exits. Designed for cron / Task Scheduler;
no daemon, no long-running process. Output goes to stdout (capture with
``>> ~/.aurora/tick.log 2>&1`` if you want a log file).

Usage:
    python bin/aurora_tick.py [--quiet] [--task-for-json '{"agent_csv_url": {"url": "..."}}']

Exit codes:
    0  any agent succeeded (status=ok)
    1  every agent reported no_op
    2  at least one agent failed
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Ensure the package root is importable regardless of cwd.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Aurora autonomous tick")
    p.add_argument("--quiet", action="store_true",
                    help="Suppress per-agent line output; print only summary.")
    p.add_argument("--task-for-json", default=None,
                    help="JSON object of per-agent task overrides.")
    args = p.parse_args(argv)

    try:
        from fantasyai.aurora.agents import default_scheduler
    except Exception as e:
        print(f"[aurora_tick] failed to import agents: {e}", file=sys.stderr)
        return 2

    task_for = None
    if args.task_for_json:
        try:
            task_for = json.loads(args.task_for_json)
        except Exception as e:
            print(f"[aurora_tick] bad --task-for-json: {e}", file=sys.stderr)
            return 2

    sched = default_scheduler()
    start = time.time()
    print(f"[aurora_tick] {time.strftime('%Y-%m-%dT%H:%M:%S')} — "
          f"running {len(sched.list_agents())} agents")

    results = sched.tick(task_for=task_for)
    elapsed = time.time() - start
    n_ok = sum(1 for r in results if r.status == "ok")
    n_noop = sum(1 for r in results if r.status == "no_op")
    n_failed = sum(1 for r in results if r.status == "failed")
    n_blocked = sum(1 for r in results if r.status == "blocked")

    if not args.quiet:
        for r in results:
            print(f"  [{r.status:>8}] {r.agent_id:<28} {r.summary}")

    print(f"[aurora_tick] done in {elapsed:.2f}s — "
          f"ok={n_ok}  no_op={n_noop}  blocked={n_blocked}  failed={n_failed}")

    if n_failed > 0:
        return 2
    if n_ok == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

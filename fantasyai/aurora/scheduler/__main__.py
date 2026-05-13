"""
CLI entry: ``python -m fantasyai.aurora.scheduler [--once|--watch|--run-now]``

Modes:
  --once       run the overnight pass exactly once and exit (suitable for cron)
  --watch      stay running; fire when scheduled or when a missed window opens
  --run-now    fire immediately, ignore schedule (manual override)
  --status     print scheduler state + the time until the next fire

Without args, defaults to --once for cron-friendliness.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from .overnight import (
    SchedulerConfig, load_config, save_config,
    run_overnight_pass, should_fire_now, _load_state,
    _next_scheduled, _now, DEFAULT_SCHEDULE_PATH, SCHEDULER_STATE_FILE,
)


def _print_status() -> int:
    cfg = load_config()
    state = _load_state()
    next_fire = _next_scheduled(cfg)
    fire_now, reason = should_fire_now(cfg, state)
    print(f"Aurora overnight scheduler — {datetime.utcnow().isoformat()}Z")
    print(f"  config:        {DEFAULT_SCHEDULE_PATH}")
    print(f"  state:         {SCHEDULER_STATE_FILE}")
    print(f"  active:        {cfg.active}")
    print(f"  run_at_local:  {cfg.run_at_local}")
    print(f"  sources:       {[s.id for s in cfg.sources if s.active]}")
    print(f"  catch_up:      mode={cfg.catch_up.mode}")
    print(f"  next fire:     {next_fire.isoformat()}")
    print(f"  fire now?      {fire_now}  ({reason})")
    print(f"  last completed:{state.last_completed_at}")
    print(f"  missed count:  {state.missed_count}")
    return 0


def _run_pass() -> int:
    print("[scheduler] starting overnight pass…")
    result = run_overnight_pass()
    print(json.dumps({
        "started_at": result["started_at"],
        "completed_at": result["completed_at"],
        "elapsed_sec": result["elapsed_sec"],
        "n_sources_attempted": result["n_sources_attempted"],
        "n_ok": result["n_ok"],
        "n_failed": result["n_failed"],
    }, indent=2))
    for r in result["results"]:
        marker = "✓" if r["ok"] else "✗"
        print(f"  {marker} {r['source']:14s} run_id={r['run_id']}  "
              f"err={r.get('error') or '-'}")
    return 0 if result["n_failed"] == 0 else 1


def _watch() -> int:
    """Long-running mode. Checks every 60s; fires when should_fire_now=True."""
    print(f"[scheduler] watch mode — checking every 60s.")
    print(f"  config: {DEFAULT_SCHEDULE_PATH}")
    cfg = load_config()
    print(f"  schedule: {cfg.run_at_local} local · sources: "
          f"{[s.id for s in cfg.sources if s.active]}")
    while True:
        try:
            cfg = load_config()      # re-read each tick so edits take effect
            fire, reason = should_fire_now(cfg)
            if fire:
                print(f"[scheduler] firing — {reason}")
                _run_pass()
                # After a fire, sleep ~5 min so we don't immediately
                # re-fire on the same window.
                time.sleep(300)
            time.sleep(60)
        except KeyboardInterrupt:
            print("\n[scheduler] interrupted; exiting watch mode.")
            return 0
        except Exception as e:
            print(f"[scheduler] tick error: {e}")
            time.sleep(60)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m fantasyai.aurora.scheduler")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--once",     action="store_true",
                    help="Run one pass and exit.")
    g.add_argument("--watch",    action="store_true",
                    help="Stay running; fire when scheduled.")
    g.add_argument("--run-now",  action="store_true",
                    help="Fire immediately regardless of schedule.")
    g.add_argument("--status",   action="store_true",
                    help="Print state + next-fire info.")
    p.add_argument("--init",     action="store_true",
                    help="Materialise the default config to disk.")
    args = p.parse_args(argv)

    if args.init:
        cfg = load_config()
        save_config(cfg)
        print(f"[scheduler] wrote default config → {DEFAULT_SCHEDULE_PATH}")
        return 0
    if args.status:
        return _print_status()
    if args.watch:
        return _watch()
    if args.run_now:
        return _run_pass()
    # Default: --once
    cfg = load_config()
    fire, reason = should_fire_now(cfg)
    if not fire:
        print(f"[scheduler] not in firing window ({reason}); exiting.")
        return 0
    return _run_pass()


if __name__ == "__main__":
    sys.exit(main())

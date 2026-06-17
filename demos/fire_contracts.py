"""
demos.fire_contracts — fire Aurora Decision Contracts on the latest run.

WHY THIS EXISTS:
    Aurora's streaming pipeline auto-fires loaded contracts on every
    finding (via the contracts_bridge in streaming/). The legacy
    batch path (drop a CSV → run analysis) does NOT auto-fire. So
    the demos need an explicit step that says "this batch run is
    done — now evaluate all contracts against its bundle and fire
    the ones that match."

    This script is that step.

USAGE (run from the worktree root):

    python -m demos.fire_contracts
        # → finds the latest run under outputs/aurora_dataset_runs/,
        #   loads contracts from ~/.aurora/decision_contracts/,
        #   evaluates each against the run's bundle,
        #   fires the ones whose trigger predicate is satisfied.

    python -m demos.fire_contracts --run-dir <path>
        # explicit run to evaluate

    python -m demos.fire_contracts --watch
        # watch outputs/aurora_dataset_runs/ for the next new run,
        # then fire automatically. Useful during recording — leave
        # this running in Terminal 3 and every CSV you drop auto-
        # tripped within ~1 second of the run completing.

    python -m demos.fire_contracts --dry-run
        # evaluate but don't actually call action URLs

The script prints a one-line report per evaluated contract so you can
see on camera which one matched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Force stdout to UTF-8 — Windows console defaults to cp1252 and chokes
# on any non-ASCII printable. This makes the colour codes + any future
# unicode glyphs work on Windows Terminal / PowerShell.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# Cosmetic terminal helpers — keep output cinematic for OBS capture.
RESET = "\x1b[0m"
BOLD  = "\x1b[1m"
DIM   = "\x1b[2m"
MINT  = "\x1b[38;5;121m"
CYAN  = "\x1b[38;5;87m"
AMBER = "\x1b[38;5;215m"
CRIT  = "\x1b[38;5;203m"
GREY  = "\x1b[38;5;240m"


def _say(msg: str, color: str = "") -> None:
    sys.stdout.write(color + msg + (RESET if color else "") + "\n")
    sys.stdout.flush()


def _find_latest_run(outputs_root: Path) -> Optional[Path]:
    if not outputs_root.exists():
        return None
    candidates = [p for p in outputs_root.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _fetch_live_state(studio_url: str) -> Optional[Dict[str, Any]]:
    """Hit the running Studio's /api/state. This is the ground truth
    — Aurora's AUTO tier runs in-process and doesn't always write
    full artifacts to disk, so live-API is more reliable than
    `build_state(run_dir)` from disk."""
    from urllib import request as _urlreq
    try:
        with _urlreq.urlopen(f"{studio_url.rstrip('/')}/api/state",
                              timeout=5) as resp:
            if resp.status >= 300:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _build_bundle(run_dir: Optional[Path],
                  *, studio_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Three strategies, in order:

      1. Hit the running Studio's /api/state directly. This is the
         most reliable — it sees the live in-memory analysis state
         the user actually ran in the browser.
      2. Build state from disk via `state_builder.build_state(run_dir)`.
      3. Fallback: minimal envelope from whatever the state_builder
         could produce.
    """
    # Strategy 1 — live API.
    if studio_url:
        live = _fetch_live_state(studio_url)
        if live and (live.get("findings") or live.get("anomalies")):
            return {
                "run":      {"run_id": live.get("run_id")
                              or (live.get("run_meta") or {}).get("run_id")},
                "findings": live.get("findings") or [],
                "anomalies": live.get("anomalies") or [],
                "forecast":  live.get("forecast") or {},
                "fabricated_count": live.get("fabricated_count", 0),
                "_source": "live_api",
            }
        if live:
            _say(f"  live /api/state had no findings — falling back to disk",
                  AMBER)
    if run_dir is None:
        return None
    # Strategy 2 — SDK bundle from disk.
    try:
        from aurora_sdk import Bundle
        from fantasyai.aurora.state_builder import build_state
        state = build_state(run_dir)
        if state and (state.get("findings") or state.get("anomalies")):
            bundle = Bundle.from_state(state, run_dir=run_dir)
            doc = bundle.doc if hasattr(bundle, "doc") else dict(bundle)
            doc["_source"] = "disk_sdk"
            return doc
    except Exception as e:
        _say(f"  SDK bundle build failed: {type(e).__name__}: {e}", AMBER)
    # Strategy 3 — minimal envelope from disk state.
    try:
        from fantasyai.aurora.state_builder import build_state
        state = build_state(run_dir)
        return {
            "run":      {"run_id": state.get("run_id") or run_dir.name},
            "findings": state.get("findings") or [],
            "anomalies": state.get("anomalies") or [],
            "forecast":  state.get("forecast") or {},
            "fabricated_count": 0,
            "_source":  "disk_fallback",
        }
    except Exception as e:
        _say(f"  fallback state build failed: {type(e).__name__}: {e}", CRIT)
        return None


def _evaluate_and_fire(run_dir: Optional[Path],
                        *, dry_run: bool = False,
                        studio_url: Optional[str] = None) -> int:
    """Returns the number of contracts that fired."""
    try:
        from fantasyai.aurora.decision_contracts.engine import (
            load_contracts, fire_contract,
        )
    except Exception as e:
        _say(f"contracts engine not importable: {e}", CRIT)
        return 0

    _say("")
    label = run_dir.name if run_dir else "live Studio state"
    _say(f"+--- FIRING CONTRACTS for {label} ----------", CYAN)

    bundle = _build_bundle(run_dir, studio_url=studio_url)
    if bundle is None:
        _say("+-  could not build bundle — abort", CRIT)
        _say(f"  hint: is the Studio running at {studio_url}? "
              f"or try --run-dir <path>", DIM)
        return 0
    src = bundle.get("_source", "?")
    _say(f"|  bundle source: {src}", DIM)

    n_findings = len(bundle.get("findings") or [])
    crit_count = sum(
        1 for f in (bundle.get("findings") or [])
        if isinstance(f, dict) and str(f.get("severity", "")).lower()
                                       in ("crit", "critical")
    )
    _say(f"|  bundle: {n_findings} findings ({crit_count} critical, "
          f"fabricated={bundle.get('fabricated_count', 0)})", GREY)
    _say("|", CYAN)

    contracts = load_contracts()
    if not contracts:
        _say("|  no contracts in ~/.aurora/decision_contracts/", AMBER)
        _say(f"|  → did you copy them? See demos/README.md § 0.2", DIM)
        _say("+--------------------------------------------------", CYAN)
        return 0

    n_fired = 0
    for c in contracts:
        try:
            rec = fire_contract(c, bundle, dry_run=dry_run)
            errs = list(getattr(rec, "errors", []) or [])
            attempted = int(getattr(rec, "actions_attempted", 0) or 0)
            succeeded = int(getattr(rec, "actions_succeeded", 0) or 0)
            if "not_satisfied" in errs:
                _say(f"|  . {c.id:32}  skip — predicate not satisfied", GREY)
                continue
            if "rate_limited" in errs:
                _say(f"|  . {c.id:32}  skip — rate limited", AMBER)
                continue
            if "dry_run" in errs:
                _say(f"|  . {c.id:32}  matched (dry run, no action)", MINT)
                n_fired += 1
                continue
            n_fired += 1
            ok = succeeded == attempted and attempted > 0
            colour = MINT if ok else AMBER
            _say(
                f"|  . {c.id:32}  FIRED  . "
                f"{succeeded}/{attempted} actions",
                colour,
            )
            for e in errs:
                _say(f"|      -> {e}", AMBER)
        except Exception as e:
            _say(f"|  . {c.id:32}  CRASH: {type(e).__name__}: {e}", CRIT)

    if n_fired:
        _say("|", CYAN)
        _say(f"|  {BOLD}{n_fired} contract(s) fired.{RESET}{CYAN}", CYAN)
    else:
        _say(f"|  no contracts matched this bundle.", AMBER)
    _say("+--------------------------------------------------", CYAN)
    return n_fired


def _watch(outputs_root: Path,
            *, poll_interval_s: float = 1.0,
            dry_run: bool = False,
            studio_url: Optional[str] = None) -> int:
    """Watch either the live Studio's run_id OR the outputs dir.

    When `studio_url` is set (default), we poll `/api/state` and fire
    every time the active run_id changes. That's the most reliable
    signal — it works whether or not the run dir on disk is populated.
    """
    if studio_url:
        _say(f"watching {studio_url}/api/state for new runs "
              f"(Ctrl+C to stop)", CYAN)
        last_run_id: Optional[str] = None
        # Establish baseline so we don't fire on the run that was
        # already there when --watch started.
        first = _fetch_live_state(studio_url)
        if first is not None:
            last_run_id = (first.get("run_id")
                            or (first.get("run_meta") or {}).get("run_id"))
            if last_run_id:
                _say(f"  current run: {last_run_id} (will fire on the NEXT run)", DIM)
        try:
            while True:
                time.sleep(poll_interval_s)
                state = _fetch_live_state(studio_url)
                if state is None:
                    continue
                rid = (state.get("run_id")
                        or (state.get("run_meta") or {}).get("run_id"))
                if rid and rid != last_run_id:
                    # Wait briefly for the state to settle.
                    time.sleep(1.5)
                    _evaluate_and_fire(None, dry_run=dry_run,
                                        studio_url=studio_url)
                    last_run_id = rid
        except KeyboardInterrupt:
            _say("\nwatch stopped.", DIM)
            return 0
    # Disk-only watcher fallback.
    _say(f"watching {outputs_root} for new runs (Ctrl+C to stop)", CYAN)
    last_seen: Optional[Path] = _find_latest_run(outputs_root)
    if last_seen:
        _say(f"  current latest: {last_seen.name} (will fire on the NEXT run)", DIM)
    try:
        while True:
            time.sleep(poll_interval_s)
            current = _find_latest_run(outputs_root)
            if current is None:
                continue
            if last_seen is not None and current == last_seen:
                continue
            # Wait a moment so the run-completion writes finish.
            time.sleep(2.0)
            _evaluate_and_fire(current, dry_run=dry_run, studio_url=None)
            last_seen = current
    except KeyboardInterrupt:
        _say("\nwatch stopped.", DIM)
        return 0


def _default_studio_url() -> str:
    """Resolve the running Studio's URL from env or sensible default."""
    port = os.environ.get("AURORA_PORT", "8000").strip()
    return f"http://127.0.0.1:{port}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="demos.fire_contracts")
    parser.add_argument("--run-dir", type=Path, default=None,
                         help="Force an on-disk run directory. Default: ask "
                              "the running Studio at --studio (which is the "
                              "ground truth — AUTO-tier runs are in-memory).")
    parser.add_argument("--studio", default=_default_studio_url(),
                         help=f"Running Studio URL (default: "
                              f"{_default_studio_url()}; honours $AURORA_PORT)")
    parser.add_argument("--no-studio", action="store_true",
                         help="Skip the live-API path; force the on-disk path.")
    parser.add_argument("--outputs-root", type=Path,
                         default=Path("outputs/aurora_dataset_runs"),
                         help="Where Aurora writes runs (default: "
                              "outputs/aurora_dataset_runs).")
    parser.add_argument("--watch", action="store_true",
                         help="Poll the running Studio + auto-fire on every "
                              "new run_id.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Evaluate but don't actually call action URLs.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    studio_url = None if args.no_studio else args.studio

    if args.watch:
        return _watch(args.outputs_root,
                       dry_run=args.dry_run, studio_url=studio_url)

    # One-shot.
    rd: Optional[Path] = args.run_dir
    if rd is None and studio_url is None:
        # Pure disk path.
        rd = _find_latest_run(args.outputs_root)
        if rd is None:
            _say(f"no runs found under {args.outputs_root}", CRIT)
            return 1
        _say(f"latest run: {rd.name}", DIM)

    n = _evaluate_and_fire(rd, dry_run=args.dry_run, studio_url=studio_url)
    return 0 if n >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

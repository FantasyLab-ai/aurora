"""
demos.sentinel_watch — turn a directory into an Aurora Sentinel data source.

This is the missing "auto-connect to a live data stream" piece. It
tells Aurora's streaming runner to watch a directory (or single file),
re-analyse on every new row, AND auto-fire every loaded Decision
Contract on every new finding — which the relay then fans out to
Discord / Slack / OBS overlay / log / device.

End-to-end pipeline once this is running:

    files dropped into <watch_path>
        → Aurora streaming runner rolls them into a window
        → analytical pipeline runs on the window
        → findings emerge
        → contracts_bridge auto-fires matching contracts
        → relay receives the webhook
        → Discord/Slack post, overlay card fires, device reacts

The Studio doesn't need to be re-pointed at the dataset between
takes; it just watches the directory forever.

Usage:

    # Start the relay first (see demos/README.md), then:
    python -m demos.sentinel_watch  C:/path/to/incoming/  \
        --file-glob *.csv  --poll 2.0

Or to drive Aurora from a non-file source via direct HTTP POST,
see Aurora's `/api/stream/ingest` endpoint + the streaming docs.

When you stop the script with Ctrl+C, the streaming runner stops too.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import request as _urlreq

# Force stdout to UTF-8 so the box-drawing chars don't choke on Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# Cosmetic terminal helpers — match fire_contracts.py for visual consistency.
RESET = "\x1b[0m"
BOLD  = "\x1b[1m"
DIM   = "\x1b[2m"
MINT  = "\x1b[38;5;121m"
CYAN  = "\x1b[38;5;87m"
AMBER = "\x1b[38;5;215m"
CRIT  = "\x1b[38;5;203m"
GREY  = "\x1b[38;5;240m"


def _say(msg: str = "", color: str = "") -> None:
    sys.stdout.write(color + msg + (RESET if color else "") + "\n")
    sys.stdout.flush()


def _default_studio_url() -> str:
    port = os.environ.get("AURORA_PORT", "8000").strip()
    return f"http://127.0.0.1:{port}"


def _post_json(url: str, body: Dict[str, Any],
                timeout_s: float = 6.0) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = _urlreq.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                  "User-Agent": "aurora-sentinel-watch/1.0"},
    )
    try:
        with _urlreq.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except Exception:
                return {"ok": resp.status < 300, "raw": raw,
                        "status": resp.status}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _get_json(url: str, timeout_s: float = 4.0) -> Dict[str, Any]:
    try:
        with _urlreq.urlopen(url, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def start_watch(path: Path,
                 *,
                 studio_url: str,
                 file_glob: str = "*.csv",
                 poll_interval_s: float = 2.0,
                 max_rows: int = 10_000,
                 fire_contracts: bool = True) -> Dict[str, Any]:
    """POST /api/stream/start with the demo-friendly defaults."""
    return _post_json(f"{studio_url.rstrip('/')}/api/stream/start", {
        "path": str(path),
        "file_glob": file_glob,
        "poll_interval_s": float(poll_interval_s),
        "max_rows": int(max_rows),
        "fire_contracts": bool(fire_contracts),
    })


def stop_watch(studio_url: str) -> Dict[str, Any]:
    return _post_json(f"{studio_url.rstrip('/')}/api/stream/stop", {})


def stream_status(studio_url: str) -> Dict[str, Any]:
    return _get_json(f"{studio_url.rstrip('/')}/api/stream/status")


def _print_banner(path: Path, studio_url: str, file_glob: str,
                   poll_interval_s: float, fire_contracts: bool) -> None:
    _say("")
    _say("=" * 64, CYAN)
    _say(f"  AURORA SENTINEL — STREAMING WATCH", BOLD + CYAN)
    _say("=" * 64, CYAN)
    _say(f"  watch path        : {path}", GREY)
    _say(f"  file glob         : {file_glob}", GREY)
    _say(f"  poll interval     : {poll_interval_s}s", GREY)
    _say(f"  studio            : {studio_url}", GREY)
    _say(f"  fire contracts    : {'yes — every new finding hits the relay'
                                    if fire_contracts else 'no'}", GREY)
    _say("=" * 64, CYAN)
    _say("")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="demos.sentinel_watch",
        description="Aurora Sentinel — watch a directory for live data + auto-fire contracts.",
    )
    parser.add_argument("path", type=Path,
                         help="Directory (or single file) for Aurora to watch.")
    parser.add_argument("--studio", default=_default_studio_url(),
                         help=f"Running Studio URL (default: "
                              f"{_default_studio_url()}; honours $AURORA_PORT)")
    parser.add_argument("--file-glob", default="*.csv",
                         help="Pattern to match new files (default: *.csv)")
    parser.add_argument("--poll", type=float, default=2.0,
                         help="Poll interval in seconds (default: 2.0)")
    parser.add_argument("--max-rows", type=int, default=10_000,
                         help="Rolling-window cap (default: 10000)")
    parser.add_argument("--no-fire-contracts", action="store_true",
                         help="Disable the contracts-bridge fan-out "
                              "(default: on — every finding fires).")
    parser.add_argument("--stop", action="store_true",
                         help="Stop a running watch and exit.")
    parser.add_argument("--status", action="store_true",
                         help="Print current watch status and exit.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.stop:
        r = stop_watch(args.studio)
        _say(json.dumps(r, indent=2))
        return 0 if r.get("ok") else 1

    if args.status:
        r = stream_status(args.studio)
        _say(json.dumps(r, indent=2))
        return 0

    path = args.path.expanduser().resolve()
    if not path.exists():
        _say(f"path does not exist: {path}", CRIT)
        return 2

    _print_banner(path, args.studio, args.file_glob, args.poll,
                   not args.no_fire_contracts)

    # Start.
    start_resp = start_watch(
        path,
        studio_url=args.studio,
        file_glob=args.file_glob,
        poll_interval_s=args.poll,
        max_rows=args.max_rows,
        fire_contracts=not args.no_fire_contracts,
    )
    if not start_resp.get("ok"):
        _say(f"  start failed: {start_resp.get('error') or start_resp}", CRIT)
        _say(f"  is the Studio running at {args.studio}?", AMBER)
        return 1

    _say(f"  watching started.", MINT)
    contracts_state = start_resp.get("contracts_attached")
    if contracts_state is False and not args.no_fire_contracts:
        _say(f"  ! contracts-bridge couldn't attach — findings will stream,",
              AMBER)
        _say(f"    but won't fire contracts. Check the Aurora log.", AMBER)
    elif contracts_state:
        _say(f"  contracts-bridge attached — every new finding fires.", MINT)

    _say("")
    _say(f"  drop files into {path} to trigger Aurora.", GREY)
    _say(f"  press Ctrl+C to stop watching.", GREY)
    _say("")

    # Status heartbeat — every 5s print a one-line summary.
    last_run_count = -1
    last_findings_count = -1
    try:
        while True:
            time.sleep(5.0)
            st = stream_status(args.studio)
            if not st.get("ok"):
                continue
            running = bool(st.get("running"))
            rc = int(st.get("run_count") or 0)
            fc = int(st.get("last_findings_count") or 0)
            wr = int(st.get("window_rows") or 0)
            ded = int(st.get("dedupe_size") or 0)
            if rc != last_run_count or fc != last_findings_count:
                _say(f"  {time.strftime('%H:%M:%S')}  "
                      f"runs={rc}  window={wr} rows  "
                      f"last_findings={fc}  deduped={ded}  "
                      f"{'(running)' if running else '(idle)'}",
                      GREY)
                last_run_count = rc
                last_findings_count = fc
    except KeyboardInterrupt:
        _say("")
        _say("  stopping watch...", DIM)
        stop_resp = stop_watch(args.studio)
        if stop_resp.get("ok"):
            _say("  stopped cleanly.", MINT)
        else:
            _say(f"  stop returned: {stop_resp.get('error') or stop_resp}",
                  AMBER)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Aurora demo replay harness.

Streams rows from a source CSV into a growing target CSV so an Aurora
streaming watcher (or a hand-triggered re-run loop) sees data arriving
"live" on camera. Configurable speed lets a 1000-row dataset compress
into 20 s for a TikTok cut.

Usage:

    # Stream the canonical bearing dataset into a target file at 50x speed:
    python -m demos.replay data/fixtures/factory_bearing_demo.csv \
                            --to demos/_live/bearing.csv \
                            --speed 50 \
                            --start-rows 100

    # Drive Aurora's streaming endpoint directly (no file needed):
    python -m demos.replay data/fixtures/factory_bearing_demo.csv \
                            --post http://127.0.0.1:8000/api/stream/ingest \
                            --speed 50

Two modes:

    --to FILE     — append rows to a target CSV. Aurora's FileWatcher
                    sees the file grow and reruns on each delta. Best
                    for the demos because the camera can show the
                    file lengthening + the contract tripping in one shot.

    --post URL    — POST each row as a JSON record to a streaming
                    endpoint. Useful for headless / agent demos that
                    don't need a visible file.

Conventions:

  * Source CSV must have a header row. Every row downstream is the
    same shape.
  * --start-rows N writes the first N rows immediately so the watcher
    has a baseline window before the "interesting" data starts
    streaming in.
  * The harness writes a small banner to stdout every row so the
    streamer can see progress while OBS is running.
  * Failures are caught + printed; the harness exits 0 unless the
    source file is missing.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request as _urlreq


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _write_header(target: Path, header: List[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()


def _append_row(target: Path, header: List[str], row: Dict[str, str]) -> None:
    with open(target, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writerow(row)


def _post_row(url: str, row: Dict[str, str], timeout_s: float = 4.0) -> bool:
    body = json.dumps(row, ensure_ascii=False).encode("utf-8")
    req = _urlreq.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                  "User-Agent": "aurora-demo-replay/1.0"},
    )
    try:
        with _urlreq.urlopen(req, timeout=timeout_s) as resp:
            return resp.status < 300
    except Exception as e:
        print(f"  POST failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def run(source: Path, *,
        target_path: Optional[Path] = None,
        post_url: Optional[str] = None,
        speed: float = 1.0,
        start_rows: int = 0,
        dt_seconds: float = 1.0,
        max_rows: Optional[int] = None) -> int:
    """Stream rows from `source` to either a file or an HTTP endpoint.

    Args:
        source:       Path to the source CSV (must have header).
        target_path:  When set, append every row here. Caller is
                      responsible for cleaning up before the next take.
        post_url:     When set, POST every row to this URL as JSON.
        speed:        Real-time multiplier. 1.0 → row-per-dt-second.
                      50.0 → 50x faster (1 row every dt/50 seconds).
        start_rows:   Number of rows to emit immediately at takeoff
                      (the baseline window for the analytical engine
                      before the "interesting" data starts).
        dt_seconds:   The notional time between rows (default 1.0).
                      Real interval = dt_seconds / speed.
        max_rows:     Stop after this many emitted rows (incl. start_rows).

    Returns 0 on clean exit.
    """
    if not source.exists():
        print(f"ERROR: source not found: {source}", file=sys.stderr)
        return 2
    if target_path is None and not post_url:
        print("ERROR: must pass --to FILE or --post URL", file=sys.stderr)
        return 2

    rows = _read_csv_rows(source)
    if not rows:
        print(f"ERROR: source has no data rows: {source}", file=sys.stderr)
        return 2
    header = list(rows[0].keys())
    n_total = len(rows)
    cap = min(max_rows, n_total) if max_rows else n_total

    # Initialise target file with header + the start window.
    if target_path is not None:
        _write_header(target_path, header)
        for r in rows[:start_rows]:
            _append_row(target_path, header, r)
        print(f"[replay] wrote header + {start_rows} baseline rows → {target_path}")

    # Stream the remainder.
    interval = max(0.001, float(dt_seconds) / max(0.001, float(speed)))
    n_done = start_rows
    start_t = time.time()
    print(f"[replay] streaming {cap - start_rows} rows · "
           f"speed={speed}x · interval={interval:.3f}s/row")

    for i in range(start_rows, cap):
        r = rows[i]
        if target_path is not None:
            _append_row(target_path, header, r)
        if post_url is not None:
            _post_row(post_url, r)
        n_done += 1
        # Banner — terse so OBS scrollback isn't crowded.
        if (i - start_rows) % 25 == 0 or i == cap - 1:
            elapsed = time.time() - start_t
            print(f"  row {i+1}/{cap}  ({n_done} sent · {elapsed:.1f}s)")
        time.sleep(interval)

    print(f"[replay] done · {n_done} rows · {(time.time() - start_t):.1f}s")
    return 0


def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="demos.replay",
        description="Stream a CSV row-by-row to simulate live data for Aurora demos.",
    )
    parser.add_argument("source", type=Path, help="Source CSV (must have header).")
    parser.add_argument("--to", type=Path, default=None,
                         help="Target CSV to append rows into (relay/Aurora reads this).")
    parser.add_argument("--post", default=None,
                         help="POST each row as JSON to this URL (alternative to --to).")
    parser.add_argument("--speed", type=float, default=1.0,
                         help="Real-time multiplier. 50 = 50x faster than dt-seconds-per-row.")
    parser.add_argument("--start-rows", type=int, default=0,
                         help="Emit this many rows immediately at startup (baseline window).")
    parser.add_argument("--dt-seconds", type=float, default=1.0,
                         help="Notional time between rows (default 1.0).")
    parser.add_argument("--max-rows", type=int, default=None,
                         help="Cap total rows emitted.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return run(args.source,
                target_path=args.to,
                post_url=args.post,
                speed=args.speed,
                start_rows=args.start_rows,
                dt_seconds=args.dt_seconds,
                max_rows=args.max_rows)


if __name__ == "__main__":
    raise SystemExit(_main())

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

ROOT = Path(__file__).resolve().parents[1]
PY = Path(sys.executable)

SCRIPTS = [
    "scripts.run_aurora_forecast_regime_causal_demo",
    "scripts.run_aurora_env_logistics_multimetric_demo",
    "scripts.run_aurora_advanced_math_demo",
    "scripts.run_aurora_math_solvers_demo",
    "scripts.run_aurora_on_public_data_demo",
]

OUTDIR = ROOT / "outputs" / "aurora_stress"
OUTDIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = OUTDIR / "stress_log.csv"


@dataclass
class RunResult:
    module: str
    ok: bool
    returncode: int
    seconds: float
    ts: float


def run_module(mod: str) -> RunResult:
    t0 = time.time()
    p = subprocess.run([str(PY), "-m", mod], capture_output=True, text=True)
    dt = time.time() - t0

    # write per-run stdout/stderr for later debugging
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe = mod.replace(".", "_")
    (OUTDIR / f"{stamp}__{safe}__stdout.txt").write_text(p.stdout or "", encoding="utf-8")
    (OUTDIR / f"{stamp}__{safe}__stderr.txt").write_text(p.stderr or "", encoding="utf-8")

    return RunResult(mod, p.returncode == 0, p.returncode, dt, time.time())


def append_csv(row: RunResult) -> None:
    header = "ts,module,ok,returncode,seconds\n"
    if not CSV_PATH.exists():
        CSV_PATH.write_text(header, encoding="utf-8")
    line = f"{row.ts},{row.module},{int(row.ok)},{row.returncode},{row.seconds:.4f}\n"
    with CSV_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=240)
    ap.add_argument("--workers", type=int, default=1)  # placeholder for later multiprocessing
    args = ap.parse_args()

    end = time.time() + args.minutes * 60
    cycle = 0

    print(f"[STRESS] Starting Aurora stress harness for {args.minutes} minutes...")
    print(f"[STRESS] Writing logs to: {OUTDIR}")

    while time.time() < end:
        cycle += 1
        print(f"\n[STRESS] cycle {cycle}")

        for mod in SCRIPTS:
            r = run_module(mod)
            append_csv(r)
            status = "OK" if r.ok else "FAIL"
            print(f"[STRESS] {status} {mod} in {r.seconds:.2f}s (rc={r.returncode})")

        # tiny pause so we don't thrash disk nonstop
        
        # Also run a random dataset pass (does not crash the harness if missing)
        try:
            r = run_module("scripts.run_aurora_dataset_runner")
            append_csv(r)
            status = "OK" if r.ok else "FAIL"
            print(f"[STRESS] {status} scripts.run_aurora_dataset_runner in {r.seconds:.2f}s (rc={r.returncode})")
        except Exception as e:
            print(f"[STRESS] WARN dataset runner crashed: {e}")

    time.sleep(2)

    print("[STRESS] Done.")


if __name__ == "__main__":
    main()

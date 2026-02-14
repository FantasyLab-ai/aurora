from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd  # noqa: F401  (kept because _load_dataset can be useful)

# Prefer relative import when running as module: python -m scripts.make_insight_plots
try:
    from .engine_artifacts import generate_all_artifacts
except Exception:  # pragma: no cover
    # Fallback if someone runs the file directly (python scripts/make_insight_plots.py)
    from scripts.engine_artifacts import generate_all_artifacts


def _pick_latest_csv(p: Path) -> Path:
    """
    Accept either a CSV path or a folder containing CSVs.
    If a folder is passed, pick the most recently modified CSV.
    """
    p = Path(p)
    if p.is_file():
        return p
    if not p.exists():
        raise FileNotFoundError(f"Dataset path not found: {p}")

    csvs = sorted([x for x in p.glob("*.csv") if x.is_file()], key=lambda x: x.stat().st_mtime, reverse=True)
    if not csvs:
        raise FileNotFoundError(f"No .csv files found in folder: {p}")
    return csvs[0]


def _load_dataset(csv_path: str, time_col: Optional[str] = None) -> pd.DataFrame:
    """
    (Utility) Load a dataset for ad-hoc plotting/debug.
    Not required for the artifact pipeline, but keeping it safe & working.
    """
    p = _pick_latest_csv(Path(csv_path))
    df = pd.read_csv(p)

    if time_col and time_col in df.columns:
        try:
            df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
        except Exception:
            pass

    return df


def _extract_last_json(stdout_text: str) -> Optional[Dict[str, Any]]:
    """
    Your runner prints a final JSON blob. Grab the last {...} block.
    """
    s = (stdout_text or "").strip()
    if not s:
        return None
    a = s.rfind("{")
    b = s.rfind("}")
    if a == -1 or b == -1 or b <= a:
        return None
    try:
        return json.loads(s[a : b + 1])
    except Exception:
        return None


def _generate_for_run_dir(run_dir: Path, runner_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    out = generate_all_artifacts(
        run_dir=run_dir,
        runner_stdout=runner_json,
        do_plots=True,
        do_guarded_llm=True,
    )
    return out


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument(
        "--run-dir",
        help="If provided, skip running the dataset runner and just generate artifacts/plots for this run directory.",
        default=None,
    )
    # Everything else is passed through to scripts.run_aurora_dataset_runner
    args, passthrough = ap.parse_known_args(argv)

    # Mode A: generate for an existing run directory
    if args.run_dir:
        out = _generate_for_run_dir(Path(args.run_dir), runner_json=None)
        print(json.dumps(out, indent=2))
        return 0

    # Mode B: run the canonical runner then generate artifacts
    cmd = [sys.executable, "-m", "scripts.run_aurora_dataset_runner"] + passthrough
    proc = subprocess.run(cmd, capture_output=True, text=True)

    # Forward output so your normal UX remains
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)

    if proc.returncode != 0:
        return proc.returncode

    runner_json = _extract_last_json(proc.stdout or "")
    if not runner_json or "run_dir" not in runner_json:
        print("[WARN] Could not detect run_dir from runner stdout JSON. No artifacts generated.", file=sys.stderr)
        return 0

    out = _generate_for_run_dir(Path(runner_json["run_dir"]), runner_json=runner_json)

    print("\n[ARTIFACTS] wrote:")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

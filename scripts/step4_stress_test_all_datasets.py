from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
RUNNER_MOD = "scripts.run_aurora_dataset_runner"
DATA_EXTERNAL = ROOT / "fantasyai" / "data" / "external"
OUTROOT = ROOT / "outputs" / "aurora_dataset_runs"
REPORT = OUTROOT / "_stress_report.json"

FLAGS = ["--seed", "42", "--also-math", "--math-v2", "--deep-math", "--also-llm"]

def main():
    if not PY.exists():
        raise SystemExit(f"Missing venv python: {PY}")
    if not DATA_EXTERNAL.exists():
        raise SystemExit(f"Missing: {DATA_EXTERNAL}")
    OUTROOT.mkdir(parents=True, exist_ok=True)

    dataset_keys = []
    for ds in sorted(DATA_EXTERNAL.iterdir()):
        raw = ds / "raw"
        if ds.is_dir() and raw.exists() and any(raw.iterdir()):
            dataset_keys.append(ds.name)

    if not dataset_keys:
        raise SystemExit("No datasets found under fantasyai/data/external/*/raw")

    results = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "flags": FLAGS,
        "datasets": [],
    }

    print(f"[INFO] stress testing {len(dataset_keys)} datasets...")

    for key in dataset_keys:
        cmd = [str(PY), "-m", RUNNER_MOD, "--dataset", key] + FLAGS
        print(f"\n=== {key} ===")
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)

        ok = (p.returncode == 0)
        rec = {
            "dataset": key,
            "ok": ok,
            "returncode": p.returncode,
            "stdout_tail": (p.stdout or "")[-4000:],
            "stderr_tail": (p.stderr or "")[-4000:],
        }
        results["datasets"].append(rec)

        print("[OK]" if ok else "[FAIL]", key)
        if not ok:
            print("stderr tail:\n", rec["stderr_tail"])

    REPORT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n[DONE] wrote stress report:", REPORT)

    fails = [d for d in results["datasets"] if not d["ok"]]
    print(f"[SUMMARY] pass={len(results['datasets'])-len(fails)} fail={len(fails)}")
    if fails:
        print("Failed datasets:", ", ".join(d["dataset"] for d in fails))
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

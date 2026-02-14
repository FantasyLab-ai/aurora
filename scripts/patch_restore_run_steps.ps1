param()

$path = ".\scripts\run_aurora_with_steps_1_4.py"
if (-not (Test-Path $path)) { throw "run_aurora_with_steps_1_4.py not found at $path" }

$ts  = Get-Date -Format "yyyyMMdd_HHmmss"
$bak = "$path.bak_restore_$ts"
Copy-Item $path $bak -Force

# Minimal clean runner: calls dataset runner module and prints JSON, then calls insight plots
$clean = @"
import argparse
import json
from pathlib import Path
import subprocess
import sys


def _run(cmd: list[str]) -> dict:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "ok": p.returncode == 0,
        "returncode": p.returncode,
        "stdout": p.stdout or "",
        "stderr": p.stderr or "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--also-math", action="store_true")
    ap.add_argument("--math-v2", action="store_true")
    ap.add_argument("--deep-math", action="store_true")
    ap.add_argument("--also-llm", action="store_true")
    args = ap.parse_args()

    # Delegate to the dataset runner (keeps your behavior stable)
    cmd = [
        sys.executable,
        "-m",
        "scripts.run_aurora_dataset_runner",
        "--dataset",
        args.dataset,
        "--seed",
        str(args.seed),
    ]
    if args.also_math:
        cmd.append("--also-math")
    if args.math_v2:
        cmd.append("--math-v2")
    if args.deep_math:
        cmd.append("--deep-math")
    if args.also_llm:
        cmd.append("--also-llm")

    out = _run(cmd)

    # Best-effort: generate insight plots for the latest run
    try:
        from scripts.make_insight_plots import main as _make_insight_plots
        # infer latest run_dir
        runs_root = Path("outputs") / "aurora_dataset_runs"
        if runs_root.exists():
            latest = sorted([p for p in runs_root.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)[0]
            _make_insight_plots(str(latest))
    except Exception:
        pass

    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
"@

Set-Content -Path $path -Value $clean -Encoding UTF8

Write-Host "✅ Restored run_aurora_with_steps_1_4.py (clean, no indentation errors, calls insight plots best-effort)." -ForegroundColor Green
Write-Host "🧷 Backup: $bak" -ForegroundColor Yellow

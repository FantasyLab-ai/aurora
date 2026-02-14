from __future__ import annotations
from pathlib import Path
from datetime import datetime
import json
import re

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_aurora_dataset_runner.py"
OUTDIR = ROOT / "outputs" / "aurora_dataset_runs"

def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = p.with_suffix(p.suffix + f".bak_{ts}")
    bak.write_bytes(p.read_bytes())
    return bak

def main() -> int:
    if not RUNNER.exists():
        raise SystemExit(f"Missing: {RUNNER}")

    txt = RUNNER.read_text(encoding="utf-8")

    # If we already added the hook, do nothing
    if "AURORA_RUNNER_STDOUT_SUMMARY_HOOK" in txt:
        print("[OK] runner stdout summary hook already present")
        return 0

    bak = backup(RUNNER)
    print(f"[OK] backup -> {bak}")

    hook = r'''
# === AURORA_RUNNER_STDOUT_SUMMARY_HOOK ===
def _aurora_print_latest_run_summary():
    """
    Print a stable JSON summary to stdout even if upstream logic stops printing.
    This does NOT affect file outputs; it only improves CLI visibility.
    """
    try:
        root = Path(__file__).resolve().parents[1]
        outdir = root / "outputs" / "aurora_dataset_runs"
        if not outdir.exists():
            return
        runs = [p for p in outdir.iterdir() if p.is_dir()]
        if not runs:
            return
        latest = max(runs, key=lambda p: p.stat().st_mtime)

        # Best-effort derive dataset_key from folder name: "YYYYMMDD_HHMMSS__<dataset>"
        dataset_key = None
        m = re.search(r"__([A-Za-z0-9_\\-]+)$", latest.name)
        if m:
            dataset_key = m.group(1)

        summary = {
            "dataset_key": dataset_key,
            "run_dir": str(latest),
            "has_math_results": (latest / "math_results.json").exists(),
            "has_deep_math": (latest / "deep_math.json").exists(),
            "has_llm_narrative": (latest / "llm_narrative.json").exists(),
            "has_storycard": (latest / "storycard.json").exists(),
        }
        print(json.dumps(summary, indent=2))
        try:
            import sys
            sys.stdout.flush()
        except Exception:
            pass
    except Exception:
        # Never break the runner just because printing failed
        return
'''.lstrip("\n")

    # Append hook near end of file (safe)
    txt2 = txt.rstrip() + "\n\n" + hook + "\n"

    # Ensure __main__ calls the hook AFTER main()/execution.
    # We will replace an existing __main__ block if found; otherwise append one.
    if re.search(r"(?ms)^\s*if\s+__name__\s*==\s*[\"']__main__[\"']\s*:\s*\n", txt2):
        # Replace the whole __main__ block with a safe canonical one.
        txt2 = re.sub(
            r"(?ms)^\s*if\s+__name__\s*==\s*[\"']__main__[\"']\s*:\s*\n.*?$",
            "if __name__ == \"__main__\":\n"
            "    try:\n"
            "        main()\n"
            "    finally:\n"
            "        _aurora_print_latest_run_summary()\n",
            txt2
        )
    else:
        txt2 += (
            "\nif __name__ == \"__main__\":\n"
            "    try:\n"
            "        main()\n"
            "    finally:\n"
            "        _aurora_print_latest_run_summary()\n"
        )

    RUNNER.write_text(txt2, encoding="utf-8")
    print("[OK] runner patched -> stdout summary restored (latest run folder)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

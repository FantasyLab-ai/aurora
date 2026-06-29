import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Optional


def _run(cmd: list[str]) -> dict[str, Any]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "ok": p.returncode == 0,
        "returncode": p.returncode,
        "stdout": p.stdout or "",
        "stderr": p.stderr or "",
    }


def _extract_last_json_dict(text: str) -> Optional[dict[str, Any]]:
    """
    Best-effort: find the last JSON object in a messy stdout stream.
    Returns dict or None. Never raises.
    """
    if not isinstance(text, str):
        return None
    s = text.strip()
    if not s:
        return None

    # Scan backwards looking for a '{' that begins a valid JSON object
    for i in range(len(s) - 1, -1, -1):
        if s[i] != "{":
            continue
        candidate = s[i:]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def _ensure_artifacts(run_dir: Path) -> None:
    """
    Best-effort: ensure plots, plots_data, and _PLOTS_INDEX exist for the run.
    Never raises.
    """
    try:
        from scripts import engine_artifacts
        engine_artifacts.generate_all_artifacts(run_dir, do_plots=True)
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--also-math", action="store_true")
    ap.add_argument("--math-v2", action="store_true")
    ap.add_argument("--deep-math", action="store_true")
    ap.add_argument("--also-llm", action="store_true")
    args = ap.parse_args()

    # Delegate to the dataset runner (keep behavior stable).
    # Frozen: re-invoke our own exe with the dataset-runner sentinel instead
    # of `python -m scripts.run_aurora_dataset_runner` (which doesn't exist
    # inside a PyInstaller bundle). Must match desktop/backend/aurora_entry.py.
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "__aurora_runner_dataset__",
               "--dataset", args.dataset, "--seed", str(args.seed)]
    else:
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

    # Extract run_dir from the dataset runner's stdout JSON (if present)
    parsed = _extract_last_json_dict(out.get("stdout", ""))
    run_dir = None
    if isinstance(parsed, dict):
        run_dir = parsed.get("run_dir") or parsed.get("out_dir") or parsed.get("output_dir")

    if run_dir:
        try:
            run_path = Path(str(run_dir))
            _ensure_artifacts(run_path)
            out["run_dir"] = str(run_path)
            out["plots_index"] = str(run_path / "_PLOTS_INDEX.json")
        except Exception:
            # even if Path conversion fails, do not break the run
            out["run_dir"] = str(run_dir)

    # Also surface inner JSON for UI debugging (safe)
    if parsed:
        out["runner_json"] = parsed

    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

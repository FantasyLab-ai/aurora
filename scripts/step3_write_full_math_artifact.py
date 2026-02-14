from __future__ import annotations

from pathlib import Path
import json
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
OUTROOT = ROOT / "outputs" / "aurora_dataset_runs"

FILES = [
    "tableselection.json",
    "math_results.json",
    "deep_math.json",
    "llm_narrative.json",
    "llm_debug.json",
    "storycard.json",
]

def load_json(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        return {"error": f"load_failed:{type(e).__name__}:{e}", "path": str(p)}

def write_json(p: Path, obj):
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

def pick_note(x):
    if isinstance(x, dict):
        return x.get("note") or x.get("error") or "unknown"
    return "unknown"

def main():
    if not OUTROOT.exists():
        raise SystemExit(f"Missing outputs root: {OUTROOT}")

    latest = sorted([d for d in OUTROOT.iterdir() if d.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    if not latest:
        raise SystemExit("No aurora_dataset_runs folders found.")
    run_dir = latest[0]

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "artifacts": {},
    }

    for fn in FILES:
        payload["artifacts"][fn] = load_json(run_dir / fn)

    math_results = payload["artifacts"].get("math_results.json") or {}
    deep_math = payload["artifacts"].get("deep_math.json") or {}

    summary = {
        "run_dir": str(run_dir),
        "math_results_ok": not isinstance(math_results, dict) or ("error" not in math_results),
        "deep_math_version": deep_math.get("version") if isinstance(deep_math, dict) else None,
        "deep_forecasting": pick_note(deep_math.get("forecasting", {})) if isinstance(deep_math, dict) else None,
        "deep_regimes": pick_note(deep_math.get("regimes", {})) if isinstance(deep_math, dict) else None,
        "deep_causal": pick_note(deep_math.get("causal_what_if", {})) if isinstance(deep_math, dict) else None,
        "deep_optimization_ok": bool(((deep_math.get("optimization_demo") or {}).get("nonlinear_example") or {}).get("success")) if isinstance(deep_math, dict) else False,
    }

    write_json(run_dir / "math_full.json", payload)
    write_json(run_dir / "run_summary.json", summary)

    print("[DONE] wrote:")
    print("  -", run_dir / "math_full.json")
    print("  -", run_dir / "run_summary.json")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()

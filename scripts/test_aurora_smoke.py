# scripts/test_aurora_smoke.py
from __future__ import annotations

import sys
from fantasyai.aurora.subprocess_runner import run_python_module

MODULES = [
    # keep this list aligned with scripts you already have working
    "scripts.run_aurora_on_public_data_demo",
    "scripts.run_aurora_math_solvers_demo",
    "scripts.run_aurora_advanced_math_demo",
    "scripts.run_aurora_env_logistics_multimetric_demo",
    "scripts.run_aurora_forecast_regime_causal_demo",
]

def main() -> int:
    failures = 0
    for m in MODULES:
        print("=" * 90)
        print(f"[SMOKE] Running {m}")
        res = run_python_module(m, timeout_s=300)
        print(f"[SMOKE] ok={res.ok}, returncode={res.returncode}, json_blocks={len(res.parsed_json_blocks)}")
        if not res.ok:
            failures += 1
            print("[SMOKE] ---- STDOUT ----")
            print(res.stdout[-4000:])
            print("[SMOKE] ---- STDERR ----")
            print(res.stderr[-4000:])
    print("=" * 90)
    if failures:
        print(f"[SMOKE] FAILURES: {failures}")
        return 1
    print("[SMOKE] ALL GOOD ✅")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

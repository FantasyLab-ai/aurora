"""Pre-Part-2 verification — calls _trigger_run directly (no HTTP).

This runs in the SAME Python process as the test suite, so it picks up
the latest code changes immediately without needing the server to restart.
Reports honestly on the 6 verification items.
"""
from __future__ import annotations

import sys, time, json, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    # Isolate the per-run learning stores so we don't pollute ~/.aurora
    os.environ.setdefault("AURORA_LEARNING_STORE", "/tmp/_v_ls.jsonl")
    os.environ.setdefault("AURORA_PRIOR_VALIDATIONS", "/tmp/_v_pv.jsonl")
    os.environ.setdefault("AURORA_AGENT_RUNLOG", "/tmp/_v_rl.jsonl")
    os.environ.setdefault("AURORA_TEMPLATE_EVIDENCE", "/tmp/_v_te.jsonl")

    sys.path.insert(0, str(ROOT))
    # Import lazily so studio_api picks up edits.
    from studio_api import _trigger_run, DEFAULT_OUTPUTS

    # Real 128MB file, 2M rows, time-series.
    file_path = Path(r"C:\Users\bgrut\Desktop\Aurora_QIE\fantasyai\data\external\energy_eia_electric_power\raw\household_power_consumption.csv")
    if not file_path.exists():
        print("!! test file missing"); return
    size_mb = file_path.stat().st_size / 1e6
    print(f"=== Test file: {file_path.name}")
    print(f"    Size: {size_mb:.1f} MB  (>50 MB tiered threshold)")

    print("\n=== Trigger _trigger_run directly ===")
    t0 = time.time()
    result = _trigger_run(
        dataset=str(file_path), seed=42,
        also_math=True, math_v2=True, deep_math=True,
        also_llm=True, also_motif=True, also_orchestrator=True,
        use_cache=False,
    )
    elapsed = time.time() - t0
    print(f"    /api/run returned in {elapsed:.1f}s")
    print(f"    ok: {result.get('ok')}")
    print(f"    tiered: {result.get('tiered')}")
    print(f"    cached: {result.get('cached')}")
    print(f"    error_summary: {result.get('error_summary')}")
    print(f"    highest_tier: {result.get('highest_tier')}")
    parent = result.get("run_dir")

    if not parent:
        print("\n!! no parent run_dir returned; can't continue")
        return

    state_file = Path(parent) / "_SAMPLING_STATE.json"
    if not state_file.exists():
        print(f"\n!! _SAMPLING_STATE.json not at {state_file}")
        return

    st = json.loads(state_file.read_text(encoding='utf-8'))

    print("\n=== ITEM 1: tier-by-tier breakdown ===")
    for tn in "0123":
        ts = (st.get("tiers") or {}).get(tn, {})
        print(f"  T{tn}: state={ts.get('state'):<10} elapsed={ts.get('elapsed_s')}s "
              f"rows={ts.get('rows_sampled')}  err={(ts.get('error') or '-')[:60]}")

    # Item 3: T1 wall-clock target <60s.
    t1 = (st.get("tiers") or {}).get("1", {})
    t1_elapsed = t1.get("elapsed_s")
    print(f"\n=== ITEM 3: T1 elapsed = {t1_elapsed}s (budget: <60s) ===")
    if t1_elapsed is not None:
        if t1_elapsed < 60:
            print(f"  ✓ T1 within budget ({t1_elapsed:.1f}s)")
        else:
            print(f"  ✗ T1 OVER BUDGET ({t1_elapsed:.1f}s)")

    # Item 5: timeout messaging audit.
    print("\n=== ITEM 5: timeout messaging audit ===")
    msg = result.get("error_summary") or ""
    if "10 minutes" in msg:
        print(f"  ✗ Legacy '10 minutes' string still present: {msg}")
    elif "exceeded the" in msg and "budget" in msg:
        print(f"  ✓ Tier-aware timeout message: {msg}")
    elif msg:
        print(f"  Note: error_summary = {msg}")
    else:
        print("  ✓ No timeout message — tier 1 succeeded")

    # Item 2 + 4: build_state + sampling block + replication.
    print("\n=== ITEMS 2 + 4: state assembly ===")
    from fantasyai.aurora.state_builder import build_state
    s = build_state(Path(parent))
    sampling = s.get("sampling")
    print(f"  state.sampling present: {sampling is not None}")
    if sampling:
        print(f"  honest_caveat: {sampling.get('honest_caveat', '(none)')[:100]}")
        print(f"  shape_detected: {sampling.get('shape_detected')}")

    repl_summary = s.get("replication_summary")
    print(f"  replication_summary present: {repl_summary is not None}")
    if repl_summary:
        print(f"    {repl_summary.get('headline')}")

    findings = s.get("findings") or []
    n_with_repl = sum(1 for f in findings if f.get("replication_status"))
    print(f"  findings: {len(findings)} total, {n_with_repl} with replication tags")

    print("\n=== Verification done ===")


if __name__ == "__main__":
    main()

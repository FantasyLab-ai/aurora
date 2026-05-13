"""End-to-end smoke test on a real run dir."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fantasyai.aurora.explainers import (
    explain_anomaly, explain_regime, explain_causal, explain_physics,
)
from fantasyai.aurora.priors import match_physics_fit

run_dir = Path(r"C:/Users/bgrut/Desktop/Aurora_QIE/outputs/aurora_dataset_runs/20260317_154832__noaa_timeseries.csv")
deep = json.loads((run_dir / "deep_math.json").read_text(encoding="utf-8"))

print("=== anomaly explainer (top point) ===")
points = ((deep.get("extensions") or {}).get("anomaly_attribution") or {}).get("points") or []
if points:
    ex = explain_anomaly(points[0], all_points=points)
    print(f"  available: {ex['available']}")
    print(f"  headline:  {ex['headline']}")
    print(f"  drivers:   {len(ex['drivers'])} drivers, top contribution {ex['drivers'][0]['contribution_pct']:.1f}%")
    print(f"  alt views: {ex['alternative_views']}")

print("\n=== regime explainer (segment 1) ===")
regimes_block = deep.get("regimes") or {}
ex = explain_regime(regimes_block, segment_index=1)
if ex.get("available"):
    print(f"  headline: {ex['headline']}")
    print(f"  deltas:")
    for d in ex["deltas"]:
        print(f"    {d['variable']} ({d['stat']}): {d['before']:.3g} → {d['after']:.3g} (delta={d['delta']:+.3g})")
else:
    print(f"  unavailable: {ex.get('reason')}")

print("\n=== causal explainer ===")
causal_block = deep.get("causal_what_if") or {}
ex = explain_causal(causal_block)
if ex.get("available"):
    print(f"  headline: {ex['headline']}")
    print(f"  estimand: {ex['estimand']}")
    print(f"  approach: {ex['identifiability']['approach']}")
    print(f"  limitations: {len(ex['limitations'])}")
else:
    print(f"  unavailable: {ex.get('reason')}")

print("\n=== physics explainer ===")
pd_block = deep.get("physics_discovery") or {}
phys_diag = deep.get("physics") or {}
ex = explain_physics(pd_block, phys_diag)
if ex.get("available"):
    print(f"  headline: {ex['headline']}")
    print(f"  candidates: {[c['name'] for c in ex['candidates_tried']]}")
    print(f"  prior_matches: {len(ex['prior_matches'])} match(es)")
    for pm in ex["prior_matches"]:
        print(f"    - {pm['display_name']} ({pm['domain']}) — {pm['confidence']:.2f} confidence")
        if pm.get("why_not"):
            print(f"      why not 100%: {pm['why_not']}")
    print(f"  what_this_law_cannot_explain: {ex['what_this_law_cannot_explain']}")
else:
    print(f"  unavailable: {ex.get('reason')}")

print("\n=== prior matcher direct (fitted best) ===")
best = pd_block.get("best") or {}
print(f"  fitted: name={best.get('name')}, params={best.get('params')}")
matches = match_physics_fit(best)
if matches:
    for m in matches:
        print(f"  - {m.display_name}: confidence={m.confidence:.3f}")
        for pname, info in m.matched_params.items():
            print(f"      {pname}: fitted={info['fitted']}  range={info['expected_range']}  in_range={info['in_range']}")
        if m.why_not:
            print(f"      why_not: {m.why_not}")
else:
    print("  (no priors matched within tolerance — fit is unphysical)")

# Synthetic clean cooling data — should match Newton cooling at high confidence
print("\n=== synthetic clean cooling fit (sanity check) ===")
clean_fit = {
    "name": "linear_ode", "model": "dy/dt = a*y + b",
    "params": {"a": -0.05, "b": 1.0, "dt": 1.0},
    "rmse_test": 0.5,
}
matches = match_physics_fit(clean_fit)
for m in matches:
    print(f"  - {m.display_name}: confidence={m.confidence:.3f}")

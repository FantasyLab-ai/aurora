#!/usr/bin/env bash
# Reproduce the AFTER capture deterministically.
#
# Same input every time — RandomState seed 20260505 inside the script.
# Use this to regenerate the bundle when atoms change.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"

PY="${PYTHON:-python}"
echo "[reproduce] Aurora pre-launch capture — 2026-05-05"
echo "[reproduce] root: $ROOT"
echo "[reproduce] python: $PY"

cd "$ROOT"
"$PY" bin/capture_demo_2026_05_05.py --out "$HERE/after"

echo
echo "[reproduce] verifying capture invariants…"
"$PY" - <<'PY'
import json, pathlib
here = pathlib.Path(__file__).resolve().parent
state_path = pathlib.Path("docs/captures/2026-05-05/after/api_state.json")
d = json.loads(state_path.read_text())
s = d["state"]

# Acceptance signals — every one must hold.
assert len(s["anomalies"]) >= 40, f"expected >=40 anomalies, got {len(s['anomalies'])}"
assert "run_meta" in s, "run_meta missing"
assert s["run_meta"].get("run_id"), "run_meta.run_id empty"

ref = (s["anomalies"][0].get("referent") or {})
assert ref.get("level_used") in ("timestamp", "named_event", "position", "entity"), \
    f"first anomaly fell to L6 row_index — expected ≥ L5 (got {ref.get('level_used')})"

n_with_count = sum(1 for f in s["findings"] if f.get("n_total_at_this_severity"))
assert n_with_count >= 1, "no finding carried n_total_at_this_severity"

# JSON-clean: re-serialise. Must not raise.
json.dumps(s)

print("[reproduce] all invariants pass.")
PY

echo "[reproduce] done — $HERE/after/"

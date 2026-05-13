"""
Capture pre-v1.2 baseline outputs of ``compute_deep_math_v3`` on the
three byte-identical-regression fixtures (factory_bearing, NOAA buoy
2024, climate_buoy_demo).

The captured JSON is committed to ``tests/fixtures/baseline_outputs/``
and used by ``tests/test_deep_math_perf.py`` to prove the v1.2 timeout
wrapper is a true no-op on these datasets (timeout never fires).

Usage::

    python scripts/capture_deep_math_baselines.py

Run ONCE before any v1.2 code changes. Re-running AFTER the fix MUST
produce byte-identical files — that's the regression test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = {
    "factory_bearing": ROOT / "data" / "fixtures" / "factory_bearing_demo.csv",
    "climate_buoy":    ROOT / "data" / "fixtures" / "climate_buoy_demo.csv",
    "noaa_buoy":       ROOT / "docs" / "captures" / "2026-05-05" / "after" / "data" / "noaa_buoy_2024.csv",
}

OUT_DIR = ROOT / "tests" / "fixtures" / "baseline_outputs"


def _scrub_for_byte_identical(obj: Any) -> Any:
    """Strip fields that contain volatile line numbers / file paths so
    the byte-identical comparison is stable across the v1.2 edits.

    The two volatile fields are:

    * ``trace`` — Python tracebacks contain absolute file paths and
      line numbers. Adding the timeout helper near the top of
      ``deep_math.py`` shifts every subsequent line number, which
      would defeat the byte-identical test without telling us
      anything about real behavior change. Replaced with a stable
      marker.
    * Floats are left intact — same numpy + same seed + same data
      MUST produce identical bytes; if they don't, that's a real
      semantic change worth catching.
    """
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if k == "trace" and isinstance(v, str):
                out[k] = "<traceback-scrubbed-for-byte-identical>"
            else:
                out[k] = _scrub_for_byte_identical(v)
        return out
    if isinstance(obj, list):
        return [_scrub_for_byte_identical(v) for v in obj]
    return obj


def _canonical_json(obj: Any) -> str:
    """Stable JSON serialization for byte-identical comparison.

    ``sort_keys=True`` removes dict-iteration nondeterminism. The
    sanitizer from the production runner is applied so the captured
    file uses the same serialization path the user actually sees.
    ``_scrub_for_byte_identical`` strips line-number-dependent fields.
    """
    from scripts.run_aurora_dataset_runner import _sanitize_for_json
    safe = _sanitize_for_json(obj)
    scrubbed = _scrub_for_byte_identical(safe)
    return json.dumps(scrubbed, sort_keys=True, indent=2, ensure_ascii=False)


def capture_one(name: str, csv_path: Path) -> Dict[str, Any]:
    import pandas as pd
    import traceback as _tb
    from scripts.run_aurora_dataset_runner import _prepare_deep_math_df
    from fantasyai.aurora.math.deep_math import compute_deep_math_v3

    print(f"[{name}] loading {csv_path.name}")
    df_full = pd.read_csv(csv_path, low_memory=False)
    print(f"  full shape: {df_full.shape}")

    # Mirror the production pre-cap so the captured output is exactly
    # what compute_deep_math_v3 produces on the input the runner gives it.
    df_deep = _prepare_deep_math_df(df_full, seed=42, max_rows=5000)
    print(f"  prepared shape: {df_deep.shape}")

    # Mirror the production runner's outer try/except (see
    # scripts/run_aurora_dataset_runner.py:902-906). Some fixtures hit
    # latent runner bugs (e.g. NOAA's high-cardinality numerics get
    # flagged as id-like, _prepare_deep_math_df drops them all, and
    # compute_deep_math_v3 then crashes on ``numeric_cols[0]``). The
    # baseline captures the error-dict shape the production runner
    # records on disk in that case.
    try:
        res = compute_deep_math_v3(df_deep, seed=42)
    except Exception as e:
        res = {
            "error": f"deep_math_failed:{type(e).__name__}:{e}",
            "trace": _tb.format_exc()[:8000],
        }
    if isinstance(res, dict):
        print(f"  produced top-level keys: {sorted(res.keys())}")
    return res


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, path in FIXTURES.items():
        if not path.exists():
            print(f"[{name}] FIXTURE MISSING at {path}")
            return 1
        res = capture_one(name, path)
        out_path = OUT_DIR / f"{name}__deep_math.json"
        out_path.write_text(_canonical_json(res), encoding="utf-8")
        print(f"  wrote {out_path.relative_to(ROOT)} "
              f"({out_path.stat().st_size:,} bytes)")
        print()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

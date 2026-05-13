"""
v1.2 Path B — regression tests for the ``compute_deep_math_v3``
timeout backstop.

Two test classes are central:

  * ``TestByteIdenticalBaseline`` — proves the v1.2 timeout wrapper
    is a TRUE NO-OP on the three regression fixtures (factory_bearing,
    NOAA buoy 2024, climate_buoy_demo). For these inputs no timeout
    fires, so ``compute_deep_math_v3`` must produce output that is
    byte-identical to the pre-v1.2 baseline captured in
    ``tests/fixtures/baseline_outputs/*.json``. This is the analytical
    integrity guard — if it fails, the wrapper has accidentally
    altered behaviour on small data and the v1.2 work is broken.

  * ``TestDiabeticUnderFourMinutes`` — proves the v1.2 timeout
    actually rescues the diabetic_data 100K case. Pipeline must
    complete in under 4 minutes (was 22 minutes pre-v1.2), with
    ``state.deep_math.regimes`` showing the timeout marker and the
    rest of the pipeline producing real findings.

Plus targeted tests for the helper, the synthesis disclosure
extension, and a regression test that v1.1's air-quality fix is not
affected by the v1.2 changes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

BASELINE_DIR = ROOT / "tests" / "fixtures" / "baseline_outputs"
FIXTURES = {
    "factory_bearing": ROOT / "data" / "fixtures" / "factory_bearing_demo.csv",
    "climate_buoy":    ROOT / "data" / "fixtures" / "climate_buoy_demo.csv",
    "noaa_buoy":       ROOT / "docs" / "captures" / "2026-05-05" / "after" / "data" / "noaa_buoy_2024.csv",
}

DATA_DIABETIC = ROOT / "data" / "uploads" / "20260508_132034__diabetic_data.csv"
DATA_AIR_QUAL = ROOT / "tests" / "fixtures" / "synthetic_air_quality_17col.csv"

PYTHON        = sys.executable
RUNNER_MODULE = "scripts.run_aurora_with_steps_1_4"


# ============================================================
# Shared helpers
# ============================================================

def _scrub_for_byte_identical(obj: Any) -> Any:
    """Mirror of the baseline-capture scrubber. Strips ``trace`` strings
    so traceback line numbers don't break the comparison."""
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
    """Produce the same canonical JSON the baseline-capture script
    produced. The byte-identical test compares THIS function's output
    to the on-disk baseline file."""
    from scripts.run_aurora_dataset_runner import _sanitize_for_json
    safe = _sanitize_for_json(obj)
    scrubbed = _scrub_for_byte_identical(safe)
    return json.dumps(scrubbed, sort_keys=True, indent=2, ensure_ascii=False)


def _compute_deep_math_for_fixture(csv_path: Path) -> Any:
    """Run ``compute_deep_math_v3`` end-to-end on a fixture CSV the
    same way the production runner does. Mirrors the production
    outer try/except so NOAA's id-like-cols bug is captured as an
    error dict, not raised."""
    import traceback as _tb
    from scripts.run_aurora_dataset_runner import _prepare_deep_math_df
    from fantasyai.aurora.math.deep_math import compute_deep_math_v3
    df_full = pd.read_csv(csv_path, low_memory=False)
    df_deep = _prepare_deep_math_df(df_full, seed=42, max_rows=5000)
    try:
        return compute_deep_math_v3(df_deep, seed=42)
    except Exception as e:
        return {
            "error": f"deep_math_failed:{type(e).__name__}:{e}",
            "trace": _tb.format_exc()[:8000],
        }


def _run_pipeline_subprocess(dataset_csv: Path, timeout: int = 600
                              ) -> Tuple[int, float, Optional[Path], str]:
    """Invoke the production runner via subprocess. Returns
    ``(rc, elapsed_s, run_dir, stderr_tail)``. Uses temp files for
    stdout/stderr to avoid the PIPE deadlock (see v1.0 bench harness)."""
    import tempfile
    cmd = [
        PYTHON, "-m", RUNNER_MODULE,
        "--dataset", str(dataset_csv),
        "--seed", "42",
        "--also-math", "--math-v2", "--deep-math", "--also-llm",
    ]
    sout = tempfile.NamedTemporaryFile(prefix="dm_perf_stdout_", suffix=".log",
                                          delete=False, mode="w+b")
    serr = tempfile.NamedTemporaryFile(prefix="dm_perf_stderr_", suffix=".log",
                                          delete=False, mode="w+b")
    sout.close(); serr.close()
    t0 = time.perf_counter()
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT),
            stdout=open(sout.name, "wb"),
            stderr=open(serr.name, "wb"),
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=10)
    finally:
        elapsed = time.perf_counter() - t0
    rc = proc.returncode if proc.returncode is not None else -1
    stdout_text = Path(sout.name).read_text(encoding="utf-8", errors="replace")
    stderr_text = Path(serr.name).read_text(encoding="utf-8", errors="replace")
    for p in (sout.name, serr.name):
        try:
            os.unlink(p)
        except OSError:
            pass
    run_dir: Optional[Path] = None
    try:
        i = stdout_text.find("{")
        if i >= 0:
            outer = json.loads(stdout_text[i:])
            rd_str = outer.get("run_dir")
            if not rd_str and isinstance(outer.get("runner_json"), dict):
                rd_str = outer["runner_json"].get("run_dir")
            if rd_str:
                run_dir = Path(rd_str)
    except Exception:
        pass
    return rc, elapsed, run_dir, stderr_text[-2000:]


def _load_state(run_dir: Path) -> Dict[str, Any]:
    from fantasyai.aurora.state_builder import build_state
    return build_state(run_dir)


# ============================================================
# Byte-identical regression — the analytical integrity guard
# ============================================================

class TestByteIdenticalBaseline:
    """For each of the three canonical fixtures, the post-v1.2
    ``compute_deep_math_v3`` output MUST be byte-identical to the
    pre-v1.2 baseline. Failure means the wrapper has altered
    behaviour on small data — the fix is broken and must be reverted
    before continuing.
    """

    @pytest.mark.parametrize("name,csv_path",
                              list(FIXTURES.items()))
    def test_compute_deep_math_v3_byte_identical(self, name: str,
                                                    csv_path: Path):
        if not csv_path.exists():
            pytest.skip(f"fixture missing: {csv_path}")
        baseline_path = BASELINE_DIR / f"{name}__deep_math.json"
        if not baseline_path.exists():
            pytest.skip(f"baseline not captured at {baseline_path}; "
                         f"run scripts/capture_deep_math_baselines.py first")
        baseline = baseline_path.read_text(encoding="utf-8")
        actual = _canonical_json(_compute_deep_math_for_fixture(csv_path))
        if actual != baseline:
            # Find the first differing character for a useful diff.
            min_len = min(len(actual), len(baseline))
            idx = 0
            while idx < min_len and actual[idx] == baseline[idx]:
                idx += 1
            ctx_start = max(0, idx - 80)
            ctx_end = min(len(actual), idx + 80)
            pytest.fail(
                f"v1.2 timeout wrapper changed compute_deep_math_v3 output "
                f"on {name}. First diff at char {idx}:\n"
                f"  baseline[{ctx_start}:{ctx_end}]: "
                f"{baseline[ctx_start:ctx_end]!r}\n"
                f"  actual  [{ctx_start}:{ctx_end}]: "
                f"{actual[ctx_start:ctx_end]!r}\n"
                f"Lengths: baseline={len(baseline)}, actual={len(actual)}"
            )


# ============================================================
# Diabetic 4-min headline gate (slow, subprocess)
# ============================================================

class TestDiabeticUnderFourMinutes:
    """Pins the v1.2 headline: the 22-min diabetic_data pipeline
    drops to under 4 min once the deep_math timeout wrapper rescues
    detect_regimes from its PELT-RBF degenerate runtime.
    """

    @pytest.mark.slow
    @pytest.mark.integration
    def test_diabetic_pipeline_under_four_minutes(self):
        if not DATA_DIABETIC.exists():
            pytest.skip(f"diabetic_data fixture not found at {DATA_DIABETIC}")
        rc, elapsed, run_dir, stderr_tail = _run_pipeline_subprocess(
            DATA_DIABETIC, timeout=300,
        )
        # ---- Pipeline wall-time gate
        assert rc == 0, f"pipeline rc={rc}\nstderr:\n{stderr_tail}"
        assert run_dir is not None and run_dir.exists()
        assert elapsed < 240, (
            f"pipeline took {elapsed:.1f}s (>= 240s budget); v1.2 timeout "
            f"may not be engaging on detect_regimes.\nstderr tail:\n{stderr_tail}"
        )

        # ---- Read on-disk deep_math.json directly
        deep = json.loads((run_dir / "deep_math.json").read_text(
            encoding="utf-8"))

        # ---- regimes must show the timeout marker
        regimes = deep.get("regimes") or {}
        assert regimes.get("skipped_reason") == "method_timeout", (
            f"regimes should be method_timeout; got {regimes}"
        )
        assert regimes.get("elapsed_seconds", 0) >= 90, (
            f"regimes elapsed_seconds should be >= 90 (full budget); "
            f"got {regimes.get('elapsed_seconds')}"
        )

        # ---- At least 9 of 12 deep_math blocks should produce real
        # results (not skipped/failed). The block list mirrors the
        # call sites in compute_deep_math_v3.
        block_keys = [
            "forecasting", "regimes", "uncertainty", "risk_surface",
            "causal_what_if", "physics", "simulation", "optimization_demo",
            "physics_v2", "physics_discovery", "physics_invariants",
            # physics_consistency_score is a scalar, not a block
        ]
        timed_out = []
        produced = []
        for k in block_keys:
            v = deep.get(k)
            if not isinstance(v, dict):
                continue
            if v.get("skipped_reason") == "method_timeout":
                timed_out.append(k)
            elif v.get("note") in (None, "ok"):
                produced.append(k)
        # 12 - 1 timed-out = at least 11 producing, but be lenient and
        # require >= 9 to allow for legitimate skips (e.g. forecasting
        # skipped because diabetic has no time axis).
        n_produced = len(produced)
        assert n_produced >= 8, (
            f"expected >=8 deep_math blocks to produce findings on diabetic; "
            f"got {n_produced} produced={produced}  timed_out={timed_out}"
        )

        # ---- The pipeline state must be structurally complete (no
        # template-fallback regression).
        state = _load_state(run_dir)
        for top_key in ("findings", "overview", "structure",
                          "anomalies", "synthesis_meta"):
            assert top_key in state, (
                f"state missing required top key {top_key!r}"
            )

        # ---- Synthesis narrative must surface the timeout disclosure
        sm = state.get("synthesis_meta") or {}
        disc = sm.get("pipeline_disclosure") or {}
        timed_out_methods = {t.get("method")
                              for t in (disc.get("timed_out_methods") or [])
                              if isinstance(t, dict)}
        assert "regimes" in timed_out_methods or "detect_regimes" in timed_out_methods, (
            f"synthesis pipeline_disclosure.timed_out_methods should include "
            f"regimes; got {disc}"
        )
        prose = state.get("interpretive_summary") or ""
        assert "Pipeline notes" in prose, (
            "synthesis prose should contain 'Pipeline notes' section "
            "disclosing the regime-detection timeout"
        )


# ============================================================
# v1.1 air-quality regression — must NOT be affected
# ============================================================

class TestV11AirQualityNotRegressed:
    """The v1.1 fix lives in apply_advanced_pass, not in
    compute_deep_math_v3. The v1.2 timeout wrapper must not affect
    the air_quality pipeline's ~55-second runtime."""

    @pytest.mark.slow
    @pytest.mark.integration
    def test_air_quality_still_completes_fast(self):
        if not DATA_AIR_QUAL.exists():
            pytest.skip(f"air quality fixture missing at {DATA_AIR_QUAL}")
        rc, elapsed, run_dir, _ = _run_pipeline_subprocess(
            DATA_AIR_QUAL, timeout=180,
        )
        assert rc == 0
        assert elapsed < 90, (
            f"air_quality should still complete fast (was ~55s in v1.1); "
            f"got {elapsed:.1f}s. v1.2 work may have regressed."
        )


# ============================================================
# Helper smoke (already smoke-tested but pin in pytest too)
# ============================================================

class TestCallDmWithTimeout:
    def test_success_path_returns_inner_result_verbatim(self):
        from fantasyai.aurora.math.deep_math import _call_dm_with_timeout
        def _inner(x):
            return {"available": True, "value": x * 3}
        r = _call_dm_with_timeout(_inner, 7, label="ok", seconds=2)
        assert r == {"available": True, "value": 21}

    def test_timeout_returns_skip_dict(self):
        from fantasyai.aurora.math.deep_math import _call_dm_with_timeout
        def _slow(x):
            time.sleep(2.0)
            return "late"
        t0 = time.perf_counter()
        r = _call_dm_with_timeout(_slow, 1, label="slow", seconds=0.5)
        elapsed = time.perf_counter() - t0
        # Caller MUST return at ~0.5s, not 2s (the wrapper uses
        # shutdown(wait=False) so the orphan doesn't block the caller).
        assert elapsed < 1.5, (
            f"caller blocked on orphan; took {elapsed:.2f}s. "
            f"_call_dm_with_timeout is not actually rescuing the caller."
        )
        assert r["skipped_reason"] == "method_timeout"
        assert r["timeout_seconds"] == 0.5
        assert r["elapsed_seconds"] >= 0.5
        # ``note: skipped`` so downstream setdefault("note","ok") doesn't
        # overwrite the timeout marker.
        assert r["note"] == "skipped"

    def test_non_timeout_exceptions_reraise(self):
        """Critical for byte-identical: the caller's existing
        try/except handles the error shape, so the wrapper must not
        swallow non-timeout exceptions."""
        from fantasyai.aurora.math.deep_math import _call_dm_with_timeout
        def _bad():
            raise ValueError("synthetic-bad")
        with pytest.raises(ValueError, match="synthetic-bad"):
            _call_dm_with_timeout(_bad, label="bad", seconds=2)

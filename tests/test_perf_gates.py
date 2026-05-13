"""
v1.1 performance-fix regression tests.

These tests prove the three v1.1 gates actually fire in production:

  * Fix 1 — HMM input sampling at T > 2000 rows
  * Fix 2 — Wavelet input sampling at T > 3000 rows
  * Fix 3 — Global 90s per-method timeout backstop in ``apply_advanced_pass``
  * Fix 4 — Synthesis layer surfaces sampling / timeout disclosure
  * Fix 5 — Frontend rendering (verified via state-shape assertions; the
            actual DOM rendering is manual-browser-tested per the brief)

The brief is explicit that these must run the real pipeline, not mocks:
each gate has at least one end-to-end ``@pytest.mark.slow`` test that
subprocesses the runner against a real fixture dataset. Faster
``@pytest.mark.integration``-only tests probe the same logic at the
``apply_advanced_pass`` level (no subprocess) so the gate behaviour can
be verified in CI without paying the full multi-minute runner cost.

Brief: each test is the regression backstop against the gate becoming
dead code. If the sampling logic stops triggering, these tests fail.
If the timeout wrapper stops applying, Test 3 fails. If the disclosure
text stops appearing, Test 6 fails.
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

# Paths to real fixtures used by these tests.
DATA_DIABETIC = ROOT / "data" / "uploads" / "20260508_132034__diabetic_data.csv"
DATA_FACTORY  = ROOT / "data" / "fixtures" / "factory_bearing_demo.csv"
DATA_AIR_QUAL = ROOT / "tests" / "fixtures" / "synthetic_air_quality_17col.csv"
PYTHON        = sys.executable
RUNNER_MODULE = "scripts.run_aurora_with_steps_1_4"
OUTPUTS_DIR   = ROOT / "outputs" / "aurora_dataset_runs"


# ============================================================
# Helpers
# ============================================================

def _run_pipeline(dataset_csv: Path, timeout: int = 600) -> Tuple[int, float, Optional[Path], str]:
    """Invoke the production runner via subprocess; return ``(exit_code,
    elapsed_seconds, run_dir, stderr_tail)``.

    Uses temp files for stdout/stderr (not PIPE) — see ``scripts/bench_pipeline.py``
    for the rationale (PIPE deadlocks when the inner runner emits a
    wall of numpy warnings).
    """
    import tempfile

    cmd = [
        PYTHON, "-m", RUNNER_MODULE,
        "--dataset", str(dataset_csv),
        "--seed", "42",
        "--also-math", "--math-v2", "--deep-math", "--also-llm",
    ]
    sout = tempfile.NamedTemporaryFile(prefix="perf_stdout_", suffix=".log",
                                          delete=False, mode="w+b")
    serr = tempfile.NamedTemporaryFile(prefix="perf_stderr_", suffix=".log",
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
            proc.kill()
            proc.wait(timeout=10)
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

    # The runner emits a JSON summary on stdout that contains run_dir
    # at the top level. The output may contain nested JSON (the
    # runner_json block) too — parse from the FIRST '{' to capture
    # the outer object, not rfind which lands inside nested structures.
    run_dir: Optional[Path] = None
    try:
        i = stdout_text.find("{")
        if i >= 0:
            outer = json.loads(stdout_text[i:])
            rd_str = outer.get("run_dir")
            # Fall back to runner_json.run_dir if the outer object
            # somehow lacks it (older runner versions).
            if not rd_str and isinstance(outer.get("runner_json"), dict):
                rd_str = outer["runner_json"].get("run_dir")
            if rd_str:
                run_dir = Path(rd_str)
    except Exception:
        pass

    return rc, elapsed, run_dir, stderr_text[-2000:]


def _load_state_for_run(run_dir: Path) -> Dict[str, Any]:
    """Re-assemble the API state from a completed run dir, without
    going through Flask. Mirrors what /api/state returns."""
    from fantasyai.aurora.state_builder import build_state
    return build_state(run_dir)


# ============================================================
# Test 1 — HMM sampling actually engages on diabetic_data 100K
# ============================================================

class TestHmmSamplingEngagesOnLargeData:
    """Pins Fix 1: HMM input is downsampled to 2000 rows at T > 2000.

    The diabetic_data dataset (101,766 × 50) is the v1.0 bench's
    worst-case shape — HMM Baum-Welch alone took 7.6 min/run there.
    With Fix 1 sampling, the per-method cost drops to under ~5s and
    the headline pipeline runtime drops from 22 min to under 4 min.

    Marked ``slow`` + ``integration`` so CI can opt out via ``-m 'not slow'``.
    """

    @pytest.mark.slow
    @pytest.mark.integration
    def test_diabetic_data_pipeline_under_four_minutes(self, tmp_path):
        if not DATA_DIABETIC.exists():
            pytest.skip(f"diabetic_data fixture not found at {DATA_DIABETIC}")
        rc, elapsed, run_dir, stderr_tail = _run_pipeline(DATA_DIABETIC, timeout=600)
        assert rc == 0, f"pipeline failed rc={rc}\nstderr:\n{stderr_tail}"
        assert run_dir is not None and run_dir.exists(), "no run_dir produced"
        assert elapsed < 240, (
            f"pipeline took {elapsed:.1f}s (>= 240s budget). Fix 1 sampling "
            f"may not be engaging on diabetic_data.\nstderr:\n{stderr_tail}"
        )
        state = _load_state_for_run(run_dir)
        hmm = state.get("hmm") or {}
        assert hmm.get("available") is True, (
            f"HMM should still produce a valid fit on the sampled data; "
            f"got {hmm}"
        )
        assert hmm.get("downsampled") is True, (
            f"HMM should report downsampled=True on 100K input; got {hmm}"
        )
        # n_train_used is "approximately 2000" — exact equality not
        # required because the stratified sampler may dedupe a handful
        # of outlier indices that overlap the even-interval grid. The
        # metadata reports what was ACTUALLY used (honest); we just
        # need to confirm the cap was applied.
        n_used = hmm.get("n_train_used")
        assert isinstance(n_used, int) and 1800 <= n_used <= 2000, (
            f"HMM should report n_train_used near 2000 (target cap); "
            f"got {n_used}"
        )
        # NOTE on n_train_total: the long-standing state_builder pre-cap
        # at 5000 rows means HMM only ever sees 5000 rows max at the
        # advanced_pass boundary, regardless of the original CSV size.
        # That's pre-existing, out of scope for v1.1. The HONEST original
        # dataset row count is surfaced separately via
        # ``hmm.dataset_rows_full`` so the synthesis disclosure can
        # report the full cascade (5000-row window of 101,766-row CSV).
        n_total = hmm.get("n_train_total")
        assert isinstance(n_total, int) and n_total <= 5000, (
            f"HMM should see at most 5000 rows (state_builder cap); "
            f"got n_train_total={n_total}"
        )
        assert hmm.get("dataset_rows_full") == 101766, (
            f"dataset_rows_full should be the original CSV row count "
            f"(101,766); got {hmm.get('dataset_rows_full')}"
        )
        # HMM must still produce findings; the cap is for speed, not silencing.
        findings = state.get("findings") or []
        hmm_findings = [f for f in findings
                        if (f.get("method") or "").startswith("hmm")]
        assert len(hmm_findings) >= 1, (
            "HMM should emit at least one latent-regime finding after sampling"
        )


# ============================================================
# Test 5 — No regression on small data (factory_bearing 1K)
# ============================================================

class TestNoRegressionOnSmallData:
    """Pins: on a 1K-row input, NO sampling triggers and the canonical
    Top Anomalies (from yesterday's Workstream-1.1 anomalies fix) still
    surface. Catches any accidental "always-sample" regression.

    Marked ``slow`` because it still subprocesses the runner — but
    factory_bearing_demo is the fastest fixture (~4s end-to-end).
    """

    @pytest.mark.slow
    @pytest.mark.integration
    def test_factory_bearing_pipeline_no_sampling(self, tmp_path):
        if not DATA_FACTORY.exists():
            pytest.skip(f"factory_bearing fixture not found")
        rc, elapsed, run_dir, stderr_tail = _run_pipeline(DATA_FACTORY, timeout=120)
        assert rc == 0, f"pipeline failed rc={rc}\nstderr:\n{stderr_tail}"
        assert run_dir is not None and run_dir.exists()
        assert elapsed < 30, (
            f"factory_bearing should complete fast (<30s); got {elapsed:.1f}s"
        )
        state = _load_state_for_run(run_dir)
        hmm = state.get("hmm") or {}
        assert hmm.get("downsampled") is False, (
            f"HMM should NOT downsample at 1K rows; got downsampled={hmm.get('downsampled')}"
        )
        assert hmm.get("n_train_used") == 1000, (
            f"HMM should run on full 1000 rows; got n_train_used={hmm.get('n_train_used')}"
        )
        assert hmm.get("n_train_total") == 1000
        assert hmm.get("sample_strategy") == "full"

        # Yesterday's Top Anomalies fix: 8/8 canonical rows must still surface.
        canonical_rows = {220, 540, 880, 971, 993, 995, 998, 999}
        anomalies = state.get("anomalies") or []
        surfaced = {
            int(a["when"].split()[-1])
            for a in anomalies
            if isinstance(a, dict) and a.get("when", "").startswith("row ")
        }
        missing = canonical_rows - surfaced
        assert not missing, (
            f"Top Anomalies regression: canonical rows {missing} not surfaced; "
            f"got {sorted(surfaced)}"
        )


# ============================================================
# Direct (non-subprocess) tests — fast, no @slow marker
# These verify the sampling helper + _maybe_hmm in isolation so the
# logic is testable without paying a full pipeline run.
# ============================================================

class TestHmmSamplingDirect:
    """Direct tests against ``_maybe_hmm`` — no subprocess. These run
    in milliseconds and verify the sampling logic itself, separate
    from the end-to-end pipeline integration."""

    def _state_with_series(self, n: int, seed: int = 42) -> Dict[str, Any]:
        rng = np.random.default_rng(seed)
        # A simple two-regime series so HMM has structure to find.
        regime_break = n // 2
        s = np.concatenate([
            rng.normal(0.0, 1.0, regime_break),
            rng.normal(5.0, 1.0, n - regime_break),
        ])
        return {"primary_series": s.tolist()}

    def test_metadata_stamped_when_downsampled(self):
        from fantasyai.aurora.stats.advanced_pass import _maybe_hmm
        state = self._state_with_series(n=10_000)
        _maybe_hmm(state)
        hmm = state["hmm"]
        assert hmm["downsampled"] is True
        # ``n_train_used`` is approximately the cap — outlier indices
        # that overlap the even-interval grid are deduped, so the actual
        # count may be slightly below the target. Honest reporting.
        assert 1800 <= hmm["n_train_used"] <= 2000
        assert hmm["n_train_total"] == 10000
        assert hmm["sample_strategy"] == "stratified_time_preserving"

    def test_metadata_stamped_when_not_downsampled(self):
        from fantasyai.aurora.stats.advanced_pass import _maybe_hmm
        state = self._state_with_series(n=800)
        _maybe_hmm(state)
        hmm = state["hmm"]
        assert hmm["downsampled"] is False
        assert hmm["n_train_used"] == 800
        assert hmm["n_train_total"] == 800
        assert hmm["sample_strategy"] == "full"

    def test_metadata_stamped_at_exact_threshold(self):
        """Boundary: T == 2000 should NOT trigger sampling."""
        from fantasyai.aurora.stats.advanced_pass import _maybe_hmm
        state = self._state_with_series(n=2000)
        _maybe_hmm(state)
        hmm = state["hmm"]
        assert hmm["downsampled"] is False
        assert hmm["n_train_used"] == 2000
        assert hmm["n_train_total"] == 2000

    def test_hmm_still_finds_structure_on_sampled_input(self):
        """After sampling, HMM must still produce a valid fit and finding."""
        from fantasyai.aurora.stats.advanced_pass import _maybe_hmm
        state = self._state_with_series(n=50_000)
        res = _maybe_hmm(state)
        assert res["applied"] is True, "HMM must still apply on sampled input"
        assert state["hmm"]["available"] is True
        findings = state.get("findings") or []
        hmm_findings = [f for f in findings
                        if (f.get("method") or "").startswith("hmm")]
        assert len(hmm_findings) >= 1


class TestWaveletSamplingDirect:
    """Direct tests against ``_maybe_wavelet`` sampling.

    Same model as ``TestHmmSamplingDirect`` — verifies the sampling
    helper + metadata stamping at the function level, without the
    multi-minute subprocess cost. The end-to-end browser-test
    equivalent is Test 2 below (slow + integration).
    """

    def _osc_state(self, n: int, p1: int = 20, p2: int = 40,
                     seed: int = 42) -> Dict[str, Any]:
        """Generate an oscillating signal whose period changes at the
        midpoint — wavelet should report ``verdict in ('drifting',
        'switching')`` on this shape, so we get a real fit to verify."""
        rng = np.random.default_rng(seed)
        t = np.arange(n)
        half = n // 2
        s = np.sin(2 * np.pi * t / p1) + 0.1 * rng.standard_normal(n)
        s[half:] = (np.sin(2 * np.pi * t[half:] / p2)
                    + 0.1 * rng.standard_normal(n - half))
        return {"primary_series": s.tolist(), "dataset_dt": 1.0}

    def test_metadata_stamped_when_downsampled(self):
        from fantasyai.aurora.stats.advanced_pass import _maybe_wavelet
        state = self._osc_state(n=10_000)
        _maybe_wavelet(state)
        w = state["wavelet_period"]
        assert w["downsampled"] is True
        # See HMM test note — approximate cap, not strict equality.
        assert 2700 <= w["n_train_used"] <= 3000
        assert w["n_train_total"] == 10000
        assert w["sample_strategy"] == "stratified_time_preserving"
        assert w["available"] is True

    def test_metadata_stamped_when_not_downsampled(self):
        from fantasyai.aurora.stats.advanced_pass import _maybe_wavelet
        state = self._osc_state(n=2_500)
        _maybe_wavelet(state)
        w = state["wavelet_period"]
        assert w["downsampled"] is False
        assert w["n_train_used"] == 2500
        assert w["n_train_total"] == 2500
        assert w["sample_strategy"] == "full"

    def test_metadata_stamped_at_exact_threshold(self):
        """Boundary: T == 3000 should NOT trigger sampling."""
        from fantasyai.aurora.stats.advanced_pass import _maybe_wavelet
        state = self._osc_state(n=3000)
        _maybe_wavelet(state)
        w = state["wavelet_period"]
        assert w["downsampled"] is False
        assert w["n_train_used"] == 3000
        assert w["n_train_total"] == 3000

    def test_wavelet_still_detects_drift_on_sampled_input(self):
        """After sampling 100K -> 3K, wavelet must still detect the
        period change present in the source signal."""
        from fantasyai.aurora.stats.advanced_pass import _maybe_wavelet
        state = self._osc_state(n=100_000, p1=20, p2=80)
        _maybe_wavelet(state)
        w = state["wavelet_period"]
        assert w["available"] is True
        # Either 'drifting' or 'switching' is acceptable — both mean
        # the wavelet noticed the period change in the sampled signal.
        assert w.get("verdict") in ("drifting", "switching"), (
            f"sampled wavelet should still detect non-stationarity; "
            f"got verdict={w.get('verdict')}"
        )


# ============================================================
# Test 2 — Wavelet sampling engages on air-quality-shaped data
# ============================================================

class TestWaveletSamplingEngagesOnAirQuality:
    """Pins Fix 2: Wavelet input is downsampled to 3000 rows at T > 3000.

    Uses the committed ``tests/fixtures/synthetic_air_quality_17col.csv``
    (9,471 × 17, bootstrapped from noaa_buoy_2024 with seed 42).
    """

    @pytest.mark.slow
    @pytest.mark.integration
    def test_air_quality_wavelet_downsampled(self):
        if not DATA_AIR_QUAL.exists():
            pytest.skip(f"air quality fixture not found at {DATA_AIR_QUAL}")
        rc, elapsed, run_dir, stderr_tail = _run_pipeline(DATA_AIR_QUAL, timeout=300)
        assert rc == 0, f"pipeline failed rc={rc}\nstderr:\n{stderr_tail}"
        assert run_dir is not None and run_dir.exists()
        state = _load_state_for_run(run_dir)
        w = state.get("wavelet_period") or {}
        assert w.get("available") is True, (
            f"wavelet should still produce a valid fit on the 3K sample; "
            f"got {w}"
        )
        assert w.get("downsampled") is True, (
            f"wavelet should report downsampled=True on 9471-row input; "
            f"got {w}"
        )
        n_used = w.get("n_train_used")
        # ~3000 with small tolerance — outlier-vs-even-grid dedupe may
        # produce slightly fewer rows. Honest reporting beats hitting
        # an arbitrary round number.
        assert isinstance(n_used, int) and 2700 <= n_used <= 3000, (
            f"wavelet should report n_train_used near 3000 (target cap); "
            f"got {n_used}"
        )
        # 9471 > 5000 → state_builder pre-cap kicks in. Wavelet sees
        # only the post-cap window. dataset_rows_full carries the
        # original.
        n_total = w.get("n_train_total")
        assert isinstance(n_total, int) and n_total <= 5000, (
            f"wavelet n_train_total should be <=5000 (state_builder cap); "
            f"got {n_total}"
        )
        assert w.get("dataset_rows_full") == 9471, (
            f"dataset_rows_full should be the original CSV row count (9471); "
            f"got {w.get('dataset_rows_full')}"
        )
        assert w.get("sample_strategy") == "stratified_time_preserving"


class TestGlobalTimeoutBackstop:
    """Pins Fix 3: the 90s global per-method timeout actually fires.

    The brief is explicit: "inject a deliberately slow method into the
    advanced pass via the test harness (a ``time.sleep(120)`` mock
    method)". We use a much shorter mock timeout (1s) and override
    ``PER_METHOD_TIMEOUT_SECONDS`` to keep the test fast — the logic
    is identical regardless of the absolute cap value. The ``_STEPS``
    list is monkeypatched, so the real methods are untouched.
    """

    def test_timeout_fires_on_slow_method(self, monkeypatch):
        """A method that sleeps past the cap MUST return a
        ``method_timeout`` skip dict, NOT block the orchestrator."""
        import time as _time
        from fantasyai.aurora.stats import advanced_pass as _ap

        def _slow_method(state):
            _time.sleep(2.0)
            return {"applied": True, "result": "this should never be seen"}

        def _fast_method(state):
            return {"applied": True, "result": "fast_ok"}

        # Patch _STEPS with one fast and one slow method, and shrink
        # the timeout to 0.5s so the test runs in ~0.5s, not 90s.
        monkeypatch.setattr(_ap, "_STEPS", [
            ("fast_mock",  _fast_method),
            ("slow_mock",  _slow_method),
        ])
        monkeypatch.setattr(_ap, "PER_METHOD_TIMEOUT_SECONDS", 0.5)

        state: Dict[str, Any] = {}
        t0 = time.perf_counter()
        out = _ap.apply_advanced_pass(state)
        elapsed = time.perf_counter() - t0

        # Orchestrator must NOT block on the orphan — total elapsed
        # should be ~0.5s (one timeout), not 2.0s (the sleep duration).
        assert elapsed < 1.5, (
            f"orchestrator blocked on orphan thread; elapsed={elapsed:.2f}s "
            "(expected ~0.5s). The timeout wrapper is not actually rescuing."
        )

        steps = out["steps"]
        # Fast method runs to completion.
        assert steps["fast_mock"]["applied"] is True
        assert steps["fast_mock"]["result"] == "fast_ok"
        # Slow method MUST be marked as method_timeout.
        slow = steps["slow_mock"]
        assert slow.get("skipped_reason") == "method_timeout", (
            f"slow method should be flagged method_timeout; got {slow}"
        )
        assert slow.get("elapsed_seconds", 0) >= 0.5, (
            f"elapsed_seconds should be at least the timeout duration; "
            f"got {slow.get('elapsed_seconds')}"
        )
        assert slow.get("timeout_seconds") == 0.5

    def test_other_methods_unaffected_when_one_times_out(self, monkeypatch):
        """A timeout in method A must not affect methods B and C."""
        import time as _time
        from fantasyai.aurora.stats import advanced_pass as _ap

        def _slow(state):
            _time.sleep(2.0)
            return {"applied": True}

        def _ok_one(state):
            state["ok_one_marker"] = True
            return {"applied": True, "result": "one"}

        def _ok_two(state):
            state["ok_two_marker"] = True
            return {"applied": True, "result": "two"}

        monkeypatch.setattr(_ap, "_STEPS", [
            ("ok_one",  _ok_one),
            ("slow",    _slow),
            ("ok_two",  _ok_two),
        ])
        monkeypatch.setattr(_ap, "PER_METHOD_TIMEOUT_SECONDS", 0.5)

        state: Dict[str, Any] = {}
        out = _ap.apply_advanced_pass(state)
        assert state.get("ok_one_marker") is True
        assert state.get("ok_two_marker") is True
        assert out["steps"]["ok_one"]["applied"] is True
        assert out["steps"]["ok_two"]["applied"] is True
        assert out["steps"]["slow"]["skipped_reason"] == "method_timeout"

    def test_method_that_raises_is_captured(self, monkeypatch):
        """An exception in a method must return a structured skip dict
        with elapsed_seconds, not raise out of apply_advanced_pass."""
        from fantasyai.aurora.stats import advanced_pass as _ap

        def _bad(state):
            raise ValueError("synthetic-bad-method")

        monkeypatch.setattr(_ap, "_STEPS", [("bad", _bad)])
        monkeypatch.setattr(_ap, "PER_METHOD_TIMEOUT_SECONDS", 5)

        out = _ap.apply_advanced_pass({})
        bad = out["steps"]["bad"]
        assert bad["applied"] is False
        assert "synthetic-bad-method" in bad.get("reason", "")
        assert "elapsed_seconds" in bad   # added by _call_with_timeout

    def test_real_methods_still_work_under_wrapper(self):
        """End-to-end: with the real timeout wrapper applied (default
        90s), the existing fast methods still produce normal results
        on small inputs. Catches a wrapper regression that would break
        the happy path."""
        from fantasyai.aurora.stats.advanced_pass import apply_advanced_pass
        rng = np.random.default_rng(42)
        state: Dict[str, Any] = {
            "primary_series": rng.standard_normal(500).tolist(),
            "dataset_matrix": rng.standard_normal((500, 3)).tolist(),
            "dataset_matrix_columns": ["a", "b", "c"],
            "dataset_dt": 1.0,
            "structure": {"time_axis": True, "time_col": "t"},
        }
        out = apply_advanced_pass(state)
        # The 17 real method names all show up in out["steps"].
        assert len(out["steps"]) == 17, (
            f"expected 17 method results; got {len(out['steps'])}"
        )
        # At least HMM ran successfully on this synthetic data.
        assert state.get("hmm", {}).get("available") is True


class TestSynthesisDisclosure:
    """Pins Fix 4: synthesis layer surfaces sampling / timeout
    disclosure when (and only when) it should.

    Direct tests against the disclosure helpers — keeps the test fast
    (no LLM, no RAG, no subprocess). The end-to-end equivalent is
    Test 4 (slow + integration on the air quality fixture).
    """

    def test_disclosure_collects_sampled_hmm(self):
        from fantasyai.aurora.synthesis.engine import (
            _collect_pipeline_disclosure,
        )
        state = {
            "hmm": {"downsampled": True, "n_train_used": 2000,
                    "n_train_total": 101766,
                    "sample_strategy": "stratified_time_preserving"},
            "advanced_methods": {"steps": {}},
        }
        disc = _collect_pipeline_disclosure(state)
        assert len(disc["sampled_methods"]) == 1
        s = disc["sampled_methods"][0]
        assert s["method"] == "hmm"
        assert s["n_train_used"] == 2000
        assert s["n_train_total"] == 101766
        assert disc["timed_out_methods"] == []

    def test_disclosure_collects_timed_out_methods(self):
        from fantasyai.aurora.synthesis.engine import (
            _collect_pipeline_disclosure,
        )
        state = {
            "advanced_methods": {"steps": {
                "mutual_info": {"skipped_reason": "method_timeout",
                                "elapsed_seconds": 90.5,
                                "timeout_seconds": 90},
                "granger":     {"skipped_reason": "method_timeout",
                                "elapsed_seconds": 91.0,
                                "timeout_seconds": 90},
            }},
        }
        disc = _collect_pipeline_disclosure(state)
        assert len(disc["timed_out_methods"]) == 2
        methods = sorted(t["method"] for t in disc["timed_out_methods"])
        assert methods == ["granger", "mutual_info"]

    def test_disclosure_appears_in_prose_when_sampled(self):
        """Fix 6: presence-check on a real sample disclosure."""
        from fantasyai.aurora.synthesis.engine import (
            _append_pipeline_disclosure,
        )
        state = {
            "hmm": {"downsampled": True, "n_train_used": 2000,
                    "n_train_total": 50000,
                    "sample_strategy": "stratified_time_preserving"},
            "advanced_methods": {"steps": {}},
        }
        prose = _append_pipeline_disclosure("Analytical narrative.", state)
        # Must contain the disclosure header and the sampling sentence.
        assert "Pipeline notes" in prose
        assert "HMM" in prose
        assert "2,000-row" in prose
        assert "50,000-row" in prose
        assert "sample" in prose.lower()

    def test_disclosure_appears_in_prose_when_timed_out(self):
        from fantasyai.aurora.synthesis.engine import (
            _append_pipeline_disclosure,
        )
        state = {
            "advanced_methods": {"steps": {
                "mutual_info": {"skipped_reason": "method_timeout",
                                "elapsed_seconds": 91.0,
                                "timeout_seconds": 90},
            }},
        }
        prose = _append_pipeline_disclosure("Analytical narrative.", state)
        assert "Pipeline notes" in prose
        assert "Mutual information" in prose
        assert "90-second" in prose
        assert "deferred" in prose

    def test_disclosure_absent_when_nothing_to_disclose(self):
        """Fix 6 negative: clean runs MUST NOT show a disclosure paragraph.
        Prevents the disclosure from becoming pollution on healthy runs."""
        from fantasyai.aurora.synthesis.engine import (
            _append_pipeline_disclosure,
        )
        state = {
            "hmm": {"downsampled": False, "n_train_used": 800,
                    "n_train_total": 800},
            "wavelet_period": {"downsampled": False},
            "advanced_methods": {"steps": {
                "hmm": {"applied": True},
                "wavelet": {"applied": True},
            }},
        }
        original = "Analytical narrative without sampling or timeouts."
        prose = _append_pipeline_disclosure(original, state)
        assert prose == original, (
            "disclosure must NOT alter prose when no sampling / timeout "
            "occurred; got modified output"
        )
        assert "Pipeline notes" not in prose

    def test_disclosure_preserved_in_synthesis_meta(self):
        """Fix 4 contract: synthesis_meta.pipeline_disclosure carries the
        structured fields so the frontend doesn't have to parse prose."""
        from fantasyai.aurora.synthesis.engine import synthesize
        state = {
            "findings": [],
            "system_model": {"template_id": "general"},
            "hmm": {"downsampled": True, "n_train_used": 2000,
                    "n_train_total": 9471,
                    "sample_strategy": "stratified_time_preserving"},
            "advanced_methods": {"steps": {
                "granger": {"skipped_reason": "method_timeout",
                             "elapsed_seconds": 90.1,
                             "timeout_seconds": 90},
            }},
        }
        synthesize(state)
        sm = state.get("synthesis_meta") or {}
        disc = sm.get("pipeline_disclosure")
        assert isinstance(disc, dict)
        assert disc["sampled_methods"][0]["method"] == "hmm"
        assert disc["timed_out_methods"][0]["method"] == "granger"


class TestAirQualityCompletesCleanly:
    """Pins Test 4 from the brief: the 9,471 × 17 air quality dataset
    that previously stalled for 10 minutes must now complete in under
    3 minutes with honest reporting of any methods that hit the
    backstop.

    Brief revision: success criterion is "at least 6 of the ~17
    advanced methods produce real findings" (not 8) because mutual
    info and Granger may legitimately hit the backstop on a 17-col
    dataset — that's the honest answer to test against, not an
    aspirational one.
    """

    @pytest.mark.slow
    @pytest.mark.integration
    def test_air_quality_full_pipeline(self):
        if not DATA_AIR_QUAL.exists():
            pytest.skip(f"air quality fixture not found at {DATA_AIR_QUAL}")
        rc, elapsed, run_dir, stderr_tail = _run_pipeline(DATA_AIR_QUAL, timeout=300)
        assert rc == 0, f"pipeline failed rc={rc}\nstderr:\n{stderr_tail}"
        assert run_dir is not None and run_dir.exists()
        assert elapsed < 180, (
            f"air quality should complete under 3 min; got {elapsed:.1f}s. "
            f"v1.1 backstop may not be engaging on per-method runtime."
        )

        state = _load_state_for_run(run_dir)

        # State must be a real complete state, not the template fallback.
        # Synthesis must be RAG-grounded.
        sm = state.get("synthesis_meta") or {}
        assert sm.get("mode") == "rag", (
            f"synthesis must be RAG-grounded (mode='rag'), got "
            f"mode={sm.get('mode')!r} on the air quality dataset"
        )

        # Count methods that produced REAL findings (applied=True).
        am = state.get("advanced_methods") or {}
        steps = am.get("steps") or {}
        applied = [k for k, v in steps.items()
                   if isinstance(v, dict) and v.get("applied") is True]
        assert len(applied) >= 6, (
            f"expected >=6 of 17 advanced methods to produce real findings "
            f"on air quality; got {len(applied)} (applied={sorted(applied)})"
        )

        # Any method NOT applied must report a clean skip OR a timeout —
        # never a silent miss. Brief: "rest honestly reported as either
        # skipped (legitimate preconditions not met) or method_timeout".
        for k, v in steps.items():
            if not isinstance(v, dict) or v.get("applied") is True:
                continue
            reason = v.get("reason") or v.get("skipped_reason")
            assert reason, (
                f"method {k} did not apply but reported no reason; got {v}"
            )

        # Disclosure must surface in synthesis_meta whenever sampling
        # OR timeouts happened.
        disc = sm.get("pipeline_disclosure") or {}
        # state_builder caps to 5000 rows; HMM cap is 2000, wavelet
        # cap is 3000. After the state_builder cap the primary_series
        # has length 5000 (>2000, >3000) → both HMM and wavelet
        # downsample on top.
        sampled_methods = {s["method"] for s in disc.get("sampled_methods") or []}
        assert "hmm" in sampled_methods, (
            f"HMM should be in sampled_methods (post-cap 5000 > 2000); got {disc}"
        )
        assert "wavelet" in sampled_methods, (
            f"wavelet should be in sampled_methods (post-cap 5000 > 3000); "
            f"got {disc}"
        )

        # If anything timed out, the disclosure prose must mention it.
        timed_out = disc.get("timed_out_methods") or []
        prose = (state.get("interpretive_summary") or "")
        if timed_out:
            assert "Pipeline notes" in prose, (
                f"timed_out methods {[t['method'] for t in timed_out]} but no "
                f"'Pipeline notes' section in interpretive_summary"
            )

        # The sampling lines from Fix 1+2 should be visible in the prose.
        assert "Pipeline notes" in prose, (
            "sampling occurred but no 'Pipeline notes' section in prose"
        )
        assert "sample" in prose.lower(), (
            "'sample' word must appear in disclosure prose"
        )


class TestStratifiedSampler:
    """Direct tests against the sampling helper."""

    def test_returns_arange_when_below_target(self):
        from fantasyai.aurora.stats.advanced_pass import (
            _stratified_time_preserving_sample,
        )
        arr = np.arange(500, dtype=float)
        idx = _stratified_time_preserving_sample(arr, target_n=1000)
        assert idx.tolist() == list(range(500))

    def test_returns_target_n_when_above(self):
        from fantasyai.aurora.stats.advanced_pass import (
            _stratified_time_preserving_sample,
        )
        rng = np.random.default_rng(0)
        arr = rng.standard_normal(50_000)
        idx = _stratified_time_preserving_sample(arr, target_n=2000)
        # ~target_n; may be slightly fewer if outliers overlap even-pick grid.
        assert 1990 <= len(idx) <= 2000

    def test_indices_sorted_ascending_time_preserving(self):
        from fantasyai.aurora.stats.advanced_pass import (
            _stratified_time_preserving_sample,
        )
        rng = np.random.default_rng(0)
        arr = rng.standard_normal(20_000)
        idx = _stratified_time_preserving_sample(arr, target_n=1500)
        assert (np.diff(idx) >= 0).all(), "indices must be ascending (time-preserving)"

    def test_extreme_outliers_always_retained(self):
        from fantasyai.aurora.stats.advanced_pass import (
            _stratified_time_preserving_sample,
        )
        rng = np.random.default_rng(0)
        arr = rng.standard_normal(50_000)
        # Inject extreme outliers that would NOT land on an even-interval grid.
        outlier_positions = [137, 8431, 24999, 41117]
        for p in outlier_positions:
            arr[p] = 50.0   # |z| ≈ 50 — way above threshold of 4.
        idx = _stratified_time_preserving_sample(arr, target_n=2000)
        for p in outlier_positions:
            assert p in idx, f"outlier at position {p} should be retained"

    def test_deterministic(self):
        """Same input → same output. No random state."""
        from fantasyai.aurora.stats.advanced_pass import (
            _stratified_time_preserving_sample,
        )
        rng = np.random.default_rng(0)
        arr = rng.standard_normal(10_000)
        idx1 = _stratified_time_preserving_sample(arr, target_n=2000)
        idx2 = _stratified_time_preserving_sample(arr, target_n=2000)
        assert idx1.tolist() == idx2.tolist()

    def test_constant_series_returns_evenly(self):
        """MAD=0 edge case — no outliers possible; even-pick covers budget."""
        from fantasyai.aurora.stats.advanced_pass import (
            _stratified_time_preserving_sample,
        )
        arr = np.full(10_000, 7.0)
        idx = _stratified_time_preserving_sample(arr, target_n=2000)
        assert len(idx) == 2000
        assert (np.diff(idx) >= 0).all()

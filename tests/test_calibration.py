"""
Calibration engine tests — every honesty rule in the design is enforced
here, because a calibration layer that can silently lie is worse than no
calibration layer at all.

Covers:
  * generators: seeded, versioned, reproducible (including pinned values
    so a platform/numpy drift is caught, not just same-process identity)
  * fingerprint: bias-corrected phi, seasonal detection that red noise
    cannot fake, missingness, irregular sampling
  * harness: calls PRODUCTION detector code paths (identity-checked)
  * store: integrity-hashed corpus, tamper refusal
  * lookup: all four statuses; no silent interpolation; no extrapolation
    beyond the envelope; distance always reported
  * attach: verdict downgrade with disclosed threshold; degrade-don't-drop
  * production wiring: run_extended_methods attaches calibration
  * shipped corpus: loads, verifies, covers the changepoint family
"""
from __future__ import annotations

import dataclasses
import json
import math

import numpy as np
import pandas as pd
import pytest

from fantasyai.aurora.calibration import (
    BASE_SEED,
    CHANGEPOINT_FAMILY,
    CalibrationConfig,
    CorpusIntegrityError,
    CorpusNotFoundError,
    GENERATORS,
    METHOD_VERSIONS,
    PRODUCTION_ENTRYPOINTS,
    annotate_findings,
    apply_calibration_to_finding,
    build_corpus,
    fingerprint_series,
    generate_cell_trial,
    load_corpus,
    lookup_calibration,
    save_corpus,
    wilson_ci,
)


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

class TestGenerators:
    def test_same_seed_same_series_forever(self):
        a = generate_cell_trial("AR1", {"phi": 0.6}, 90, 3).values
        b = generate_cell_trial("AR1", {"phi": 0.6}, 90, 3).values
        assert np.array_equal(a, b)

    def test_different_trials_differ(self):
        a = generate_cell_trial("IIDNormal", {}, 90, 0).values
        b = generate_cell_trial("IIDNormal", {}, 90, 1).values
        assert not np.array_equal(a, b)

    def test_param_change_changes_stream(self):
        a = generate_cell_trial("AR1", {"phi": 0.4}, 90, 0).values
        b = generate_cell_trial("AR1", {"phi": 0.6}, 90, 0).values
        assert not np.array_equal(a, b)

    def test_pinned_values_catch_platform_drift(self):
        # These exact values are part of the corpus contract: if numpy's
        # PCG64 stream or our seeding scheme ever changes, this fails and
        # the corpus must be rebuilt under a new version.
        x = generate_cell_trial("IIDNormal", {}, 10, 0).values
        assert x[0] == pytest.approx(-0.915578, abs=1e-5)
        assert x[4] == pytest.approx(0.071444, abs=1e-5)

    def test_all_generators_versioned_and_null(self):
        assert set(GENERATORS) == {
            "IIDNormal", "AR1", "ARMA", "SeasonalStationary",
            "HeavyTailed", "IrregularSampled",
        }
        for gen in GENERATORS.values():
            assert gen.version == "1.0.0"
            assert gen.varies  # declares what it moves

    def test_base_seed_pinned(self):
        assert BASE_SEED == 20260815

    def test_irregular_supplies_timestamps(self):
        s = generate_cell_trial("IrregularSampled", {}, 50, 0)
        assert s.timestamps is not None and len(s.timestamps) == 50
        assert np.all(np.diff(s.timestamps) > 0)

    def test_ar1_rejects_nonstationary_phi(self):
        with pytest.raises(ValueError):
            generate_cell_trial("AR1", {"phi": 1.0}, 90, 0)

    def test_heavy_tail_requires_finite_variance(self):
        with pytest.raises(ValueError):
            generate_cell_trial("HeavyTailed", {"df": 2.0}, 90, 0)


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_phi_hat_bias_correction_beats_naive(self):
        # On n=90 AR(1) phi=0.6 the naive lag-1 estimate is biased toward
        # zero (E[r1] ~ phi - (1+3phi)/n ~ 0.569). The corrected estimator
        # must land closer to the truth on average.
        naive, corrected = [], []
        for t in range(200):
            x = generate_cell_trial("AR1", {"phi": 0.6}, 90, t).values
            xc = x - x.mean()
            r1 = float(np.dot(xc[:-1], xc[1:]) / np.dot(xc, xc))
            naive.append(r1)
            corrected.append(fingerprint_series(x).phi_hat)
        assert abs(np.mean(corrected) - 0.6) < abs(np.mean(naive) - 0.6)
        assert abs(np.mean(corrected) - 0.6) < 0.05

    def test_missingness_counted(self):
        x = np.ones(100) + np.random.default_rng(0).normal(0, 1, 100)
        x[10:20] = np.nan
        fp = fingerprint_series(x)
        assert fp.missingness == pytest.approx(0.10)
        assert fp.n == 90

    def test_heavy_tails_move_kurtosis(self):
        heavy = fingerprint_series(
            generate_cell_trial("HeavyTailed", {"df": 5.0}, 2000, 0).values
        )
        normal = fingerprint_series(
            generate_cell_trial("IIDNormal", {}, 2000, 0).values
        )
        assert heavy.excess_kurtosis > normal.excess_kurtosis + 0.5

    def test_seasonal_detected_and_red_noise_rejected(self):
        hits = sum(
            fingerprint_series(
                generate_cell_trial("SeasonalStationary", {"period": 7}, 90, t).values
            ).seasonal_period in (6, 7, 8)
            for t in range(30)
        )
        assert hits >= 24  # strong cycle: detected in the large majority
        false_hits = sum(
            fingerprint_series(
                generate_cell_trial("AR1", {"phi": 0.8}, 90, t).values
            ).seasonal_period is not None
            for t in range(30)
        )
        assert false_hits <= 3  # red noise must not fake seasonality

    def test_irregular_sampling_flagged(self):
        s = generate_cell_trial("IrregularSampled", {}, 90, 0)
        fp = fingerprint_series(s.values, s.timestamps)
        assert fp.sampling_regular is False
        fp2 = fingerprint_series(s.values, np.arange(90.0))
        assert fp2.sampling_regular is True


# ---------------------------------------------------------------------------
# Harness — production fidelity
# ---------------------------------------------------------------------------

class TestHarness:
    def test_harness_calls_production_code_paths(self):
        # Identity, not equivalence: the harness must run the very
        # functions production runs. A reimplementation would make the
        # whole corpus fiction.
        from fantasyai.aurora.math.methods.bocpd import fit_bocpd
        from fantasyai.aurora.math.regimes import detect_regimes_worldclass
        eps = PRODUCTION_ENTRYPOINTS()
        assert eps["bocpd"] is fit_bocpd
        assert eps["cusum"] is detect_regimes_worldclass
        assert eps["pelt"] is detect_regimes_worldclass

    def test_production_defaults_pinned_or_corpus_is_stale(self):
        # If any of these change, the shipped corpus no longer describes
        # production behaviour: bump METHOD_VERSIONS and rebuild.
        import inspect
        from fantasyai.aurora.math.methods.bocpd import fit_bocpd
        sig = inspect.signature(fit_bocpd)
        assert sig.parameters["hazard"].default == pytest.approx(1.0 / 250.0)
        assert sig.parameters["threshold"].default == 0.5

        import fantasyai.aurora.math.regimes as regimes
        src = inspect.getsource(regimes.detect_regimes_worldclass)
        assert "threshold=8.0" in src, (
            "CUSUM production threshold changed — rebuild the calibration corpus"
        )
        src_r = inspect.getsource(regimes._ruptures)
        assert "pen=3.0" in src_r and '"rbf"' in src_r.replace("'", '"'), (
            "PELT production settings changed — rebuild the calibration corpus"
        )
        assert METHOD_VERSIONS == {"bocpd": "1.0.0", "cusum": "1.0.0", "pelt": "1.0.0"}

    def test_gates_recorded_as_ineligible_not_silent(self):
        from fantasyai.aurora.calibration import run_changepoint_family
        # n=90 is below PELT's 120-observation production gate.
        out = run_changepoint_family(
            generate_cell_trial("IIDNormal", {}, 90, 0).values
        )
        assert out["pelt"] is None
        assert out["cusum"] in (True, False)
        assert out["bocpd"] in (True, False)

    def test_micro_corpus_shows_autocorrelation_inflation(self):
        grid = [("IIDNormal", {}, 90), ("AR1", {"phi": 0.8}, 90)]
        corpus = build_corpus(grid, trials=30, corpus_version="vtest")
        cusum = {e["generator"]: e for e in corpus["entries"]
                 if e["method"] == "cusum"}
        assert cusum["AR1"]["fdr"] > cusum["IIDNormal"]["fdr"] + 0.3

    def test_parallel_build_is_byte_identical(self):
        # Workers move the wall clock, never a number: same grid, same
        # trials, sequential vs 2 processes → identical entries.
        grid = [("IIDNormal", {}, 90), ("AR1", {"phi": 0.6}, 90)]
        seq = build_corpus(grid, trials=12, corpus_version="vp", workers=1)
        par = build_corpus(grid, trials=12, corpus_version="vp", workers=2)
        assert seq["entries"] == par["entries"]

    def test_wilson_ci(self):
        lo, hi = wilson_ci(0, 4000)
        assert lo == 0.0 and 0.0 < hi < 0.002
        lo, hi = wilson_ci(4000, 4000)
        assert hi == 1.0 and lo > 0.998
        lo, hi = wilson_ci(50, 100)
        assert lo < 0.5 < hi and (hi - lo) < 0.2


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _tiny_corpus() -> dict:
    return {
        "corpus_version": "vt",
        "format": "aurora.calibration.corpus",
        "base_seed": BASE_SEED,
        "trials_per_cell": 10,
        "methods": ["cusum"],
        "method_versions": {"cusum": "1.0.0"},
        "generator_versions": {},
        "method_defaults": {},
        "n_cells": 1,
        "n_entries": 1,
        "build_seconds": 0.0,
        "entries": [{
            "method": "cusum", "method_version": "1.0.0",
            "generator": "AR1", "generator_version": "1.0.0",
            "params": {"phi": 0.6}, "n": 90,
            "regime_measured": {
                "n": 90, "phi_hat": 0.58, "missingness": 0.0,
                "sampling_regular": True, "excess_kurtosis": 0.0,
                "skewness": 0.0, "seasonal_period": None,
            },
            "trials": 10, "eligible": 10, "fired": 4,
            "fdr": 0.4, "ci95": [0.17, 0.69], "nominal_alpha": None,
            "seed": {"base": BASE_SEED, "scheme": "sha256(cell)+trial"},
        }],
    }


class TestStore:
    def test_roundtrip_and_hash(self, tmp_path):
        p = save_corpus(_tiny_corpus(), directory=tmp_path)
        assert p.exists() and (tmp_path / "corpus_vt.sha256").exists()
        doc = load_corpus("vt", directory=tmp_path)
        assert doc["entries"][0]["fdr"] == 0.4
        assert len(doc["_sha256"]) == 64

    def test_tampered_corpus_refused(self, tmp_path):
        p = save_corpus(_tiny_corpus(), directory=tmp_path)
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["entries"][0]["fdr"] = 0.01  # the lie calibration exists to catch
        p.write_text(json.dumps(doc, sort_keys=True, indent=1), encoding="utf-8")
        with pytest.raises(CorpusIntegrityError):
            load_corpus("vt", directory=tmp_path)

    def test_missing_corpus_is_not_a_default(self, tmp_path):
        with pytest.raises(CorpusNotFoundError):
            load_corpus("nope", directory=tmp_path)


# ---------------------------------------------------------------------------
# Lookup — the honesty rules
# ---------------------------------------------------------------------------

def _fake_corpus() -> dict:
    """Hand-built corpus with known numbers, for exact lookup assertions."""
    def entry(gen, params, n, phi_hat, fdr, fired, eligible=1000, **regime):
        r = {"n": n, "phi_hat": phi_hat, "missingness": 0.0,
             "sampling_regular": True, "excess_kurtosis": 0.0,
             "skewness": 0.0, "seasonal_period": None}
        r.update(regime)
        return {
            "method": "cusum", "method_version": "1.0.0",
            "generator": gen, "generator_version": "1.0.0",
            "params": params, "n": n, "regime_measured": r,
            "trials": eligible, "eligible": eligible, "fired": fired,
            "fdr": fdr, "ci95": list(wilson_ci(fired, eligible)),
            "nominal_alpha": None,
        }
    return {
        "corpus_version": "vfake",
        "format": "aurora.calibration.corpus",
        "_sha256": "f" * 64,
        "entries": [
            entry("IIDNormal", {}, 90, 0.0, 0.0, 0),
            entry("AR1", {"phi": 0.4}, 90, 0.40, 0.10, 100),
            entry("AR1", {"phi": 0.6}, 90, 0.60, 0.40, 400),
            entry("AR1", {"phi": 0.8}, 90, 0.80, 0.90, 900),
        ],
    }


def _fp(**kw) -> "object":
    from fantasyai.aurora.calibration import RegimeFingerprint
    base = dict(n=90, phi_hat=0.6, missingness=0.0, sampling_regular=True,
                excess_kurtosis=0.0, skewness=0.0, seasonal_period=None)
    base.update(kw)
    return RegimeFingerprint(**base)


class TestLookup:
    def test_calibrated_snap_reports_distance(self):
        blk = lookup_calibration(_fp(phi_hat=0.58), "cusum", _fake_corpus())
        assert blk["status"] == "calibrated"
        assert blk["empirical_fdr"] == pytest.approx(0.40)
        assert blk["regime_distance"]["phi_hat"] == pytest.approx(0.02)
        assert blk["matched_regime"]["phi_hat"] == 0.60
        assert blk["observed_regime"]["phi_hat"] == 0.58
        assert blk["corpus_sha256"] == "f" * 64
        assert blk["fdr_ceiling"] == 0.25

    def test_between_grid_points_interpolates_and_says_so(self):
        blk = lookup_calibration(_fp(phi_hat=0.50), "cusum", _fake_corpus())
        assert blk["status"] == "interpolated"
        assert blk["empirical_fdr"] == pytest.approx(0.25, abs=0.01)
        interp = blk["interpolation"]
        assert interp["dimension"] == "phi_hat"
        assert interp["max_error"] == pytest.approx(0.15)
        assert len(interp["between"]) == 2

    def test_outside_envelope_is_refused_with_dimension_named(self):
        blk = lookup_calibration(_fp(phi_hat=0.95), "cusum", _fake_corpus())
        assert blk["status"] == "unavailable"
        assert blk["outside_envelope_dimension"] == "phi_hat"
        assert "empirical_fdr" not in blk  # no number, ever

        blk = lookup_calibration(_fp(n=5000), "cusum", _fake_corpus())
        assert blk["status"] == "unavailable"
        assert blk["outside_envelope_dimension"] == "n"

        blk = lookup_calibration(_fp(missingness=0.3), "cusum", _fake_corpus())
        assert blk["status"] == "unavailable"
        assert blk["outside_envelope_dimension"] == "missingness"

        blk = lookup_calibration(_fp(seasonal_period=7), "cusum", _fake_corpus())
        assert blk["status"] == "unavailable"
        assert blk["outside_envelope_dimension"] == "seasonal_period"

        blk = lookup_calibration(
            _fp(sampling_regular=False), "cusum", _fake_corpus()
        )
        assert blk["status"] == "unavailable"
        assert blk["outside_envelope_dimension"] == "sampling_regular"

    def test_uncalibrated_method_is_first_class(self):
        blk = lookup_calibration(_fp(), "welch_t", _fake_corpus())
        assert blk["status"] == "not_yet_calibrated"
        assert "empirical_fdr" not in blk

    def test_zero_baseline_never_becomes_a_ratio(self):
        blk = lookup_calibration(_fp(phi_hat=0.58), "cusum", _fake_corpus())
        # IID cell fired 0/1000 — a ratio would divide by an unmeasured 0.
        assert blk["fdr_vs_iid"] is None
        assert "0/1000" in blk["fdr_vs_iid_note"]
        assert blk["iid_baseline_fdr"] == 0.0

    def test_remediation_only_when_derivable(self):
        from fantasyai.aurora.calibration import derive_remediation
        corpus = _fake_corpus()
        blk = lookup_calibration(_fp(phi_hat=0.58), "cusum", corpus)
        rem = derive_remediation(blk, _fp(phi_hat=0.58), "cusum", corpus)
        actions = {r["action"] for r in rem}
        assert "require_consecutive_confirmations" in actions
        assert "pre_whiten" in actions
        # No larger-n cell with an acceptable rate exists in the fake
        # corpus, so extend_series must NOT be invented.
        assert "extend_series" not in actions
        for r in rem:  # every remediation names its numbers
            assert any(ch.isdigit() for ch in r["detail"])


# ---------------------------------------------------------------------------
# Attach — verdict interaction + degrade-don't-drop
# ---------------------------------------------------------------------------

def _fired_finding() -> dict:
    return {
        "severity": "warn", "confidence": 0.8,
        "title": "CUSUM: 1 change point in 'demand'",
        "description": "CUSUM detected a mean shift at row 47.",
        "method": "cusum", "claim_id": "cusum-0001", "fabricated": False,
        "evidence": {"status": "fit", "target_col": "demand",
                     "changepoints": [47]},
    }


class TestAttach:
    def test_high_fdr_downgrades_verdict_and_discloses_threshold(self):
        f = _fired_finding()
        values = generate_cell_trial("AR1", {"phi": 0.6}, 90, 12).values
        apply_calibration_to_finding(f, values, corpus=_fake_corpus())
        assert f["verdict"] == "not_identifiable"
        assert f["severity"] == "info"
        assert f["calibration"]["verdict_downgraded"] is True
        assert f["calibration"]["fdr_ceiling"] == 0.25
        # The raw statistic is contextualised, never hidden.
        assert f["evidence"]["changepoints"] == [47]
        assert "NOT IDENTIFIABLE" in f["description"]

    def test_under_ceiling_keeps_verdict(self):
        f = _fired_finding()
        values = generate_cell_trial("AR1", {"phi": 0.4}, 90, 12).values
        apply_calibration_to_finding(
            f, values, corpus=_fake_corpus(),
        )
        blk = f["calibration"]
        assert blk["status"] in ("calibrated", "interpolated")
        assert blk["empirical_fdr"] <= 0.25
        assert "verdict" not in f
        assert f["severity"] == "warn"

    def test_silent_finding_not_downgraded(self):
        f = _fired_finding()
        f["evidence"]["changepoints"] = []
        values = generate_cell_trial("AR1", {"phi": 0.6}, 90, 12).values
        apply_calibration_to_finding(f, values, corpus=_fake_corpus())
        assert "verdict" not in f  # nothing fired; nothing to downgrade

    def test_custom_ceiling_is_recorded(self):
        f = _fired_finding()
        values = generate_cell_trial("AR1", {"phi": 0.6}, 90, 12).values
        cfg = CalibrationConfig(fdr_ceiling=0.9)
        apply_calibration_to_finding(f, values, corpus=_fake_corpus(), config=cfg)
        assert f["calibration"]["fdr_ceiling"] == 0.9
        assert "verdict" not in f  # 0.40 < 0.9 — no downgrade

    def test_calibration_failure_never_blocks_the_finding(self, monkeypatch):
        import fantasyai.aurora.calibration.store as store
        monkeypatch.setattr(
            store, "get_corpus_cached",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk on fire")),
        )
        f = _fired_finding()
        values = generate_cell_trial("AR1", {"phi": 0.6}, 90, 12).values
        out = apply_calibration_to_finding(f, values)  # no corpus passed
        assert out is f
        assert f["calibration"]["status"] == "unavailable"
        assert f["evidence"]["changepoints"] == [47]  # finding intact


# ---------------------------------------------------------------------------
# Production wiring + shipped corpus
# ---------------------------------------------------------------------------

class TestProductionWiring:
    def test_run_extended_methods_attaches_calibration(self):
        from fantasyai.aurora.math.methods import run_extended_methods
        rng = np.random.default_rng(11)
        df = pd.DataFrame({
            "value": generate_cell_trial("AR1", {"phi": 0.6}, 120, 5).values,
            "value2": rng.normal(0, 1, 120),
        })
        out = run_extended_methods(df, target_col="value")
        by_method = {f.get("method"): f for f in out["findings"]}
        bocpd = by_method.get("bocpd")
        assert bocpd is not None
        cal = bocpd.get("calibration")
        assert isinstance(cal, dict)
        assert cal["status"] in ("calibrated", "interpolated")
        assert 0.0 <= cal["empirical_fdr"] <= 1.0
        assert cal["corpus_sha256"]
        # A non-changepoint method that fit carries the explicit marker.
        others = [f for m, f in by_method.items()
                  if m not in CHANGEPOINT_FAMILY
                  and (f.get("evidence") or {}).get("status") == "fit"]
        assert others, "expected at least one non-changepoint fit"
        assert all(
            f.get("calibration", {}).get("status") == "not_yet_calibrated"
            for f in others
        )

    def test_calibration_block_rides_into_the_bundle_hash(self):
        from aurora_sdk.bundle import build_bundle_from_state, verify_bundle
        f = _fired_finding()
        values = generate_cell_trial("AR1", {"phi": 0.6}, 90, 12).values
        apply_calibration_to_finding(f, values, corpus=_fake_corpus())
        state = {"run_meta": {"run_id": "cal-test"}, "dataset": {"name": "t"},
                 "findings": [f]}
        bundle = build_bundle_from_state(state)
        assert verify_bundle(bundle) is True
        stored = bundle["integrity"]["content_hash"]
        # Tampering with the calibration block must break the hash.
        bundle["findings"][0]["calibration"]["empirical_fdr"] = 0.01
        from aurora_sdk.bundle import _compute_content_hash
        assert _compute_content_hash(bundle) != stored


class TestShippedCorpus:
    def test_corpus_v1_ships_verified_and_covers_the_family(self):
        corpus = load_corpus("v1")
        assert corpus["base_seed"] == BASE_SEED
        assert corpus["trials_per_cell"] == 4000
        methods = {e["method"] for e in corpus["entries"]}
        assert set(CHANGEPOINT_FAMILY) <= methods
        for e in corpus["entries"]:
            assert e["eligible"] > 0
            assert 0.0 <= e["fdr"] <= 1.0
            assert e["ci95"][0] <= e["fdr"] <= e["ci95"][1]
            assert e["generator_version"] == GENERATORS[e["generator"]].version
            assert e["method_version"] == METHOD_VERSIONS[e["method"]]

    def test_the_phantom_signal_cell_exists(self):
        # The demo depends on this cell: CUSUM on AR(1) phi=0.6, n=90.
        corpus = load_corpus("v1")
        cell = [e for e in corpus["entries"]
                if e["method"] == "cusum" and e["generator"] == "AR1"
                and e["params"].get("phi") == 0.6 and e["n"] == 90]
        assert len(cell) == 1
        # The measured inflation the whole feature exists to expose.
        assert cell[0]["fdr"] > 0.25

    def test_manifest_ships(self):
        from fantasyai.aurora.calibration.store import CORPUS_DIR
        assert (CORPUS_DIR / "MANIFEST.md").exists()

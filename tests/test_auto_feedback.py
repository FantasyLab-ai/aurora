"""
Tests for ``fantasyai/aurora/learning/auto_feedback.py``.

Verifies the implicit-feedback logic emits ✓ / ✗ on the right shape of
state and stays silent when there isn't enough evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_LEARNING_STORE", str(tmp_path / "ls.jsonl"))
    yield


# ============================================================
# compute_signals — pure function, no I/O
# ============================================================

def _strong_state(prior_id="newton_cooling", prior_conf=0.92,
                  shape="exponential decay", winner="linear_ode",
                  fit_gap_pct=0.30):
    """Synthesize a state dict with a clean, internally-coherent fit."""
    runner_rmse = 1.0
    winner_rmse = runner_rmse * (1 - fit_gap_pct)
    return {
        "physics": {
            "discovered_law": {"name": winner, "rmse": winner_rmse, "aic": -100},
            "all_candidates": [
                {"name": winner, "rmse_test": winner_rmse, "ok": True},
                {"name": "logistic", "rmse_test": runner_rmse, "ok": True},
            ],
            "prior_matches": [{
                "prior_id": prior_id,
                "display_name": "Newton's law of cooling",
                "confidence": prior_conf,
                "domain": "thermal",
            }],
            "calculus": {
                "available": True, "likely_shape": shape,
                "confidence": 0.8, "monotonic": True,
                "dy_dt_mean": -0.5, "inflection_points": 0,
            },
            "dimensional_check": {
                "is_consistent": True, "confidence": 0.9, "note": "ok",
            },
        },
        "dataset": {"name": "synthetic_cooling.csv", "rows": 200, "cols": 2},
    }


class TestComputeSignals:

    def test_strong_state_high_composite(self):
        from fantasyai.aurora.learning import compute_signals
        sigs = compute_signals(_strong_state())
        assert sigs["composite"] is not None
        assert sigs["composite"] >= 0.7
        assert sigs["n_active"] >= 3
        # Coherence + prior strength + dimensional pass should all fire.
        assert sigs["prior_strength"] >= 0.9
        assert sigs["signature_coherence"] is not None
        assert sigs["dimensional_pass"] == 1.0

    def test_signal_inactive_when_no_prior_match(self):
        from fantasyai.aurora.learning import compute_signals
        s = _strong_state()
        s["physics"]["prior_matches"] = []
        sigs = compute_signals(s)
        assert sigs["prior_strength"] is None

    def test_signature_disagreement_drags_composite_down(self):
        """oscillatory shape but linear_ode winner = explicit disagreement."""
        from fantasyai.aurora.learning import compute_signals
        s = _strong_state(shape="oscillatory")
        sigs = compute_signals(s)
        # Coherence signal becomes 0.2 → composite ≤ 0.2.
        assert sigs["signature_coherence"] == 0.2
        assert sigs["composite"] <= 0.3

    def test_dimensional_failure_zeroes_signal(self):
        from fantasyai.aurora.learning import compute_signals
        s = _strong_state()
        s["physics"]["dimensional_check"]["is_consistent"] = False
        sigs = compute_signals(s)
        assert sigs["dimensional_pass"] == 0.0
        assert sigs["composite"] == 0.0  # weakest-link

    def test_low_fit_gap_lowers_quality(self):
        from fantasyai.aurora.learning import compute_signals
        # Winner only beats runner-up by 1% → fit_quality should be tiny.
        s = _strong_state(fit_gap_pct=0.01)
        sigs = compute_signals(s)
        assert sigs["fit_quality"] is not None
        assert sigs["fit_quality"] < 0.2

    def test_cross_run_consistency_signal(self):
        from fantasyai.aurora.learning import compute_signals
        past = [
            {"row_type": "run_learning_v1", "dataset_name": "synthetic_cooling.csv",
             "best_prior": {"id": "newton_cooling"}},
            {"row_type": "run_learning_v1", "dataset_name": "synthetic_cooling.csv",
             "best_prior": {"id": "newton_cooling"}},
        ]
        sigs = compute_signals(_strong_state(), past_rows=past)
        assert sigs["cross_run_consistency"] is not None
        # 2 agreeing, 0 disagreeing, prior 0.5, smoothing 0.5
        # → (2+0.5)/(2+1) = 0.833
        assert sigs["cross_run_consistency"] > 0.6


# ============================================================
# record_auto_feedback — persistence + thresholds
# ============================================================

class TestRecordAutoFeedback:

    def test_strong_state_records_auto_up(self):
        from fantasyai.aurora.learning import record_auto_feedback
        from fantasyai.aurora.learning.store import LearningStore
        row = record_auto_feedback("/fake/run", _strong_state())
        assert row is not None
        assert row["verdict"] == "auto_up"
        assert row["row_type"] == "auto_feedback_v1"
        assert row["best_prior_id"] == "newton_cooling"
        # Should appear in the store too.
        rows = LearningStore.default().read_all()
        assert any(r.get("row_type") == "auto_feedback_v1" for r in rows)

    def test_disagreement_records_auto_down(self):
        from fantasyai.aurora.learning import record_auto_feedback
        # oscillatory shape + linear_ode winner = mismatch → composite low.
        s = _strong_state(shape="oscillatory")
        row = record_auto_feedback("/fake/run", s)
        assert row is not None
        assert row["verdict"] == "auto_down"
        assert "disagrees" in row.get("rationale", "")

    def test_weak_state_records_nothing(self):
        from fantasyai.aurora.learning import record_auto_feedback
        # Only 1 active signal (prior_strength only — strip the rest) → record
        # nothing because we require ≥ 2 active signals to claim anything.
        s = {
            "physics": {
                "discovered_law": {"name": "linear_ode"},
                "prior_matches": [{"prior_id": "newton_cooling",
                                   "display_name": "Newton's", "confidence": 0.85}],
                # No all_candidates → no fit_quality
                # No calculus → no signature_coherence
                # No dimensional_check
            },
            "dataset": {"name": "x.csv"},
        }
        row = record_auto_feedback("/fake/run", s)
        assert row is None

    def test_auto_up_blocked_when_prior_confidence_low(self):
        """Even if composite ≥ 0.7, auto-up is gated on prior_conf ≥ 0.7."""
        from fantasyai.aurora.learning import record_auto_feedback
        s = _strong_state(prior_conf=0.55)
        # composite will likely be 0.55 (the prior_strength signal).
        # With composite = 0.55, neither auto_up (≥0.7) nor auto_down (≤0.3) fires.
        row = record_auto_feedback("/fake/run", s)
        # Either no row (composite in dead zone) or auto-up blocked by gate.
        if row is not None:
            assert row["verdict"] != "auto_up"


# ============================================================
# auto_feedback_summary — for the topbar badge
# ============================================================

class TestSummary:

    def test_summary_counts_correctly(self):
        from fantasyai.aurora.learning import (
            append_run, record_auto_feedback, record_feedback,
            auto_feedback_summary,
        )
        # Seed: 1 run + 1 auto_up + 1 human up + 1 human down.
        state = _strong_state()
        append_run(Path("/fake/r1"), state)
        record_auto_feedback("/fake/r1", state)
        record_feedback(run_dir="/fake/r1", finding_index=0,
                        finding_title="t", finding_method=None, verdict="up")
        record_feedback(run_dir="/fake/r1", finding_index=1,
                        finding_title="t", finding_method=None, verdict="down")
        s = auto_feedback_summary()
        assert s["n_runs"] == 1
        assert s["n_auto_up"] == 1
        assert s["n_human_up"] == 1
        assert s["n_human_down"] == 1
        assert s["n_signals_total"] == 3   # 1 auto-up + 1 human-up + 1 human-down

    def test_empty_summary_zero(self):
        from fantasyai.aurora.learning import auto_feedback_summary
        s = auto_feedback_summary()
        assert s["n_runs"] == 0
        assert s["n_signals_total"] == 0

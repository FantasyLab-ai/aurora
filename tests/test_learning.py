"""
Tests for ``fantasyai/aurora/learning``: store + meta_learner + feedback.

We use a tmp_path-scoped store path (via the ``AURORA_LEARNING_STORE`` env
var) so the user's real ~/.aurora/learning_store.jsonl is never touched.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Force the learning module to write into a tmp file, NOT ~/.aurora."""
    store_path = tmp_path / "learning_store.jsonl"
    monkeypatch.setenv("AURORA_LEARNING_STORE", str(store_path))
    yield store_path


# ============================================================
# store.py
# ============================================================

class TestStore:

    def test_default_store_path_uses_env_var(self, _isolated_store):
        from fantasyai.aurora.learning.store import default_store_path
        assert default_store_path() == _isolated_store

    def test_append_and_read(self):
        from fantasyai.aurora.learning import append_run, read_all
        from fantasyai.aurora.learning.store import LearningStore
        store = LearningStore.default()
        # Synthetic state — minimal but realistic shape.
        state = {
            "physics": {
                "discovered_law": {"name": "linear_ode", "rmse": 0.5, "aic": -100},
                "calculus": {"available": True, "likely_shape": "exponential decay",
                             "monotonic": True, "n_points": 200,
                             "dy_dt_mean": -0.5, "inflection_points": 0},
                "prior_matches": [{
                    "prior_id": "newton_cooling", "display_name": "Newton's law of cooling",
                    "domain": "thermal", "confidence": 0.92,
                }],
            },
            "dataset": {"name": "synthetic_cooling.csv", "rows": 200, "cols": 3},
            "structure": {"time_col": "t"},
        }
        row = append_run(Path("/fake/run/dir"), state)
        assert row is not None
        rows = read_all()
        assert len(rows) == 1
        assert rows[0]["row_type"] == "run_learning_v1"
        assert rows[0]["best_fit"]["name"] == "linear_ode"
        assert rows[0]["best_prior"]["id"] == "newton_cooling"
        assert rows[0]["calculus"]["likely_shape"] == "exponential decay"

    def test_append_swallows_nan_inf(self):
        """NaN/Inf must NOT corrupt the JSONL (json.dumps with default emits 'NaN' literal)."""
        import math, json
        from fantasyai.aurora.learning.store import LearningStore, _json_safe
        store = LearningStore.default()
        store.append({"row_type": "run_learning_v1", "calc": {"a": math.nan, "b": math.inf,
                                                              "c": 1.0, "d": "ok"}})
        # The file should round-trip through json.loads.
        line = store.path.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert parsed["calc"]["a"] is None
        assert parsed["calc"]["b"] is None
        assert parsed["calc"]["c"] == 1.0
        assert parsed["calc"]["d"] == "ok"

    def test_count_zero_when_empty(self):
        from fantasyai.aurora.learning.store import LearningStore
        assert LearningStore.default().count() == 0


# ============================================================
# meta_learner.py — score_similarity + suggest_priors_for_signature
# ============================================================

class TestSimilarity:

    def test_identical_signatures_score_max(self):
        from fantasyai.aurora.learning import score_similarity
        sig = {"likely_shape": "exponential decay", "monotonic": True,
               "dy_dt_mean": -0.5, "inflection_points": 0,
               "dominant_frequency": None}
        score = score_similarity(sig, sig, domain_a="thermal", domain_b="thermal")
        assert score >= 0.9, f"identical signatures should score near 1.0, got {score}"

    def test_completely_different_score_low(self):
        from fantasyai.aurora.learning import score_similarity
        a = {"likely_shape": "linear-monotonic", "monotonic": True,
             "dy_dt_mean": 1.0, "inflection_points": 0, "dominant_frequency": None}
        b = {"likely_shape": "oscillatory", "monotonic": False,
             "dy_dt_mean": 0.0, "inflection_points": 50, "dominant_frequency": 1.0}
        score = score_similarity(a, b, domain_a="thermal", domain_b="bio")
        # Different on every weighted feature; should be near 0.
        assert score < 0.2, f"expected low similarity, got {score}"

    def test_partial_shape_match(self):
        """'exponential decay' vs 'exponential-like growth' should partially match."""
        from fantasyai.aurora.learning import score_similarity
        a = {"likely_shape": "exponential decay", "monotonic": True,
             "dy_dt_mean": -0.5, "inflection_points": 0}
        b = {"likely_shape": "exponential-like growth", "monotonic": True,
             "dy_dt_mean": -0.5, "inflection_points": 0}
        score = score_similarity(a, b)
        # First word "exponential" matches, plus monotonic + deriv direction.
        assert score > 0.3
        assert score < 1.0


class TestSuggestPriors:

    def _seed(self, n_cooling=3, n_oscillator=2):
        """Append synthetic past runs to the store."""
        from fantasyai.aurora.learning import append_run
        # n_cooling Newton's-cooling-style runs.
        for i in range(n_cooling):
            append_run(Path(f"/fake/cool{i}"), {
                "physics": {
                    "discovered_law": {"name": "linear_ode", "rmse": 0.5},
                    "calculus": {"available": True, "likely_shape": "exponential decay",
                                 "monotonic": True, "dy_dt_mean": -0.5,
                                 "inflection_points": 0, "dominant_frequency": None},
                    "prior_matches": [{
                        "prior_id": "newton_cooling",
                        "display_name": "Newton's law of cooling",
                        "domain": "thermal", "confidence": 0.85,
                    }],
                },
                "dataset": {"name": f"cooling_{i}.csv", "rows": 200, "cols": 2},
            })
        # n_oscillator damped-oscillator runs.
        for i in range(n_oscillator):
            append_run(Path(f"/fake/osc{i}"), {
                "physics": {
                    "discovered_law": {"name": "damped_oscillator", "rmse": 1.2},
                    "calculus": {"available": True, "likely_shape": "oscillatory",
                                 "monotonic": False, "dy_dt_mean": 0.0,
                                 "inflection_points": 40, "dominant_frequency": 0.5},
                    "prior_matches": [{
                        "prior_id": "damped_oscillator",
                        "display_name": "Damped harmonic oscillator",
                        "domain": "industrial", "confidence": 0.78,
                    }],
                },
                "dataset": {"name": f"osc_{i}.csv", "rows": 500, "cols": 3},
            })

    def test_returns_empty_when_store_empty(self):
        from fantasyai.aurora.learning import suggest_priors_for_signature
        out = suggest_priors_for_signature({"likely_shape": "anything"})
        assert out == []

    def test_returns_empty_when_no_matching_signature(self):
        from fantasyai.aurora.learning import suggest_priors_for_signature
        self._seed(n_cooling=2, n_oscillator=2)
        # A complex signature that matches neither cooling nor oscillator.
        weird = {"likely_shape": "saturation / sigmoid-like", "monotonic": True,
                 "dy_dt_mean": 0.1, "inflection_points": 1, "dominant_frequency": None}
        out = suggest_priors_for_signature(weird, min_n=2, min_similarity=0.6)
        # Should not bubble up cooling or oscillator (different shape).
        ids = {s["prior_id"] for s in out}
        assert "newton_cooling" not in ids
        assert "damped_oscillator" not in ids

    def test_promotes_majority_winner(self):
        from fantasyai.aurora.learning import suggest_priors_for_signature
        self._seed(n_cooling=4, n_oscillator=1)
        cool_sig = {"likely_shape": "exponential decay", "monotonic": True,
                    "dy_dt_mean": -0.5, "inflection_points": 0,
                    "dominant_frequency": None}
        out = suggest_priors_for_signature(cool_sig, min_n=2)
        assert out, "expected at least one suggestion"
        top = out[0]
        assert top["prior_id"] == "newton_cooling"
        assert top["support_n"] == 4
        assert "Newton's law of cooling" in top["display_name"]
        assert top["mean_confidence"] > 0.7
        assert len(top["example_run_dirs"]) <= 3
        assert "Matched on 4" in top["rationale"]

    def test_min_n_filters_out_weak_evidence(self):
        from fantasyai.aurora.learning import suggest_priors_for_signature
        self._seed(n_cooling=1, n_oscillator=1)   # only 1 of each
        cool_sig = {"likely_shape": "exponential decay", "monotonic": True,
                    "dy_dt_mean": -0.5, "inflection_points": 0}
        out = suggest_priors_for_signature(cool_sig, min_n=3)
        assert out == [], "min_n=3 should filter out priors with only 1 supporter"


# ============================================================
# feedback.py
# ============================================================

class TestFeedback:

    def test_record_and_retrieve(self):
        from fantasyai.aurora.learning import record_feedback, recent_feedback
        row = record_feedback(
            run_dir="/fake/run", finding_index=0,
            finding_title="Anomaly in pressure", finding_method="iso-forest",
            verdict="up", note="confirmed by ops",
        )
        assert row["row_type"] == "feedback_v1"
        assert row["verdict"] == "up"
        rs = recent_feedback()
        assert len(rs) == 1
        assert rs[0]["finding_title"] == "Anomaly in pressure"

    def test_invalid_verdict_rejected(self):
        from fantasyai.aurora.learning import record_feedback
        with pytest.raises(ValueError):
            record_feedback(run_dir="/r", finding_index=0,
                            finding_title="t", finding_method=None,
                            verdict="banana")

    def test_recent_feedback_orders_newest_first(self):
        import time
        from fantasyai.aurora.learning import record_feedback, recent_feedback
        record_feedback(run_dir="/r", finding_index=0,
                        finding_title="first", finding_method=None, verdict="up")
        time.sleep(1.05)   # ensure ts string differs
        record_feedback(run_dir="/r", finding_index=1,
                        finding_title="second", finding_method=None, verdict="down")
        rs = recent_feedback()
        assert len(rs) == 2
        assert rs[0]["finding_title"] == "second"


# ============================================================
# Integration: state_builder appends + suggests
# ============================================================

class TestStateBuilderIntegration:

    def test_state_builder_appends_a_row(self, tmp_path, monkeypatch):
        """build_state on a real run dir should leave a learning row behind."""
        from fantasyai.aurora.learning import read_all
        from fantasyai.aurora.state_builder import build_state
        # Use the smallest already-existing run dir on this machine.
        # Skip the test if no runs exist (clean checkout).
        runs_root = ROOT.parents[2] / "outputs" / "aurora_dataset_runs"
        if not runs_root.exists():
            pytest.skip("no past runs to test against")
        candidates = sorted(
            (p for p in runs_root.iterdir() if p.is_dir() and (p / "deep_math.json").exists()),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if not candidates:
            pytest.skip("no past runs with deep_math.json")
        before = len(read_all())
        build_state(candidates[0])
        after = len(read_all())
        assert after >= before + 1, "build_state should have appended one learning row"

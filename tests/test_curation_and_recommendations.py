"""
Tests for ``priors/curation.py`` and ``recommendations/``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_PRIOR_CURATION", str(tmp_path / "curation.jsonl"))
    monkeypatch.setenv("AURORA_ACTION_LIBRARY", str(tmp_path / "actions.jsonl"))
    yield


# ============================================================
# Curation: duplicates, contradictions, deprecation, diff
# ============================================================

class TestCurationDuplicates:

    def test_finds_overlapping_runner_form(self):
        from fantasyai.aurora.priors.curation import find_duplicates
        # Many priors map to runner_form="linear_ode" — they should pair up.
        dups = find_duplicates(threshold=0.5)
        # We expect at least Newton's cooling and exponential to look similar
        # (same runner_form + same domain in many catalogues).
        assert isinstance(dups, list)
        # Should not crash; should find at least one same-runner-form pair.
        assert any(d.score >= 0.5 for d in dups)

    def test_threshold_filters_strict(self):
        from fantasyai.aurora.priors.curation import find_duplicates
        strict = find_duplicates(threshold=0.99)
        loose = find_duplicates(threshold=0.5)
        assert len(strict) <= len(loose)


class TestCurationDeprecation:

    def test_deprecate_unknown_prior_raises(self):
        from fantasyai.aurora.priors.curation import deprecate
        with pytest.raises(ValueError):
            deprecate("not_a_real_prior", reason="test")

    def test_deprecate_then_query_overrides(self):
        from fantasyai.aurora.priors.curation import (
            deprecate, deprecation_overrides,
        )
        deprecate("newton_cooling",
                  reason="superseded for ε<1 case",
                  superseded_by="exponential")
        overrides = deprecation_overrides()
        assert "newton_cooling" in overrides
        assert overrides["newton_cooling"]["reason"]
        assert overrides["newton_cooling"]["superseded_by"] == "exponential"

    def test_undeprecate_removes_override(self):
        from fantasyai.aurora.priors.curation import (
            deprecate, undeprecate, deprecation_overrides,
        )
        deprecate("newton_cooling", reason="test")
        undeprecate("newton_cooling", reason="reverted")
        overrides = deprecation_overrides()
        assert "newton_cooling" not in overrides


class TestCurationDiff:

    def test_snapshot_and_diff_added_removed_modified(self):
        from fantasyai.aurora.priors.curation import (
            snapshot_library, diff_libraries,
        )
        before = snapshot_library()
        # Synthesize an "after" snapshot: drop one, add one, modify one.
        after = [dict(p) for p in before]
        removed_id = after[0]["id"]
        after = after[1:]
        after.append({"id": "fake_new_prior", "display_name": "New",
                       "domain": "general", "matches_runner_form": "linear_ode",
                       "structural_form": "x = 0",
                       "citation": "test", "description": "",
                       "free_parameters": {}, "sign_constraints": {},
                       "identifiability_conditions": [],
                       "falsification_conditions": [],
                       "domain_of_validity": {}, "assumptions": [],
                       "status": "candidate", "version": "1.0.0",
                       "deprecation": None})
        # Modify the next one's display_name.
        after[0] = {**after[0], "display_name": "MODIFIED"}
        diff = diff_libraries(before, after)
        assert any(p["id"] == "fake_new_prior" for p in diff["added"])
        assert any(p["id"] == removed_id for p in diff["removed"])
        assert any(m["changes"].get("display_name") for m in diff["modified"])


# ============================================================
# Recommendations
# ============================================================

class TestRecommendations:

    def test_high_confidence_finding_returns_do_x(self):
        from fantasyai.aurora.recommendations import build_recommendation
        f = {"title": "Anomaly in pressure at row 12",
             "method": "iso-forest", "severity": "crit", "confidence": 0.85}
        rec = build_recommendation(f)
        assert rec.confidence_band == "high"
        assert rec.kind == "do_x"
        assert rec.headline.startswith("DO:")
        assert rec.action_id == "investigate_anomaly"
        assert rec.predicted_outcome["success_probability"] is not None

    def test_low_confidence_finding_returns_collect_more_data(self):
        from fantasyai.aurora.recommendations import build_recommendation
        f = {"title": "Possible regime shift", "method": "ruptures",
             "severity": "info", "confidence": 0.20}
        rec = build_recommendation(f)
        assert rec.confidence_band == "low"
        assert rec.kind == "collect_more_data"
        assert "GATHER MORE DATA" in rec.headline

    def test_medium_confidence_returns_do_x_if_y(self):
        from fantasyai.aurora.recommendations import build_recommendation
        f = {"title": "Forecast peak: 3.4m", "method": "AR(1)",
             "severity": "warn", "confidence": 0.55}
        rec = build_recommendation(f)
        assert rec.confidence_band == "medium"
        assert rec.kind == "do_x_if_y"
        assert rec.headline.startswith("CONSIDER:")

    def test_recommendation_carries_predicted_no_action(self):
        from fantasyai.aurora.recommendations import build_recommendation
        f = {"title": "Anomaly", "method": "iso-forest",
             "severity": "crit", "confidence": 0.85}
        rec = build_recommendation(f)
        assert rec.predicted_no_action.get("summary")
        assert "Critical" in rec.predicted_no_action["summary"]

    def test_record_outcome_calibrates_base_rate(self):
        from fantasyai.aurora.recommendations import (
            record_action_outcome,
        )
        from fantasyai.aurora.recommendations.recommender import calibrated_base_rate
        # Seed 4 successful outcomes for investigate_anomaly.
        for i in range(4):
            record_action_outcome(action_id="investigate_anomaly",
                                  run_dir=f"/r{i}", finding_index=0,
                                  outcome="successful")
        base = calibrated_base_rate("investigate_anomaly")
        assert base["n"] == 4
        assert base["source"] == "calibrated"
        # 4 successes + Beta prior(K=4 with base 0.4) → α=1.6+4=5.6, β=2.4
        # rate = 5.6/(5.6+2.4) = 0.70 — i.e. updated upward from the 0.40 prior.
        assert base["rate"] > 0.5

    def test_invalid_outcome_rejected(self):
        from fantasyai.aurora.recommendations import record_action_outcome
        with pytest.raises(ValueError):
            record_action_outcome(action_id="investigate_anomaly",
                                  run_dir="/r", finding_index=0,
                                  outcome="banana")

    def test_list_actions_filter_by_domain(self):
        from fantasyai.aurora.recommendations import list_actions
        all_actions = list_actions()
        assert len(all_actions) >= 3
        # Industrial filter should still include "general" actions.
        ind = list_actions(domain="industrial")
        ids = {a.id for a in ind}
        assert "lower_operational_threshold" in ids
        # "general" actions also surface under any domain query.
        assert "investigate_anomaly" in ids

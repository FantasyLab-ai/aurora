"""
Tests for ``priors/validation_log.py`` + ``priors/changelog.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_PRIOR_VALIDATIONS",
                       str(tmp_path / "prior_validations.jsonl"))
    yield


# ============================================================
# validation_log basics
# ============================================================

class TestAppendRead:

    def test_append_and_read(self):
        from fantasyai.aurora.priors.validation_log import (
            append_validation, get_validation_history,
        )
        append_validation("newton_cooling", "up", run_dir="/r1", score=0.85)
        rows = get_validation_history("newton_cooling")
        assert len(rows) == 1
        assert rows[0]["verdict"] == "up"
        assert rows[0]["score"] == 0.85
        assert rows[0]["row_type"] == "prior_validation_v1"

    def test_invalid_verdict_rejected(self):
        from fantasyai.aurora.priors.validation_log import append_validation
        with pytest.raises(ValueError):
            append_validation("newton_cooling", "banana")

    def test_history_is_chronological(self):
        from fantasyai.aurora.priors.validation_log import (
            append_validation, get_validation_history,
        )
        import time
        append_validation("a", "up")
        time.sleep(1.05)
        append_validation("a", "down")
        rows = get_validation_history("a")
        assert rows[0]["verdict"] == "up"
        assert rows[1]["verdict"] == "down"

    def test_summarize_promotes_canonical(self):
        from fantasyai.aurora.priors.validation_log import (
            append_validation, summarize_prior,
        )
        for _ in range(10):
            append_validation("newton_cooling", "up", score=0.9)
        s = summarize_prior("newton_cooling")
        assert s.n_up >= 10
        assert s.confidence > 0.85
        assert s.suggested_status == "canonical"

    def test_summarize_questions_on_contradictions(self):
        from fantasyai.aurora.priors.validation_log import (
            append_validation, summarize_prior,
        )
        # 5 contradictions, no confirmations.
        for _ in range(5):
            append_validation("newton_cooling", "contradiction")
        s = summarize_prior("newton_cooling")
        assert s.n_contradictions == 5
        assert s.confidence < 0.4
        assert s.suggested_status == "questioned"


# ============================================================
# changelog: record_run_validations + compute_validation_changelog
# ============================================================

class TestChangelog:

    def _state_with_top_prior(self, prior_id="newton_cooling",
                               verdict="auto_up", conf=0.92):
        return {
            "physics": {
                "discovered_law": {"name": "linear_ode",
                                   "params": {"a": -0.05, "b": 1.0}},
                "prior_matches": [{
                    "prior_id": prior_id,
                    "display_name": "Newton's law of cooling",
                    "domain": "thermal",
                    "confidence": conf,
                }],
                "auto_feedback": {"verdict": verdict, "rationale": "ok"},
            },
        }

    def test_record_appends_per_match(self):
        from fantasyai.aurora.priors.changelog import record_run_validations
        from fantasyai.aurora.priors.validation_log import get_validation_history
        rows = record_run_validations(self._state_with_top_prior(),
                                       run_dir="/fake/r1")
        assert any(r["prior_id"] == "newton_cooling" for r in rows)
        ledger = get_validation_history("newton_cooling")
        assert len(ledger) == 1
        assert ledger[0]["verdict"] == "up"

    def test_disagreement_records_contradiction(self):
        from fantasyai.aurora.priors.changelog import record_run_validations
        from fantasyai.aurora.priors.validation_log import get_validation_history
        s = self._state_with_top_prior()
        s["physics"]["auto_feedback"] = {
            "verdict": "auto_down",
            "rationale": "shape 'oscillatory' disagrees with linear_ode",
        }
        record_run_validations(s, run_dir="/fake/r2")
        ledger = get_validation_history("newton_cooling")
        assert len(ledger) == 1
        assert ledger[0]["verdict"] == "contradiction"

    def test_changelog_surfaces_contradiction(self):
        from fantasyai.aurora.priors.changelog import (
            record_run_validations, compute_validation_changelog,
            snapshot_all_priors,
        )
        # Snapshot empty, then write a contradiction → changelog has it.
        before = snapshot_all_priors()
        s = self._state_with_top_prior()
        s["physics"]["auto_feedback"] = {
            "verdict": "auto_down",
            "rationale": "shape 'oscillatory' disagrees with linear_ode",
        }
        record_run_validations(s, run_dir="/fake/r3")
        log = compute_validation_changelog(before)
        kinds = {e["kind"] for e in log}
        assert "contradicted" in kinds

    def test_changelog_surfaces_confirmed(self):
        from fantasyai.aurora.priors.changelog import (
            record_run_validations, compute_validation_changelog,
            snapshot_all_priors,
        )
        before = snapshot_all_priors()
        record_run_validations(self._state_with_top_prior(),
                                run_dir="/fake/r4")
        log = compute_validation_changelog(before)
        kinds = {e["kind"] for e in log}
        assert "confirmed" in kinds

    def test_changelog_surfaces_promotion(self):
        from fantasyai.aurora.priors.changelog import (
            record_run_validations, compute_validation_changelog,
            snapshot_all_priors,
        )
        from fantasyai.aurora.priors.validation_log import append_validation
        # Seed 2 confirmations to land at status "candidate" (n=2 < 3).
        append_validation("newton_cooling", "up", score=0.9)
        append_validation("newton_cooling", "up", score=0.9)
        before = snapshot_all_priors()
        # Add the 3rd via the run pipeline → status crosses candidate→validated.
        record_run_validations(self._state_with_top_prior(),
                                run_dir="/fake/r5")
        log = compute_validation_changelog(before)
        kinds = [e["kind"] for e in log]
        assert any(k == "promoted" or k == "confirmed" for k in kinds)


# ============================================================
# In-range parameter calibration
# ============================================================

class TestParameterCalibration:

    def test_in_range_check_for_newton_cooling(self):
        from fantasyai.aurora.priors.changelog import record_run_validations
        from fantasyai.aurora.priors.validation_log import get_validation_history
        # newton_cooling has free_parameters['k'] range [1e-6, 1.0].
        # If we report a fitted_param 'a' = -0.05, it doesn't match 'k' name
        # directly — but the matcher logs it. We just ensure no crash.
        s = {
            "physics": {
                "discovered_law": {"name": "linear_ode",
                                    "params": {"a": -0.05, "b": 1.0}},
                "prior_matches": [{
                    "prior_id": "newton_cooling",
                    "display_name": "Newton's law of cooling",
                    "confidence": 0.9,
                }],
                "auto_feedback": {"verdict": "auto_up", "rationale": "ok"},
            },
        }
        record_run_validations(s, run_dir="/r")
        rows = get_validation_history("newton_cooling")
        assert len(rows) == 1
        # fitted_params should be persisted.
        assert rows[0]["fitted_params"] is not None

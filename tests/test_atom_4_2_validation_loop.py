"""
Atom 4.2: validation loop + draft changelog writer.

Hermetic — uses tmp_path for the priors library and changelog root, so
tests don't touch ~/.aurora/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.scheduler.changelog import (   # noqa: E402
    save_prior, load_prior, list_priors, library_summary,
    update_priors_from_run, promote_candidates_if_ready,
    deprecate_if_contradicted, write_draft_changelog,
    run_validation_loop, ValidationEvent,
    DRAFT_DIR_NAME, PROMOTION_MIN_VALIDATIONS,
)


def _make_match_state(*, prior_match_title="Newton's law of cooling",
                      confidence=0.85, domain="enviro") -> dict:
    """Synthesise a state dict whose findings include a physics-prior match."""
    return {
        "system_model": {"template_id": domain},
        "findings": [
            {"title": f"Matches known law: {prior_match_title}",
             "method": "physics_prior_matcher",
             "description": f"{prior_match_title} fit",
             "confidence": confidence,
             "severity": "ok"},
        ],
    }


# ============================================================
# Library load/save
# ============================================================

class TestLibrary:

    def test_save_and_load(self, tmp_path):
        prior = {
            "id": "test_law",
            "domain": "enviro",
            "display_name": "Test law",
            "structural_form": "y = a*x",
            "status": "candidate",
            "support_n": 1,
            "confidence": 0.5,
            "validation_runs": ["run_1"],
            "provenance": [], "versions": [],
        }
        save_prior(prior, root=tmp_path)
        loaded = load_prior("test_law", "enviro", root=tmp_path)
        assert loaded["id"] == "test_law"
        assert loaded["confidence"] == 0.5

    def test_list_priors_filters_by_status(self, tmp_path):
        save_prior({"id": "a", "domain": "enviro", "status": "canonical",
                     "support_n": 1, "confidence": 0.7,
                     "display_name": "A", "structural_form": "a"},
                    root=tmp_path)
        save_prior({"id": "b", "domain": "enviro", "status": "candidate",
                     "support_n": 1, "confidence": 0.5,
                     "display_name": "B", "structural_form": "b"},
                    root=tmp_path)
        canonical = list_priors(status="canonical", root=tmp_path)
        candidate = list_priors(status="candidate", root=tmp_path)
        assert len(canonical) == 1 and canonical[0]["id"] == "a"
        assert len(candidate) == 1 and candidate[0]["id"] == "b"

    def test_library_summary(self, tmp_path):
        save_prior({"id": "a", "domain": "enviro", "status": "canonical",
                     "support_n": 1, "confidence": 0.7,
                     "display_name": "A", "structural_form": "a"},
                    root=tmp_path)
        save_prior({"id": "b", "domain": "finance", "status": "candidate",
                     "support_n": 1, "confidence": 0.5,
                     "display_name": "B", "structural_form": "b"},
                    root=tmp_path)
        s = library_summary(tmp_path)
        assert s["total"]["canonical"] == 1
        assert s["total"]["candidate"] == 1
        assert s["by_domain"]["enviro"]["canonical"] == 1
        assert s["by_domain"]["finance"]["candidate"] == 1


# ============================================================
# Per-run classification
# ============================================================

class TestUpdatePriorsFromRun:

    def test_creates_candidate_when_no_match(self, tmp_path):
        state = _make_match_state(prior_match_title="brand new relation",
                                    confidence=0.6, domain="enviro")
        events = update_priors_from_run(state, run_id="run_1",
                                          priors_root=tmp_path)
        assert any(e.kind == "candidate" for e in events)
        # Library now has one candidate.
        priors = list_priors(root=tmp_path)
        assert len(priors) == 1
        assert priors[0]["status"] == "candidate"

    def test_strengthens_existing_match(self, tmp_path):
        # Pre-populate library with a candidate at confidence 0.5.
        save_prior({"id": "newton_s_law_of_cooling",
                     "domain": "enviro",
                     "display_name": "Newton's Law Of Cooling",
                     "structural_form": "Newton's law of cooling",
                     "status": "candidate",
                     "support_n": 1, "confidence": 0.5,
                     "validation_runs": ["older_run"],
                     "provenance": [{"run_id": "older_run",
                                      "status": "candidate",
                                      "delta": 0.0, "ts": "2026-01-01"}],
                     "versions": [], "deprecation_history": []},
                    root=tmp_path)
        state = _make_match_state(prior_match_title="Newton's law of cooling",
                                    confidence=0.85)
        events = update_priors_from_run(state, run_id="run_2",
                                          priors_root=tmp_path)
        assert any(e.kind == "strengthened" for e in events)
        loaded = load_prior("newton_s_law_of_cooling", "enviro", root=tmp_path)
        assert loaded["support_n"] == 2
        assert loaded["confidence"] > 0.5

    def test_state_with_no_findings_is_safe(self, tmp_path):
        events = update_priors_from_run({"findings": []}, run_id="r",
                                          priors_root=tmp_path)
        assert events == []


# ============================================================
# Promotion gate
# ============================================================

class TestPromotion:

    def test_promotes_when_thresholds_met(self, tmp_path):
        # 3 confirmation provenance entries with 100% consensus.
        save_prior({"id": "law_x", "domain": "enviro",
                     "display_name": "Law X", "structural_form": "y=ax",
                     "status": "candidate",
                     "support_n": 3, "confidence": 0.7,
                     "validation_runs": ["r1", "r2", "r3"],
                     "provenance": [
                         {"run_id": "r1", "status": "candidate", "delta": 0.0, "ts": "2026-01-01"},
                         {"run_id": "r2", "status": "strengthened", "delta": 0.04, "ts": "2026-01-02"},
                         {"run_id": "r3", "status": "strengthened", "delta": 0.04, "ts": "2026-01-03"},
                     ],
                     "versions": [], "deprecation_history": []},
                    root=tmp_path)
        events = promote_candidates_if_ready(priors_root=tmp_path)
        assert any(e.kind == "promoted" for e in events)
        loaded = load_prior("law_x", "enviro", root=tmp_path)
        assert loaded["status"] == "canonical"

    def test_does_not_promote_below_threshold(self, tmp_path):
        save_prior({"id": "law_y", "domain": "enviro",
                     "display_name": "Law Y", "structural_form": "y",
                     "status": "candidate",
                     "support_n": 1, "confidence": 0.5,
                     "validation_runs": ["r1"],
                     "provenance": [{"run_id": "r1", "status": "candidate", "delta": 0, "ts": "2026-01-01"}],
                     "versions": [], "deprecation_history": []},
                    root=tmp_path)
        events = promote_candidates_if_ready(priors_root=tmp_path)
        assert not any(e.kind == "promoted" for e in events)
        assert load_prior("law_y", "enviro", root=tmp_path)["status"] == "candidate"

    def test_low_consensus_blocks_promotion(self, tmp_path):
        # 3 validations but mostly contradicted.
        save_prior({"id": "law_z", "domain": "enviro",
                     "display_name": "Law Z", "structural_form": "z",
                     "status": "candidate",
                     "support_n": 3, "confidence": 0.5,
                     "validation_runs": ["r1", "r2", "r3"],
                     "provenance": [
                         {"run_id": "r1", "status": "candidate", "delta": 0, "ts": "2026-01-01"},
                         {"run_id": "r2", "status": "contradicted", "delta": -0.5, "ts": "2026-01-02"},
                         {"run_id": "r3", "status": "weakened", "delta": -0.3, "ts": "2026-01-03"},
                     ],
                     "versions": [], "deprecation_history": []},
                    root=tmp_path)
        events = promote_candidates_if_ready(priors_root=tmp_path)
        assert not any(e.kind == "promoted" for e in events)


# ============================================================
# Deprecation gate
# ============================================================

class TestDeprecation:

    def test_deprecates_repeatedly_contradicted_canonical(self, tmp_path):
        from datetime import datetime, timezone
        recent = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        save_prior({"id": "old_law", "domain": "economics",
                     "display_name": "Old Law",
                     "structural_form": "old",
                     "status": "canonical",
                     "support_n": 5, "confidence": 0.6,
                     "validation_runs": ["r1", "r2", "r3", "r4"],
                     "provenance": [
                         {"run_id": "r1", "status": "contradicted", "delta": -0.5, "ts": recent},
                         {"run_id": "r2", "status": "contradicted", "delta": -0.5, "ts": recent},
                         {"run_id": "r3", "status": "contradicted", "delta": -0.5, "ts": recent},
                     ],
                     "versions": [], "deprecation_history": []},
                    root=tmp_path)
        events = deprecate_if_contradicted(priors_root=tmp_path)
        assert any(e.kind == "deprecated" for e in events)
        loaded = load_prior("old_law", "economics", root=tmp_path)
        assert loaded["status"] == "deprecated"
        # Crucially: NOT deleted. File still exists.
        assert load_prior("old_law", "economics", root=tmp_path) is not None
        # And deprecation_history records why.
        assert len(loaded["deprecation_history"]) == 1


# ============================================================
# Draft changelog writer + review gate
# ============================================================

class TestDraftChangelog:

    def test_writes_to_draft_dir(self, tmp_path):
        events = [ValidationEvent(
            kind="strengthened", prior_id="x", display_name="X",
            domain="enviro", delta="+0.04", rationale="test",
            run_ids=["r1"], support_n_before=1, support_n_after=2,
        )]
        draft = write_draft_changelog(events,
                                        priors_root=tmp_path / "priors",
                                        changelog_root=tmp_path / "log")
        # Lives under /draft/, not /published/.
        assert DRAFT_DIR_NAME in str(draft)
        d = json.loads(draft.read_text(encoding="utf-8"))
        assert d["review_required"] is True
        assert d["published"] is False
        assert len(d["events"]) == 1


# ============================================================
# Trusted-source filter (USER UPLOADS NEVER CONTRIBUTE — v1 invariant)
# ============================================================

class TestTrustedSourceFilter:

    def test_user_upload_skipped(self, tmp_path):
        # A "run" from a user upload should NOT mutate the prior library.
        # We pass a build_state stub that returns a state with a finding,
        # and assert the library is empty after run_validation_loop.
        def fake_build_state(rd):
            return _make_match_state(prior_match_title="brand new",
                                       confidence=0.7)
        run_dir = tmp_path / "user_run"
        run_dir.mkdir()
        run_results = [{
            "ok": True, "source": "user_upload",
            "run_id": "user_run", "run_dir": str(run_dir),
        }]
        result = run_validation_loop(
            run_results, priors_root=tmp_path / "priors",
            changelog_root=tmp_path / "log",
            build_state_fn=fake_build_state,
        )
        # Library is empty — user uploads excluded.
        assert library_summary(tmp_path / "priors")["total"]["candidate"] == 0
        # The summary lists the source as skipped.
        skip = result["sources_summary"][0]
        assert skip["validated"] is False
        assert skip["skip_reason"] == "untrusted_source"

    def test_trusted_source_contributes(self, tmp_path):
        def fake_build_state(rd):
            return _make_match_state(prior_match_title="trusted match",
                                       confidence=0.7)
        run_dir = tmp_path / "noaa_run"
        run_dir.mkdir()
        run_results = [{
            "ok": True, "source": "noaa",
            "run_id": "noaa_run", "run_dir": str(run_dir),
        }]
        result = run_validation_loop(
            run_results, priors_root=tmp_path / "priors",
            changelog_root=tmp_path / "log",
            build_state_fn=fake_build_state,
        )
        # Library now has one candidate.
        assert library_summary(tmp_path / "priors")["total"]["candidate"] == 1
        # Draft changelog file exists.
        assert Path(result["draft_changelog"]).exists()

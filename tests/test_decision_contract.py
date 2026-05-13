"""
Tests for ``fantasyai.aurora.decision_contract`` and ``narrative_engine``.

Covers:
  - 21 packaged default contracts exist + can be looked up by id
  - Each template ships exactly 3 contracts (explore / avoid / confirm)
  - Confidence band mapping
  - Suppression matrix decisions for the 9 cells (3 bands × 3 asymmetries)
  - Narrative engine: filter, rank, select, render, trace
  - Same artifacts → different surface text under different contracts
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# Default contract library
# ============================================================

class TestDefaultContracts:

    def test_21_defaults_present(self):
        from fantasyai.aurora.decision_contract import list_default_contracts
        cs = list_default_contracts()
        assert len(cs) == 21

    def test_three_per_template(self):
        from fantasyai.aurora.decision_contract import suggest_contracts_for_template
        for t in ("thermal", "finance", "enviro", "bio", "industrial",
                  "research", "seismology"):
            ts = suggest_contracts_for_template(t)
            assert len(ts) == 3, f"{t} has {len(ts)} contracts"
            classes = {c.contract_class for c in ts}
            assert "explore" in classes
            assert "avoid_harm" in classes
            assert "confirm" in classes

    def test_get_default_contract_by_id(self):
        from fantasyai.aurora.decision_contract import get_default_contract
        c = get_default_contract("thermal:avoid_overheat")
        assert c is not None
        assert c.cost_asymmetry == "false_negative_costly"

    def test_unknown_id_returns_none(self):
        from fantasyai.aurora.decision_contract import get_default_contract
        assert get_default_contract("nonexistent:foo") is None

    def test_all_have_required_fields(self):
        from fantasyai.aurora.decision_contract import list_default_contracts
        for c in list_default_contracts():
            assert c.id and ":" in c.id
            assert c.template_id
            assert c.contract_class
            assert c.objective
            assert 0.0 <= c.confidence_floor <= 1.0


# ============================================================
# Confidence × cost-asymmetry matrix
# ============================================================

class TestSuppressionMatrix:

    def test_fp_costly_low_suppresses(self):
        from fantasyai.aurora.decision_contract import suppression_decision
        assert suppression_decision(0.2, "false_positive_costly") == "suppress"

    def test_fp_costly_medium_hedges(self):
        from fantasyai.aurora.decision_contract import suppression_decision
        assert suppression_decision(0.5, "false_positive_costly") == "hedge"

    def test_fp_costly_high_renders(self):
        from fantasyai.aurora.decision_contract import suppression_decision
        assert suppression_decision(0.8, "false_positive_costly") == "render"

    def test_fn_costly_low_renders(self):
        from fantasyai.aurora.decision_contract import suppression_decision
        # Don't suppress when missing the signal hurts more.
        assert suppression_decision(0.2, "false_negative_costly") == "render"

    def test_fn_costly_high_amplifies(self):
        from fantasyai.aurora.decision_contract import suppression_decision
        assert suppression_decision(0.9, "false_negative_costly") == "amplify"

    def test_symmetric_low_inspects(self):
        from fantasyai.aurora.decision_contract import suppression_decision
        assert suppression_decision(0.2, "symmetric") == "inspect"

    def test_symmetric_high_renders(self):
        from fantasyai.aurora.decision_contract import suppression_decision
        assert suppression_decision(0.85, "symmetric") == "render"

    def test_unknown_asymmetry_falls_to_symmetric(self):
        from fantasyai.aurora.decision_contract import suppression_decision
        # Garbage input → "symmetric" branch.
        assert suppression_decision(0.5, "weird_value") == "render"


# ============================================================
# Confidence band
# ============================================================

class TestConfidenceBand:

    def test_high(self):
        from fantasyai.aurora.decision_contract import confidence_band
        assert confidence_band(0.8) == "high"

    def test_medium(self):
        from fantasyai.aurora.decision_contract import confidence_band
        assert confidence_band(0.5) == "medium"

    def test_low(self):
        from fantasyai.aurora.decision_contract import confidence_band
        assert confidence_band(0.2) == "low"

    def test_invalid_input_falls_to_low(self):
        from fantasyai.aurora.decision_contract import confidence_band
        assert confidence_band("garbage") == "low"


# ============================================================
# Narrative engine
# ============================================================

class TestNarrativeEngine:

    def _state(self):
        return {
            "system_model": {
                "template_id": "thermal",
                "vocabulary": {
                    "anomaly_term": "thermal excursion",
                    "regime_term": "thermal regime",
                    "breach_term": "overheat",
                    "advisory_term": "operational advisory",
                },
            },
            "findings": [
                {"title": "Anomaly in temperature at row 100",
                 "description": "z=4.5σ", "confidence": 0.9,
                 "severity": "crit", "method": "iso-forest",
                 "referent": {"primary_label": "row 100",
                               "level_used": "row_index",
                               "inspect_only": True}},
                {"title": "Forecast peak: 312 K",
                 "description": "method AR(1), horizon 24",
                 "confidence": 0.55, "severity": "warn",
                 "method": "AR(1)",
                 "referent": {"primary_label": "+24 hours",
                               "level_used": "position"}},
                {"title": "Anomaly in load at row 200",
                 "description": "marginal", "confidence": 0.15,
                 "severity": "info", "method": "iso-forest",
                 "referent": {"primary_label": "row 200",
                               "level_used": "row_index"}},
            ],
        }

    def test_avoid_harm_keeps_low_confidence(self):
        from fantasyai.aurora.narrative_engine import render_narrative
        from fantasyai.aurora.decision_contract import get_default_contract
        st = self._state()
        c = get_default_contract("thermal:avoid_overheat")
        render_narrative(st, c)
        # Low-confidence finding stays visible (FN-costly).
        treatments = [f.get("surface_treatment") for f in st["findings"]]
        assert any(t in ("render", "amplify") for t in treatments)

    def test_confirm_suppresses_low_confidence(self):
        from fantasyai.aurora.narrative_engine import render_narrative
        from fantasyai.aurora.decision_contract import get_default_contract
        st = self._state()
        c = get_default_contract("thermal:confirm_law")
        render_narrative(st, c)
        # FP-costly + low confidence → suppress. Inspected bucket grows.
        assert len(st.get("inspected_findings") or []) >= 1

    def test_amplify_marks_high_confidence_under_avoid_harm(self):
        from fantasyai.aurora.narrative_engine import render_narrative
        from fantasyai.aurora.decision_contract import get_default_contract
        st = self._state()
        c = get_default_contract("thermal:avoid_overheat")
        render_narrative(st, c)
        # High-confidence anomaly should be amplified.
        amped = [f for f in st["findings"]
                 if f.get("surface_treatment") == "amplify"]
        assert len(amped) >= 1

    def test_trace_records_decision(self):
        from fantasyai.aurora.narrative_engine import (
            render_narrative, renderer_trace_for_finding,
        )
        from fantasyai.aurora.decision_contract import get_default_contract
        st = self._state()
        c = get_default_contract("thermal:explore")
        render_narrative(st, c)
        for f in st["findings"]:
            tr = renderer_trace_for_finding(f)
            assert tr.get("contract_id") == "thermal:explore"
            assert tr.get("template_id")
            assert "treatment" in tr

    def test_default_contract_when_none_passed(self):
        from fantasyai.aurora.narrative_engine import render_narrative
        st = self._state()
        render_narrative(st, contract=None)
        # No crash; engine ran.
        assert "narrative_engine" in st

    def test_same_artifacts_different_surface_under_different_contracts(self):
        """The core promise: same findings, different headlines."""
        from fantasyai.aurora.narrative_engine import render_narrative
        from fantasyai.aurora.decision_contract import get_default_contract
        st_a = self._state()
        st_b = self._state()
        render_narrative(st_a, get_default_contract("thermal:explore"))
        render_narrative(st_b, get_default_contract("thermal:avoid_overheat"))
        title_a = st_a["findings"][0]["narrative"]["title"]
        title_b = st_b["findings"][0]["narrative"]["title"]
        # Contracts produced different surface text on the same input.
        assert title_a != title_b


# ============================================================
# Engine kind classifier
# ============================================================

class TestKindClassifier:

    def test_anomaly(self):
        from fantasyai.aurora.narrative_engine import resolve_finding_kind
        assert resolve_finding_kind({"title": "Anomaly in temperature at row 1",
                                     "method": "iso-forest"}) == "anomaly"

    def test_forecast(self):
        from fantasyai.aurora.narrative_engine import resolve_finding_kind
        assert resolve_finding_kind({"title": "Forecast peak: 1.2",
                                     "method": "AR(1)"}) == "forecast"

    def test_regime(self):
        from fantasyai.aurora.narrative_engine import resolve_finding_kind
        assert resolve_finding_kind({"title": "2 regimes detected",
                                     "method": "PELT"}) == "regime"

    def test_physics_match(self):
        from fantasyai.aurora.narrative_engine import resolve_finding_kind
        assert resolve_finding_kind({"title": "Matches known law: Newton",
                                     "method": "physics_prior_matcher"}) == "physics_match"

    def test_generic_fallback(self):
        from fantasyai.aurora.narrative_engine import resolve_finding_kind
        assert resolve_finding_kind({"title": "Something",
                                     "method": "unknown"}) == "generic"

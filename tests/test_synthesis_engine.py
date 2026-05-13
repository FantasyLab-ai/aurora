"""
Synthesis engine — pattern matching, slot extraction, equation translation,
domain context. The synthesis engine produces three new state fields:

  - state.interpretive_summary    (1-2 paragraph "WHAT THIS MEANS" prose)
  - state.equation_translations   (per-finding-index → intuition map)
  - state.domain_context          (0-3 short editorial cards)
  - state.synthesis_meta          (which template fired, score, kinds)

Glass-box: the prose is deterministic — every paragraph is reproducible
from the templates + state. These tests pin that determinism.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# Fixtures — minimal "states" that exercise specific paths
# ============================================================

def _physics_match_state(domain: str = "enviro") -> dict:
    return {
        "system_model": {"template_id": domain},
        "findings": [
            {
                "title": "Physical model fit: damped_harmonic_oscillator",
                "description": "Best fit zeta=0.31 omega=2.4",
                "method": "physics_prior_matcher",
                "severity": "info",
                "confidence": 0.91,
                "params": {"zeta": 0.31, "omega": 2.4},
            },
            {
                "title": "Anomaly in pressure at row 1248",
                "description": "z=4.2",
                "method": "iqr",
                "severity": "crit",
                "confidence": 0.85,
            },
        ],
        "forecast": {"available": True, "headline_value": "12.3 m/s",
                     "horizon": 24},
    }


def _anomaly_only_state() -> dict:
    return {
        "system_model": {"template_id": "finance"},
        "findings": [
            {"title": "Anomaly at row 50", "description": "z=4.0",
             "method": "iqr", "severity": "crit", "confidence": 0.8},
            {"title": "Anomaly at row 51", "description": "z=3.7",
             "method": "iqr", "severity": "warn", "confidence": 0.7},
        ],
    }


def _empty_state() -> dict:
    return {"system_model": {"template_id": "enviro"}, "findings": []}


# ============================================================
# Engine core
# ============================================================

def test_synthesis_writes_all_four_state_keys():
    """The contract: a synthesize() call must populate all four fields,
    even when the run is sparse (so the frontend can rely on them)."""
    from fantasyai.aurora.synthesis import synthesize

    state = _physics_match_state()
    synthesize(state)
    assert "interpretive_summary" in state
    assert "equation_translations" in state
    assert "domain_context" in state
    assert "synthesis_meta" in state


def test_synthesis_meta_carries_canonical_keys():
    """The frontend pill and the changelog collector both read
    template_id / score / specificity / kinds_seen / active_domain.
    These must always be present after a successful template fire."""
    from fantasyai.aurora.synthesis import synthesize

    state = _physics_match_state()
    synthesize(state)
    meta = state["synthesis_meta"]
    assert meta.get("template_id"), "no template fired"
    assert meta.get("score") is not None
    assert isinstance(meta.get("kinds_seen"), list)
    assert "physics_match" in meta["kinds_seen"]
    assert "anomaly" in meta["kinds_seen"]
    assert meta.get("active_domain") == "enviro"


def test_synthesis_produces_nonempty_prose_on_real_finding():
    """Sanity: we generate at least a sentence of summary."""
    from fantasyai.aurora.synthesis import synthesize

    state = _physics_match_state()
    synthesize(state)
    assert state["interpretive_summary"]
    assert len(state["interpretive_summary"]) > 40
    # No leftover slot placeholders.
    assert "{" not in state["interpretive_summary"]


def test_synthesis_empty_findings_gracefully_falls_back():
    """When the run produced nothing, the engine must not crash and
    must not invent prose; it falls back to low_signal_fallback."""
    from fantasyai.aurora.synthesis import synthesize

    state = _empty_state()
    synthesize(state)
    # Either the fallback fired, or no template fired (both acceptable).
    summ = state["interpretive_summary"]
    if summ:
        assert "{" not in summ
    assert isinstance(state["equation_translations"], dict)
    assert isinstance(state["domain_context"], list)


# ============================================================
# Pattern matching / classification
# ============================================================

def test_classify_finding_kind_recognizes_physics_match():
    from fantasyai.aurora.synthesis import classify_finding_kind
    f = {"title": "Physical model fit: damped_harmonic_oscillator",
         "method": "physics_prior_matcher"}
    assert classify_finding_kind(f) == "physics_match"


def test_classify_finding_kind_recognizes_anomaly():
    from fantasyai.aurora.synthesis import classify_finding_kind
    f = {"title": "Anomaly in pressure at row 1248", "method": "iqr"}
    assert classify_finding_kind(f) == "anomaly"


def test_evaluate_match_counts_required_and_optional():
    """The match evaluator returns {matched, score, n_req_ok, ...}."""
    from fantasyai.aurora.synthesis import classify_finding_kind, evaluate_match
    findings = [
        {"title": "Anomaly A", "method": "iqr"},
        {"title": "Anomaly B", "method": "iqr"},
        {"title": "Physical model fit: foo", "method": "physics_prior_matcher"},
    ]
    # Stamp synthesis kinds (matcher does this in real flow).
    for f in findings:
        f["_synthesis_kind"] = classify_finding_kind(f)
    cond = {"requires": [{"kind": "anomaly", "min_count": 2}],
            "optional": [{"kind": "physics_match"}]}
    ev = evaluate_match(cond, findings)
    assert ev["matched"] is True
    assert ev["n_req_ok"] == 1
    assert ev["n_req_total"] == 1
    assert ev["n_opt_ok"] == 1
    # score = req_satisfied + 0.2 * (opt_satisfied / opt_total)
    assert ev["score"] == pytest.approx(1.2)


# ============================================================
# Slot extraction
# ============================================================

def test_extract_slot_simple_selector():
    from fantasyai.aurora.synthesis import extract_slot
    state = {"forecast": {"headline_value": "12.3"}}
    v = extract_slot("forecast.headline_value", state)
    assert v == "12.3"


def test_extract_slot_count_aggregator():
    from fantasyai.aurora.synthesis import (
        extract_slot, classify_finding_kind,
    )
    findings = [
        {"title": "Anomaly A", "method": "iqr"},
        {"title": "Anomaly B", "method": "iqr"},
        {"title": "Physical model fit: foo", "method": "physics_prior_matcher"},
    ]
    for f in findings:
        f["_synthesis_kind"] = classify_finding_kind(f)
    state = {"findings": findings}
    n = extract_slot("count(findings[kind=anomaly])", state)
    assert int(str(n)) == 2


# ============================================================
# Equation translation
# ============================================================

def test_translate_equation_damped_harmonic_zeta_underdamped():
    """zeta=0.31 should land in the underdamped band (0.05–0.5)."""
    from fantasyai.aurora.synthesis import (
        translate_equation, list_equation_translations,
    )
    finding = {
        "title": "Physical model fit: damped_harmonic_oscillator",
        "description": "zeta=0.31 omega=2.4",
        "method": "physics_prior_matcher",
        "params": {"zeta": 0.31, "omega": 2.4},
    }
    res = translate_equation(finding, list_equation_translations())
    assert res is not None
    assert res["form_id"] == "damped_harmonic"
    phrases = " ".join(res["intuition_phrases"]).lower()
    assert "underdamped" in phrases
    # No leftover {placeholder}.
    assert "{" not in " ".join(res["intuition_phrases"])
    assert res["parameter_values"]["zeta"] == pytest.approx(0.31)


def test_translate_equation_returns_none_when_no_match():
    """Findings unrelated to any rule must NOT receive a translation —
    the translator never invents."""
    from fantasyai.aurora.synthesis import (
        translate_equation, list_equation_translations,
    )
    finding = {"title": "Anomaly at row 5", "method": "iqr",
               "description": "z=3.4"}
    assert translate_equation(finding, list_equation_translations()) is None


def test_translate_equation_normalizes_underscores():
    """`damped_harmonic_oscillator` (underscores) must match the
    rule's `damped harmonic` (space) substring after normalization."""
    from fantasyai.aurora.synthesis import (
        translate_equation, list_equation_translations,
    )
    finding = {"title": "damped_harmonic_oscillator best fit",
               "params": {"zeta": 0.8}}
    res = translate_equation(finding, list_equation_translations())
    assert res is not None
    assert res["form_id"] == "damped_harmonic"


# ============================================================
# Domain context
# ============================================================

def test_domain_context_only_from_active_domain():
    """When system_model.template_id == 'enviro', we must NOT see a
    finance / sports / bio context note. The library is gated by domain."""
    from fantasyai.aurora.synthesis import synthesize

    state = _physics_match_state(domain="enviro")
    synthesize(state)
    notes = state["domain_context"]
    assert isinstance(notes, list)
    for n in notes:
        # Every selected note must declare its domain (set by engine).
        assert n.get("domain") in (None, "enviro")


# ============================================================
# Changelog logging — synthesis_template_fires
# ============================================================

def test_collect_synthesis_template_fires_returns_record():
    from fantasyai.aurora.synthesis import synthesize
    from fantasyai.aurora.scheduler.changelog import (
        collect_synthesis_template_fires,
    )
    state = _physics_match_state()
    synthesize(state)
    fire = collect_synthesis_template_fires(state, "run_xyz")
    assert fire is not None
    assert fire["run_id"] == "run_xyz"
    assert fire["template_id"]
    assert isinstance(fire["kinds_seen"], list)


def test_collect_synthesis_template_fires_returns_none_without_meta():
    """Pre-synthesis-engine state has no synthesis_meta — collector
    must return None rather than crashing."""
    from fantasyai.aurora.scheduler.changelog import (
        collect_synthesis_template_fires,
    )
    assert collect_synthesis_template_fires({}, "x") is None
    assert collect_synthesis_template_fires(
        {"synthesis_meta": {}}, "x") is None


def test_aggregate_synthesis_fires_counts_by_template():
    from fantasyai.aurora.scheduler.changelog import (
        aggregate_synthesis_fires,
    )
    fires = [
        {"template_id": "research_publishable_signal",
         "active_domain": "research",
         "kinds_seen": ["physics_match", "anomaly"]},
        {"template_id": "research_publishable_signal",
         "active_domain": "research",
         "kinds_seen": ["physics_match"]},
        {"template_id": "anomaly_dominated",
         "active_domain": "finance",
         "kinds_seen": ["anomaly"]},
    ]
    agg = aggregate_synthesis_fires(fires)
    assert agg["n_runs_with_synthesis"] == 3
    assert agg["by_template"]["research_publishable_signal"] == 2
    assert agg["by_template"]["anomaly_dominated"] == 1
    assert agg["by_domain"]["research"] == 2
    assert agg["by_domain"]["finance"] == 1
    assert agg["by_kind"]["physics_match"] == 2
    assert agg["by_kind"]["anomaly"] == 2


def test_aggregate_synthesis_fires_handles_empty():
    from fantasyai.aurora.scheduler.changelog import (
        aggregate_synthesis_fires,
    )
    agg = aggregate_synthesis_fires([])
    assert agg["n_runs_with_synthesis"] == 0
    assert agg["by_template"] == {}

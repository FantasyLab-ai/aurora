"""
Atom 3.1: SeedRanker — config-driven weights + Spearman correlation
test against the hand-labeled golden fixture.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

scipy_stats = pytest.importorskip("scipy.stats")
np = pytest.importorskip("numpy")

from fantasyai.aurora import seed_ranker as _ranker   # noqa: E402


# ============================================================
# Config loading + override
# ============================================================

class TestConfig:

    def test_default_config_has_weights_summing_to_one(self):
        cfg = _ranker.DEFAULT_CONFIG
        rel_w = cfg["relevance"]["weights"]
        sur_w = cfg["surprise"]["weights"]
        assert abs(sum(rel_w.values()) - 1.0) < 1e-9
        assert abs(sum(sur_w.values()) - 1.0) < 1e-9

    def test_load_config_uses_default_when_no_file(self, tmp_path,
                                                    monkeypatch):
        # Point env override at a non-existent path; expect defaults.
        nonexistent = tmp_path / "missing.json"
        monkeypatch.setenv("AURORA_SEED_RANKER_CONFIG", str(nonexistent))
        # Clear cache between tests.
        _ranker._CACHE["config"] = None
        cfg = _ranker.load_config()
        assert cfg["version"] == "v1"
        assert cfg["relevance"]["weights"]["keyword"] == 0.40

    def test_user_config_overrides_defaults(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "seed_ranker.json"
        cfg_path.write_text(json.dumps({
            "relevance": {"weights": {"keyword": 0.99, "kind": 0.01,
                                       "confidence": 0, "referent": 0,
                                       "severity": 0}}
        }))
        monkeypatch.setenv("AURORA_SEED_RANKER_CONFIG", str(cfg_path))
        _ranker._CACHE["config"] = None
        cfg = _ranker.load_config()
        assert cfg["relevance"]["weights"]["keyword"] == 0.99


# ============================================================
# Empty-seed contract
# ============================================================

class TestEmptySeed:

    def test_no_seed_yields_empty_lists(self):
        relevant, surprise = _ranker.rank(
            [{"title": "x", "confidence": 0.5}], "")
        assert relevant == [] and surprise == []

    def test_attach_to_state_no_seed(self):
        state = {"findings": [{"title": "x", "confidence": 0.5}]}
        _ranker.attach_to_state(state, seed_text="")
        assert "relevant_findings" not in state
        assert "surprise_findings" not in state
        assert state["seed_ranker"] == {"active": False}


# ============================================================
# Relevance basic correctness
# ============================================================

class TestRelevanceBasics:

    def test_keyword_match_boosts_score(self):
        rel = _ranker.score_relevance(
            {"title": "Anomaly in temperature at row 5",
             "description": "z=4.5", "severity": "crit", "confidence": 0.8},
            seed_text="anomaly in temperature",
        )
        assert rel.score > 0.5
        assert rel.components["keyword"] > 0
        assert rel.components["kind"] == 1.0    # "anomaly" is a kind synonym

    def test_unrelated_text_low_score(self):
        rel = _ranker.score_relevance(
            {"title": "Forecast peak: 2.1 m", "description": "AR(1)",
             "severity": "info", "confidence": 0.5},
            seed_text="hurricane impact",
        )
        # No keyword overlap, kind doesn't match → score should be low.
        assert rel.score < 0.4

    def test_kind_hint_recognised(self):
        # User says "regime" → finding kind regime → kind component fires.
        rel = _ranker.score_relevance(
            {"title": "Regime change at 2024-08-12",
             "method": "PELT", "severity": "warn", "confidence": 0.7},
            seed_text="regime shift in inflation",
        )
        assert rel.components["kind"] == 1.0


# ============================================================
# Surprise basic correctness
# ============================================================

class TestSurpriseBasics:

    def test_high_significance_low_relevance_yields_high_surprise(self):
        finding = {"title": "Anomaly in fare_amount at 2024-06-01",
                    "description": "fare=high z=+4.1", "severity": "crit",
                    "confidence": 0.85, "z": 4.1}
        # Seed about something else entirely.
        sur = _ranker.score_surprise(finding, seed_text="bottleneck on routes",
                                       relevance_value=0.10)
        assert sur.score > 0.5
        assert sur.components["unusualness"] > 0.85

    def test_contradiction_when_seed_says_stable_but_finding_says_shift(self):
        finding = {"title": "Regime shift at 2024-04-01",
                    "severity": "warn", "confidence": 0.7}
        sur = _ranker.score_surprise(finding,
                                       seed_text="this market has been stable",
                                       relevance_value=0.10)
        assert sur.components["contradiction"] == 1.0


# ============================================================
# Spearman correlation against golden fixture (the headline test)
# ============================================================

def _load_golden() -> dict:
    p = ROOT / "tests" / "fixtures" / "seed_ranker" / "golden_relevance.jsonl"
    items = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        items.append(json.loads(line))
    grouped = defaultdict(list)
    for it in items:
        grouped[it["seed"]].append(it)
    return grouped


class TestSpearman:

    def test_relevance_correlates_with_human_ranking(self):
        groups = _load_golden()
        coeffs = []
        for seed, items in groups.items():
            tid = items[0].get("template_id")
            scored = [(it, _ranker.score_relevance(
                            it["finding"], seed, template_id=tid).score)
                       for it in items]
            # Sort actual ranks by descending score (highest score = rank 1).
            scored.sort(key=lambda t: -t[1])
            actual_ranks = []
            expected_ranks = []
            for rank_pos, (it, _score) in enumerate(scored, start=1):
                actual_ranks.append(rank_pos)
                expected_ranks.append(it["expected_rank"])
            rho, _p = scipy_stats.spearmanr(actual_ranks, expected_ranks)
            coeffs.append(rho)
            print(f"  seed={seed!r:60s}  rho={rho:.3f}")
        median_rho = float(np.median(coeffs))
        print(f"  median Spearman rho = {median_rho:.3f}")
        assert median_rho >= 0.70, \
            f"median Spearman {median_rho:.2f} below 0.70 threshold"


# ============================================================
# attach_to_state integration
# ============================================================

class TestAttachToState:

    def test_attaches_dual_sections_when_seed_present(self):
        findings = [
            {"title": "Anomaly in temperature at 2024-08-12",
             "description": "z=4.5", "severity": "crit", "confidence": 0.85,
             "method": "iso-forest"},
            {"title": "Forecast peak: 2.1 m",
             "description": "AR horizon 24", "severity": "info",
             "confidence": 0.5, "method": "AR(1)"},
            {"title": "Regime change at 2024-08-12",
             "description": "shift to high-state", "severity": "warn",
             "confidence": 0.7, "method": "PELT"},
        ]
        state = {"findings": findings}
        _ranker.attach_to_state(state, seed_text="anomaly in temperature",
                                  template_id="enviro")
        assert state["seed_ranker"]["active"] is True
        assert "relevant_findings" in state
        # Findings array UNCHANGED (the seed never excludes).
        assert state["findings"] is findings
        # The anomaly finding should be in relevant_findings.
        assert any("Anomaly in temperature" in r["finding"]["title"]
                    for r in state["relevant_findings"])

    def test_findings_unchanged_across_three_different_seeds(self):
        # Acceptance criterion from the brief: "Run the same dataset with
        # three different seeds and verify the surface output reorders
        # meaningfully but the underlying artifacts are identical."
        findings_master = [
            {"title": "Anomaly in temperature at 2024-08-12",
             "severity": "crit", "confidence": 0.85, "method": "iso-forest"},
            {"title": "Forecast peak: 2.1 m",
             "severity": "info", "confidence": 0.5, "method": "AR(1)"},
            {"title": "Regime change at 2024-08-12",
             "severity": "warn", "confidence": 0.7, "method": "PELT"},
            {"title": "Physical model fit: damped_oscillator",
             "severity": "ok", "confidence": 0.7,
             "method": "physics_discovery"},
        ]
        seeds = [
            "anomaly in temperature",
            "regime shift",
            "physics law match",
        ]
        outputs = []
        for s in seeds:
            state = {"findings": [dict(f) for f in findings_master]}
            _ranker.attach_to_state(state, seed_text=s, template_id="enviro")
            outputs.append({
                "relevant_titles": [r["finding"]["title"]
                                     for r in state["relevant_findings"]],
                "n_findings": len(state["findings"]),
            })
        # All three runs have the same raw findings.
        for o in outputs:
            assert o["n_findings"] == 4
        # The relevant_titles ordering MUST differ across seeds (otherwise
        # the seed isn't doing anything).
        assert (outputs[0]["relevant_titles"] !=
                outputs[1]["relevant_titles"]
                or
                outputs[1]["relevant_titles"] !=
                outputs[2]["relevant_titles"])

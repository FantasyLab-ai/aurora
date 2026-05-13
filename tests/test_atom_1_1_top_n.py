"""
Atom 1.1: Top-N truncation fixes — regression net.

Locks in three behaviours:
  1. _build_anomalies emits ALL anomalies (no [:10] cap).
  2. _build_motifs emits ALL motifs (no [:10] cap).
  3. _build_findings filters by severity FIRST, sorts, THEN truncates.
     n_total_at_this_severity is recorded so the narrative reads honestly.

The bug being fixed: on a dataset that produces 47 anomalies where
top-by-score happen to be info-level, the legacy code would:
  - emit only 10 anomalies (cap)
  - then in findings synthesis, take anomalies[:5] then filter for
    crit/warn → could yield 0 findings even when 7 critical exist further
    down the list.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.state_builder import (    # noqa: E402
    _build_anomalies, _build_motifs, _build_findings,
)


# ============================================================
# _build_anomalies — no [:10] cap
# ============================================================

class TestAnomaliesNoCap:

    def test_emits_all_50_anomalies(self):
        # Synthesize 50 anomaly points with descending scores.
        points = [
            {
                "rank": i + 1,
                "row_id": i * 10,
                "score": 6.0 - i * 0.05,
                "top_feature_drivers": [
                    {"feature": "x", "robust_z": 4.0 - i * 0.03,
                     "direction": "high"},
                ],
            }
            for i in range(50)
        ]
        deep = {"extensions": {"anomaly_attribution": {"points": points}}}
        out = _build_anomalies(deep)
        assert len(out) == 50, "emit ALL anomalies; do not cap at 10"

    def test_emits_15_when_15_present(self):
        points = [{"rank": i+1, "row_id": i, "score": 4.0,
                   "top_feature_drivers": [{"feature":"x","robust_z":2.0,"direction":"high"}]}
                  for i in range(15)]
        deep = {"extensions": {"anomaly_attribution": {"points": points}}}
        assert len(_build_anomalies(deep)) == 15

    def test_emits_3_when_3_present(self):
        points = [{"rank":i+1, "row_id":i, "score":4.0,
                   "top_feature_drivers":[{"feature":"x","robust_z":2.0,"direction":"high"}]}
                  for i in range(3)]
        deep = {"extensions": {"anomaly_attribution": {"points": points}}}
        assert len(_build_anomalies(deep)) == 3

    def test_severity_split_correct_at_scale(self):
        # Engineer scores so we get exactly 7 crit, 12 warn, 31 info.
        points = []
        for i in range(7):
            points.append({"rank":i+1, "row_id":i, "score":6.0,
                           "top_feature_drivers":[{"feature":"x","robust_z":4.0,"direction":"high"}]})
        for i in range(7, 19):
            points.append({"rank":i+1, "row_id":i, "score":3.5,
                           "top_feature_drivers":[{"feature":"x","robust_z":2.5,"direction":"high"}]})
        for i in range(19, 50):
            points.append({"rank":i+1, "row_id":i, "score":1.5,
                           "top_feature_drivers":[{"feature":"x","robust_z":1.5,"direction":"high"}]})
        deep = {"extensions": {"anomaly_attribution": {"points": points}}}
        out = _build_anomalies(deep)
        n_crit = sum(1 for a in out if a["severity"] == "crit")
        n_warn = sum(1 for a in out if a["severity"] == "warn")
        n_info = sum(1 for a in out if a["severity"] == "info")
        assert n_crit == 7
        assert n_warn == 12
        assert n_info == 31


# ============================================================
# _build_motifs — no [:10] cap
# ============================================================

class TestMotifsNoCap:

    def test_emits_all_25_motifs(self):
        motifs = [
            {"kind": "high_volatility", "title": f"M{i}", "score": 1.0,
             "details": {"col": "x", "std": 0.5}}
            for i in range(25)
        ]
        # Pass deep=None so the matrix-profile enrichment is skipped (it
        # adds extra motifs that would confuse the count assertion).
        out = _build_motifs({"motifs": motifs}, deep=None,
                             resolved=None, structure=None)
        # Just confirm the original 25 all came through.
        original_kinds = [m for m in out if m["kind"] == "high_volatility"]
        assert len(original_kinds) == 25, "emit ALL motifs; do not cap at 10"


# ============================================================
# _build_findings — filter-then-sort-then-truncate
# ============================================================

class TestFindingsFilterFirstSortThenTruncate:

    def test_does_not_skip_crit_when_top5_anomalies_are_info(self):
        # The bug: legacy code did anomalies[:5] then severity filter.
        # If the top 5 (by sort order) were info, the findings list would
        # have ZERO anomaly findings — even when 10 critical exist further
        # down. New behaviour: filter → sort → truncate.
        anomalies = []
        # Top 5 by source order: info-level
        for i in range(5):
            anomalies.append({"severity": "info", "score": 8.0,
                               "column": "noise", "when": f"row {i}",
                               "description": "info-level"})
        # Next 10: critical (the ones we MUST surface)
        for i in range(5, 15):
            anomalies.append({"severity": "crit", "score": 6.0,
                               "column": "real_signal", "when": f"row {i}",
                               "description": "critical anomaly"})
        findings = _build_findings(
            anomalies, forecast={}, regimes=[], causal={},
            physics={}, motifs=[], report=None, orchestrator=None,
        )
        anomaly_findings = [f for f in findings
                             if f["title"].startswith("Anomaly")]
        assert len(anomaly_findings) == 5, \
            "must surface 5 crit anomaly findings, not 0"
        # All 5 must be crit/warn — never info.
        for f in anomaly_findings:
            assert f["severity"] in ("crit", "warn")

    def test_records_n_total_at_this_severity(self):
        anomalies = []
        # 47 critical anomalies in a row.
        for i in range(47):
            anomalies.append({"severity": "crit", "score": 6.0,
                               "column": "x", "when": f"row {i}",
                               "description": "crit"})
        findings = _build_findings(
            anomalies, forecast={}, regimes=[], causal={}, physics={},
            motifs=[], report=None, orchestrator=None,
        )
        anomaly_findings = [f for f in findings
                             if f["title"].startswith("Anomaly")]
        assert len(anomaly_findings) == 5
        for f in anomaly_findings:
            assert f["n_total_at_this_severity"] == 47
            assert f["n_total_anomalies"] == 47

    def test_critical_sorted_before_warn(self):
        # 3 warn (high score) + 3 crit (lower score). Crit must surface first.
        anomalies = [
            {"severity": "warn", "score": 4.5, "column": "a", "when": "row 0",
             "description": ""},
            {"severity": "warn", "score": 4.5, "column": "b", "when": "row 1",
             "description": ""},
            {"severity": "warn", "score": 4.5, "column": "c", "when": "row 2",
             "description": ""},
            {"severity": "crit", "score": 5.5, "column": "d", "when": "row 3",
             "description": ""},
            {"severity": "crit", "score": 5.5, "column": "e", "when": "row 4",
             "description": ""},
            {"severity": "crit", "score": 5.5, "column": "f", "when": "row 5",
             "description": ""},
        ]
        findings = _build_findings(
            anomalies, forecast={}, regimes=[], causal={}, physics={},
            motifs=[], report=None, orchestrator=None,
        )
        anomaly_findings = [f for f in findings
                             if f["title"].startswith("Anomaly")]
        # First 3 should be crit, last 2 should be warn.
        sevs = [f["severity"] for f in anomaly_findings]
        assert sevs[:3] == ["crit", "crit", "crit"]
        assert all(s == "warn" for s in sevs[3:])

    def test_score_breaks_ties_within_severity(self):
        # 5 crit anomalies with scores 5.5, 5.0, 5.5, 5.0, 6.0.
        # After sort, top 5 should be ordered by score descending.
        anomalies = [
            {"severity": "crit", "score": s, "column": f"c{i}",
             "when": f"row {i}", "description": ""}
            for i, s in enumerate([5.5, 5.0, 5.5, 5.0, 6.0])
        ]
        findings = _build_findings(
            anomalies, forecast={}, regimes=[], causal={}, physics={},
            motifs=[], report=None, orchestrator=None,
        )
        anomaly_findings = [f for f in findings
                             if f["title"].startswith("Anomaly")]
        scores_in_order = [a.get("score") for a in anomalies
                            if any(a["column"] in f["title"]
                                    for f in anomaly_findings)]
        # The first finding's "when" should reference the highest-score row.
        # Top score is row 4 (score 6.0).
        assert "row 4" in anomaly_findings[0]["title"]


# ============================================================
# Narrative honesty — counts reflect ALL anomalies
# ============================================================

class TestNarrativeHonesty:

    def test_copilot_fallback_reports_real_count(self):
        from fantasyai.aurora.state_builder import _build_copilot
        anomalies = [{"severity": "crit", "description": f"crit_{i}"}
                      for i in range(47)]
        copilot = _build_copilot(
            narrative=None, report=None, anomalies=anomalies,
            forecast={}, regimes=[], physics={},
        )
        body = copilot["messages"][0]["body"]
        assert "47" in body
        # When n>5, the wording switches to "the most significant are surfaced".
        assert ("most significant" in body) or ("47 anomalies" in body)

    def test_copilot_fallback_small_count_reads_clean(self):
        from fantasyai.aurora.state_builder import _build_copilot
        anomalies = [{"severity": "crit", "description": "x"} for _ in range(2)]
        copilot = _build_copilot(
            narrative=None, report=None, anomalies=anomalies,
            forecast={}, regimes=[], physics={},
        )
        body = copilot["messages"][0]["body"]
        # Small-N branch — don't say "most significant are surfaced".
        assert "2 anomalies" in body
        assert "most significant" not in body

"""
Audit fixes — pins the two correctness invariants the user flagged:

  1. Domain pills exist for sports / logistics / economics, and
     domain_config knows about them.
  2. Anomaly counts surfaced to the UI (overview tile, copilot narrative,
     anomBadge) reflect the TRUE total, not the runner's top_k=10 detail
     cap. The detailed `points[]` stays at top_k for performance — but
     the headline counts are honest.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

# The runner's anomaly_attribution module lives in the parent Aurora
# repo (the runner's source-of-truth) — the worktree's fantasyai package
# shadows it, so we load by absolute path via importlib.util.
import importlib.util

_RUNNER_REPO = Path("C:/Users/bgrut/Desktop/Aurora_QIE")
_runner_path = _RUNNER_REPO / "fantasyai" / "aurora" / "math" / "anomaly_attribution.py"
_runner_available = _runner_path.exists()


def _load_runner_anomaly_attribution():
    spec = importlib.util.spec_from_file_location(
        "_runner_anomaly_attribution", _runner_path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner_only = pytest.mark.skipif(
    not _runner_available,
    reason="runner-side anomaly_attribution.py only exists in the parent repo",
)


# ============================================================
# Fix 1 — domain pills + configs
# ============================================================

class TestDomainConfigs:

    def test_sports_logistics_economics_registered(self):
        from fantasyai.aurora.domain_config import (
            list_domains, get_domain_config,
        )
        ids = {d["id"] for d in list_domains()}
        for new in ("sports", "logistics", "economics"):
            assert new in ids, f"domain {new} not registered"

    def test_each_has_real_metadata(self):
        from fantasyai.aurora.domain_config import get_domain_config
        for new in ("sports", "logistics", "economics"):
            cfg = get_domain_config(new)
            assert cfg.id == new
            assert cfg.display_name
            assert cfg.one_liner
            assert isinstance(cfg.suggested_questions, list)
            assert len(cfg.suggested_questions) >= 2
            assert isinstance(cfg.terminology, dict)

    def test_medical_config_now_present(self):
        # Pre-existing bug: a MEDICAL pill existed in the topbar but no
        # config — this fix added one.
        from fantasyai.aurora.domain_config import get_domain_config
        cfg = get_domain_config("medical")
        assert cfg.id == "medical"
        assert cfg.display_name == "Medical"

    def test_apply_domain_overrides_terminology(self):
        from fantasyai.aurora.domain_config import apply_domain_to_state
        state = {
            "findings": [{"title": "Anomaly in fatigue at row 50",
                            "description": "fatigue rising"}],
        }
        apply_domain_to_state(state, "sports")
        # "Anomaly" rewrites to "Outlier game" per the SPORTS terminology dict.
        assert "Outlier game" in state["findings"][0]["title"]


class TestDomainPillsHTML:

    def test_topbar_has_three_new_pills(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        for label in ("ECONOMICS", "SPORTS", "LOGISTICS"):
            assert f">{label}<" in html, f"domain pill {label} missing from topbar"

    def test_label_to_id_map_includes_new_domains(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        for k in ("'economics':", "'sports':", "'logistics':", "'medical':"):
            assert k in html, f"labelToId map missing entry: {k}"

    def test_domain_row_supports_wrap(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        # 11 pills now — need flex-wrap to avoid overflow.
        assert "flex-wrap: wrap" in html or "flex-wrap:wrap" in html
        assert ".domain-row" in html


# ============================================================
# Fix 2 — anomaly counts
# ============================================================

@runner_only
class TestAnomalyAttributionCounts:
    """Runner-side: explain_anomalies must report n_total / n_critical /
    n_warn / n_info that reflect the TRUE count, not top_k."""

    def test_n_total_counts_all_above_threshold(self):
        # Build a frame engineered to produce > top_k anomalies.
        rng = np.random.RandomState(42)
        n = 500
        values = rng.normal(0, 1, n)
        # Inject 25 anomalies with clearly elevated z-energy.
        for i in range(25):
            values[i * 5] = 8.0       # well above sig threshold
        df = pd.DataFrame({"x": values, "y": rng.normal(0, 1, n)})
        scores = pd.Series(np.abs(values), index=df.index)
        explain_anomalies = _load_runner_anomaly_attribution().explain_anomalies
        out = explain_anomalies(df, anomaly_scores=scores,
                                  top_k=10, significance_threshold=3.0)
        # Detail array bound to top_k.
        assert len(out["points"]) <= 10
        # Honest count surfaces ALL above-threshold rows.
        assert out["n_total"] >= 20, f"n_total only {out['n_total']}"
        # Detail count separately reported.
        assert out["n_points_returned"] <= 10
        # The headline counts must be greater-than-or-equal to the detail count.
        assert out["n_total"] >= out["n_points_returned"]

    def test_severity_buckets_sum_to_total(self):
        rng = np.random.RandomState(0)
        n = 300
        values = rng.normal(0, 1, n)
        for i in range(15):
            values[i] = 6.0
        for i in range(15, 30):
            values[i] = 4.0
        df = pd.DataFrame({"x": values, "y": rng.normal(0, 1, n)})
        scores = pd.Series(np.abs(values), index=df.index)
        explain_anomalies = _load_runner_anomaly_attribution().explain_anomalies
        out = explain_anomalies(df, anomaly_scores=scores, top_k=10)
        assert out["n_critical"] + out["n_warn"] + out["n_info"] == out["n_total"]

    def test_top_k_returned_even_when_few_real_anomalies(self):
        # Even when n_total is tiny, points may still hold up to top_k
        # entries (the highest-scoring rows regardless of threshold).
        rng = np.random.RandomState(0)
        df = pd.DataFrame({
            "x": rng.normal(0, 1, 100),
            "y": rng.normal(0, 1, 100),
        })
        scores = pd.Series(np.abs(df["x"]), index=df.index)
        explain_anomalies = _load_runner_anomaly_attribution().explain_anomalies
        out = explain_anomalies(df, anomaly_scores=scores, top_k=10)
        # No engineered anomalies → n_total may be small.
        assert "n_total" in out
        # But we still return up to top_k detailed entries for inspection.
        assert isinstance(out["points"], list)


class TestStateBuilderHonestCounts:
    """state_builder must propagate the runner's n_total to the overview
    tile + the anomalies block, NOT len(points)."""

    def _staged_deep(self, n_total=47, n_returned=10):
        """Synthesise a deep_math.json-shaped dict where the runner has
        already truncated points[] to n_returned but recorded n_total."""
        return {
            "extensions": {
                "anomaly_attribution": {
                    "note": "ok", "top_k": 10,
                    "significance_threshold": 3.0,
                    "n_total": n_total,
                    "n_critical": 5,
                    "n_warn": 12,
                    "n_info": n_total - 5 - 12,
                    "n_points_returned": n_returned,
                    "points": [
                        {"rank": i+1, "row_id": str(i),
                         "score": 6.0 - 0.1 * i,
                         "top_feature_drivers": [
                             {"feature": "x", "robust_z": 4.0,
                              "direction": "high"},
                         ]}
                        for i in range(n_returned)
                    ],
                },
            },
            "target_col": "x",
        }

    def test_build_anomaly_counts_uses_n_total(self):
        from fantasyai.aurora.state_builder import (
            _build_anomalies, _build_anomaly_counts,
        )
        deep = self._staged_deep(n_total=47, n_returned=10)
        anomalies = _build_anomalies(deep)
        counts = _build_anomaly_counts(deep, anomalies)
        # Detailed array is bound to n_returned.
        assert len(anomalies) == 10
        # But the honest count is the runner's n_total.
        assert counts["n_total"] == 47
        assert counts["n_critical"] == 5
        assert counts["n_warn"] == 12
        assert counts["n_detailed"] == 10

    def test_overview_uses_honest_counts(self):
        from fantasyai.aurora.state_builder import (
            _build_anomalies, _build_overview,
        )
        deep = self._staged_deep(n_total=47, n_returned=10)
        anomalies = _build_anomalies(deep)
        overview = _build_overview(
            structure={}, anomalies=anomalies, regimes=[], motifs=[],
            forecast={}, physics={}, report=None, deep=deep,
        )
        # Headline: TRUE total, not top-10.
        assert overview["anomalies_count"] == 47
        assert overview["critical_anomalies"] == 5
        # Detail count separately surfaced so the UI can say
        # "showing top 10 of 47".
        assert overview["anomalies_detailed"] == 10

    def test_overview_falls_back_for_legacy_artifacts(self):
        """Old runs that predate the n_total field — overview still works."""
        from fantasyai.aurora.state_builder import (
            _build_anomalies, _build_overview,
        )
        # Legacy-shaped: only points[], no n_total.
        legacy_deep = {
            "extensions": {
                "anomaly_attribution": {
                    "note": "ok",
                    "points": [
                        {"rank": i+1, "row_id": str(i),
                         "score": 6.0,
                         "top_feature_drivers": [{"feature": "x",
                                                   "robust_z": 4.0,
                                                   "direction": "high"}]}
                        for i in range(8)
                    ],
                },
            },
        }
        anomalies = _build_anomalies(legacy_deep)
        overview = _build_overview(
            structure={}, anomalies=anomalies, regimes=[], motifs=[],
            forecast={}, physics={}, report=None, deep=legacy_deep,
        )
        # Falls back to len(anomalies) — no crash, honest about what we
        # know (8 points, all crit by score).
        assert overview["anomalies_count"] == 8

    def test_copilot_narrative_uses_true_count(self):
        from fantasyai.aurora.state_builder import (
            _build_anomalies, _build_copilot, _build_anomaly_counts,
        )
        deep = self._staged_deep(n_total=47, n_returned=10)
        anomalies = _build_anomalies(deep)
        # Stash counts on the report (mirrors what build_state does).
        report = {"_anomaly_counts": _build_anomaly_counts(deep, anomalies)}
        copilot = _build_copilot(
            narrative=None, report=report, anomalies=anomalies,
            forecast={}, regimes=[], physics={},
        )
        body = copilot["messages"][0]["body"]
        # The TRUE count must appear in the narrative.
        assert "47" in body, f"copilot narrative missing true count: {body}"
        # And the honest cap disclosure when detail < total.
        assert "top 10" in body or "driver attribution" in body

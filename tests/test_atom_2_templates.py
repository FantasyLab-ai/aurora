"""
Atom 2.1 + 2.4: economics + sports templates load, validate, identify
realistic data.
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


# ============================================================
# Schema-level checks — JSON parses + required fields present
# ============================================================

REQUIRED_TOP_LEVEL = {
    "id", "version", "display_name", "domain_tag", "description",
    "identification", "topology_skeleton", "characteristic_patterns",
    "vocabulary", "action_library", "constraints",
}
REQUIRED_IDENT = {
    "column_keywords", "cadence_hints", "domain_tags", "min_keyword_hits",
}
REQUIRED_TOPOLOGY = {"entities", "expected_relationships"}
REQUIRED_VOCAB = {
    "anomaly_term", "regime_term", "pattern_term",
    "action_term", "breach_term", "advisory_term",
}


def _load_template(template_id: str) -> dict:
    p = (ROOT / "fantasyai" / "aurora" / "system_model" /
         "templates" / template_id / "v1.json")
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.mark.parametrize("template_id", ["economics", "sports", "logistics"])
class TestNewTemplatesShape:

    def test_loads(self, template_id):
        t = _load_template(template_id)
        assert t["id"] == template_id

    def test_top_level_fields(self, template_id):
        t = _load_template(template_id)
        missing = REQUIRED_TOP_LEVEL - set(t.keys())
        assert not missing, f"missing: {missing}"

    def test_identification_complete(self, template_id):
        t = _load_template(template_id)
        ident = t["identification"]
        missing = REQUIRED_IDENT - set(ident.keys())
        assert not missing, f"identification missing: {missing}"
        assert isinstance(ident["column_keywords"], list)
        assert len(ident["column_keywords"]) >= 5

    def test_topology(self, template_id):
        t = _load_template(template_id)
        topo = t["topology_skeleton"]
        missing = REQUIRED_TOPOLOGY - set(topo.keys())
        assert not missing
        assert len(topo["entities"]) >= 4
        assert len(topo["expected_relationships"]) >= 2
        for e in topo["entities"]:
            assert {"id", "label", "role"}.issubset(e.keys())
        for r in topo["expected_relationships"]:
            assert {"id", "source", "target", "kind"}.issubset(r.keys())

    def test_patterns_have_detection(self, template_id):
        t = _load_template(template_id)
        for p in t["characteristic_patterns"]:
            assert "id" in p and "name" in p and "detection_rule" in p

    def test_actions_have_metadata(self, template_id):
        t = _load_template(template_id)
        for a in t["action_library"]:
            assert "id" in a and "label" in a and "description" in a
            assert 0.0 <= float(a.get("base_rate_success", 0)) <= 1.0
            assert a.get("confidence_band_required") in ("low", "medium", "high")

    def test_vocabulary_complete(self, template_id):
        t = _load_template(template_id)
        v = t["vocabulary"]
        missing = REQUIRED_VOCAB - set(v.keys())
        assert not missing

    def test_seed_examples_present(self, template_id):
        t = _load_template(template_id)
        # All new templates ship with seed examples for Workstream 3.
        assert isinstance(t.get("seed_examples"), list)
        assert len(t["seed_examples"]) >= 3


# ============================================================
# Identification — fires on realistic FRED-like + NBA-like data
# ============================================================

class TestEconomicsIdentification:

    def test_fires_on_fred_style_columns(self):
        from fantasyai.aurora.system_model.instantiator import (
            instantiate_system_model, identify_domain,
        )
        # Realistic FRED-like state: monthly UNRATE + CPIAUCSL + FEDFUNDS +
        # T10Y3M + INDPRO + UMCSENT.
        state = {
            "structure": {
                "available": True,
                "time_axis": True,
                "time_col": "date",
                "cadence": "30d",
                "columns": [
                    {"name": "date",         "kind": "time", "fill_pct": 100.0},
                    {"name": "unrate",       "kind": "n",    "fill_pct": 100.0},
                    {"name": "cpiaucsl",     "kind": "n",    "fill_pct": 100.0},
                    {"name": "fedfunds",     "kind": "n",    "fill_pct": 100.0},
                    {"name": "t10y3m",       "kind": "n",    "fill_pct": 100.0},
                    {"name": "indpro",       "kind": "n",    "fill_pct": 100.0},
                    {"name": "umcsent",      "kind": "n",    "fill_pct": 100.0},
                ],
            },
            "anomalies": [], "regimes": [], "motifs": [], "findings": [],
            "physics": {"available": False},
            "forecast": {"available": False},
            "causal": {"available": False},
            "overview": {"available": True},
            "dataset": {"name": "fred_macro_panel.csv", "path": ""},
        }
        # PHASE A1 (v1.2.x): the economics template DOES top the ranking for
        # FRED-style columns (high keyword density on unrate/cpiaucsl/etc.),
        # but the template's slot labels are semantic (leading, lagging,
        # inflation, ...) and do not match raw FRED column codes by
        # substring. With slot-match-ratio < 0.30 AND score < 0.40 the
        # instantiator falls back to a generic column-name graph so the
        # user sees their actual indicators as nodes (better UX than a
        # half-empty template). The test verifies BOTH facts:
        #   (a) the ranker still identifies economics as the best match,
        #       so the system *knows* this is economic data;
        #   (b) the instantiated topology falls back to column-name
        #       entities (semantic correctness over template-firing).
        t, ranked = identify_domain(state)
        assert ranked and ranked[0].get("template_id") == "economics", \
            f"economics should top the ranking on FRED columns, got {ranked[0] if ranked else None}"
        sm = instantiate_system_model(state)
        sm_dict = sm.to_dict() if hasattr(sm, "to_dict") else sm
        # Either the template fires (legacy behavior) or it falls back to
        # the column-name graph (new behavior). Both are acceptable; what
        # the test FORBIDS is identifying as a wrong template (e.g., enviro).
        assert sm_dict.get("template_id") in ("economics", "(none)"), \
            f"economics or fallback expected, got {sm_dict.get('template_id')}"

    def test_does_not_fire_on_unrelated_columns(self):
        from fantasyai.aurora.system_model.instantiator import (
            instantiate_system_model,
        )
        # Pure environmental data — must NOT identify as economics.
        state = {
            "structure": {
                "available": True,
                "time_axis": True,
                "time_col": "ts",
                "columns": [
                    {"name": "ts",            "kind": "time", "fill_pct": 100.0},
                    {"name": "wave_height",   "kind": "n",    "fill_pct": 99.0},
                    {"name": "wind_speed",    "kind": "n",    "fill_pct": 99.0},
                    {"name": "sea_surf_temp", "kind": "n",    "fill_pct": 99.0},
                ],
            },
            "anomalies": [], "regimes": [], "motifs": [], "findings": [],
            "physics": {"available": False},
            "forecast": {"available": False},
            "causal": {"available": False},
            "overview": {"available": True},
            "dataset": {"name": "buoy.csv", "path": ""},
        }
        sm = instantiate_system_model(state)
        sm_dict = sm.to_dict() if hasattr(sm, "to_dict") else sm
        assert sm_dict.get("template_id") != "economics"


class TestLogisticsIdentification:

    def test_fires_on_nyc_taxi_columns(self):
        from fantasyai.aurora.system_model.instantiator import (
            instantiate_system_model, identify_domain,
        )
        # NYC TLC trip-style state — pu_location_id + do_location_id.
        state = {
            "structure": {
                "available": True,
                "time_axis": True,
                "time_col": "tpep_pickup_datetime",
                "columns": [
                    {"name": "tpep_pickup_datetime", "kind": "time", "fill_pct": 100.0},
                    {"name": "pu_location_id",       "kind": "cat",  "fill_pct": 100.0},
                    {"name": "do_location_id",       "kind": "cat",  "fill_pct": 100.0},
                    {"name": "fare_amount",          "kind": "n",    "fill_pct": 100.0},
                    {"name": "trip_distance",        "kind": "n",    "fill_pct": 100.0},
                    {"name": "passengers",           "kind": "n",    "fill_pct": 100.0},
                ],
            },
            "anomalies": [], "regimes": [], "motifs": [], "findings": [],
            "physics": {"available": False},
            "forecast": {"available": False},
            "causal": {"available": False},
            "overview": {"available": True},
            "dataset": {"name": "nyc_taxi.csv", "path": ""},
        }
        # PHASE A1 (v1.2.x): see fred_style test for rationale. Identifier
        # still ranks logistics first; instantiator may fall back when
        # the template's semantic slot names don't substring-match the
        # NYC TLC raw column codes.
        t, ranked = identify_domain(state)
        assert ranked and ranked[0].get("template_id") == "logistics", \
            f"logistics should top the ranking, got {ranked[0] if ranked else None}"
        sm = instantiate_system_model(state)
        sm_dict = sm.to_dict() if hasattr(sm, "to_dict") else sm
        assert sm_dict.get("template_id") in ("logistics", "(none)"), \
            f"logistics or fallback expected, got {sm_dict.get('template_id')}"

    def test_fires_on_air_segment_columns(self):
        from fantasyai.aurora.system_model.instantiator import (
            instantiate_system_model, identify_domain,
        )
        state = {
            "structure": {
                "available": True,
                "time_axis": False,
                "columns": [
                    {"name": "year",        "kind": "n",   "fill_pct": 100.0},
                    {"name": "month",       "kind": "n",   "fill_pct": 100.0},
                    {"name": "origin",      "kind": "cat", "fill_pct": 100.0},
                    {"name": "destination", "kind": "cat", "fill_pct": 100.0},
                    {"name": "passengers",  "kind": "n",   "fill_pct": 100.0},
                    {"name": "freight_kg",  "kind": "n",   "fill_pct": 100.0},
                ],
            },
            "anomalies": [], "regimes": [], "motifs": [], "findings": [],
            "physics": {"available": False},
            "forecast": {"available": False},
            "causal": {"available": False},
            "overview": {"available": True},
            "dataset": {"name": "bts_air_segments.csv", "path": ""},
        }
        # PHASE A1 (v1.2.x): air-segment data has 0 slot-matches against
        # logistics template (template uses 'origin_node', 'sink_node',
        # 'shipment', etc.; raw columns are origin, destination,
        # passengers, freight_kg). Falls back to column-name graph.
        t, ranked = identify_domain(state)
        assert ranked and ranked[0].get("template_id") == "logistics", \
            f"logistics should top the ranking, got {ranked[0] if ranked else None}"
        sm = instantiate_system_model(state)
        sm_dict = sm.to_dict() if hasattr(sm, "to_dict") else sm
        assert sm_dict.get("template_id") in ("logistics", "(none)"), \
            f"logistics or fallback expected, got {sm_dict.get('template_id')}"


class TestSportsIdentification:

    def test_fires_on_nba_player_game_columns(self):
        from fantasyai.aurora.system_model.instantiator import (
            instantiate_system_model,
        )
        # Realistic NBA player-game shape (basketball-reference-like).
        state = {
            "structure": {
                "available": True,
                "time_axis": True,
                "time_col": "game_date",
                "cadence": "1d",
                "columns": [
                    {"name": "player",     "kind": "cat",  "fill_pct": 100.0},
                    {"name": "team",       "kind": "cat",  "fill_pct": 100.0},
                    {"name": "opponent",   "kind": "cat",  "fill_pct": 100.0},
                    {"name": "game_date",  "kind": "time", "fill_pct": 100.0},
                    {"name": "minutes",    "kind": "n",    "fill_pct": 99.0},
                    {"name": "pts",        "kind": "n",    "fill_pct": 99.0},
                    {"name": "ast",        "kind": "n",    "fill_pct": 99.0},
                    {"name": "reb",        "kind": "n",    "fill_pct": 99.0},
                    {"name": "fg_pct",     "kind": "n",    "fill_pct": 99.0},
                    {"name": "plus_minus", "kind": "n",    "fill_pct": 99.0},
                ],
            },
            "anomalies": [], "regimes": [], "motifs": [], "findings": [],
            "physics": {"available": False},
            "forecast": {"available": False},
            "causal": {"available": False},
            "overview": {"available": True},
            "dataset": {"name": "nba_player_game.csv", "path": ""},
        }
        sm = instantiate_system_model(state)
        sm_dict = sm.to_dict() if hasattr(sm, "to_dict") else sm
        assert sm_dict.get("template_id") == "sports"

    def test_subdomain_recorded(self):
        # The v1.json carries subdomain: "basketball.nba.player_game"; we
        # don't require the instantiator to expose it on the SystemModel
        # right now — but the JSON itself must declare it.
        t = _load_template("sports")
        assert t.get("subdomain") == "basketball.nba.player_game"
        assert "not_in_scope_v1" in t


# ============================================================
# Sanity: existing 7 templates still load (no schema regression)
# ============================================================

class TestExistingTemplatesUnaffected:

    @pytest.mark.parametrize("tid", [
        "enviro", "finance", "industrial", "medical",
        "ops", "research", "seismology",
    ])
    def test_existing_template_still_loads(self, tid):
        t = _load_template(tid)
        assert t["id"] == tid

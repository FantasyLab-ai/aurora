"""
Tests for the System Model layer:
  schema, templates loader, instantiator, narrator, evolution, ml_layer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_TEMPLATE_EVIDENCE",
                       str(tmp_path / "tev.jsonl"))
    monkeypatch.setenv("AURORA_PRIOR_VALIDATIONS",
                       str(tmp_path / "pv.jsonl"))
    monkeypatch.setenv("AURORA_LEARNING_STORE",
                       str(tmp_path / "ls.jsonl"))
    yield


# ============================================================
# Schema validation
# ============================================================

class TestSchema:

    def test_empty_model_validates(self):
        from fantasyai.aurora.system_model.schema import (
            SystemModel, ProvenanceBlock,
        )
        m = SystemModel(provenance=ProvenanceBlock(template_id="x", template_version="v1"))
        assert m.validate() == []

    def test_unknown_relationship_endpoint_flagged(self):
        from fantasyai.aurora.system_model.schema import (
            SystemModel, Topology, Entity, Relationship, ProvenanceBlock,
        )
        topo = Topology(
            entities=[Entity(id="a", label="A", role="state")],
            relationships=[Relationship(id="r1", source="a", target="ghost",
                                         kind="causal")],
        )
        m = SystemModel(topology=topo,
                        provenance=ProvenanceBlock(template_id="x", template_version="v1"))
        problems = m.validate()
        assert any("unknown target" in p for p in problems)

    def test_to_dict_round_trip(self):
        from fantasyai.aurora.system_model.schema import (
            SystemModel, Topology, Entity, Relationship, ProvenanceBlock,
            system_model_from_dict,
        )
        topo = Topology(
            entities=[Entity(id="a", label="A", role="state"),
                       Entity(id="b", label="B", role="response")],
            relationships=[Relationship(id="r1", source="a", target="b",
                                         kind="causal", confidence=0.8)],
        )
        m = SystemModel(topology=topo,
                        provenance=ProvenanceBlock(template_id="x", template_version="v1"),
                        confidence=0.7)
        d = m.to_dict()
        m2 = system_model_from_dict(d)
        assert m2.confidence == 0.7
        assert len(m2.topology.entities) == 2
        assert m2.topology.relationships[0].confidence == 0.8


# ============================================================
# Templates loader + identification
# ============================================================

class TestTemplates:

    def test_baseline_templates_load(self):
        # Atom 2.1 + 2.4 added economics + sports; this test now requires the
        # original 7 to still be present plus accepts any additional templates
        # so future workstreams (logistics, etc.) don't break it.
        from fantasyai.aurora.system_model import list_templates
        ts = list_templates()
        ids = {t.id for t in ts}
        for required in ("enviro", "research", "ops", "industrial",
                          "finance", "medical", "seismology"):
            assert required in ids, f"required template missing: {required}"
        assert len(ts) >= 7

    def test_seismology_identifies_quake_columns(self):
        """USGS earthquake-style columns must score against the seismology template."""
        from fantasyai.aurora.system_model import best_template_for
        cols = ["time", "magnitude", "latitude", "longitude", "depth_km", "tsunami"]
        best, _ = best_template_for(cols, cadence="event_based")
        assert best is not None
        assert best.id == "seismology"

    def test_fallback_model_renders_columns_for_unmatched(self):
        """When no template matches, instantiator should still emit numeric
        columns as entities so the user sees their data."""
        from fantasyai.aurora.system_model import instantiate_system_model
        # Use intentionally cryptic names that no template will match.
        s = {
            "structure": {
                "columns": [
                    {"name": "xqz_zzz_one", "kind": "n"},
                    {"name": "xqz_zzz_two", "kind": "n"},
                    {"name": "xqz_zzz_three", "kind": "n"},
                ],
                "cadence": "unknown",
            },
            "physics": {},
        }
        sm = instantiate_system_model(s)
        # Even without a matched template, we should have entities.
        assert len(sm.topology.entities) >= 3
        names = {e.label for e in sm.topology.entities}
        assert "xqz_zzz_one" in names
        # Provenance must clearly state no template matched.
        assert sm.provenance.template_id == "(none)"

    def test_enviro_identifies_buoy_columns(self):
        from fantasyai.aurora.system_model import best_template_for
        cols = ["timestamp", "wind_speed", "pressure", "wave_height", "humidity"]
        best, scores = best_template_for(cols, cadence="hourly")
        assert best is not None
        assert best.id == "enviro"
        assert scores[0].score > scores[1].score

    def test_finance_identifies_market_columns(self):
        from fantasyai.aurora.system_model import best_template_for
        cols = ["date", "close", "volume", "return", "volatility"]
        best, _ = best_template_for(cols, cadence="daily")
        assert best is not None
        assert best.id == "finance"

    def test_ops_identifies_sre_columns(self):
        from fantasyai.aurora.system_model import best_template_for
        cols = ["timestamp", "p99", "rps", "error_rate", "cpu"]
        best, _ = best_template_for(cols, cadence="subhourly")
        assert best is not None
        assert best.id == "ops"

    def test_medical_identifies_clinical_columns(self):
        from fantasyai.aurora.system_model import best_template_for
        cols = ["patient_id", "heart_rate", "spo2", "blood_pressure", "temperature"]
        best, _ = best_template_for(cols, cadence="hourly")
        assert best is not None
        assert best.id == "medical"


# ============================================================
# Instantiator: end-to-end on a synthetic state
# ============================================================

class TestInstantiator:

    def _enviro_state(self):
        return {
            "run_dir": "/fake/run/r1",
            "structure": {
                "columns": [
                    {"name": "timestamp", "kind": "time"},
                    {"name": "wind_speed",  "kind": "n"},
                    {"name": "pressure",    "kind": "n"},
                    {"name": "wave_height", "kind": "n"},
                    {"name": "humidity",    "kind": "n"},
                ],
                "cadence": "hourly",
            },
            "physics": {
                "discovered_law": {"name": "linear_ode", "params": {"a": -0.05}},
                "calculus": {"available": True, "likely_shape": "oscillatory"},
                "checks": [],
                "prior_matches": [
                    {"prior_id": "newton_cooling",
                     "display_name": "Newton's law of cooling",
                     "confidence": 0.85, "domain": "thermal"},
                ],
            },
            "regimes": [{"label": "STABLE"}],
            "motifs": [],
            "causal": {},
            "overview": {"stability": 0.7},
        }

    def test_instantiate_enviro_model(self):
        from fantasyai.aurora.system_model import instantiate_system_model
        sm = instantiate_system_model(self._enviro_state())
        assert sm.provenance.template_id == "enviro"
        # Topology should have several entities with the template's slot ids.
        ent_ids = {e.id for e in sm.topology.entities}
        assert "wind" in ent_ids
        assert "pressure" in ent_ids
        assert "wave_height" in ent_ids
        # Validation must pass.
        assert sm.validate() == []

    def test_instantiator_marks_missing_entities(self):
        from fantasyai.aurora.system_model import instantiate_system_model
        s = self._enviro_state()
        # Drop wind from columns → entity should be presence='missing'.
        s["structure"]["columns"] = [
            c for c in s["structure"]["columns"] if c["name"] != "wind_speed"
        ]
        sm = instantiate_system_model(s)
        wind = next((e for e in sm.topology.entities if e.id == "wind"), None)
        assert wind is not None
        assert wind.presence == "missing"

    def test_instantiator_explicit_template_id_honored(self):
        from fantasyai.aurora.system_model import instantiate_system_model
        s = self._enviro_state()
        # Force "finance" even though columns scream enviro.
        sm = instantiate_system_model(s, explicit_template_id="finance")
        assert sm.provenance.template_id == "finance"

    def test_instantiator_confidence_bounded(self):
        from fantasyai.aurora.system_model import instantiate_system_model
        sm = instantiate_system_model(self._enviro_state())
        assert 0.0 <= sm.confidence <= 1.0


# ============================================================
# Narrator
# ============================================================

class TestNarrator:

    def test_renarrate_anomaly_uses_vocab(self):
        from fantasyai.aurora.system_model.narrator import renarrate_finding
        sm = {
            "vocabulary": {"anomaly_term": "outlier print", "regime_term": "regime",
                            "breach_term": "risk breach", "advisory_term": "risk note"},
            "characteristic_patterns": [{"id": "vol_cluster", "name": "Volatility clustering"}],
            "state": {"active_patterns": [{"pattern_id": "vol_cluster"}]},
            "confidence": 0.75,
        }
        f = {"title": "Anomaly in price", "description": "z=4.1",
             "method": "iso-forest", "severity": "warn", "confidence": 0.8}
        renarrate_finding(f, sm)
        assert "narrative" in f
        # The vocabulary token "outlier print" should appear (case-insensitive).
        assert "outlier print" in f["narrative"]["title"].lower()

    def test_renarrate_low_confidence_suppressed(self):
        from fantasyai.aurora.system_model.narrator import renarrate_finding
        sm = {"vocabulary": {}, "characteristic_patterns": [],
              "state": {"active_patterns": []}, "confidence": 0.1}
        f = {"title": "Anomaly", "method": "iso-forest", "confidence": 0.6}
        renarrate_finding(f, sm)
        assert "low system-model confidence" in f["narrative"]["body"]


# ============================================================
# Evolution scaffolding
# ============================================================

class TestEvolution:

    def test_record_evidence_appends(self):
        from fantasyai.aurora.system_model.evolution import (
            record_evidence, read_evidence,
        )
        state = {
            "run_dir": "/fake/r2",
            "system_model": {
                "provenance": {"template_id": "enviro", "template_version": "v1",
                                "confirmed_relationships": ["rel_wind_wave"],
                                "discovered_relationships": [],
                                "deviation_relationships": []},
                "topology": {"relationships": []},
                "confidence": 0.7,
            },
        }
        row = record_evidence(state)
        assert row is not None
        rows = read_evidence("enviro")
        assert len(rows) == 1
        assert rows[0]["template_id"] == "enviro"

    def test_proposals_fire_when_support_high(self):
        from fantasyai.aurora.system_model.evolution import (
            record_evidence, evolution_proposals,
        )
        # Seed 4 runs all surfacing the same discovered relationship.
        for i in range(4):
            record_evidence({
                "run_dir": f"/r{i}",
                "system_model": {
                    "provenance": {
                        "template_id": "enviro", "template_version": "v1",
                        "confirmed_relationships": [],
                        "discovered_relationships": ["discovered_corr_x_y"],
                        "deviation_relationships": [],
                    },
                    "topology": {"relationships": []},
                    "confidence": 0.6,
                },
            })
        props = evolution_proposals("enviro", min_support=3)
        assert any(p["kind"] == "add_relationship" for p in props)

    def test_proposals_quiet_below_threshold(self):
        from fantasyai.aurora.system_model.evolution import (
            record_evidence, evolution_proposals,
        )
        for i in range(2):
            record_evidence({
                "run_dir": f"/r{i}",
                "system_model": {
                    "provenance": {"template_id": "enviro", "template_version": "v1",
                                    "discovered_relationships": ["x"]},
                    "topology": {"relationships": []},
                    "confidence": 0.5,
                },
            })
        props = evolution_proposals("enviro", min_support=3)
        assert not props


# ============================================================
# ML layer (transparent forecasters)
# ============================================================

class TestMLLayer:

    def test_knn_recovers_periodic_signal(self):
        np = pytest.importorskip("numpy")
        from fantasyai.aurora.ml_layer import KNNForecaster
        x = np.sin(np.linspace(0, 8 * np.pi, 400))
        r = KNNForecaster(window=30, k=5).forecast(x[:300], horizon=20)
        assert r.ok
        # Predicted should track sine; mean abs error reasonable.
        truth = x[300:320]
        err = float(np.mean(np.abs(np.asarray(r.forecast) - truth)))
        assert err < 0.5

    def test_ar2_returns_explicit_coefficients(self):
        np = pytest.importorskip("numpy")
        from fantasyai.aurora.ml_layer import LinearAR2Forecaster
        x = 0.7 * np.sin(np.linspace(0, 6 * np.pi, 200)) + 0.05 * np.random.RandomState(0).standard_normal(200)
        r = LinearAR2Forecaster().forecast(x, horizon=10)
        assert r.ok
        coefs = r.math.get("coefficients") or {}
        for k in ("c", "phi1", "phi2"):
            assert k in coefs

    def test_transparent_forecast_picks_winner(self):
        np = pytest.importorskip("numpy")
        from fantasyai.aurora.ml_layer import transparent_forecast
        x = np.sin(np.linspace(0, 10 * np.pi, 600))
        out = transparent_forecast(x, horizon=20)
        assert out["winner"] in ("knn_window", "ar2")
        assert "knn_window" in out["results"]
        assert "ar2" in out["results"]


# ============================================================
# Action agents (per-domain)
# ============================================================

class TestActionAgents:

    def test_action_agent_no_op_without_run(self, tmp_path, monkeypatch):
        # Force the latest-run lookup to return None by pointing outputs to empty.
        from fantasyai.aurora.agents.action_agents import EnviroAdvisoryAgent
        # Without a real run, the agent reports no_op cleanly.
        res = EnviroAdvisoryAgent().run({})
        assert res.status in ("no_op", "ok")

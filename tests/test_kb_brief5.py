"""
Brief 5 — bulk knowledge-bank ingestion infrastructure tests.

Coverage:
  * License field is mandatory (entries without one are rejected).
  * Known-license registry: attach_license raises on unknown labels.
  * Quarantine queue: failed entries land in the quarantine table
    with a reason + raw data; admin can resolve them.
  * Source modules: each one's ``run_ingest`` returns a structured
    "skipped" result when its prerequisites aren't met (no API key,
    missing source files, missing optional libs) — never crashes.
  * Pre-flight: returns a structured report covering embedder,
    bank consistency, GPU, disk, license audit, checkpoints.
  * Pipeline orchestrator: iterates sources, accumulates per-source
    results, calls progress callback, never blocks on one source's
    failure.
  * Stage runner: ``run_stage`` resolves stage_id and runs every
    source in the stage.
  * License audit endpoint: lists per-license counts.
  * Findings byte-identical regression: bank-related changes don't
    drift findings.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def isolated_bank(tmp_path, monkeypatch):
    db_path = tmp_path / "kb.db"
    monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
    import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
    ss._BANK = None
    yield db_path
    ss._BANK = None


# ============================================================
# License field tests
# ============================================================

class TestLicenseRequired:
    def test_entry_without_license_is_quarantined(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank import get_bank
        from fantasyai.aurora.knowledge_bank.schema import (
            KnowledgeEntry, EntryReference,
        )
        bank = get_bank()
        # Deliberately omit license to verify it gets caught.
        e = KnowledgeEntry(
            id="bad:no_license", source="test", source_version="1",
            domain=["bio"], name="bad", definition="no license attached.",
            references=[EntryReference(citation="x")],
            review_status="auto_approved",
            ingest_method="ontology_etl",
        )
        res = bank.insert_many([e])
        assert res["n_inserted"] == 0
        assert res["n_quarantined"] == 1
        # The quarantine queue records the reason.
        q = bank.list_quarantine()
        assert len(q) == 1
        assert "license" in q[0]["reason"].lower()

    def test_attach_license_unknown_raises(self):
        from fantasyai.aurora.knowledge_bank.schema import (
            KnowledgeEntry, attach_license,
        )
        e = KnowledgeEntry(id="x", source="t", source_version="1",
                             name="x", definition="real definition.")
        with pytest.raises(ValueError):
            attach_license(e, "Some Made-Up License 7.0")

    def test_attach_license_propagates_url_and_commercial_flag(self):
        from fantasyai.aurora.knowledge_bank.schema import (
            KnowledgeEntry, attach_license,
        )
        e = KnowledgeEntry(id="x", source="t", source_version="1",
                             name="x", definition="real definition.")
        attach_license(e, "CC BY 4.0")
        assert "creativecommons" in e.license_url
        assert e.commercial_use_allowed is True
        assert e.license_verified_at  # non-null timestamp


# ============================================================
# Quarantine flow
# ============================================================

class TestQuarantine:
    def test_resolve_quarantine(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank import get_bank
        from fantasyai.aurora.knowledge_bank.schema import KnowledgeEntry
        bank = get_bank()
        # Insert a bad entry.
        e = KnowledgeEntry(id="x:bad", source="t", source_version="1",
                             name="x", definition="def.")  # missing license
        bank.insert_many([e])
        rows = bank.list_quarantine()
        assert rows
        qid = rows[0]["id"]
        ok = bank.resolve_quarantine(qid, resolution="rejected")
        assert ok
        # After resolution, default-listing (unresolved=True) hides it.
        rows2 = bank.list_quarantine(only_unresolved=True)
        assert all(r["id"] != qid for r in rows2)


# ============================================================
# Seed entries all carry license post-Brief-5
# ============================================================

class TestSeedHasLicense:
    def test_every_seed_entry_has_license_string(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank import get_bank
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        run_seed_ingest()
        bank = get_bank()
        cur = bank._conn.execute(
            """SELECT COUNT(*) FROM entries
               WHERE revoked = 0 AND (license IS NULL OR license = '')"""
        )
        assert cur.fetchone()[0] == 0


# ============================================================
# Source modules — each must skip cleanly without crashing
# ============================================================

class TestSourceModuleSkipsCleanly:
    def test_fred_skips_without_api_key(self, isolated_bank, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        from fantasyai.aurora.knowledge_bank.ingest.sources import fred_metadata
        from fantasyai.aurora.knowledge_bank import get_bank
        res = fred_metadata.run_ingest(get_bank(),
                                          Path(tempfile.mkdtemp()))
        assert res["ok"] is False
        assert res["reason"] == "missing_FRED_API_KEY"
        assert "FRED_API_KEY" in res["fix"]

    def test_gene_ontology_skips_without_obo(self, isolated_bank, tmp_path):
        from fantasyai.aurora.knowledge_bank.ingest.sources import gene_ontology
        from fantasyai.aurora.knowledge_bank import get_bank
        res = gene_ontology.run_ingest(get_bank(), tmp_path,
                                          obo_path=tmp_path / "missing.obo")
        assert res["ok"] is False
        assert res["reason"] == "missing_obo_file"

    def test_uniprot_skips_without_dat(self, isolated_bank, tmp_path):
        from fantasyai.aurora.knowledge_bank.ingest.sources import uniprot
        from fantasyai.aurora.knowledge_bank import get_bank
        res = uniprot.run_ingest(get_bank(), tmp_path,
                                    dat_path=tmp_path / "missing.dat.gz")
        assert res["ok"] is False
        assert res["reason"] in ("missing_uniprot_dat_file",
                                    "biopython_not_installed")

    def test_wikidata_skips_without_dump(self, isolated_bank, tmp_path,
                                            monkeypatch):
        monkeypatch.delenv("WIKIDATA_DUMP_PATH", raising=False)
        from fantasyai.aurora.knowledge_bank.ingest.sources import wikidata
        from fantasyai.aurora.knowledge_bank import get_bank
        res = wikidata.run_ingest(get_bank(), tmp_path)
        assert res["ok"] is False
        assert res["reason"] == "missing_dump"

    def test_textbook_extractor_skips_without_dir(self, isolated_bank,
                                                     tmp_path):
        from fantasyai.aurora.knowledge_bank.ingest.sources import textbook_extractor
        from fantasyai.aurora.knowledge_bank import get_bank
        res = textbook_extractor.run_ingest(
            get_bank(), tmp_path, textbooks_dir=tmp_path / "nope"
        )
        assert res["ok"] is False
        assert res["reason"] == "no_textbooks_dir"

    def test_usgs_runs_with_curated_baseline(self, isolated_bank, tmp_path):
        """USGS module ships with a small curated baseline; must run
        cleanly without external dependencies."""
        from fantasyai.aurora.knowledge_bank.ingest.sources import usgs_geological
        from fantasyai.aurora.knowledge_bank import get_bank
        res = usgs_geological.run_ingest(get_bank(), tmp_path)
        assert res["ok"] is True
        assert res["n_inserted"] >= 5

    def test_pipeline_orchestrator_handles_mixed(self, isolated_bank,
                                                    tmp_path, monkeypatch):
        """Run multiple sources at once; pipeline collects results
        without one source's failure blocking the others."""
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        from fantasyai.aurora.knowledge_bank.ingest import run_ingest
        res = run_ingest(["seed", "usgs_geological", "fred_metadata"],
                          checkpoint_dir=tmp_path)
        assert res["sources"]["seed"]["ok"] is True
        assert res["sources"]["usgs_geological"]["ok"] is True
        assert res["sources"]["fred_metadata"]["ok"] is False
        # The total_inserted reflects only the successful sources.
        assert res["total_inserted"] >= 30


# ============================================================
# Pre-flight
# ============================================================

class TestPreflight:
    def test_returns_structured_report(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank.preflight import run_preflight
        rep = run_preflight()
        assert isinstance(rep, dict)
        assert "checks" in rep
        # Required check keys.
        for k in ("sentence_transformers", "embedder",
                    # Brief: "Embedder Device Configuration" renamed
                    # the GPU check to ``embedder_device`` and made it
                    # diagnostic rather than a deficiency report.
                    "bank_embedder_consistency", "embedder_device", "disk",
                    "license_audit", "checkpoints"):
            assert k in rep["checks"]


# ============================================================
# Stage runner
# ============================================================

class TestStageRunner:
    def test_unknown_stage_returns_error(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank.ingest import run_stage
        res = run_stage("not_a_stage")
        assert res["ok"] is False

    def test_stages_dict_covers_all_six(self):
        from fantasyai.aurora.knowledge_bank.ingest.pipeline import (
            INGEST_STAGES,
        )
        assert len(INGEST_STAGES) == 6
        # Each stage carries label + sources + expected_entries.
        for sid, info in INGEST_STAGES.items():
            assert info.get("label")
            assert info.get("sources")
            assert info.get("expected_entries")


# ============================================================
# Parser correctness — feed each source module a tiny synthetic
# fixture and verify the schema mapping + license attachment.
# These exercise the actual transformation code without needing
# the real source files (gigabytes).
# ============================================================

class TestGeneOntologyParser:
    def test_parses_synthetic_obo_term(self, isolated_bank, tmp_path):
        from fantasyai.aurora.knowledge_bank.ingest.sources import gene_ontology
        from fantasyai.aurora.knowledge_bank import get_bank
        # Synthetic miniature OBO file — header + one real-shape term.
        obo = tmp_path / "tiny.obo"
        obo.write_text(
            "format-version: 1.2\n"
            "data-version: releases/2026-05-06\n"
            "ontology: go\n"
            "\n"
            "[Term]\n"
            'id: GO:0007623\n'
            "name: circadian rhythm\n"
            "namespace: biological_process\n"
            'def: "Any biological process in an organism that recurs '
            'with a regularity of approximately 24 hours." [GOC:dph, '
            'PMID:11237011]\n'
            "synonym: \"diurnal rhythm\" RELATED [GOC:dph]\n"
            "is_a: GO:0048511 ! rhythmic process\n"
            "xref: PMID:11237011\n"
            "\n",
            encoding="utf-8",
        )
        res = gene_ontology.run_ingest(get_bank(), tmp_path, obo_path=obo)
        assert res["ok"] is True
        assert res["n_inserted"] == 1
        e = get_bank().get("go_0007623")
        assert e is not None
        assert e.name == "circadian rhythm"
        assert e.concept_type == "biological_process"
        assert "approximately 24 hours" in e.definition.lower()
        assert "go_0048511" in e.related_concepts
        assert "diurnal rhythm" in e.aliases
        assert e.license == "CC BY 4.0"
        assert e.commercial_use_allowed is True
        assert e.source_version == "releases/2026-05-06"


class TestNistParser:
    def test_skips_when_unreachable(self, isolated_bank, tmp_path,
                                       monkeypatch):
        """No network: NIST module returns a clean skip rather than
        crashing."""
        from fantasyai.aurora.knowledge_bank.ingest.sources import nist_reference
        from fantasyai.aurora.knowledge_bank import get_bank
        # Force the URL fetch to return [] by monkey-patching.
        monkeypatch.setattr(nist_reference, "_stream_constants",
                              lambda: [])
        res = nist_reference.run_ingest(get_bank(), tmp_path)
        assert res["ok"] is False
        assert res["reason"] == "nist_constants_unreachable"

    def test_parses_synthetic_constants(self, isolated_bank, tmp_path,
                                           monkeypatch):
        from fantasyai.aurora.knowledge_bank.ingest.sources import nist_reference
        from fantasyai.aurora.knowledge_bank import get_bank
        monkeypatch.setattr(nist_reference, "_stream_constants", lambda: [
            {"name": "speed of light in vacuum", "value": "299792458",
              "uncertainty": "exact", "unit": "m s^-1"},
            {"name": "Planck constant", "value": "6.62607015e-34",
              "uncertainty": "exact", "unit": "J Hz^-1"},
        ])
        res = nist_reference.run_ingest(get_bank(), tmp_path)
        assert res["ok"] is True
        assert res["n_inserted"] == 2
        # License attached.
        cur = get_bank()._conn.execute(
            "SELECT license FROM entries WHERE source = 'nist'"
        )
        for (lic,) in cur.fetchall():
            assert lic == "Public Domain (US Government)"


class TestNoaaParser:
    def test_parses_synthetic_station(self, isolated_bank, tmp_path,
                                         monkeypatch):
        from fantasyai.aurora.knowledge_bank.ingest.sources import noaa_documentation
        from fantasyai.aurora.knowledge_bank import get_bank
        monkeypatch.setattr(noaa_documentation, "_stream_stations",
                              lambda: iter([
                                  {"station_id": "41001", "owner": "NDBC",
                                    "type": "buoy", "hull": "discus",
                                    "name": "East Hatteras",
                                    "payload": "AMPS",
                                    "location": "34.7N 72.7W",
                                    "timezone": "EST", "forecast": "y",
                                    "note": "Active 1972-present"},
                                  {"station_id": "", "owner": "x",
                                    "type": "x", "hull": "x", "name": "x",
                                    "payload": "x", "location": "x",
                                    "timezone": "", "forecast": "",
                                    "note": ""},
                              ]))
        res = noaa_documentation.run_ingest(get_bank(), tmp_path)
        assert res["ok"] is True
        assert res["n_inserted"] >= 1
        e = get_bank().get("noaa_station_41001")
        assert e is not None
        assert "East Hatteras" in e.name
        assert "34.7N" in e.definition
        assert e.license == "Public Domain (US Government)"


class TestFredParser:
    def test_series_to_entry_maps_correctly(self):
        from fantasyai.aurora.knowledge_bank.ingest.sources.fred_metadata import (
            _series_to_entry,
        )
        series = {
            "id": "GDP", "title": "Gross Domestic Product",
            "notes": "BEA Account Code: A191RC. Quarterly seasonally adjusted.",
            "units": "Billions of Dollars", "frequency": "Quarterly",
            "observation_start": "1947-01-01",
            "observation_end": "2026-01-01",
            "seasonal_adjustment": "Seasonally Adjusted Annual Rate",
            "popularity": 99,
        }
        entry = _series_to_entry(series, "2026-05-06")
        assert entry is not None
        assert entry.id == "fred_series_GDP"
        assert entry.source == "fred"
        assert "Quarterly" in entry.definition
        assert entry.domain == ["economics", "finance"]
        assert entry.license.startswith("FRED Public Data")
        # Discontinued series → None.
        out = _series_to_entry({"id": "X", "title": "DISCONTINUED test",
                                  "notes": "x"}, "v")
        assert out is None


class TestWikidataParser:
    def test_entity_mapping_filters_correctly(self):
        from fantasyai.aurora.knowledge_bank.ingest.sources.wikidata import (
            _entity_to_entry,
        )
        # An eligible scientific concept (instance of physical quantity)
        # with enough statements + en sitelink.
        entity = {
            "id": "Q11471",
            "labels": {"en": {"value": "atmospheric pressure"}},
            "descriptions": {"en": {"value": "force per unit area "
                                        "exerted by atmospheric gases"}},
            "claims": {
                "P31": [{"mainsnak": {"datavalue": {"value":
                                                          {"id": "Q170790"}}}}],
                **{f"P{i}": [{"x": i}] for i in range(20)},
            },
            "sitelinks": {"enwiki": {"title": "Atmospheric pressure"}},
            "aliases": {"en": [{"value": "barometric pressure"}]},
        }
        entry = _entity_to_entry(entity)
        assert entry is not None
        assert entry.id == "wikidata_Q11471"
        assert "atmospheric" in entry.name.lower()
        assert "physics" in entry.domain
        assert entry.license == "CC0 1.0"
        # An excluded class (Q5 = human) → None.
        bad = dict(entity)
        bad["claims"] = {"P31": [{"mainsnak": {"datavalue": {"value":
                                                                  {"id": "Q5"}}}}]}
        assert _entity_to_entry(bad) is None
        # Missing English description → None.
        nodesc = dict(entity); nodesc["descriptions"] = {}
        assert _entity_to_entry(nodesc) is None


# ============================================================
# Findings regression — Brief 5 must not drift findings
# ============================================================

class TestFindingsByteIdentical:
    def test_findings_unchanged_with_kb_changes(self, isolated_bank,
                                                   monkeypatch):
        """Brief 5 is a knowledge-layer brief; findings must remain
        byte-identical regardless of bank state."""
        monkeypatch.setenv("AURORA_SYNTHESIS_MODE", "template")
        from fantasyai.aurora.synthesis import synthesize, list_templates
        list_templates(force_reload=True)

        def make_state():
            return {
                "system_model": {"template_id": "industrial"},
                "findings": [
                    {"title": "Anomaly in pressure at row 100",
                      "method": "iso-forest + robust-Z",
                      "severity": "crit", "confidence": 0.92,
                      "z": 4.2, "score": 4.2},
                    {"title": "Physical model fit: damped_harmonic_oscillator",
                      "method": "physics_discovery",
                      "severity": "info", "confidence": 0.85,
                      "params": {"zeta": 0.3, "omega": 1.4}},
                ],
                "anomalies": [],
                "structure": {"available": True, "time_axis": True,
                                "cadence": "1h", "columns": []},
            }

        # Run with empty bank.
        s1 = make_state()
        synthesize(s1)
        f1 = json.dumps([{k: v for k, v in f.items()
                            if k != "_synthesis_kind"}
                           for f in s1["findings"]],
                         sort_keys=True, default=str)

        # Now ingest the seed bank (full Brief-5 with license fields).
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        run_seed_ingest()

        s2 = make_state()
        synthesize(s2)
        f2 = json.dumps([{k: v for k, v in f.items()
                            if k != "_synthesis_kind"}
                           for f in s2["findings"]],
                         sort_keys=True, default=str)
        assert f1 == f2, "findings drifted after Brief-5 KB changes"

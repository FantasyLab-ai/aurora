"""
Tests for the Aurora Research Kit generator.

Coverage:
  * methods.md contains the expected sections + cites used methods
  * references.bib contains entries for known + unknown methods
  * unknown methods get a placeholder @misc entry
  * replication.json carries deterministic re-run info
  * .zenodo.json conforms to Zenodo's expected top-level shape
  * write_research_kit produces all six files
  * overwrite guard prevents accidental clobbers
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aurora_sdk import Bundle
from fantasyai.aurora.research_kit import (
    generate_methods_md,
    generate_references_bib,
    generate_replication_json,
    generate_zenodo_json,
    generate_readme,
    write_research_kit,
)


def _sample_state():
    return {
        "run_id": "rk-test-001",
        "run_meta": {
            "run_id": "rk-test-001",
            "state": "complete",
            "tier": "auto",
            "started_at": "2026-05-12T15:00:00",
            "completed_at": "2026-05-12T15:01:30",
        },
        "dataset": {
            "name": "factory_bearing_demo.csv",
            "rows": 1000, "cols": 5,
            "size_mb": 0.04, "path": None,
        },
        "structure": {"available": True, "time_axis": True, "cadence": "5s",
                       "columns": []},
        "findings": [
            {"rank": 1, "severity": "crit", "method": "ISO-FOREST + ROBUST-Z",
              "title": "anom", "claim_id": "a0"},
            {"rank": 2, "severity": "warn", "method": "Granger",
              "title": "warn", "claim_id": "g0"},
            {"rank": 3, "severity": "info", "method": "matrix_profile",
              "title": "motif", "claim_id": "m0"},
            {"rank": 4, "severity": "info", "method": "INVENTED_METHOD",
              "title": "?", "claim_id": "x0"},
        ],
        "system_model": {
            "template_id": "(none)",
            "confidence": 0.55,
            "topology": {"entities": [], "relationships": []},
        },
        "synthesis": {"assumptions": [
            "robust z-score uses Hampel 1974 thresholds",
            "AR(1) σ propagates analytically",
        ]},
        "forecast": {"available": False},
        "anomalies": [], "regimes": [], "motifs": [],
        "causal": {}, "physics": {},
    }


@pytest.fixture
def sample_bundle():
    return Bundle.from_state(_sample_state()).doc


# ============================================================
# methods.md
# ============================================================

class TestMethodsMd:

    def test_contains_required_sections(self, sample_bundle):
        md = generate_methods_md(sample_bundle)
        for section in ("# Methods", "## Data", "## Pipeline overview",
                         "## Methods used", "## Assumptions",
                         "## Confidence", "## Reproducibility"):
            assert section in md, f"missing section: {section}"

    def test_cites_known_methods_inline(self, sample_bundle):
        md = generate_methods_md(sample_bundle)
        # Granger fires inline citation
        assert "granger1969" in md

    def test_dataset_hash_only_when_present(self, sample_bundle):
        md = generate_methods_md(sample_bundle)
        # sample bundle has no sha256, so no hash sentence
        assert "SHA-256 hash is" not in md

    def test_unknown_method_gets_placeholder_note(self, sample_bundle):
        md = generate_methods_md(sample_bundle)
        assert "INVENTED_METHOD" in md
        # description placeholder for camera-ready
        assert "fill in for camera-ready" in md

    def test_confidence_tiered_label(self, sample_bundle):
        md = generate_methods_md(sample_bundle)
        # confidence is 0.55 → "moderate"
        assert "55%" in md
        assert "moderate" in md.lower()

    def test_includes_bundle_hash_in_repro_section(self, sample_bundle):
        md = generate_methods_md(sample_bundle)
        h = sample_bundle["integrity"]["content_hash"]
        assert h in md


# ============================================================
# references.bib
# ============================================================

class TestReferencesBib:

    def test_contains_known_method_citations(self, sample_bundle):
        bib = generate_references_bib(sample_bundle)
        # iso-forest → liu2008, granger → granger1969
        assert "liu2008isolation" in bib or "iso-forest" in bib
        assert "granger1969" in bib
        assert "@article" in bib or "@inproceedings" in bib

    def test_unknown_method_gets_placeholder_entry(self, sample_bundle):
        bib = generate_references_bib(sample_bundle)
        assert "aurora-invented" in bib.lower() or "@misc" in bib

    def test_includes_aurora_self_citation(self, sample_bundle):
        bib = generate_references_bib(sample_bundle)
        assert "aurora-qie" in bib
        assert "Aurora-QIE" in bib

    def test_well_formed_bibtex(self, sample_bundle):
        bib = generate_references_bib(sample_bundle)
        # Each @entry has a matching close brace, no nested @entries
        assert bib.count("@") <= bib.count("}\n") + 1


# ============================================================
# replication.json
# ============================================================

class TestReplicationJson:

    def test_carries_aurora_version_and_hash(self, sample_bundle):
        rep = generate_replication_json(sample_bundle)
        assert rep["aurora_version"] == sample_bundle["aurora_version"]
        assert rep["bundle_hash"] == sample_bundle["integrity"]["content_hash"]

    def test_contains_dataset_subblock(self, sample_bundle):
        rep = generate_replication_json(sample_bundle)
        assert rep["dataset"]["basename"] == "factory_bearing_demo.csv"
        assert rep["dataset"]["rows"] == 1000

    def test_contains_instructions(self, sample_bundle):
        rep = generate_replication_json(sample_bundle)
        assert isinstance(rep["instructions"], list)
        assert len(rep["instructions"]) >= 3

    def test_schema_tag(self, sample_bundle):
        rep = generate_replication_json(sample_bundle)
        assert rep["schema"] == "aurora.replication/v1"


# ============================================================
# .zenodo.json
# ============================================================

class TestZenodoJson:

    def test_metadata_block_present(self, sample_bundle):
        z = generate_zenodo_json(sample_bundle)
        assert "metadata" in z
        m = z["metadata"]
        for k in ("title", "description", "creators", "keywords",
                  "upload_type", "access_right", "license"):
            assert k in m

    def test_creators_default_present(self, sample_bundle):
        z = generate_zenodo_json(sample_bundle)
        assert z["metadata"]["creators"]
        assert isinstance(z["metadata"]["creators"], list)

    def test_custom_title_and_creators_honored(self, sample_bundle):
        z = generate_zenodo_json(
            sample_bundle,
            title="My custom title",
            creators=[{"name": "Smith, Alice", "affiliation": "Lab X"}],
            keywords=["custom", "kw"],
            description="custom desc",
        )
        m = z["metadata"]
        assert m["title"] == "My custom title"
        assert m["creators"][0]["name"] == "Smith, Alice"
        assert "custom" in m["keywords"]
        assert m["description"] == "custom desc"


# ============================================================
# write_research_kit — end-to-end
# ============================================================

class TestWriteResearchKit:

    def test_writes_all_files(self, sample_bundle, tmp_path: Path):
        paths = write_research_kit(sample_bundle, tmp_path / "kit")
        for p in (paths.readme, paths.methods, paths.bib,
                   paths.replication, paths.zenodo, paths.bundle):
            assert p.exists(), f"missing: {p}"

    def test_bundle_copy_is_valid_json(self, sample_bundle, tmp_path: Path):
        paths = write_research_kit(sample_bundle, tmp_path / "kit")
        loaded = json.loads(paths.bundle.read_text(encoding="utf-8"))
        assert loaded["bundle_version"].startswith("1.")

    def test_overwrite_guard(self, sample_bundle, tmp_path: Path):
        write_research_kit(sample_bundle, tmp_path / "kit")
        with pytest.raises(FileExistsError):
            write_research_kit(sample_bundle, tmp_path / "kit")
        # With overwrite=True, should succeed.
        write_research_kit(sample_bundle, tmp_path / "kit", overwrite=True)

    def test_zenodo_metadata_parsable(self, sample_bundle, tmp_path: Path):
        paths = write_research_kit(sample_bundle, tmp_path / "kit")
        z = json.loads(paths.zenodo.read_text(encoding="utf-8"))
        assert "metadata" in z


# ============================================================
# README
# ============================================================

class TestReadme:

    def test_includes_identifying_fields(self, sample_bundle):
        rd = generate_readme(sample_bundle)
        assert "rk-test-001" in rd
        assert "factory_bearing_demo.csv" in rd
        assert sample_bundle["integrity"]["content_hash"] in rd

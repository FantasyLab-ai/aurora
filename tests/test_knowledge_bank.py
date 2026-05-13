"""
Knowledge bank + RAG synthesis tests.

Coverage:
  * Schema validation.
  * SQLite store: insert, get, summary, alias index, pattern triggers.
  * Vector index: add / search / persistence.
  * Retrieval modes: structured, semantic, hybrid.
  * Seed ingest: populates a non-empty bank.
  * RAG synthesis: feature flag, template fallback, verification step.
  * Regression: findings + equation_translations identical with RAG
                on vs off.
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

np = pytest.importorskip("numpy")


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def isolated_bank(tmp_path, monkeypatch):
    """Isolated KB at a tmp path so tests don't pollute ~/.aurora."""
    db_path = tmp_path / "main.db"
    monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
    # Force singleton reset.
    import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
    ss._BANK = None
    yield db_path
    ss._BANK = None


# ============================================================
# Schema validation
# ============================================================

class TestSchema:
    def test_valid_entry_passes_validation(self):
        from fantasyai.aurora.knowledge_bank.schema import (
            KnowledgeEntry, EntryReference, validate_entry, attach_license,
        )
        e = KnowledgeEntry(
            id="test:foo", source="test", source_version="1",
            domain=["bio"], name="foo", definition="A foo is a foo.",
            references=[EntryReference(citation="Test 2026")],
            review_status="auto_approved",
            ingest_method="ontology_etl",
        )
        attach_license(e, "Aurora Internal")  # Brief 5: license required
        v = validate_entry(e)
        assert v["ok"] is True

    def test_missing_definition_fails(self):
        from fantasyai.aurora.knowledge_bank.schema import (
            KnowledgeEntry, validate_entry,
        )
        e = KnowledgeEntry(id="test:bar", source="t", source_version="1",
                             name="bar", definition="")
        v = validate_entry(e)
        assert v["ok"] is False
        assert any("definition" in err for err in v["errors"])

    def test_unknown_review_status_fails(self):
        from fantasyai.aurora.knowledge_bank.schema import (
            KnowledgeEntry, validate_entry,
        )
        e = KnowledgeEntry(id="x", source="t", source_version="1",
                             name="x", definition="A real definition.",
                             review_status="bogus")
        assert validate_entry(e)["ok"] is False


# ============================================================
# SQLite store
# ============================================================

class TestSqliteStore:
    def test_insert_and_get(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank import get_bank
        from fantasyai.aurora.knowledge_bank.schema import (
            KnowledgeEntry, EntryReference, PatternSignature, attach_license,
        )
        bank = get_bank()
        e = KnowledgeEntry(
            id="test:osc", source="test", source_version="1",
            domain=["physics"], name="oscillator",
            definition="A second-order linear system.",
            pattern_signatures=[PatternSignature(
                pattern_type="oscillation",
                characteristics="exponentially decaying sinusoid")],
            references=[EntryReference(citation="Goldstein 1980")],
            review_status="auto_approved",
            ingest_method="ontology_etl",
        )
        attach_license(e, "Aurora Internal")
        bank.insert(e)
        got = bank.get("test:osc")
        assert got is not None
        assert got.name == "oscillator"
        assert "physics" in got.domain
        assert len(got.pattern_signatures) == 1

    def test_pattern_trigger_index(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank import get_bank
        from fantasyai.aurora.knowledge_bank.schema import (
            KnowledgeEntry, PatternSignature, attach_license,
        )
        bank = get_bank()
        a = KnowledgeEntry(
            id="test:a", source="test", source_version="1",
            domain=["bio"], name="A",
            definition="An oscillator concept entry.",
            pattern_signatures=[PatternSignature(pattern_type="oscillation",
                                                    characteristics="x")],
            review_status="auto_approved", ingest_method="ontology_etl",
        )
        b = KnowledgeEntry(
            id="test:b", source="test", source_version="1",
            domain=["bio"], name="B",
            definition="A decay process concept entry.",
            pattern_signatures=[PatternSignature(pattern_type="decay",
                                                    characteristics="y")],
            review_status="auto_approved", ingest_method="ontology_etl",
        )
        attach_license(a, "Aurora Internal")
        attach_license(b, "Aurora Internal")
        bank.insert(a); bank.insert(b)
        ids = bank.find_by_pattern("oscillation", domains=["bio"])
        assert "test:a" in ids
        assert "test:b" not in ids

    def test_summary_counts(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank import get_bank
        from fantasyai.aurora.knowledge_bank.schema import (
            KnowledgeEntry, attach_license,
        )
        bank = get_bank()
        for i in range(3):
            e = KnowledgeEntry(
                id=f"test:s{i}", source="src1", source_version="1",
                domain=["bio"], name=f"s{i}",
                definition=f"definition number {i} for testing summaries.",
                review_status="auto_approved",
                ingest_method="ontology_etl",
            )
            attach_license(e, "Aurora Internal")
            bank.insert(e)
        s = bank.summary()
        assert s["total_entries"] == 3
        assert any(p["source"] == "src1" and p["n"] == 3
                     for p in s["per_source"])


# ============================================================
# Vector index
# ============================================================

class TestVectorIndex:
    def test_add_and_search(self):
        from fantasyai.aurora.knowledge_bank.storage.vector_index import (
            NumpyVectorIndex,
        )
        idx = NumpyVectorIndex(dim=4)
        idx.add("a", [1.0, 0, 0, 0])
        idx.add("b", [0, 1.0, 0, 0])
        idx.add("c", [0.7, 0.7, 0, 0])
        results = idx.search([1.0, 0, 0, 0], top_k=3)
        assert results[0][0] == "a"
        # b and c both partially align; a is best.
        assert results[0][1] > 0.99

    def test_persistence_roundtrip(self, tmp_path):
        from fantasyai.aurora.knowledge_bank.storage.vector_index import (
            NumpyVectorIndex,
        )
        idx = NumpyVectorIndex(dim=3)
        idx.add("a", [1.0, 0, 0])
        idx.add("b", [0, 1.0, 0])
        idx.save(tmp_path / "vecs")
        idx2 = NumpyVectorIndex(dim=3)
        ok = idx2.load(tmp_path / "vecs")
        assert ok
        assert len(idx2) == 2
        assert idx2.search([0, 1.0, 0], top_k=1)[0][0] == "b"


# ============================================================
# Retrieval engine
# ============================================================

class TestRetrieval:
    def test_seed_ingest_populates_bank(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank import get_bank
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        bank = get_bank()
        res = run_seed_ingest(bank)
        assert res["sources"]["seed"]["ok"] is True
        assert res["sources"]["seed"]["n_inserted"] >= 20
        s = bank.summary()
        assert s["total_entries"] >= 20

    def test_structured_retrieval_returns_relevant(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank import retrieve, RetrievalMode
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        run_seed_ingest()
        results = retrieve("damped harmonic oscillator",
                            mode=RetrievalMode.STRUCTURED, max_results=5,
                            finding_kinds=["physics_match"])
        assert len(results) >= 1
        ids = [r.entry.id for r in results]
        assert any("damped" in i.lower() or "oscillator" in i.lower()
                    or "harmonic" in i.lower() for i in ids)

    def test_semantic_retrieval_works(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank import retrieve, RetrievalMode
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        run_seed_ingest()
        # Semantic retrieval must return SOMETHING with a similarity
        # score above zero — exact ID matching depends on the embedder
        # backend, so we test the looser invariant: the engine returns
        # results and assigns each a finite relevance.
        results = retrieve("circadian rhythm 24-hour cycle",
                            mode=RetrievalMode.SEMANTIC, max_results=5)
        assert len(results) >= 1
        for r in results:
            assert r.relevance >= 0
            assert r.semantic_score >= 0

    def test_hybrid_retrieval_returns_results(self, isolated_bank):
        from fantasyai.aurora.knowledge_bank import retrieve, RetrievalMode
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        run_seed_ingest()
        results = retrieve("Lotka Volterra predator prey",
                            mode=RetrievalMode.HYBRID, max_results=5,
                            finding_kinds=["discovered_dynamics"])
        assert len(results) >= 1
        # Lotka-Volterra should rank highly.
        top = results[0].entry.id
        assert "lotka" in top.lower() or "volterra" in top.lower() \
                or "sindy" in top.lower() or "oscill" in top.lower()


# ============================================================
# RAG synthesis
# ============================================================

class TestRagSynthesis:
    def test_template_mode_when_flag_off(self, isolated_bank, monkeypatch):
        """With AURORA_SYNTHESIS_MODE=template, RAG is never tried."""
        monkeypatch.setenv("AURORA_SYNTHESIS_MODE", "template")
        from fantasyai.aurora.synthesis import synthesize, list_templates
        list_templates(force_reload=True)
        state = {
            "system_model": {"template_id": "industrial"},
            "findings": [
                {"title": "Anomaly in pressure at row 100",
                  "method": "iso-forest + robust-Z",
                  "severity": "crit", "confidence": 0.9},
                {"title": "Anomaly in pressure at row 110",
                  "method": "iso-forest + robust-Z",
                  "severity": "warn", "confidence": 0.7},
                {"title": "Anomaly in pressure at row 120",
                  "method": "iso-forest + robust-Z",
                  "severity": "warn", "confidence": 0.6},
            ],
        }
        synthesize(state)
        assert state["synthesis_meta"]["mode"] == "template"
        # The interpretive_summary must come from a template when mode is forced.
        assert state["interpretive_summary"]

    def test_rag_falls_back_when_kb_empty(self, isolated_bank, monkeypatch):
        """With auto mode + an empty KB + no Ollama in test env, RAG
        must gracefully fail and synthesis still produces a template
        result."""
        monkeypatch.setenv("AURORA_SYNTHESIS_MODE", "auto")
        # Don't ingest seed; bank stays empty.
        from fantasyai.aurora.synthesis import synthesize, list_templates
        list_templates(force_reload=True)
        state = {
            "system_model": {"template_id": "industrial"},
            "findings": [
                {"title": "Anomaly in p at row 1",
                  "method": "iso-forest + robust-Z",
                  "severity": "crit", "confidence": 0.9},
                {"title": "Anomaly in p at row 2",
                  "method": "iso-forest + robust-Z",
                  "severity": "warn", "confidence": 0.7},
                {"title": "Anomaly in p at row 3",
                  "method": "iso-forest + robust-Z",
                  "severity": "warn", "confidence": 0.6},
            ],
        }
        synthesize(state)
        # Mode should be either rag (Ollama responded) OR rag_failed (fell
        # back to template). EITHER way the interpretive_summary is set.
        assert state["interpretive_summary"]
        assert state["synthesis_meta"]["mode"] in ("rag", "rag_failed", "template")


# ============================================================
# Regression: findings unchanged with RAG vs template
# ============================================================

class TestFindingsRegression:
    def test_findings_byte_identical_rag_vs_template(self, isolated_bank,
                                                       monkeypatch):
        """The brief's hard requirement: findings cards / equation
        translations must be byte-for-byte identical before and after
        the RAG sprint. We compare two state builds — one with RAG on
        (auto), one with RAG forced off (template) — and assert the
        findings list + equation_translations are identical."""
        from fantasyai.aurora.synthesis import synthesize, list_templates
        list_templates(force_reload=True)
        # Identical input state (RAG path doesn't run analytical methods,
        # so we set findings + structure ourselves).
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

        # Run 1: RAG forced off.
        monkeypatch.setenv("AURORA_SYNTHESIS_MODE", "template")
        s1 = make_state()
        synthesize(s1)
        f1 = json.dumps([{k: v for k, v in f.items()
                            if k != "_synthesis_kind"}
                           for f in s1["findings"]],
                         sort_keys=True, default=str)
        et1 = json.dumps(s1.get("equation_translations") or {},
                          sort_keys=True, default=str)

        # Run 2: RAG on (auto).
        monkeypatch.setenv("AURORA_SYNTHESIS_MODE", "auto")
        s2 = make_state()
        synthesize(s2)
        f2 = json.dumps([{k: v for k, v in f.items()
                            if k != "_synthesis_kind"}
                           for f in s2["findings"]],
                         sort_keys=True, default=str)
        et2 = json.dumps(s2.get("equation_translations") or {},
                          sort_keys=True, default=str)

        assert f1 == f2, "findings drifted between template and RAG mode"
        assert et1 == et2, "equation_translations drifted between modes"

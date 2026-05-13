"""
RAG synthesis regression tests.

Pin the bug from the "RAG Synthesis Regression Diagnosis and Fix"
brief: Gemma 12B was returning numeric footnote-style citations like
``[2]`` (mimicking the ``[N]`` markers we used in the FINDINGS block of
the prompt). The verification regex matched ``[2]``, looked up entry id
``"2"``, didn't find it → marked every claim unverified →
verified_fraction < 0.20 → RAG rejected → badge dropped to TEMPLATE.

These tests exercise the full RAG path with a mocked Ollama, covering:
  * Clean entry-id citations → verified ratio == 1.0
  * Numeric citations recover via the index fallback
  * Mixed entry-id + numeric → both resolved
  * Truly-bogus citations stay unverified (regression guard against
    over-tolerant verification)
  * The end-to-end engine wraps RAG correctly: when RAG ok=True, the
    synthesis_meta.mode is ``"rag"`` (the headline acceptance criterion)
  * Empty findings + empty bank ⇒ rag_failed cleanly
  * Findings prompt has NO ``[N]`` markers (regression guard against
    the format collision that caused the bug)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def isolated_seeded_bank(tmp_path, monkeypatch):
    """Fresh bank, seed-ingested. Used by every test below."""
    monkeypatch.setenv("AURORA_KB_PATH", str(tmp_path / "main.db"))
    import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
    ss._BANK = None
    from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
    run_seed_ingest()
    yield ss.get_bank()
    ss._BANK = None


@pytest.fixture
def physics_state():
    """Real-shape state with a damped-harmonic finding so retrieval
    has something to match."""
    return {
        "system_model": {"template_id": "enviro"},
        "findings": [
            {"title": "Physical model fit: damped_harmonic_oscillator",
              "description": "zeta=0.31 omega=2.4",
              "method": "physics_prior_matcher",
              "severity": "info", "confidence": 0.91,
              "kind_hint": "physics_match",
              "params": {"zeta": 0.31, "omega": 2.4}},
            {"title": "Anomaly in pressure at row 1248",
              "description": "z=4.2", "method": "iqr",
              "severity": "crit", "confidence": 0.85,
              "kind_hint": "anomaly"},
        ],
    }


# ============================================================
# Verification — citation-format coverage
# ============================================================

class TestCitationVerification:
    def _mk_retrieved(self, ids):
        """Build minimal RetrievedEntry-like objects for verification."""
        from fantasyai.aurora.knowledge_bank.schema import KnowledgeEntry
        from fantasyai.aurora.knowledge_bank.retrieval.engine import (
            RetrievedEntry,
        )
        out = []
        for i, eid in enumerate(ids):
            e = KnowledgeEntry(id=eid, source="seed", source_version="1",
                                 name=eid, definition="x.")
            out.append(RetrievedEntry(
                entry=e, relevance=1.0 - 0.05 * i,
                structured_score=1.0, semantic_score=1.0,
                matched_via=("structured",),
            ))
        return out

    def test_clean_entry_id_citations_verify(self, isolated_seeded_bank):
        from fantasyai.aurora.synthesis.rag import _verify_output
        retr = self._mk_retrieved(["seed:foo", "seed:bar"])
        text = (
            "First claim happens [seed:foo]. "
            "Second claim happens [seed:bar]. "
            "Third claim is also true [seed:foo]."
        )
        v = _verify_output(text, retr)
        assert v["n_total"] == 3
        assert v["n_verified"] == 3
        assert v["n_recovered"] == 0

    def test_numeric_citations_recover_via_index(self, isolated_seeded_bank):
        """The exact regression: model writes [0] and [1] instead of
        entry IDs. Verification must recover them by mapping to the
        N-th retrieved entry, not reject the whole output."""
        from fantasyai.aurora.synthesis.rag import _verify_output
        retr = self._mk_retrieved(["seed:foo", "seed:bar", "seed:baz"])
        text = ("Claim one [0]. Claim two [1]. Claim three [2].")
        v = _verify_output(text, retr)
        assert v["n_verified"] == 3, (
            "numeric citations should recover via index, not be rejected"
        )
        assert v["n_recovered"] == 3
        # Resolved ids point at the right retrieved entries.
        assert v["claims"][0]["cited_entries"] == ["seed:foo"]
        assert v["claims"][1]["cited_entries"] == ["seed:bar"]
        assert v["claims"][2]["cited_entries"] == ["seed:baz"]

    def test_one_indexed_fallback_when_zero_indexed_out_of_range(
            self, isolated_seeded_bank):
        """Production prefers 0-indexed; the 1-indexed fallback only
        kicks in when the 0-indexed lookup is out of range.

        With 3 entries: ``[1]`` and ``[2]`` resolve via 0-indexed (so
        they map to the 2nd and 3rd entries). ``[3]`` is out of
        0-indexed range → falls back to 1-indexed → maps to the 3rd
        entry. All three get verified."""
        from fantasyai.aurora.synthesis.rag import _verify_output
        retr = self._mk_retrieved(["seed:foo", "seed:bar", "seed:baz"])
        text = "Claim A [1]. Claim B [2]. Claim C [3]."
        v = _verify_output(text, retr)
        assert v["n_verified"] == 3
        assert v["n_recovered"] == 3
        # 0-indexed wins for in-range numbers.
        assert v["claims"][0]["cited_entries"] == ["seed:bar"]
        assert v["claims"][1]["cited_entries"] == ["seed:baz"]
        # Only out-of-0-range numbers use the 1-indexed fallback.
        assert v["claims"][2]["cited_entries"] == ["seed:baz"]

    def test_mixed_citation_styles(self, isolated_seeded_bank):
        from fantasyai.aurora.synthesis.rag import _verify_output
        retr = self._mk_retrieved(["seed:foo", "seed:bar"])
        text = "Real claim [seed:foo]. Numeric claim [1]. Both work."
        v = _verify_output(text, retr)
        # First sentence has clean entry id, second recovered numerically,
        # third has no citation (uncited).
        assert v["n_verified"] == 2
        assert v["n_recovered"] == 1

    def test_truly_bogus_citation_stays_unverified(self,
                                                       isolated_seeded_bank):
        """Regression guard against over-tolerant verification — a
        non-numeric, non-matching token must NOT be accepted."""
        from fantasyai.aurora.synthesis.rag import _verify_output
        retr = self._mk_retrieved(["seed:foo"])
        text = "Bogus claim [made_up_entry_99]. Real one [seed:foo]."
        v = _verify_output(text, retr)
        assert v["n_verified"] == 1   # only the seed:foo claim
        assert v["n_uncertain"] == 1  # bogus claim flagged

    def test_out_of_range_numeric_does_not_crash(self,
                                                    isolated_seeded_bank):
        """[42] when only 2 entries retrieved must fail cleanly, not raise."""
        from fantasyai.aurora.synthesis.rag import _verify_output
        retr = self._mk_retrieved(["seed:foo", "seed:bar"])
        text = "Claim [42]. Real claim [seed:foo]."
        v = _verify_output(text, retr)
        assert v["n_verified"] == 1


# ============================================================
# Prompt format — regression guard against the [N] collision
# ============================================================

class TestPromptFormatHardening:
    def test_findings_block_contains_no_bracket_markers(self,
                                                            physics_state):
        """The exact root cause was that the findings block used
        ``[0] [1] [2]`` markers and Gemma copied that format. Make
        sure we never reintroduce that."""
        from fantasyai.aurora.synthesis.rag import _format_findings
        text = _format_findings(physics_state)
        # The findings text must NOT contain any [N] markers — that
        # was the format that collided with [entry_id] citations.
        import re
        bracket_n = re.findall(r"\[\d+\]", text)
        assert not bracket_n, (
            f"findings block leaks numeric bracket markers: {bracket_n} — "
            "that collides with the citation grammar (Brief: RAG "
            "Synthesis Regression Diagnosis and Fix)"
        )

    def test_prompt_includes_allowed_citations_block(self,
                                                        isolated_seeded_bank,
                                                        physics_state):
        from fantasyai.aurora.synthesis.rag import _build_prompt
        from fantasyai.aurora.knowledge_bank.retrieval.engine import (
            retrieve_for_state,
        )
        retrieved = retrieve_for_state(physics_state, max_results=4)
        msgs = _build_prompt(physics_state, retrieved)
        user = msgs[1]["content"]
        assert "ALLOWED CITATIONS" in user
        for r in retrieved:
            # Every retrieved entry id appears explicitly in the prompt.
            assert f"[{r.entry.id}]" in user

    def test_prompt_warns_against_numeric_citations(self,
                                                       isolated_seeded_bank,
                                                       physics_state):
        from fantasyai.aurora.synthesis.rag import _build_prompt
        from fantasyai.aurora.knowledge_bank.retrieval.engine import (
            retrieve_for_state,
        )
        retrieved = retrieve_for_state(physics_state, max_results=4)
        msgs = _build_prompt(physics_state, retrieved)
        system = msgs[0]["content"]
        # The brief is explicit: tell the model NOT to use [1] etc.
        assert "[1]" in system or "[3]" in system  # the negative example
        assert "NEVER" in system.upper() or "never" in system


# ============================================================
# End-to-end engine wraps RAG correctly
# ============================================================

class TestEngineRagWrapping:
    def test_rag_path_end_to_end_with_mocked_ollama(self,
                                                        isolated_seeded_bank,
                                                        physics_state):
        """The headline acceptance criterion: with Ollama mocked to
        return well-cited prose, the engine sets synthesis_meta.mode =
        'rag' on a real-shape state."""
        canned = (
            "The damped harmonic oscillator finding matches a canonical "
            "physics prior [seed:damped_harmonic_oscillator]. The "
            "associated anomalies are consistent with this dynamics "
            "[seed:damped_harmonic_oscillator]. A small caveat: "
            "extrapolation past the observed regime is unreliable "
            "[seed:damped_harmonic_oscillator]."
        )
        with patch("fantasyai.aurora.synthesis.rag._ollama_reachable",
                     return_value=True), \
              patch("fantasyai.aurora.synthesis.rag._call_ollama",
                      return_value={"ok": True, "text": canned,
                                      "model": "mock-gemma:12b",
                                      "error": None}):
            from fantasyai.aurora.synthesis import synthesize, list_templates
            list_templates(force_reload=True)
            synthesize(physics_state)
        meta = physics_state["synthesis_meta"]
        assert meta["mode"] == "rag", (
            f"engine should set mode='rag' on successful RAG; got "
            f"mode={meta['mode']} reason={(meta.get('rag') or {}).get('reason')}"
        )
        assert meta["rag"]["n_claims_verified"] >= 1
        # The interpretive summary must be the LLM output, not a template.
        assert physics_state["interpretive_summary"] == canned

    def test_rag_falls_back_when_verification_fails_completely(self,
                                                                  isolated_seeded_bank,
                                                                  physics_state):
        """If the LLM returns prose with NO valid or recoverable
        citations, the engine should drop to template mode — not
        crash, not lie about the mode."""
        bogus = ("Claim one [bogus_entry_a]. Claim two [bogus_entry_b]. "
                  "Claim three [bogus_entry_c].")
        with patch("fantasyai.aurora.synthesis.rag._ollama_reachable",
                     return_value=True), \
              patch("fantasyai.aurora.synthesis.rag._call_ollama",
                      return_value={"ok": True, "text": bogus,
                                      "model": "mock", "error": None}):
            from fantasyai.aurora.synthesis import synthesize, list_templates
            list_templates(force_reload=True)
            synthesize(physics_state)
        # Engine routed to template; rag block carries the diagnosis.
        meta = physics_state["synthesis_meta"]
        assert meta["mode"] != "rag"
        assert meta["rag"]["mode"] == "rag_failed"
        assert "verification" in meta["rag"]["reason"].lower()

    def test_rag_falls_back_when_ollama_unreachable(self,
                                                        isolated_seeded_bank,
                                                        physics_state):
        with patch("fantasyai.aurora.synthesis.rag._ollama_reachable",
                     return_value=False):
            from fantasyai.aurora.synthesis import synthesize, list_templates
            list_templates(force_reload=True)
            synthesize(physics_state)
        meta = physics_state["synthesis_meta"]
        assert meta["mode"] != "rag"
        assert meta["rag"]["reason"] == "ollama_unreachable"

    def test_rag_logs_exception_with_traceback(self, isolated_seeded_bank,
                                                  physics_state, caplog):
        """If retrieval raises, the exception must be logged with
        exc_info=True so future regressions are diagnosable."""
        import logging
        caplog.set_level(logging.WARNING, logger="aurora.rag")
        with patch(
            "fantasyai.aurora.knowledge_bank.retrieval.engine.retrieve_for_state",
            side_effect=RuntimeError("synthetic retrieval failure"),
        ):
            from fantasyai.aurora.synthesis.rag import synthesize_rag
            res = synthesize_rag(physics_state)
        assert res["ok"] is False
        assert res["mode"] == "rag_failed"
        assert "retrieval_failed" in res["reason"]
        # The warning should have been logged with the exception info.
        any_traceback = any(
            r.exc_info and "synthetic retrieval failure" in str(r.exc_info[1])
            for r in caplog.records
        )
        assert any_traceback, (
            "fallback at retrieval must log with exc_info=True so the "
            "exception is recoverable from server logs alone"
        )


# ============================================================
# Synthesis-mode flag semantics — must not have changed
# ============================================================

class TestSynthesisModeFlag:
    def test_default_is_auto(self, monkeypatch):
        monkeypatch.delenv("AURORA_SYNTHESIS_MODE", raising=False)
        from fantasyai.aurora.synthesis.rag import synthesis_mode
        assert synthesis_mode() == "auto"

    def test_explicit_template_skips_rag(self, isolated_seeded_bank,
                                            physics_state, monkeypatch):
        monkeypatch.setenv("AURORA_SYNTHESIS_MODE", "template")
        # Even with Ollama mocked to be reachable, template mode must
        # never call it.
        with patch("fantasyai.aurora.synthesis.rag._call_ollama") as mock_call:
            from fantasyai.aurora.synthesis import synthesize, list_templates
            list_templates(force_reload=True)
            synthesize(physics_state)
            assert not mock_call.called, (
                "template mode must short-circuit before any LLM call"
            )
        assert physics_state["synthesis_meta"]["mode"] != "rag"

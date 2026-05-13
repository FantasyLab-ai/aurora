"""
Vector-index dim-mismatch regression tests.

Scenario reproduced (Brief: knowledge bank vector-index rebuild):
  1. Maintainer's bank was originally embedded with the 384-dim
     ``HashedTfIdfEmbedder`` fallback.
  2. After installing sentence-transformers, the active embedder
     becomes 768-dim ``SentenceTransformerEmbedder``.
  3. ``--seed-only`` re-ingest tried to push 768-dim vectors into the
     384-dim index and crashed with ``vectors shape (59, 768)
     incompatible with dim 384``.

These tests pin the fix:
  * Bank open detects mismatch and sets ``needs_vector_rebuild``.
  * ``rebuild_vector_index()`` backs up the .npy + .ids.json sidecars,
    re-embeds every entry, and saves a fresh index at the new dim.
  * ``run_seed_ingest`` triggers the rebuild automatically before
    any source runs, so the user-visible operation is "re-ingest seed"
    rather than "manually rebuild then re-ingest".
  * Preflight ``bank_embedder_consistency`` reports ``verdict =
    inconsistent`` with a clear ``fix`` field.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# Mock embedder so the test doesn't need sentence-transformers
# ============================================================

class _MockEmbedder768:
    """Stand-in for SentenceTransformerEmbedder. Deterministic so
    repeated calls produce identical vectors."""
    name = "sentence_transformer"
    model = "mock-mpnet-768"
    dim = 768

    def embed(self, text: str):
        # Deterministic hash → 768-d vector. Stable across runs so
        # we can assert specific entries are queryable post-rebuild.
        h = abs(hash(text or "")) % (2**31)
        rng = np.random.default_rng(h)
        v = rng.standard_normal(self.dim).astype(float)
        v /= np.linalg.norm(v) or 1.0
        return v.tolist()

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


# ============================================================
# Fixture — a fresh bank pre-populated by the 384-dim TF-IDF
# fallback embedder, then re-opened with a mock 768-dim embedder.
# ============================================================

@pytest.fixture
def mismatched_bank(tmp_path, monkeypatch):
    """Build a 384-dim bank, then swap the active embedder to a 768-dim
    mock. Returns the path so callers can re-open as needed."""
    db_path = tmp_path / "main.db"
    monkeypatch.setenv("AURORA_KB_PATH", str(db_path))

    # Step 1: open with the default (384-dim TF-IDF fallback) embedder
    # and seed-ingest. This persists a 384-dim index sidecar.
    import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
    ss._BANK = None
    bank = ss.get_bank()
    initial_dim = bank._vector_index.dim
    assert initial_dim == 384, ("test setup expects the default "
                                  "fallback embedder to be TF-IDF 384-d")
    from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
    run_seed_ingest()
    bank.save_vector_index()
    n_seeded = ss.get_bank().summary()["total_entries"]
    assert n_seeded > 0

    # Step 2: swap out the embedder for the 768-d mock and reset the
    # singleton so the next ``get_bank()`` call rebuilds with the new
    # embedder. Patch the IMPORTED reference inside sqlite_store —
    # that's the one the bank actually calls. Patching the embedder
    # module alone doesn't reach the bank because ``from .embedder
    # import get_embedder`` was resolved at import time.
    monkeypatch.setattr(
        "fantasyai.aurora.knowledge_bank.storage.sqlite_store.get_embedder",
        lambda: _MockEmbedder768(),
    )
    import fantasyai.aurora.knowledge_bank.storage.embedder as em
    monkeypatch.setattr(em, "get_embedder", lambda: _MockEmbedder768())
    ss._BANK = None
    yield db_path
    ss._BANK = None


# ============================================================
# Tests
# ============================================================

class TestRebuildOnDimMismatch:

    def test_open_detects_mismatch(self, mismatched_bank):
        """Bank construction must detect the dim disagreement and set
        the ``needs_vector_rebuild`` flag (without auto-rebuilding —
        that requires opt-in via run_seed_ingest or explicit call)."""
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        bank = ss.get_bank()
        assert bank._embedder.dim == 768
        assert bank._vector_index.dim == 384
        assert bank.needs_vector_rebuild is True

    def test_rebuild_vector_index_succeeds(self, mismatched_bank):
        """Calling rebuild_vector_index directly must:
          - back up the existing .npy + .ids.json
          - produce a fresh 768-d index
          - re-embed every non-revoked entry"""
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        bank = ss.get_bank()
        n_entries = bank.summary()["total_entries"]
        res = bank.rebuild_vector_index()
        assert res["ok"] is True
        assert res["new_dim"] == 768
        assert res["n_re_embedded"] == n_entries
        # Backups created — one for each sidecar (the .npy and the
        # .ids.json file). Filenames carry a "pre_rebuild_" infix.
        assert len(res["backups"]) == 2
        assert any("pre_rebuild_" in b and b.endswith(".npy")
                     for b in res["backups"])
        assert any("pre_rebuild_" in b and b.endswith(".json")
                     for b in res["backups"])
        # Index now matches embedder.
        assert bank._vector_index.dim == 768
        assert bank.needs_vector_rebuild is False

    def test_seed_reingest_auto_rebuilds(self, mismatched_bank):
        """The exact failing user-visible operation. Must NOT crash;
        the pipeline should auto-rebuild and then re-ingest."""
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        before = ss.get_bank().summary()["total_entries"]
        result = run_seed_ingest()
        assert "vector_index_rebuild" in result
        rb = result["vector_index_rebuild"]
        assert rb["ok"] is True
        assert rb["new_dim"] == 768
        # After rebuild, seed re-ingest itself succeeded.
        assert result["sources"]["seed"]["n_inserted"] >= 1
        # Bank invariant: dim aligned, no flag set.
        bank = ss.get_bank()
        assert bank._vector_index.dim == 768
        assert bank.needs_vector_rebuild is False
        # Total entries did not shrink.
        assert ss.get_bank().summary()["total_entries"] >= before

    def test_post_rebuild_search_returns_results(self, mismatched_bank):
        """End-to-end: after rebuild, retrieval against the index
        returns sensible results — proving entries are queryable."""
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        run_seed_ingest()
        bank = ss.get_bank()
        # Embed a query string with the mock embedder + search.
        q = bank._embedder.embed("oscillator that decays over time")
        hits = bank._vector_index.search(q, top_k=5)
        # Mock embedder is random; we don't assert specific ordering,
        # only that the search runs and returns the requested count.
        assert len(hits) <= 5
        assert len(hits) > 0  # non-empty
        # Every hit's id must resolve to a real entry in SQLite.
        for hid, _score in hits:
            assert bank.get(hid) is not None

    def test_idempotent_rebuild(self, mismatched_bank):
        """Calling rebuild twice in a row must not corrupt the bank;
        the second call detects the dims already match and is a
        cheap no-op (but still returns ok)."""
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        bank = ss.get_bank()
        bank.rebuild_vector_index()
        # No mismatch flag now.
        assert bank.needs_vector_rebuild is False
        n_first = bank._vector_index.__len__()
        # Second rebuild — still legal, still 768-d.
        res = bank.rebuild_vector_index()
        assert res["ok"] is True
        assert bank._vector_index.dim == 768
        assert bank._vector_index.__len__() == n_first

    def test_preflight_reports_inconsistent(self, mismatched_bank):
        """Preflight ``bank_embedder_consistency`` must report
        ``verdict = inconsistent`` with a clear ``fix`` string before
        the user has rebuilt."""
        from fantasyai.aurora.knowledge_bank.preflight import run_preflight
        rep = run_preflight()
        check = rep["checks"]["bank_embedder_consistency"]
        assert check.get("verdict") == "inconsistent"
        assert check.get("mismatch") is True
        assert check.get("vector_index_dim") == 384
        assert check.get("embedder_dim") == 768
        assert "fix" in check
        assert "--seed-only" in check["fix"]
        # And there's at least one warning about the mismatch.
        assert any("dim mismatch" in w.lower() or "dim" in w.lower()
                     for w in rep["warnings"])

    def test_preflight_reports_ok_after_rebuild(self, mismatched_bank):
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss.get_bank().rebuild_vector_index()
        from fantasyai.aurora.knowledge_bank.preflight import run_preflight
        rep = run_preflight()
        check = rep["checks"]["bank_embedder_consistency"]
        assert check.get("verdict") == "ok"
        assert check.get("mismatch") is False
        assert check.get("vector_index_dim") == 768

    def test_backups_written_to_disk(self, mismatched_bank):
        """The .npy + .ids.json backups should exist as actual files
        we can inspect — the user can roll back if needed."""
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        bank = ss.get_bank()
        res = bank.rebuild_vector_index()
        for b in res["backups"]:
            p = Path(b)
            assert p.exists(), f"backup not found on disk: {p}"


class TestNoMismatchPath:
    """Regression: when there's no dim mismatch (fresh bank, or
    previously-rebuilt bank), normal ingestion behaviour is unchanged."""

    def test_fresh_bank_no_rebuild_triggered(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AURORA_KB_PATH", str(tmp_path / "main.db"))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss._BANK = None
        bank = ss.get_bank()
        assert bank.needs_vector_rebuild is False
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        result = run_seed_ingest()
        # No rebuild ran because no mismatch existed.
        assert "vector_index_rebuild" not in result
        assert result["sources"]["seed"]["n_inserted"] > 0

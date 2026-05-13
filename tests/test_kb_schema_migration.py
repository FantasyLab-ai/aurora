"""
v1 → v2 schema migration regression tests.

Scenario reproduced: the maintainer's bank at ~/.aurora/knowledge_bank/main.db
was created in the previous sprint with 32 seed entries, before the
license columns existed. Opening it with the new code crashed with
``sqlite3.OperationalError: no such column: license`` because
``CREATE INDEX … ON entries(license)`` ran inside ``executescript``
BEFORE the v1 → v2 migration could ALTER TABLE.

These tests pin the fix:
  1. Build a v1 schema by hand (no license columns, no schema_version row).
  2. Insert v1-shape rows into it.
  3. Open with the current (post-fix) KnowledgeBankStore.
  4. Migration must run cleanly: license column added, schema_version=2,
     existing rows preserved with NULL license.
  5. Re-ingest the seed populates license values for re-ingested entries.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# Helpers — build a real v1 database by hand
# ============================================================

V1_SCHEMA = """
CREATE TABLE schema_version (
  version INTEGER PRIMARY KEY
);
CREATE TABLE entries (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_version TEXT NOT NULL,
  domain_csv TEXT,
  concept_type TEXT,
  review_status TEXT,
  name TEXT,
  aliases_json TEXT,
  definition TEXT,
  mechanism TEXT,
  related_concepts_json TEXT,
  pattern_signatures_json TEXT,
  quantitative_ranges_json TEXT,
  caveats_json TEXT,
  references_json TEXT,
  embedding_model TEXT,
  ingest_method TEXT,
  quality_score REAL,
  ingested_at TEXT,
  revoked INTEGER DEFAULT 0,
  revoked_reason TEXT
);
CREATE INDEX idx_entries_source  ON entries(source);
CREATE INDEX idx_entries_review  ON entries(review_status);
CREATE INDEX idx_entries_revoked ON entries(revoked);

CREATE TABLE pattern_triggers (
  entry_id TEXT,
  pattern_type TEXT,
  characteristics TEXT,
  detection_hints TEXT,
  FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
);
CREATE INDEX idx_pattern_type  ON pattern_triggers(pattern_type);
CREATE INDEX idx_pattern_entry ON pattern_triggers(entry_id);

CREATE TABLE domain_index (
  entry_id TEXT,
  domain   TEXT,
  FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
);
CREATE INDEX idx_domain_entry ON domain_index(entry_id);
CREATE INDEX idx_domain_value ON domain_index(domain);

CREATE TABLE alias_index (
  entry_id TEXT,
  alias    TEXT COLLATE NOCASE,
  FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
);
CREATE INDEX idx_alias_value ON alias_index(alias);
CREATE INDEX idx_alias_entry ON alias_index(entry_id);

CREATE TABLE sources (
  name TEXT PRIMARY KEY,
  version TEXT,
  last_ingested_at TEXT,
  n_entries INTEGER DEFAULT 0
);
"""


def _build_v1_database(path: Path, *, n_seed_rows: int = 5) -> None:
    """Create a SQLite file at ``path`` with the exact v1 schema and
    ``n_seed_rows`` rows. No license columns exist; no schema_version
    row exists (which is the second axis of the migration discriminator)."""
    conn = sqlite3.connect(str(path))
    conn.executescript(V1_SCHEMA)
    for i in range(n_seed_rows):
        conn.execute(
            """INSERT INTO entries (id, source, source_version, domain_csv,
                                       concept_type, review_status, name,
                                       definition, ingest_method,
                                       quality_score, ingested_at, revoked)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (f"seed:legacy_{i}", "aurora_seed", "2026-04-15",
              "bio,medical", "concept", "human_approved", f"legacy entry {i}",
              f"Hand-authored seed entry {i} from before the license schema "
              "existed.", "human_authored", 1.0, "2026-04-15T00:00:00Z"),
        )
    conn.commit()
    conn.close()


# ============================================================
# Tests
# ============================================================

class TestV1ToV2Migration:
    def test_v1_database_opens_without_crash(self, tmp_path, monkeypatch):
        """The reported failure mode: v1 bank opened with new code
        crashed at schema init. Must open cleanly post-fix."""
        db_path = tmp_path / "v1.db"
        _build_v1_database(db_path)

        # Reset bank singleton + point env at the v1 DB.
        monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss._BANK = None
        # Opening must not raise.
        bank = ss.get_bank()
        assert bank is not None

    def test_license_column_exists_after_migration(self, tmp_path,
                                                       monkeypatch):
        db_path = tmp_path / "v1.db"
        _build_v1_database(db_path)
        monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss._BANK = None
        bank = ss.get_bank()
        # Introspect: license + 3 sibling columns must now exist.
        cur = bank._conn.execute("PRAGMA table_info(entries)")
        cols = {row[1] for row in cur.fetchall()}
        for required in ("license", "license_url",
                          "license_verified_at", "commercial_use_allowed"):
            assert required in cols, f"missing v2 column: {required}"

    def test_schema_version_bumped_to_2(self, tmp_path, monkeypatch):
        db_path = tmp_path / "v1.db"
        _build_v1_database(db_path)
        monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss._BANK = None
        bank = ss.get_bank()
        cur = bank._conn.execute("SELECT MAX(version) FROM schema_version")
        assert int(cur.fetchone()[0]) == 2

    def test_existing_v1_rows_preserved(self, tmp_path, monkeypatch):
        """Migration is additive — existing entries must not be lost or
        corrupted. License columns simply default to NULL until the row
        is re-ingested."""
        db_path = tmp_path / "v1.db"
        _build_v1_database(db_path, n_seed_rows=5)
        monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss._BANK = None
        bank = ss.get_bank()
        cur = bank._conn.execute("SELECT COUNT(*) FROM entries")
        assert int(cur.fetchone()[0]) == 5
        cur = bank._conn.execute(
            "SELECT id, license FROM entries ORDER BY id"
        )
        rows = cur.fetchall()
        assert rows[0][0] == "seed:legacy_0"
        # license is NULL or empty until re-ingest populates it.
        assert rows[0][1] in (None, "")

    def test_idempotent_reopen(self, tmp_path, monkeypatch):
        """Opening a freshly-migrated DB a SECOND time must also succeed
        — the migration logic should detect schema_version=2 and become
        a no-op."""
        db_path = tmp_path / "v1.db"
        _build_v1_database(db_path)
        monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        for _ in range(3):
            ss._BANK = None
            bank = ss.get_bank()
            cur = bank._conn.execute("SELECT MAX(version) FROM schema_version")
            assert int(cur.fetchone()[0]) == 2
            # And exactly ONE row in schema_version (no duplicates from
            # repeated INSERTs).
            cur = bank._conn.execute("SELECT COUNT(*) FROM schema_version")
            assert int(cur.fetchone()[0]) == 1

    def test_seed_re_ingest_populates_license(self, tmp_path, monkeypatch):
        """After migration, --seed-only re-ingest should populate the
        license fields for the entries it actually inserts. (Legacy v1
        rows that are NOT in the canonical seed remain orphaned with
        NULL license — surfaced via the license_audit warning, not
        auto-overwritten.)"""
        db_path = tmp_path / "v1.db"
        _build_v1_database(db_path)
        monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss._BANK = None
        # Open + run the seed re-ingest.
        from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
        res = run_seed_ingest()
        seed = res["sources"]["seed"]
        assert seed["n_inserted"] > 0
        # Every entry the seed pipeline inserted DOES have a license.
        # (We identify them by the canonical seed:metabolic_syndrome
        # etc. IDs; any seed:legacy_N rows from the v1 fixture are
        # explicitly NOT in the seed canon and stay unlicensed.)
        bank = ss.get_bank()
        cur = bank._conn.execute(
            "SELECT id, license FROM entries "
            "WHERE source = 'aurora_seed' AND id NOT LIKE 'seed:legacy_%'"
        )
        rows = cur.fetchall()
        assert len(rows) >= seed["n_inserted"]
        for entry_id, lic in rows:
            assert lic, f"canonical seed entry {entry_id} has no license"

    def test_fresh_database_skips_migration(self, tmp_path, monkeypatch):
        """A fresh (non-existent) DB should land directly at v2 with
        license columns present, without going through the v1 path."""
        db_path = tmp_path / "fresh.db"
        assert not db_path.exists()
        monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss._BANK = None
        bank = ss.get_bank()
        cur = bank._conn.execute("PRAGMA table_info(entries)")
        cols = {row[1] for row in cur.fetchall()}
        assert "license" in cols
        cur = bank._conn.execute("SELECT MAX(version) FROM schema_version")
        assert int(cur.fetchone()[0]) == 2


class TestPostMigrationDownstreamChecks:
    """The two downstream checks that broke when the schema migration
    failed: license_audit and bank_embedder_consistency. Both query
    ``entries.license`` directly. After the migration fix they must
    succeed on a v1 → v2 database."""

    def test_license_audit_runs_on_migrated_database(self, tmp_path,
                                                        monkeypatch):
        db_path = tmp_path / "v1.db"
        _build_v1_database(db_path)
        monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss._BANK = None
        from fantasyai.aurora.knowledge_bank.preflight import run_preflight
        rep = run_preflight()
        la = rep["checks"]["license_audit"]
        assert "error" not in la, f"license_audit errored: {la.get('error')}"
        # Pre-migration rows are unlicensed; the audit must report this
        # as a count, not a crash.
        assert la["n_unlicensed"] >= 5

    def test_bank_embedder_consistency_runs_on_migrated_database(
            self, tmp_path, monkeypatch):
        db_path = tmp_path / "v1.db"
        _build_v1_database(db_path)
        monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss._BANK = None
        from fantasyai.aurora.knowledge_bank.preflight import run_preflight
        rep = run_preflight()
        check = rep["checks"]["bank_embedder_consistency"]
        assert "error" not in check, \
            f"bank_embedder_consistency errored: {check.get('error')}"


class TestFreshDatabaseStillWorks:
    """Brief 5's prior tests must still pass."""

    def test_fresh_database_inserts_with_license(self, tmp_path, monkeypatch):
        db_path = tmp_path / "fresh.db"
        monkeypatch.setenv("AURORA_KB_PATH", str(db_path))
        import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
        ss._BANK = None
        from fantasyai.aurora.knowledge_bank import get_bank
        from fantasyai.aurora.knowledge_bank.schema import (
            KnowledgeEntry, attach_license,
        )
        e = KnowledgeEntry(
            id="t:1", source="t", source_version="1",
            domain=["bio"], name="t",
            definition="a real definition string",
            review_status="auto_approved", ingest_method="ontology_etl",
        )
        attach_license(e, "Aurora Internal")
        get_bank().insert(e)
        got = get_bank().get("t:1")
        assert got is not None
        assert got.license == "Aurora Internal"

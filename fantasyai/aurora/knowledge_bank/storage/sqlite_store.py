"""
SQLite-backed knowledge-bank store.

Schema:
  entries(id TEXT PRIMARY KEY, source TEXT, source_version TEXT,
          domain_csv TEXT, concept_type TEXT, review_status TEXT,
          name TEXT, aliases_json TEXT, definition TEXT, mechanism TEXT,
          related_concepts_json TEXT,
          pattern_signatures_json TEXT, quantitative_ranges_json TEXT,
          caveats_json TEXT, references_json TEXT,
          embedding_model TEXT,
          ingest_method TEXT, quality_score REAL, ingested_at TEXT,
          revoked INTEGER, revoked_reason TEXT)

  -- Index over the structured trigger surface so retrieval can ask
  -- "find entries with pattern_type=oscillation in bio domain" without
  -- scanning every JSON blob.
  pattern_triggers(entry_id, pattern_type, characteristics, detection_hints)
  domain_index(entry_id, domain)
  alias_index(entry_id, alias)

  sources(name PRIMARY KEY, version, last_ingested_at, n_entries)

Embeddings live in a sidecar :class:`NumpyVectorIndex` (sister file
under the same data dir). The store is responsible for keeping the
SQL schema and the vector index in sync — additions and removals go
through both.

Bank file location: ``~/.aurora/knowledge_bank/main.db`` by default.
Override via ``AURORA_KB_PATH`` env var or the ``path`` arg to
:func:`get_bank`.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..schema import KnowledgeEntry, validate_entry
from .embedder import get_embedder
from .vector_index import NumpyVectorIndex


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def _default_bank_path() -> Path:
    env = os.environ.get("AURORA_KB_PATH")
    if env:
        return Path(env)
    return Path.home() / ".aurora" / "knowledge_bank" / "main.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class KnowledgeBankStore:
    """SQL-backed entry store with co-located vector index.

    Thread-safe via per-instance lock. SQLite itself is thread-safe with
    appropriate connection-per-thread; we use a single connection guarded
    by a Lock since Aurora's runtime is light on parallelism here.
    """

    SCHEMA_VERSION = 2     # Brief 5: bumped — added license columns

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else _default_bank_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        self._embedder = get_embedder()
        self._init_schema()
        self._vector_index = NumpyVectorIndex(dim=self._embedder.dim)
        # ``load()`` clobbers the index dim with whatever was on disk,
        # so a 384-dim TfIdf index on disk + a 768-dim neural embedder
        # in memory ends up with self._vector_index.dim = 384. Detect
        # that disagreement here so callers (preflight, run_seed_ingest)
        # can report or rebuild without crashing on the next add_batch.
        loaded = self._vector_index.load(self.path.with_suffix(""))
        self.needs_vector_rebuild = (
            loaded and self._vector_index.dim != self._embedder.dim
        )
        if self.needs_vector_rebuild:
            import logging
            logging.getLogger("aurora.kb").warning(
                "Vector index dim (%d) does not match active embedder "
                "dim (%d). Run `--seed-only` (or call "
                "bank.rebuild_vector_index()) to re-embed all entries.",
                self._vector_index.dim, self._embedder.dim,
            )

    # ---------- schema ----------

    def _init_schema(self) -> None:
        """Set up schema in the strict order: migrate-first, configure-second.

        The previous implementation ran one big ``executescript`` that
        included ``CREATE INDEX … ON entries(license)`` BEFORE the
        v1 → v2 ALTER TABLE could add the ``license`` column. Opening
        a v1 database (created last sprint with 32 seeds) crashed with
        ``no such column: license`` before any migration ran.

        Correct order:
          1. Create the ``schema_version`` table (always safe).
          2. Determine current version (default to 1 for legacy rows
             with no schema_version entry — those tables predate the
             license columns).
          3. Run any version-bump migrations (additive ALTER TABLE).
             Bump schema_version *after* each successful migration.
          4. Only NOW create the rest of the schema (entries table for
             fresh DBs, all indices, all secondary tables).
        Step 4 references columns that step 3 just added, so the
        ordering matters.
        """
        c = self._conn

        # ---- Step 1: schema_version table.
        c.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
              version INTEGER PRIMARY KEY
            );
        """)

        # ---- Step 2: read current version.
        existing_entries_table = self._table_exists("entries")
        cur = c.execute("SELECT MAX(version) FROM schema_version")
        row = cur.fetchone()
        cur_v = int(row[0]) if row and row[0] is not None else None
        if cur_v is None:
            # No version row. Two sub-cases:
            #   a) no entries table either → fresh DB; current = SCHEMA_VERSION.
            #   b) entries table exists with no version row → legacy v1 bank
            #      (created last sprint before schema_version was tracked).
            cur_v = 1 if existing_entries_table else self.SCHEMA_VERSION

        # ---- Step 3: run migrations BEFORE any column-dependent DDL.
        if cur_v < 2:
            self._migrate_v1_to_v2()
            cur_v = 2

        # Persist current version. INSERT OR REPLACE to keep one row.
        c.execute("DELETE FROM schema_version")
        c.execute("INSERT INTO schema_version (version) VALUES (?)",
                   (self.SCHEMA_VERSION,))

        # ---- Step 4: create-or-confirm the rest of the schema. Every
        # column referenced in an INDEX below is now guaranteed to
        # exist (either freshly created here or freshly migrated above).
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS entries (
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
              revoked_reason TEXT,
              license TEXT,
              license_url TEXT,
              license_verified_at TEXT,
              commercial_use_allowed INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_entries_source  ON entries(source);
            CREATE INDEX IF NOT EXISTS idx_entries_review  ON entries(review_status);
            CREATE INDEX IF NOT EXISTS idx_entries_revoked ON entries(revoked);
            CREATE INDEX IF NOT EXISTS idx_entries_license ON entries(license);

            CREATE TABLE IF NOT EXISTS quarantine (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              attempted_id TEXT,
              source TEXT,
              source_version TEXT,
              reason TEXT,
              raw_data_json TEXT,
              quarantined_at TEXT,
              resolved INTEGER DEFAULT 0,
              resolution TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_quar_source   ON quarantine(source);
            CREATE INDEX IF NOT EXISTS idx_quar_resolved ON quarantine(resolved);

            CREATE TABLE IF NOT EXISTS pattern_triggers (
              entry_id TEXT,
              pattern_type TEXT,
              characteristics TEXT,
              detection_hints TEXT,
              FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_pattern_type  ON pattern_triggers(pattern_type);
            CREATE INDEX IF NOT EXISTS idx_pattern_entry ON pattern_triggers(entry_id);

            CREATE TABLE IF NOT EXISTS domain_index (
              entry_id TEXT,
              domain   TEXT,
              FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_domain_entry ON domain_index(entry_id);
            CREATE INDEX IF NOT EXISTS idx_domain_value ON domain_index(domain);

            CREATE TABLE IF NOT EXISTS alias_index (
              entry_id TEXT,
              alias    TEXT COLLATE NOCASE,
              FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_alias_value ON alias_index(alias);
            CREATE INDEX IF NOT EXISTS idx_alias_entry ON alias_index(entry_id);

            CREATE TABLE IF NOT EXISTS sources (
              name TEXT PRIMARY KEY,
              version TEXT,
              last_ingested_at TEXT,
              n_entries INTEGER DEFAULT 0
            );
            """
        )
        c.commit()

    def _table_exists(self, name: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        )
        return cur.fetchone() is not None

    def _column_exists(self, table: str, column: str) -> bool:
        cur = self._conn.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cur.fetchall())

    def _migrate_v1_to_v2(self) -> None:
        """Additive migration: add the four license columns to a v1
        entries table. Idempotent — uses PRAGMA introspection to skip
        columns that already exist."""
        if not self._table_exists("entries"):
            # Fresh DB; the CREATE TABLE in step 4 will produce a v2
            # schema directly. Nothing to migrate.
            return
        v2_columns = (
            ("license",                "TEXT"),
            ("license_url",            "TEXT"),
            ("license_verified_at",    "TEXT"),
            ("commercial_use_allowed", "INTEGER DEFAULT 0"),
        )
        for col, ddl in v2_columns:
            if not self._column_exists("entries", col):
                # The ALTER may still raise if a partial migration left
                # the column behind under a different shape; swallow and
                # continue rather than blocking the whole bank.
                try:
                    self._conn.execute(
                        f"ALTER TABLE entries ADD COLUMN {col} {ddl}"
                    )
                except sqlite3.OperationalError:
                    pass

    # ---------- vector index rebuild ----------

    def rebuild_vector_index(self, *,
                                new_dim: Optional[int] = None,
                                progress_cb=None) -> Dict[str, Any]:
        """Wipe and rebuild the vector index from the entries table.

        Used when the active embedder's dim differs from what the
        persisted index was built with (e.g. upgrading from the 384-dim
        TF-IDF fallback to the 768-dim neural embedder). The SQLite
        entries themselves are preserved — only the vector index is
        wiped and regenerated.

        Returns a structured report with backup paths, counts, and the
        new dim so the caller (run_seed_ingest, preflight, admin UI)
        can surface the operation.
        """
        target_dim = int(new_dim or self._embedder.dim)
        base = self.path.with_suffix("")
        npy = Path(str(base) + ".npy")
        ids = Path(str(base) + ".ids.json")
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        # Microsecond-resolution suffix so successive rebuilds in the
        # same second don't collide.
        usec = _dt.now().strftime("%f")
        backups: List[str] = []
        with self._lock:
            # Backup the existing sidecars before wiping.
            for p in (npy, ids):
                if p.exists():
                    bp = p.with_name(
                        p.stem + f".pre_rebuild_{ts}_{usec}" + p.suffix
                    )
                    # If the destination already exists (e.g. extreme
                    # rapid succession), append a counter.
                    counter = 0
                    while bp.exists():
                        counter += 1
                        bp = p.with_name(
                            p.stem
                            + f".pre_rebuild_{ts}_{usec}_{counter}"
                            + p.suffix
                        )
                    p.rename(bp)
                    backups.append(str(bp))

            # Fresh in-memory index at the new dim.
            from .vector_index import NumpyVectorIndex
            self._vector_index = NumpyVectorIndex(dim=target_dim)

            # Walk every non-revoked entry and re-embed. Iterate in
            # batches so we never load the whole bank into memory.
            cur = self._conn.execute(
                """SELECT id, name, definition, mechanism
                   FROM entries WHERE revoked = 0 ORDER BY id"""
            )
            BATCH = 256
            ids_buf: List[str] = []
            vec_buf: List[List[float]] = []
            n_done = 0
            n_failed = 0
            while True:
                rows = cur.fetchmany(BATCH)
                if not rows:
                    break
                for eid, name, definition, mechanism in rows:
                    text = ((name or "") + ". "
                             + (definition or "") + " "
                             + (mechanism or "")).strip()
                    try:
                        v = self._embedder.embed(text)
                        if len(v) != target_dim:
                            raise ValueError(
                                f"embedder returned dim {len(v)}, "
                                f"expected {target_dim}"
                            )
                        ids_buf.append(eid)
                        vec_buf.append(v)
                    except Exception:
                        n_failed += 1
                        continue
                    # Also persist the new embedding model on the row
                    # so we don't lose track of which model produced it.
                    self._conn.execute(
                        "UPDATE entries SET embedding_model = ? WHERE id = ?",
                        (self._embedder.model, eid),
                    )
                if ids_buf:
                    self._vector_index.add_batch(ids_buf, vec_buf)
                    n_done += len(ids_buf)
                    ids_buf = []
                    vec_buf = []
                if progress_cb:
                    progress_cb({"phase": "rebuild_vector_index",
                                  "n_done": n_done, "n_failed": n_failed,
                                  "target_dim": target_dim})

            self._conn.commit()
            # Persist the new index alongside the bank.
            self._vector_index.save(base)
            # The bank is now consistent again.
            self.needs_vector_rebuild = False

        return {
            "ok": True,
            "rebuilt": True,
            "old_dim": (int(self._vector_index.dim)
                          if self._vector_index.dim != target_dim else None),
            "new_dim": target_dim,
            "n_re_embedded": n_done,
            "n_failed": n_failed,
            "backups": backups,
            "embedder_model": self._embedder.model,
        }

    # ---------- core ops ----------

    def insert(self, entry: KnowledgeEntry, *,
                 validate: bool = True,
                 generate_embedding: bool = True) -> Dict[str, Any]:
        """Insert (or replace) a single entry. Returns ``{ok, errors}``.

        When ``generate_embedding`` is True and entry.embedding is None,
        an embedding is computed from definition + mechanism + name.
        """
        if validate:
            v = validate_entry(entry)
            if not v["ok"]:
                return v
        # Embedding generation.
        if generate_embedding and entry.embedding is None:
            text = (entry.name + ". " + entry.definition + " "
                     + entry.mechanism)
            entry.embedding = self._embedder.embed(text)
            entry.embedding_model = self._embedder.model
        if not entry.ingested_at:
            entry.ingested_at = _now_iso()

        with self._lock:
            self._upsert_row(entry)
            self._reindex_entry(entry)
            self._conn.commit()
        return {"ok": True, "errors": [], "warnings": []}

    def insert_many(self, entries: Iterable[KnowledgeEntry], *,
                      validate: bool = True,
                      generate_embedding: bool = True,
                      progress_cb=None,
                      quarantine_failures: bool = True) -> Dict[str, Any]:
        """Bulk insert with optional quarantine of failed entries.

        When ``quarantine_failures`` is True (Brief 5 default), entries
        that fail validation (license missing, schema invalid, etc.)
        are written to the quarantine table for maintainer review
        rather than silently skipped.
        """
        ok = 0
        errs: List[Tuple[str, List[str]]] = []
        n_quarantined = 0
        ids: List[str] = []
        vecs: List[List[float]] = []
        with self._lock:
            for i, entry in enumerate(entries):
                if validate:
                    v = validate_entry(entry)
                    if not v["ok"]:
                        if len(errs) < 20:
                            errs.append((entry.id, v["errors"]))
                        if quarantine_failures:
                            self._quarantine(entry, "; ".join(v["errors"]))
                            n_quarantined += 1
                        continue
                if generate_embedding and entry.embedding is None:
                    text = (entry.name + ". " + entry.definition + " "
                             + entry.mechanism)
                    entry.embedding = self._embedder.embed(text)
                    entry.embedding_model = self._embedder.model
                if not entry.ingested_at:
                    entry.ingested_at = _now_iso()
                self._upsert_row(entry)
                self._reindex_entry(entry, defer_vector=True)
                if entry.embedding:
                    ids.append(entry.id)
                    vecs.append(entry.embedding)
                ok += 1
                if progress_cb and (ok % 100 == 0):
                    progress_cb(ok)
            if ids:
                # Add to index in one batch.
                self._vector_index.add_batch(ids, vecs)
            self._conn.commit()
        return {"ok": True, "n_inserted": ok, "n_errors": len(errs),
                 "first_errors": errs, "n_quarantined": n_quarantined}

    # ---------- quarantine ----------

    def _quarantine(self, entry: KnowledgeEntry, reason: str) -> None:
        """Persist a failed-validation entry for maintainer review."""
        try:
            raw = entry.to_json()
        except Exception:
            raw = json.dumps({"error": "could_not_serialise"})
        self._conn.execute(
            """INSERT INTO quarantine (attempted_id, source, source_version,
                                          reason, raw_data_json, quarantined_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry.id, entry.source, entry.source_version,
              reason[:500], raw, _now_iso()),
        )

    def list_quarantine(self, *, only_unresolved: bool = True,
                          limit: int = 200) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM quarantine"
        if only_unresolved:
            sql += " WHERE resolved = 0"
        sql += f" ORDER BY id DESC LIMIT {int(limit)}"
        cur = self._conn.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def resolve_quarantine(self, qid: int, resolution: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE quarantine SET resolved = 1, resolution = ? WHERE id = ?",
                (resolution, qid),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def _upsert_row(self, entry: KnowledgeEntry) -> None:
        d = entry.to_dict()
        self._conn.execute(
            """INSERT OR REPLACE INTO entries
               (id, source, source_version, domain_csv, concept_type,
                review_status, name, aliases_json, definition, mechanism,
                related_concepts_json, pattern_signatures_json,
                quantitative_ranges_json, caveats_json, references_json,
                embedding_model, ingest_method, quality_score,
                ingested_at, revoked, revoked_reason,
                license, license_url, license_verified_at,
                commercial_use_allowed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?)
            """,
            (
                d["id"], d["source"], d["source_version"],
                ",".join(d.get("domain") or []),
                d.get("concept_type"), d.get("review_status"),
                d.get("name"),
                json.dumps(d.get("aliases") or []),
                d.get("definition"), d.get("mechanism"),
                json.dumps(d.get("related_concepts") or []),
                json.dumps(d.get("pattern_signatures") or []),
                json.dumps(d.get("quantitative_ranges") or []),
                json.dumps(d.get("caveats") or []),
                json.dumps(d.get("references") or []),
                d.get("embedding_model"),
                d.get("ingest_method"), float(d.get("quality_score") or 1.0),
                d.get("ingested_at"),
                1 if d.get("revoked") else 0, d.get("revoked_reason"),
                d.get("license") or "",
                d.get("license_url") or "",
                d.get("license_verified_at"),
                1 if d.get("commercial_use_allowed") else 0,
            ),
        )
        # Bump source counter.
        self._conn.execute(
            """INSERT INTO sources (name, version, last_ingested_at, n_entries)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(name) DO UPDATE SET
                 version=excluded.version,
                 last_ingested_at=excluded.last_ingested_at,
                 n_entries=n_entries + 1""",
            (d["source"], d["source_version"], _now_iso()),
        )

    def _reindex_entry(self, entry: KnowledgeEntry, *,
                         defer_vector: bool = False) -> None:
        """Refresh the structured trigger / domain / alias indices for
        one entry. Called after upsert."""
        c = self._conn
        c.execute("DELETE FROM pattern_triggers WHERE entry_id = ?",
                   (entry.id,))
        c.execute("DELETE FROM domain_index WHERE entry_id = ?",
                   (entry.id,))
        c.execute("DELETE FROM alias_index WHERE entry_id = ?",
                   (entry.id,))
        for ps in entry.pattern_signatures:
            c.execute(
                """INSERT INTO pattern_triggers
                   (entry_id, pattern_type, characteristics, detection_hints)
                   VALUES (?, ?, ?, ?)""",
                (entry.id, (ps.pattern_type or "").lower(),
                  ps.characteristics or "", ps.detection_hints or ""),
            )
        for d in (entry.domain or []):
            c.execute("INSERT INTO domain_index (entry_id, domain) VALUES (?, ?)",
                       (entry.id, str(d).lower()))
        c.execute("INSERT INTO alias_index (entry_id, alias) VALUES (?, ?)",
                   (entry.id, (entry.name or "").lower()))
        for a in (entry.aliases or []):
            if a:
                c.execute("INSERT INTO alias_index (entry_id, alias) VALUES (?, ?)",
                           (entry.id, str(a).lower()))
        # Vector index.
        if not defer_vector and entry.embedding is not None:
            # Replace existing vector if present.
            self._vector_index.remove(entry.id)
            self._vector_index.add(entry.id, entry.embedding)

    # ---------- queries ----------

    def get(self, entry_id: str) -> Optional[KnowledgeEntry]:
        cur = self._conn.execute(
            "SELECT * FROM entries WHERE id = ? AND revoked = 0",
            (entry_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return _row_to_entry(self._conn, row)

    def get_many(self, entry_ids: Sequence[str]) -> List[KnowledgeEntry]:
        if not entry_ids:
            return []
        # Chunked IN-clause to keep SQL parameter count bounded.
        out: List[KnowledgeEntry] = []
        chunk = 500
        for i in range(0, len(entry_ids), chunk):
            ids = list(entry_ids[i:i + chunk])
            placeholders = ",".join("?" * len(ids))
            cur = self._conn.execute(
                f"SELECT * FROM entries WHERE id IN ({placeholders}) AND revoked = 0",
                ids,
            )
            for row in cur.fetchall():
                out.append(_row_to_entry(self._conn, row))
        return out

    def find_by_pattern(self, pattern_type: str,
                          *, domains: Optional[Sequence[str]] = None,
                          limit: int = 50) -> List[str]:
        """Return entry IDs whose pattern_signatures match ``pattern_type``."""
        sql = ("SELECT pt.entry_id FROM pattern_triggers pt "
               "JOIN entries e ON e.id = pt.entry_id "
               "WHERE pt.pattern_type = ? AND e.revoked = 0")
        args: List[Any] = [pattern_type.lower()]
        if domains:
            placeholders = ",".join("?" * len(domains))
            sql += (f" AND pt.entry_id IN (SELECT entry_id FROM domain_index "
                    f"WHERE domain IN ({placeholders}))")
            args.extend([d.lower() for d in domains])
        sql += f" LIMIT {int(limit)}"
        cur = self._conn.execute(sql, args)
        return [r[0] for r in cur.fetchall()]

    def find_by_alias(self, term: str,
                        *, domains: Optional[Sequence[str]] = None,
                        limit: int = 20) -> List[str]:
        """Return entry IDs matching ``term`` (case-insensitive prefix on alias_index)."""
        sql = ("SELECT DISTINCT a.entry_id FROM alias_index a "
               "JOIN entries e ON e.id = a.entry_id "
               "WHERE a.alias LIKE ? AND e.revoked = 0")
        args: List[Any] = [term.lower() + "%"]
        if domains:
            placeholders = ",".join("?" * len(domains))
            sql += (f" AND a.entry_id IN (SELECT entry_id FROM domain_index "
                    f"WHERE domain IN ({placeholders}))")
            args.extend([d.lower() for d in domains])
        sql += f" LIMIT {int(limit)}"
        cur = self._conn.execute(sql, args)
        return [r[0] for r in cur.fetchall()]

    def search_vector(self, query_text_or_vec, top_k: int = 20,
                        *, domains: Optional[Sequence[str]] = None
                        ) -> List[Tuple[str, float]]:
        """Semantic top-k. Accepts a text string or a pre-computed vector."""
        if isinstance(query_text_or_vec, str):
            vec = self._embedder.embed(query_text_or_vec)
        else:
            vec = query_text_or_vec
        candidate_ids: Optional[List[str]] = None
        if domains:
            placeholders = ",".join("?" * len(domains))
            cur = self._conn.execute(
                f"""SELECT DISTINCT entry_id FROM domain_index
                    WHERE domain IN ({placeholders})""",
                [d.lower() for d in domains],
            )
            candidate_ids = [r[0] for r in cur.fetchall()]
            if not candidate_ids:
                return []
        return self._vector_index.search(vec, top_k=top_k,
                                            candidate_ids=candidate_ids)

    # ---------- admin ----------

    def revoke(self, entry_id: str, reason: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE entries SET revoked = 1, revoked_reason = ? WHERE id = ?",
                (reason, entry_id),
            )
            self._vector_index.remove(entry_id)
            self._conn.commit()
            return cur.rowcount > 0

    def summary(self) -> Dict[str, Any]:
        cur = self._conn.execute(
            """SELECT source, COUNT(*),
                      SUM(CASE WHEN review_status='auto_approved' THEN 1 ELSE 0 END) auto,
                      SUM(CASE WHEN review_status='pending_review' THEN 1 ELSE 0 END) pending,
                      SUM(CASE WHEN review_status='human_approved' THEN 1 ELSE 0 END) human
               FROM entries WHERE revoked = 0
               GROUP BY source"""
        )
        per_source = []
        total = 0
        for src, n, n_auto, n_pending, n_human in cur.fetchall():
            per_source.append({
                "source": src, "n": int(n),
                "auto_approved": int(n_auto or 0),
                "pending_review": int(n_pending or 0),
                "human_approved": int(n_human or 0),
            })
            total += int(n)
        try:
            cur2 = self._conn.execute(
                """SELECT di.domain, COUNT(DISTINCT di.entry_id)
                   FROM domain_index di
                   INNER JOIN entries e ON e.id = di.entry_id
                   WHERE e.revoked = 0
                   GROUP BY di.domain"""
            )
            per_domain = [{"domain": d, "n": int(n)} for d, n in cur2.fetchall()]
        except Exception:
            per_domain = []
        return {
            "total_entries": total,
            "per_source": per_source,
            "per_domain": per_domain,
            "n_vectors": len(self._vector_index),
            "embedding_model": self._embedder.model,
            "embedding_dim": self._embedder.dim,
            "path": str(self.path),
        }

    def save_vector_index(self) -> None:
        self._vector_index.save(self.path.with_suffix(""))

    def close(self) -> None:
        self.save_vector_index()
        self._conn.close()


# ---------------------------------------------------------------------------
# Row → entry hydration
# ---------------------------------------------------------------------------

def _row_to_entry(conn: sqlite3.Connection, row: Tuple) -> KnowledgeEntry:
    cols = [c[0] for c in conn.execute("SELECT * FROM entries LIMIT 0").description]
    d = dict(zip(cols, row))
    return KnowledgeEntry.from_dict({
        "id": d["id"], "source": d["source"],
        "source_version": d["source_version"],
        "domain": (d["domain_csv"] or "").split(",") if d["domain_csv"] else [],
        "concept_type": d["concept_type"] or "",
        "review_status": d["review_status"] or "pending_review",
        "name": d["name"] or "",
        "aliases": json.loads(d["aliases_json"] or "[]"),
        "definition": d["definition"] or "",
        "mechanism": d["mechanism"] or "",
        "related_concepts": json.loads(d["related_concepts_json"] or "[]"),
        "pattern_signatures": json.loads(d["pattern_signatures_json"] or "[]"),
        "quantitative_ranges": json.loads(d["quantitative_ranges_json"] or "[]"),
        "caveats": json.loads(d["caveats_json"] or "[]"),
        "references": json.loads(d["references_json"] or "[]"),
        "embedding_model": d["embedding_model"],
        "ingest_method": d["ingest_method"] or "human_authored",
        "quality_score": float(d["quality_score"] or 1.0),
        "ingested_at": d["ingested_at"],
        "revoked": bool(d["revoked"]),
        "revoked_reason": d["revoked_reason"],
        # Brief 5 — license fields. Use .get(k) on the dict-style sqlite
        # row so older schemas (no column) round-trip cleanly.
        "license": (d.get("license") if hasattr(d, "get")
                      else (d["license"] if "license" in d else "")) or "",
        "license_url": (d.get("license_url") if hasattr(d, "get")
                          else (d["license_url"] if "license_url" in d else "")) or "",
        "license_verified_at": (d.get("license_verified_at") if hasattr(d, "get")
                                  else (d["license_verified_at"] if "license_verified_at" in d else None)),
        "commercial_use_allowed": bool(
            d.get("commercial_use_allowed") if hasattr(d, "get")
            else (d["commercial_use_allowed"]
                  if "commercial_use_allowed" in d else 0)
        ),
    })


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_BANK: Optional[KnowledgeBankStore] = None
_BANK_LOCK = threading.Lock()


def get_bank(path: Optional[Path] = None) -> KnowledgeBankStore:
    """Return the singleton KnowledgeBankStore, creating it lazily."""
    global _BANK
    with _BANK_LOCK:
        if _BANK is None or (path is not None and Path(path) != _BANK.path):
            _BANK = KnowledgeBankStore(path)
        return _BANK

"""
Standalone migration CLI for the knowledge bank.

The schema migration runs automatically every time
:class:`KnowledgeBankStore` opens the database, so this CLI is a backup
path: it lets the maintainer run the migration explicitly (with a
diagnostic report) and back up the existing DB before any column
operations run.

Usage:
  python -m fantasyai.aurora.knowledge_bank.migrate
  python -m fantasyai.aurora.knowledge_bank.migrate --backup
  python -m fantasyai.aurora.knowledge_bank.migrate --rebuild
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def _detect_schema_version(db_path: Path) -> Dict[str, Any]:
    """Open the DB read-only-ish, report version + license-column status."""
    if not db_path.exists():
        return {"exists": False, "version": None,
                "has_license_column": False}
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='schema_version'"
        )
        has_version_table = cur.fetchone() is not None
        version = None
        if has_version_table:
            try:
                cur = conn.execute("SELECT MAX(version) FROM schema_version")
                row = cur.fetchone()
                if row and row[0] is not None:
                    version = int(row[0])
            except sqlite3.OperationalError:
                version = None
        # If no version row but we have an entries table, treat as v1.
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='entries'"
        )
        has_entries = cur.fetchone() is not None
        if version is None and has_entries:
            version = 1
        # license column?
        has_license = False
        n_entries = 0
        if has_entries:
            cur = conn.execute("PRAGMA table_info(entries)")
            has_license = any(row[1] == "license"
                                for row in cur.fetchall())
            cur = conn.execute("SELECT COUNT(*) FROM entries")
            n_entries = int(cur.fetchone()[0])
        return {
            "exists": True, "version": version,
            "has_license_column": has_license,
            "has_entries_table": has_entries,
            "n_entries": n_entries,
        }
    finally:
        conn.close()


def _backup(db_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(
        f"{db_path.stem}.pre_migration_{ts}{db_path.suffix}"
    )
    shutil.copy2(str(db_path), str(backup_path))
    return backup_path


def _migrate(db_path: Path) -> Dict[str, Any]:
    """Run the in-store migration by simply opening the bank."""
    import os
    os.environ["AURORA_KB_PATH"] = str(db_path)
    import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
    ss._BANK = None
    bank = ss.get_bank()  # this triggers _init_schema → migration
    cur = bank._conn.execute("SELECT MAX(version) FROM schema_version")
    return {"ok": True,
             "schema_version_after": int(cur.fetchone()[0])}


def _rebuild(db_path: Path) -> Dict[str, Any]:
    """Back up the existing DB, delete it, then create a fresh v2 bank
    and re-ingest the seed."""
    backup = _backup(db_path) if db_path.exists() else None
    if db_path.exists():
        db_path.unlink()
    # Touching the bank initializes a fresh v2 schema.
    import os
    os.environ["AURORA_KB_PATH"] = str(db_path)
    import fantasyai.aurora.knowledge_bank.storage.sqlite_store as ss
    ss._BANK = None
    ss.get_bank()
    from fantasyai.aurora.knowledge_bank.ingest import run_seed_ingest
    res = run_seed_ingest()
    return {"ok": True, "backup": str(backup) if backup else None,
             "seed_result": res}


def main() -> int:
    p = argparse.ArgumentParser(prog="aurora-kb-migrate",
                                  description="Knowledge-bank schema migration.")
    p.add_argument("--db",
                    help="Override AURORA_KB_PATH for this run")
    p.add_argument("--backup", action="store_true",
                    help="Copy the DB to <name>.pre_migration_<ts>.db before "
                         "migrating")
    p.add_argument("--rebuild", action="store_true",
                    help="Backup + delete the existing DB, then create a "
                         "fresh v2 bank and re-ingest the seed. Use only "
                         "when migration cannot proceed.")
    p.add_argument("--check", action="store_true",
                    help="Report schema version + column status, do nothing")
    args = p.parse_args()

    if args.db:
        import os
        os.environ["AURORA_KB_PATH"] = args.db
    from fantasyai.aurora.knowledge_bank.storage.sqlite_store import (
        _default_bank_path,
    )
    db_path = Path(args.db) if args.db else _default_bank_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    info = _detect_schema_version(db_path)
    print("=" * 70)
    print(f"Aurora KB migration · {db_path}")
    print("=" * 70)
    print(f"  exists:               {info['exists']}")
    print(f"  detected version:     {info.get('version')}")
    print(f"  has license column:   {info.get('has_license_column')}")
    print(f"  has entries table:    {info.get('has_entries_table')}")
    print(f"  n entries:            {info.get('n_entries')}")
    print()

    if args.check:
        return 0

    if args.rebuild:
        print("Rebuilding (backup + delete + fresh schema + seed re-ingest)…")
        res = _rebuild(db_path)
        print(f"  backup:               {res['backup']}")
        print(f"  seed_inserted:        "
                f"{res['seed_result']['sources']['seed']['n_inserted']}")
        return 0

    if args.backup and info.get("exists"):
        bp = _backup(db_path)
        print(f"  backup written:       {bp}")

    print("Running migration via in-store schema initializer…")
    res = _migrate(db_path)
    print(f"  schema_version after: {res['schema_version_after']}")
    print()
    print("OK. The bank is ready for ingestion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

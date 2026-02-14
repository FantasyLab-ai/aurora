import sqlite3, sys
from pathlib import Path

root = Path.home() / "Desktop" / "FantasyAI"
db_path = root / "jobs.db"

conn = sqlite3.connect(str(db_path))
try:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(jobs)")
    cols = [row[1] for row in cur.fetchall()]
    if "account" not in cols:
        cur.execute("ALTER TABLE jobs ADD COLUMN account TEXT DEFAULT 'main'")
        conn.commit()
        print("[migrate] added account column with default=main")
    else:
        print("[migrate] account column already exists")
finally:
    conn.close()

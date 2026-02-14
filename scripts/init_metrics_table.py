import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "jobs.db"

DDL = """
CREATE TABLE IF NOT EXISTS video_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    platform TEXT NOT NULL,
    video_url TEXT,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    collected_at REAL NOT NULL,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_video_metrics_job ON video_metrics(job_id);
CREATE INDEX IF NOT EXISTS idx_video_metrics_url ON video_metrics(video_url);
"""

def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(DDL)
    conn.commit()
    conn.close()
    print(f"[init_metrics_table] video_metrics table ready at {DB_PATH}")

if __name__ == "__main__":
    main()

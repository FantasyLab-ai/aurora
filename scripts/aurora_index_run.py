import json
import sqlite3
from pathlib import Path
from datetime import datetime


DB_PATH = Path(r"C:\Users\bgrut\Desktop\Aurora_QIE\aurora_runs.db")


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _ensure_schema(conn: sqlite3.Connection):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS aurora_runs (
        run_id TEXT PRIMARY KEY,
        run_dir TEXT NOT NULL,
        created_at TEXT,
        dataset TEXT,
        seed INTEGER,
        status TEXT,
        meta_json TEXT,
        scorecard_json TEXT,
        plots_index_json TEXT
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS aurora_artifacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        path TEXT NOT NULL,
        mime TEXT,
        created_at TEXT,
        UNIQUE(run_id, kind, name),
        FOREIGN KEY(run_id) REFERENCES aurora_runs(run_id)
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS aurora_insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT,
        UNIQUE(run_id, kind),
        FOREIGN KEY(run_id) REFERENCES aurora_runs(run_id)
    )
    """)
    conn.commit()


def index_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(str(run_dir))

    run_id = run_dir.name
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    # Core files
    p_run_meta = run_dir / "_RUN_META.json"
    p_score = run_dir / "_ENGINE_SCORECARD.json"
    p_plots_index = run_dir / "_PLOTS_INDEX.json"

    run_meta = _read_json(p_run_meta) or {}
    score = _read_json(p_score) or {}
    plots_index = _read_json(p_plots_index) or {}

    dataset = run_meta.get("dataset") or run_meta.get("dataset_name") or run_meta.get("name")
    seed = run_meta.get("seed")
    created_at = run_meta.get("created_at") or run_meta.get("started_at") or None

    # If created_at looks like epoch seconds, convert; otherwise store as-is
    if isinstance(created_at, (int, float)):
        created_at = datetime.utcfromtimestamp(created_at).isoformat(timespec="seconds") + "Z"

    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_schema(conn)

        # Upsert run row
        conn.execute("""
        INSERT INTO aurora_runs (run_id, run_dir, created_at, dataset, seed, status, meta_json, scorecard_json, plots_index_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            run_dir=excluded.run_dir,
            created_at=COALESCE(excluded.created_at, aurora_runs.created_at),
            dataset=COALESCE(excluded.dataset, aurora_runs.dataset),
            seed=COALESCE(excluded.seed, aurora_runs.seed),
            status=excluded.status,
            meta_json=excluded.meta_json,
            scorecard_json=excluded.scorecard_json,
            plots_index_json=excluded.plots_index_json
        """, (
            run_id,
            str(run_dir),
            created_at,
            dataset,
            seed if isinstance(seed, int) else None,
            "indexed",
            json.dumps(run_meta, ensure_ascii=False),
            json.dumps(score, ensure_ascii=False),
            json.dumps(plots_index, ensure_ascii=False),
        ))

        # Artifacts: key json files
        for name, p in [
            ("_RUN_META.json", p_run_meta),
            ("_ENGINE_SCORECARD.json", p_score),
            ("_PLOTS_INDEX.json", p_plots_index),
        ]:
            if p.exists():
                conn.execute("""
                INSERT OR IGNORE INTO aurora_artifacts (run_id, kind, name, path, mime, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (run_id, "json", name, str(p), "application/json", now))

        # Artifacts: plots
        plots_dir = run_dir / "plots"
        if plots_dir.exists():
            for p in plots_dir.glob("*.png"):
                conn.execute("""
                INSERT OR IGNORE INTO aurora_artifacts (run_id, kind, name, path, mime, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (run_id, "plot", p.name, str(p), "image/png", now))

        # Insights: plots_data json payloads
        plots_data_dir = run_dir / "plots_data"
        if plots_data_dir.exists():
            for p in plots_data_dir.glob("*.json"):
                payload = _read_json(p)
                if payload is None:
                    continue
                kind = payload.get("kind") or p.stem
                conn.execute("""
                INSERT INTO aurora_insights (run_id, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, kind) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    created_at=excluded.created_at
                """, (run_id, str(kind), json.dumps(payload, ensure_ascii=False), now))

        conn.commit()
        return {"ok": True, "run_id": run_id, "run_dir": str(run_dir)}
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    out = index_run(Path(args.run_dir))
    print(json.dumps(out, indent=2))

import os, sys, time, sqlite3
from pathlib import Path
import subprocess

ROOT    = Path(os.path.expanduser("~")) / "Desktop" / "FantasyAI"
VENV_PY = ROOT / "shooter_venv" / "Scripts" / "python.exe"
DB_PATH = ROOT / "jobs.db"
EXPORTS = ROOT / "exports"

def _run_fantasy_agents():
    """Run fantasyai_agents.py once (Shooter + Veo + download)."""
    script = ROOT / "fantasyai_agents.py"
    cmd = [str(VENV_PY), str(script)]
    print("[batch] running fantasyai_agents:", " ".join(cmd))
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"[batch] WARN: fantasyai_agents exit code {proc.returncode}")
    return proc.returncode

def _attach_latest_export_to_latest_job():
    """Attach newest exports/*.mp4 to the latest TODO job in jobs.db."""
    if not DB_PATH.exists():
        print("[batch] WARN: jobs.db missing; skip attach")
        return

    if not EXPORTS.exists():
        print("[batch] WARN: exports/ missing; skip attach")
        return

    mp4s = list(EXPORTS.glob("*.mp4"))
    if not mp4s:
        print("[batch] WARN: no mp4s in exports/; skip attach")
        return

    latest_mp4 = max(mp4s, key=lambda p: p.stat().st_mtime)
    print(f"[batch] latest mp4: {latest_mp4}")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        # Latest job that is still not posted
        cur.execute("""
            SELECT id FROM jobs
            WHERE status != 'posted'
            ORDER BY created_at DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            print("[batch] WARN: no jobs to attach file_path to")
            return
        job_id = row[0]
        cur.execute(
            "UPDATE jobs SET file_path = ?, status = ? WHERE id = ?",
            (str(latest_mp4), "generated", job_id),
        )
        conn.commit()
        print(f"[batch] attached {latest_mp4} to job {job_id} (status=generated)")
    finally:
        conn.close()

def main(limit: int = 3, delay: float = 5.0):
    print(f"[batch] running {limit} job(s) with delay={delay}s")
    for i in range(limit):
        print(f"[batch] === Job {i+1}/{limit} ===")
        rc = _run_fantasy_agents()
        if rc != 0:
            print("[batch] WARN: fantasyai_agents failed; continuing anyway")
        _attach_latest_export_to_latest_job()
        time.sleep(delay)

if __name__ == "__main__":
    # Optional CLI arg: python scripts/run_batch_jobs.py 5
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    main(limit=lim)

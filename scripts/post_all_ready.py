import os, sys, sqlite3, subprocess
from pathlib import Path

ROOT    = Path(os.path.expanduser("~")) / "Desktop" / "FantasyAI"
VENV_PY = ROOT / "shooter_venv" / "Scripts" / "python.exe"
DB_PATH = ROOT / "jobs.db"
POSTER  = ROOT / "post_tiktok_playwright.py"

def _build_caption(row):
    # row order: id, topic, platform, caption, hashtags, cta, file_path, prompt, status, created_at
    _, topic, platform, caption, hashtags, cta, file_path, prompt, status, created_at = row
    parts = []
    if caption:
        parts.append(caption.strip())
    if hashtags:
        parts.append(hashtags.strip())
    caption_text = " ".join(parts).strip()
    if cta:
        caption_text = (caption_text + "\n" + cta.strip()).strip()
    return caption_text

def main():
    if not DB_PATH.exists():
        print("[post_all] ERROR: jobs.db missing")
        sys.exit(1)
    if not POSTER.exists():
        print("[post_all] ERROR: post_tiktok_playwright.py missing")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, topic, platform, caption, hashtags, cta, file_path, prompt, status, created_at
            FROM jobs
            WHERE file_path IS NOT NULL
              AND file_path != ''
              AND status != 'posted'
            ORDER BY created_at ASC
        """)
        jobs = cur.fetchall()
        if not jobs:
            print("[post_all] no ready jobs to post")
            return

        print(f"[post_all] found {len(jobs)} job(s) to post")
        for row in jobs:
            job_id = row[0]
            file_path = row[6]
            cap = _build_caption(row)

            print(f"[post_all] posting job {job_id} -> {file_path}")
            cmd = [str(VENV_PY), str(POSTER), file_path, cap]
            proc = subprocess.run(cmd)
            if proc.returncode == 0:
                print(f"[post_all] job {job_id} posted OK, marking as posted")
                cur.execute(
                    "UPDATE jobs SET status = ? WHERE id = ?",
                    ("posted", job_id),
                )
                conn.commit()
            else:
                print(f"[post_all] WARN: post script exit code {proc.returncode} for job {job_id}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()

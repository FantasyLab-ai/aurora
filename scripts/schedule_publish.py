import sqlite3
from scripts.db_init import ensure_schema
from scripts.publishers.youtube_publisher import schedule_video as yt_sched
from scripts.publishers.tiktok_publisher import schedule_video as tt_sched

DB = "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/fantasyai.db"

def _fetch_ready(con):
    cur = con.execute("""
      SELECT id, platform, video_path, title, description, tags_csv, COALESCE(scheduled_for_utc, datetime('now'))
      FROM posts
      WHERE status='ready' AND video_path IS NOT NULL
    """)
    return cur.fetchall()

def _mark(con, pid:int, status:str, pub_id:str|None):
    con.execute("UPDATE posts SET status=?, published_id=? WHERE id=?", (status, pub_id, pid))
    con.commit()

def run():
    ensure_schema()
    con = sqlite3.connect(DB)
    rows = _fetch_ready(con)
    if not rows:
        print("[schedule_publish] nothing to schedule.")
        return
    for (pid, platform, video, title, desc, tags_csv, when_utc) in rows:
        tags = [t.strip() for t in (tags_csv or "").split(",") if t.strip()]
        if platform == "youtube":
            res = yt_sched(video, title or "FantasyLab.ai", desc or "", tags, when_utc)
        elif platform == "tiktok":
            res = tt_sched(video, title or "FantasyLab.ai", desc or "", tags, when_utc)
        else:
            print(f"[schedule_publish] unknown platform {platform} id={pid}")
            _mark(con, pid, "failed", None); continue
        _mark(con, pid, "scheduled", res.get("id"))
        print(f"[schedule_publish] {platform} scheduled id={pid} -> {res.get('id')}")

if __name__ == "__main__":
    run()

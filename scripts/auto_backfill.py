from __future__ import annotations
import os, re, glob, time, json, math, sqlite3, pathlib
from datetime import datetime, timezone
from difflib import SequenceMatcher

DB  = os.getenv("METRICS_DB",  "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")
OUT = os.getenv("OUTPUTS_DIR","/mnt/c/Users/bgrut/Desktop/FantasyAI/outputs")
PROMPT_DIR = "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/prompts"

# ensure DB exists
try:
    from scripts.db_init import ensure_schema, DB_PATH
    ensure_schema()
except Exception as e:
    print(f"[auto_backfill] schema ensure skipped: {e}")

import sqlite3
con = sqlite3.connect("/mnt/c/Users/bgrut/Desktop/FantasyAI/data/fantasyai.db")


def _db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS asset_links(
      platform TEXT, post_id TEXT, asset_path TEXT,
      title TEXT, score REAL, linked_ts INTEGER,
      PRIMARY KEY(platform, post_id, asset_path)
    )""")
    con.commit(); return con

def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+"," ", (s or "").lower())
    return " ".join(s.split())

def _sim(a: str,b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def _mtime_iso(p: str) -> str:
    t = os.path.getmtime(p)
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()

def _load_unlinked_posts(con):
    # expect a posts table populated by analytics pulls
    cur = con.execute("""
      SELECT platform, post_id, title, COALESCE(published_at_utc,published_at,'') as pub_utc
      FROM posts
      WHERE post_id NOT IN (
        SELECT post_id FROM asset_links
      )
      ORDER BY published_at_utc DESC
    """)
    return cur.fetchall()

def _nearest_by_time(files: list[str], pub_iso: str, window_hours: int = 12):
    if not pub_iso: return None, 0.0
    pub_ts = datetime.fromisoformat(pub_iso.replace("Z","+00:00")).timestamp()
    best = None; best_dt = None
    for f in files:
        dt = abs(os.path.getmtime(f) - pub_ts) / 3600.0
        if best_dt is None or dt < best_dt:
            best, best_dt = f, dt
    if best and best_dt <= window_hours:
        return best, float(max(0.0, 1.0 - best_dt/window_hours))
    return None, 0.0

def _prompt_titles():
    out = []
    for p in glob.glob(os.path.join(PROMPT_DIR, "prompt_*.json")):
        try:
            j = json.load(open(p,"r",encoding="utf-8"))
            t = j.get("title") or j.get("topic") or j.get("caption") or ""
            out.append( (p, _slug(t)) )
        except Exception:
            continue
    return out

def run():
    con = _db()
    posts = _load_unlinked_posts(con)
    files = sorted(glob.glob(os.path.join(OUT, "*.mp4")))
    titles = _prompt_titles()

    if not posts:
        print("ℹ️ No unlinked posts.")
        return

    for plat, pid, title, pub in posts:
        s_title = _slug(title or "")
        best_file, time_score = _nearest_by_time(files, pub, window_hours=12)
        text_score = 0.0
        if best_file:
            base = os.path.splitext(os.path.basename(best_file))[0]
            text_score = _sim(s_title, _slug(base))
            # also compare with prompt titles
            for _, pt in titles:
                text_score = max(text_score, _sim(s_title, pt))
        score = 0.6*time_score + 0.4*text_score
        if best_file and score >= 0.55:
            con.execute("""INSERT OR REPLACE INTO asset_links(platform,post_id,asset_path,title,score,linked_ts)
                           VALUES(?,?,?,?,?,?)""",
                        (plat, pid, best_file, title or "", score, int(time.time())))
            print(f"🔄 Backfilled: {plat}:{pid} ← {os.path.basename(best_file)} (score {score:.2f})")
    con.commit(); con.close()
    print("✅ Auto-backfill complete.")

if __name__ == "__main__":
    run()

from __future__ import annotations
import os, re, glob, sqlite3, hashlib, time, json, datetime
from difflib import SequenceMatcher

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")
OUT = os.getenv("OUTPUTS_DIR", "/mnt/c/Users/bgrut/Desktop/FantasyAI/outputs")

def _db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS asset_links(
      platform TEXT, post_id TEXT, asset_path TEXT,
      title TEXT, score REAL, linked_ts INTEGER,
      PRIMARY KEY(platform, post_id, asset_path)
    )""")
    con.commit(); return con

def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(s.split())[:200]

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def load_posts(con):
    cur = con.execute("SELECT platform, post_id, title FROM posts ORDER BY last_seen_ts DESC")
    return [(r[0], r[1], r[2] or "") for r in cur.fetchall()]

def run():
    con = _db()
    posts = load_posts(con)
    if not posts:
        print(ℹ️ No posts to link yet.")
        return
    files = sorted(glob.glob(os.path.join(OUT, "*.mp4")))
    if not files:
        print("ℹ️ No local mp4 files in outputs/.")
        return
    for f in files:
        # Try to infer a title from filename (you already save prompt JSON nearby)
        base = os.path.basename(f)
        guess = _slug(os.path.splitext(base)[0])
        best = None
        for plat, pid, title in posts:
            sc = _sim(guess, _slug(title))
            if best is None or sc > best[0]:
                best = (sc, plat, pid, title)
        if best and best[0] >= 0.55:
            sc, plat, pid, ttl = best
            con.execute("""INSERT OR REPLACE INTO asset_links(platform,post_id,asset_path,title,score,linked_ts)
                           VALUES(?,?,?,?,?,?)""",
                        (plat, pid, f, ttl, sc, int(time.time())))
            print(f"🔗 Linked {base} → {plat}:{pid} ({sc:.2f})")
    con.commit(); con.close()
    print("✅ Manifest join complete.")

if __name__ == "__main__":
    run()

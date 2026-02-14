from __future__ import annotations
import os, time, random, sqlite3, numpy as np
from scripts.contextual_bandit_v2 import suggest_slots as _dummy_slots  # just for consistency
from scripts.contextual_bandit_v2 import record_outcome as bandit_record
from scripts.embeddings import embed_reduced

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")

TEMPLATES = [
  "POV: {core}. Wait for the twist.",
  "I asked Veo to create {core} — here's what happened.",
  "Everyone gets this wrong about {core}.",
  "Watch this {core} transform in 10 seconds.",
  "How to nail {core} (no fluff).",
  "We tried {core}. Results shocked us.",
  "3 mistakes people make with {core}."
]

def _db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS ab_hooks(
      hook_id TEXT PRIMARY KEY, text TEXT, created_ts INTEGER, active INTEGER DEFAULT 1
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS ab_logs(
      ts INTEGER, platform TEXT, topic_key TEXT, hook_id TEXT, hook_text TEXT,
      published_iso_utc TEXT, views INTEGER, likes INTEGER, comments INTEGER,
      avg_view_duration_sec REAL, play_rate REAL
    )""")
    con.commit(); return con

def ensure_default_hooks():
    con = _db()
    cur = con.execute("SELECT COUNT(*) FROM ab_hooks")
    n = int(cur.fetchone()[0])
    if n == 0:
        now = int(time.time())
        rows = [(f"hook{i}", t, now, 1) for i,t in enumerate(TEMPLATES)]
        con.executemany("INSERT OR REPLACE INTO ab_hooks(hook_id,text,created_ts,active) VALUES(?,?,?,?)", rows)
        con.commit()
    con.close()

def generate_hooks_from_topic(core: str, k: int = 5):
    ensure_default_hooks()
    core = (core or "this idea").strip()
    # Pick k active templates
    con = _db()
    rows = con.execute("SELECT hook_id, text FROM ab_hooks WHERE active=1").fetchall()
    con.close()
    random.shuffle(rows)
    rows = rows[:k]
    hooks = []
    for hid, tmpl in rows:
        hooks.append( (hid, tmpl.format(core=core)) )
    return hooks  # list[(hook_id, hook_text)]

def select_best_hook(platform: str, topic_key: str, topic_hint: str, length_s: int = 30, trend_score: float = 0.0):
    """
    Very light-weight: score hooks by cosine to topic embedding + a small random jitter.
    If you want, you can swap this for a full LinTS over hooks as well.
    """
    cand = generate_hooks_from_topic(topic_hint, k=5)
    e_topic = embed_reduced(topic_hint or "")
    scores = []
    for hid, txt in cand:
        e = embed_reduced(txt)
        sc = float(np.dot(e_topic, e)) + random.uniform(-0.05, 0.05)
        scores.append((sc, hid, txt))
    scores.sort(reverse=True)
    best = scores[0][1], scores[0][2]
    return best  # (hook_id, hook_text)

def record_hook_outcome(platform: str, topic_key: str, hook_id: str, hook_text: str,
                        published_iso_utc: str, **metrics):
    con = _db()
    con.execute("""INSERT INTO ab_logs(ts,platform,topic_key,hook_id,hook_text,published_iso_utc,views,likes,comments,avg_view_duration_sec,play_rate)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (int(time.time()), platform, topic_key, hook_id, hook_text, published_iso_utc,
                 metrics.get("views"), metrics.get("likes"), metrics.get("comments"),
                 metrics.get("avg_view_duration_sec"), metrics.get("play_rate")))
    con.commit(); con.close()

    # Also teach the main bandit with the hook text as a context feature
    bandit_record(platform, topic_key, published_iso_utc, hook_text=hook_text, **metrics)

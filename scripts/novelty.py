from __future__ import annotations
import os, time, hashlib, sqlite3, math

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")

def _db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS topic_memory(
        topic_hash TEXT PRIMARY KEY, topic TEXT, last_ts INTEGER, hits INTEGER DEFAULT 1
    )""")
    return con

def novelty_score(topic: str, now: int | None = None) -> float:
    now = now or int(time.time())
    h = hashlib.sha256(topic.strip().lower().encode()).hexdigest()[:16]
    con = _db()
    row = con.execute("SELECT last_ts,hits FROM topic_memory WHERE topic_hash=?", (h,)).fetchone()
    if not row:
        con.execute("INSERT INTO topic_memory(topic_hash,topic,last_ts,hits) VALUES(?,?,?,1)", (h, topic, now))
        con.commit(); con.close()
        return 1.0  # brand new
    last_ts, hits = row
    age_h = (now - last_ts) / 3600.0
    decay = 1.0 / (1.0 + hits*0.5) * (1.0 - (1.0/(1.0+age_h/12.0)))
    # 0..1: more recent & repeated → lower; older & rare → higher
    con.execute("UPDATE topic_memory SET last_ts=?, hits=hits+1 WHERE topic_hash=?", (now, h))
    con.commit(); con.close()
    return max(0.0, min(1.0, decay))

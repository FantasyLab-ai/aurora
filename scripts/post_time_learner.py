from __future__ import annotations
import os, time, math, sqlite3, json
from datetime import datetime, timedelta
from dateutil import tz

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")
TZ = os.getenv("POST_TZ", "America/Los_Angeles")

def _db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS post_times(
      platform TEXT, topic_key TEXT, dow INTEGER, hour INTEGER,
      plays INTEGER DEFAULT 0, wins INTEGER DEFAULT 0,
      PRIMARY KEY(platform,topic_key,dow,hour)
    )""")
    return con

def _key(topic: str) -> str:
    t = topic.strip().lower()
    return t.split(":")[0][:80] if ":" in t else t[:80]

def record_outcome(platform: str, topic: str, when_iso: str, success: bool):
    con = _db()
    dt = datetime.fromisoformat(when_iso.replace("Z","+00:00")).astimezone(tz.gettz(TZ))
    dow, hour = dt.weekday(), dt.hour
    k = _key(topic)
    con.execute("""INSERT INTO post_times(platform,topic_key,dow,hour,plays,wins)
                   VALUES(?,?,?,?,1,?)
                   ON CONFLICT(platform,topic_key,dow,hour) DO UPDATE SET
                   plays=plays+1, wins=wins+?""", (platform,k,dow,hour,1 if success else 0, 1 if success else 0))
    con.commit(); con.close()

def suggest(platform: str, topic: str, horizon_hours: int = 48) -> str:
    """Pick best (dow,hour) via UCB; return RFC3339 in UTC within next horizon."""
    con = _db()
    k = _key(topic)
    rows = con.execute("SELECT dow,hour,plays,wins FROM post_times WHERE platform=? AND topic_key=?", (platform,k)).fetchall()
    con.close()
    # If no data, pick reasonable defaults (evening local)
    cand = [(d,h) for d in range(7) for h in (10,12,15,18,20)]
    stats = {(d,h):(0,0) for d,h in cand}
    for d,h,p,w in rows:
        stats[(d,h)] = (p,w)
    N = sum(p for (p,_w) in stats.values()) or 1
    # UCB
    best, best_score = None, -1.0
    for (d,h),(p,w) in stats.items():
        n = max(1,p); mean = w/n
        ucb = mean + math.sqrt(2*math.log(N+1)/n)
        if ucb > best_score:
            best, best_score = (d,h), ucb
    # Find the next occurrence within horizon
    now = datetime.now(tz=tz.gettz(TZ))
    target_d, target_h = best
    for i in range(horizon_hours+1):
        dt = now + timedelta(hours=i)
        if dt.weekday()==target_d and dt.hour==target_h:
            return dt.astimezone(tz.UTC).replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00","Z")
    # fallback: next 2 hours
    return (now.astimezone(tz.UTC) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00","Z")

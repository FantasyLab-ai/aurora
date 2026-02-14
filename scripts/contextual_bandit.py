from __future__ import annotations
import os, math, time, json, sqlite3, numpy as np
from datetime import datetime, timedelta
from dateutil import tz
from scripts.embeddings import embed_reduced, EMB_DIM_REDUCED

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")
POST_TZ = os.getenv("POST_TZ", "America/Los_Angeles")

ALPHA_PRIOR = float(os.getenv("BANDIT_ALPHA", "1.0"))   # ridge prior
SIGMA2      = float(os.getenv("BANDIT_SIGMA2",  "1.0")) # noise var
DECAY       = float(os.getenv("BANDIT_DECAY",    "0.995"))

def _db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS bandit_params_v2(
      platform TEXT, d INTEGER, A BLOB, b BLOB, updated_ts INTEGER,
      PRIMARY KEY(platform)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS bandit_logs_v2(
      ts INTEGER, platform TEXT, topic_key TEXT, hook TEXT, local_dt TEXT,
      reward REAL, features BLOB
    )""")
    con.commit(); return con

def _one_hot(n: int, idx: int) -> np.ndarray:
    v = np.zeros(n, dtype=np.float32); v[idx]=1.0; return v

def _features(platform: str, topic_key: str, local_dt: datetime,
              length_s: int, trend_score: float, hook_text: str|None) -> np.ndarray:
    dow  = _one_hot(7,  local_dt.weekday())
    hr   = _one_hot(24, local_dt.hour)
    lb   = _one_hot(3, 0 if length_s<30 else 1 if length_s<60 else 2)
    trend= np.array([float(trend_score)], dtype=np.float32)
    bias = np.array([1.0], dtype=np.float32)

    # Embeddings (topic + hook)
    et = embed_reduced(topic_key or "")
    eh = embed_reduced(hook_text or "")

    return np.concatenate([dow, hr, lb, trend, et, eh, bias]).astype(np.float64)

def _load_params(con, platform: str, d: int):
    cur = con.execute("SELECT d,A,b FROM bandit_params_v2 WHERE platform=?", (platform,))
    row = cur.fetchone()
    if row:
        d_old = int(row[0]); A = np.frombuffer(row[1], dtype=np.float64).reshape(d_old,d_old); b = np.frombuffer(row[2], dtype=np.float64).reshape(d_old,1)
        if d_old != d:
            # reset if dimensionality changed (e.g., EMB_DIM_REDUCED changed)
            A = (ALPHA_PRIOR * np.eye(d)).astype(np.float64); b = np.zeros((d,1), dtype=np.float64)
            con.execute("UPDATE bandit_params_v2 SET d=?,A=?,b=?,updated_ts=? WHERE platform=?",
                        (d, A.tobytes(), b.tobytes(), int(time.time()), platform))
            con.commit()
    else:
        A = (ALPHA_PRIOR * np.eye(d)).astype(np.float64)
        b = np.zeros((d,1), dtype=np.float64)
        con.execute("INSERT OR REPLACE INTO bandit_params_v2(platform,d,A,b,updated_ts) VALUES(?,?,?,?,?)",
                    (platform, d, A.tobytes(), b.tobytes(), int(time.time())))
        con.commit()
    return A,b

def _save_params(con, platform: str, A: np.ndarray, b: np.ndarray):
    con.execute("UPDATE bandit_params_v2 SET A=?, b=?, updated_ts=? WHERE platform=?",
                (A.tobytes(), b.tobytes(), int(time.time()), platform))
    con.commit()

def _retention_reward(views: int|None, avg_view_duration_sec: float|None,
                      length_s: int|None, likes: int|None, comments: int|None,
                      watch_time_sec: float|None, play_rate: float|None) -> float:
    # Components in [0,1] roughly
    rv = 0.0
    if views is not None:
        rv = math.tanh(math.log1p(max(0, views))/10.0)    # saturates gently
    rret = 0.0
    if avg_view_duration_sec and length_s and length_s>0:
        rret = max(0.0, min(1.0, avg_view_duration_sec/float(length_s)))
    reng = 0.0
    if views and likes is not None:
        reng = min(1.0, likes/max(1,views))
    if views and comments is not None:
        reng = 0.7*reng + 0.3*min(1.0, comments/max(1,views))
    rplay = float(play_rate) if play_rate is not None else 0.0
    rwt = 0.0
    if watch_time_sec and views:
        rwt = max(0.0, min(1.0, (watch_time_sec/max(1,views))/max(1, length_s or 1)))
    # Blend: views 40%, retention 30%, engagement 15%, play 5%, wt/view 10%
    r = 0.40*rv + 0.30*rret + 0.15*reng + 0.05*rplay + 0.10*rwt
    return float(max(0.0, min(1.0, r)))

def record_outcome(platform: str, topic_key: str, published_iso_utc: str,
                   length_s: int = 30, trend_score: float = 0.0,
                   hook_text: str|None = None,
                   views: int|None = None, likes: int|None = None,
                   comments: int|None = None, avg_view_duration_sec: float|None = None,
                   watch_time_sec: float|None = None, play_rate: float|None = None):
    # localize
    local_dt = datetime.fromisoformat(published_iso_utc.replace("Z","+00:00")).astimezone(tz.gettz(POST_TZ))
    x = _features(platform, topic_key, local_dt, length_s, trend_score, hook_text)
    d = x.size; x = x.reshape(d,1)

    reward = _retention_reward(views, avg_view_duration_sec, length_s, likes, comments, watch_time_sec, play_rate)

    con = _db()
    A,b = _load_params(con, platform, d)
    # forgetting
    A *= DECAY; b *= DECAY
    A += x @ x.T
    b += x * reward
    _save_params(con, platform, A, b)

    con.execute("""INSERT INTO bandit_logs_v2(ts,platform,topic_key,hook,local_dt,reward,features)
                   VALUES(?,?,?,?,?,?,?)""",
                (int(time.time()), platform, topic_key, hook_text or "", local_dt.isoformat(), reward, x.tobytes()))
    con.commit(); con.close()

def suggest_slots(platform: str, topic_key: str, hours_ahead: int = 72, k: int = 6,
                  length_s: int = 30, trend_score: float = 0.0, hook_text: str|None=None):
    now_local = datetime.now(tz.gettz(POST_TZ))
    x0 = _features(platform, topic_key, now_local, length_s, trend_score, hook_text)
    d = x0.size

    con = _db(); A,b = _load_params(con, platform, d); con.close()
    A_inv = np.linalg.inv(A)
    mu = A_inv @ b
    # Thompson sample
    theta = np.random.multivariate_normal(mu.flatten(), SIGMA2*A_inv).reshape(d,1)

    cand = []
    for h in range(1, hours_ahead+1):
        t = now_local + timedelta(hours=h)
        x = _features(platform, topic_key, t, length_s, trend_score, hook_text).reshape(d,1)
        cand.append( (float((theta.T @ x)[0,0]), t) )
    cand.sort(reverse=True)
    return [t for _,t in cand[:k]]

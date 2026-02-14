# /scripts/feature_store.py
from __future__ import annotations
import os, sqlite3, json, time
from typing import Optional, Dict, Any, List

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")

def _db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS post_features(
        platform TEXT, topic_key TEXT, post_id TEXT,
        created_ts INTEGER, payload_json TEXT,
        PRIMARY KEY(platform, topic_key, post_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS post_outcomes(
        platform TEXT, topic_key TEXT, post_id TEXT,
        updated_ts INTEGER, metrics_json TEXT,
        PRIMARY KEY(platform, topic_key, post_id))""")
    con.commit(); return con

def upsert_features(platform:str, topic_key:str, post_id:str, payload:Dict[str,Any]):
    con=_db(); now=int(time.time())
    con.execute("""INSERT OR REPLACE INTO post_features(platform,topic_key,post_id,created_ts,payload_json)
                  VALUES(?,?,?,?,?)""",
                (platform, topic_key, post_id, now, json.dumps(payload)))
    con.commit(); con.close()

def upsert_outcomes(platform:str, topic_key:str, post_id:str, metrics:Dict[str,Any]):
    con=_db(); now=int(time.time())
    con.execute("""INSERT OR REPLACE INTO post_outcomes(platform,topic_key,post_id,updated_ts,metrics_json)
                  VALUES(?,?,?,?,?)""",
                (platform, topic_key, post_id, now, json.dumps(metrics)))
    con.commit(); con.close()

def fetch_dataset(limit:int=10000) -> List[Dict[str,Any]]:
    con=_db()
    rows = con.execute("""
      SELECT f.platform, f.topic_key, f.post_id, f.payload_json, o.metrics_json
      FROM post_features f
      LEFT JOIN post_outcomes o
        ON f.platform=o.platform AND f.topic_key=o.topic_key AND f.post_id=o.post_id
      ORDER BY f.created_ts DESC
      LIMIT ?""", (limit,)).fetchall()
    con.close()
    out=[]
    for plat,topic,post,pj,mj in rows:
        out.append({"platform":plat,"topic_key":topic,"post_id":post,
                    "features":json.loads(pj or "{}"),
                    "metrics":json.loads(mj or "{}")})
    return out

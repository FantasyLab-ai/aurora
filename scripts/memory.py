import os, json, sqlite3
from datetime import datetime
from pathlib import Path
from scripts.db import _connect

def remember(topic:str, key:str, value:str):
    con=_connect(); cur=con.cursor()
    cur.execute("""INSERT INTO episode_memory(topic,key,value,ts)
    VALUES(?,?,?,?)
    ON CONFLICT(topic,key) DO UPDATE SET value=excluded.value, ts=excluded.ts
    """, (topic, key, value, datetime.utcnow().isoformat()))
    con.commit(); con.close()

def recall(topic:str)->dict:
    con=_connect(); cur=con.cursor()
    rows=cur.execute("SELECT key,value FROM episode_memory WHERE topic=?",(topic,)).fetchall()
    con.close()
    return {k:v for (k,v) in rows}

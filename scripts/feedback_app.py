import os, sqlite3, streamlit as st, pandas as pd, time

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)

def _ensure():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS runs(
        ts INTEGER, topic TEXT, prompt_hash TEXT, prompt TEXT,
        success INTEGER, duration_s REAL, file_path TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS labels(
        prompt_hash TEXT PRIMARY KEY, thumbs_up INTEGER DEFAULT 0, thumbs_down INTEGER DEFAULT 0,
        last_ts INTEGER
    )""")
    con.close()
_ensure()

st.set_page_config(layout="wide"); st.title("FantasyAI Feedback")

con = sqlite3.connect(DB, check_same_thread=False)
df = pd.read_sql_query("SELECT rowid, datetime(ts,'unixepoch') as when, topic, prompt_hash, success, duration_s, file_path, prompt FROM runs ORDER BY ts DESC LIMIT 200", con)
st.dataframe(df, use_container_width=True)

def vote(hash: str, up: bool):
    if up: con.execute("INSERT INTO labels(prompt_hash,thumbs_up,thumbs_down,last_ts) VALUES(?,1,0,strftime('%s','now')) ON CONFLICT(prompt_hash) DO UPDATE SET thumbs_up=thumbs_up+1,last_ts=strftime('%s','now')", (hash,))
    else:  con.execute("INSERT INTO labels(prompt_hash,thumbs_up,thumbs_down,last_ts) VALUES(?,0,1,strftime('%s','now')) ON CONFLICT(prompt_hash) DO UPDATE SET thumbs_down=thumbs_down+1,last_ts=strftime('%s','now')", (hash,))
    con.commit()

st.subheader("Quick markup")
for _, row in df.head(20).iterrows():
    c1, c2, c3, c4 = st.columns([3,4,1,1])
    with c1: st.write(f"**{row['topic']}**"); st.caption(row['when'])
    with c2: st.code(row["prompt"], language="markdown")
    with c3:
        if st.button("👍", key=f"up-{row['rowid']}"): vote(row["prompt_hash"], True)
    with c4:
        if st.button("👎", key=f"down-{row['rowid']}"): vote(row["prompt_hash"], False)

st.success("Votes saved immediately. Bandit uses them as priors.")

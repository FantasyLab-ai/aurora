import sqlite3, pandas as pd, os, time, streamlit as st

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")

st.title("FantasyAI Runs")
if not os.path.exists(DB):
    st.warning("No DB yet.")
else:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("select datetime(ts,'unixepoch') as ts, topic, prompt_hash, success, duration_s, file_path from runs order by ts desc limit 500", con)
    st.dataframe(df)
    if not df.empty:
        st.subheader("Win rate by prompt")
        agg = df.groupby("prompt_hash")["success"].mean().sort_values(ascending=False)
        st.bar_chart(agg)

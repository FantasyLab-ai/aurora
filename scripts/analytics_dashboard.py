import os, sqlite3, pandas as pd, numpy as np, streamlit as st
from datetime import datetime
from dateutil import tz

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")
TZ = os.getenv("POST_TZ", "America/Los_Angeles")

st.set_page_config(layout="wide", page_title="FantasyAI Analytics")
st.title("FantasyAI Analytics")

con = sqlite3.connect(DB)

profiles = pd.read_sql_query("SELECT * FROM profiles ORDER BY platform, username", con)
posts = pd.read_sql_query("SELECT * FROM posts", con)

c1,c2,c3 = st.columns(3)
with c1: st.metric("YouTube followers", int(profiles.query("platform=='youtube'")["followers"].fillna(0).max() or 0))
with c2: st.metric("TikTok followers", int(profiles.query("platform=='tiktok'")["followers"].fillna(0).max() or 0))
with c3: st.metric("Posts tracked", len(posts))

st.subheader("Recent posts")
st.dataframe(posts.sort_values("last_seen_ts", ascending=False).head(50), use_container_width=True)

# Heatmaps (views by local DOW/HOUR)
if not posts.empty:
    def dt_parts(x):
        if not isinstance(x,str) or not x: return None
        try:
            dt = datetime.fromisoformat(x.replace("Z","+00:00")).astimezone(tz.gettz(TZ))
            return dt.weekday(), dt.hour
        except Exception:
            return None
    tmp = posts.copy()
    tmp["dow_hour"] = tmp["published_at_utc"].apply(dt_parts)
    tmp = tmp[tmp["dow_hour"].notna()]
    tmp["dow"]  = tmp["dow_hour"].apply(lambda t: t[0])
    tmp["hour"] = tmp["dow_hour"].apply(lambda t: t[1])
    for plat in ["youtube","tiktok"]:
        st.subheader(f"Best times — {plat}")
        tplat = tmp[tmp["platform"]==plat]
        if tplat.empty: 
            st.info("No publish times available yet.")
            continue
        pivot = pd.pivot_table(tplat, values="views", index="dow", columns="hour", aggfunc=np.nanmean)
        st.write("Average views by day/hour (local).")
        st.dataframe(pivot.fillna(0).astype(int), use_container_width=True)

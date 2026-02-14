# /scripts/operator_board_app.py
from __future__ import annotations
import os, json, pandas as pd, streamlit as st
from scripts.feature_store import fetch_dataset
from scripts.contextual_bandit_v2 import suggest_slots

st.set_page_config(page_title="FantasyAI Operator", layout="wide")
st.title("FantasyAI Operator")

data = fetch_dataset(limit=2000)
if not data:
    st.info("No records yet. Generate a video first.")
    st.stop()

rows=[]
for r in data:
    feats = r.get("features") or {}
    met  = r.get("metrics") or {}
    rows.append({
        "platform": r["platform"], "topic_key": r["topic_key"], "post_id": r["post_id"],
        "length_s": feats.get("length_s"),
        "trend_score": feats.get("trend_score"),
        "views": met.get("views"), "likes": met.get("likes"),
        "comments": met.get("comments"),
        "avg_view_duration_sec": met.get("avg_view_duration_sec"),
    })
df = pd.DataFrame(rows)
st.dataframe(df, use_container_width=True)

st.subheader("Suggest next posting slots")
platform = st.selectbox("Platform", ["tiktok","youtube"])
topic_key = st.text_input("Topic key", value=data[0]["topic_key"])
length_s = int(st.number_input("Length (s)", value=int(df["length_s"].dropna().mean() or 30)))
trend_score = float(st.number_input("Trend score", value=float(df["trend_score"].dropna().mean() or 0.3)))

if st.button("Suggest Slots"):
    slots = suggest_slots(platform, topic_key, hours_ahead=72, k=5, length_s=length_s, trend_score=trend_score)
    st.write(slots)

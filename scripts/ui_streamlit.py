# /mnt/c/Users/bgrut/Desktop/FantasyAI/scripts/ui_streamlit.py
import os, json, urllib.request, socket
import streamlit as st

CDP_HOST = st.text_input("CDP Host", os.getenv("CHROME_CDP_HOST","127.0.0.1"))
CDP_PORT = st.text_input("CDP Port", os.getenv("CHROME_CDP_PORT","9222"))
FLOW_URL = st.text_input("Flow URL", os.getenv("FLOW_URL",""))
OUT_DIR  = st.text_input("Outputs Dir", os.getenv("OUTPUTS_DIR","/mnt/c/Users/bgrut/Desktop/FantasyAI/outputs"))

col1, col2 = st.columns(2)
with col1:
    if st.button("Check CDP"):
        base=f"http://{CDP_HOST}:{CDP_PORT}"
        st.write("Base:", base)
        try:
            s=socket.socket(); s.settimeout(2); s.connect((CDP_HOST, int(CDP_PORT))); s.close()
            with urllib.request.urlopen(base+"/json/version", timeout=2) as r:
                st.success(f"CDP OK: {json.loads(r.read()).get('Browser')}")
        except Exception as e:
            st.error(f"CDP FAIL: {e}")

topic   = st.text_input("Topic", "wildlife")
script  = st.text_area("Script", "A wildlife conservationist returns baby owls to their burrow…")
style   = st.text_input("Style", "cinematic, 9:16, 40s")
title   = st.text_input("Title", "POV: owl rescue")
tags    = st.text_input("Tags CSV", "wildlife,rescue,owls")

if st.button("Generate (CDP)"):

    os.environ["USE_CHROME_CDP"] = "1"
    os.environ.pop("SELENIUM_OK", None)
    os.environ["CHROME_CDP_HOST"] = CDP_HOST
    os.environ["CHROME_CDP_PORT"] = CDP_PORT
    if FLOW_URL: os.environ["FLOW_URL"] = FLOW_URL
    os.environ["VEO_URL"] = os.environ.get("FLOW_URL", FLOW_URL)
    os.environ["OUTPUTS_DIR"] = OUT_DIR

    from importlib import import_module, reload
    p = import_module("scripts.pipeline_final"); p = reload(p)
    payload = {"topic":topic,"script":script,"style":style,"title":title,"tags":[t.strip() for t in tags.split(",") if t.strip()]}
    try:
        out = p.generate_veo_video_cdp(json.dumps(payload))
        st.json(out)
    except Exception as e:
        st.error(str(e))

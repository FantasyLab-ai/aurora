from __future__ import annotations
import os, time, json, re, html, urllib.parse, urllib.request
from typing import Any, Dict, List

UA = os.getenv("HTTP_UA","Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

def _get(url: str, accept_json: bool = False) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept":"*/*"})
    with urllib.request.urlopen(req, timeout=15) as r:
        b = r.read()
    return b.decode("utf-8","ignore")

def _find_initial_data(html_txt: str) -> dict:
    # Look for ytInitialData or ytcfg blobs
    m = re.search(r"var ytInitialData = ({.*?});</script>", html_txt, re.S)
    if not m:
        m = re.search(r"window\[\s*['\"]ytInitialData['\"]\s*\]\s*=\s*({.*?});", html_txt, re.S)
    return json.loads(m.group(1)) if m else {}

def fetch_channel_and_videos_by_handle_free(handle: str) -> dict:
    handle = handle.lstrip("@")
    url = f"https://www.youtube.com/@{handle}/videos"
    txt = _get(url)
    data = _find_initial_data(txt)
    out = {"channel":{"handle":handle},"videos":[]}
    # Walk the grid renderer for video metadata
    try:
        sec = data["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][1]["tabRenderer"]["content"]\
            ["richGridRenderer"]["contents"]
        for c in sec:
            r = c.get("richItemRenderer",{}).get("content",{}).get("videoRenderer",{})
            vid = r.get("videoId")
            title = r.get("title",{}).get("runs",[{"text":""}])[0]["text"]
            view_text = r.get("viewCountText",{}).get("simpleText","")
            views = None
            m = re.search(r"([\d,\.]+)\s*views", view_text, re.I)
            if m:
                views = m.group(1).replace(",","")
            out["videos"].append({
                "id": vid, "title": title, "views_est": int(float(views)) if views else None,
                "published_text": r.get("publishedTimeText",{}).get("simpleText","")
            })
    except Exception:
        pass
    return out

# Optional official API (free with API key; more reliable)
def fetch_via_api(handle_or_channel_id: str, api_key: str) -> dict:
    base = "https://www.googleapis.com/youtube/v3"
    def jget(url): return json.loads(_get(url))
    # Resolve handle → channelId if needed
    if not handle_or_channel_id.startswith("UC"):
        handle = handle_or_channel_id.lstrip("@")
        ch = jget(f"{base}/channels?forHandle={urllib.parse.quote('@'+handle)}&part=id,snippet,statistics&key={api_key}")
        items = ch.get("items",[])
        if not items: return {}
        cid = items[0]["id"]
        ch_obj = items[0]
    else:
        cid = handle_or_channel_id
        ch = jget(f"{base}/channels?id={cid}&part=id,snippet,statistics&key={api_key}")
        ch_obj = ch.get("items",[{}])[0]
    # latest videos
    search = jget(f"{base}/search?channelId={cid}&part=id&order=date&type=video&maxResults=50&key={api_key}")
    vid_ids = ",".join(i["id"]["videoId"] for i in search.get("items",[]))
    vids = []
    if vid_ids:
        vi = jget(f"{base}/videos?part=snippet,statistics&id={vid_ids}&key={api_key}")
        for it in vi.get("items",[]):
            s, st = it["snippet"], it["statistics"]
            vids.append({
                "id": it["id"], "title": s["title"], "published_at": s["publishedAt"],
                "views": int(st.get("viewCount","0")), "likes": int(st.get("likeCount","0")),
                "comments": int(st.get("commentCount","0"))
            })
    return {"channel": ch_obj, "videos": vids}

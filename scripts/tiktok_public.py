from __future__ import annotations
import os, re, json, urllib.request

UA = os.getenv("HTTP_UA","Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept":"text/html"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8","ignore")

def _extract_sigi(html_txt: str) -> dict:
    m = re.search(r'<script id="SIGI_STATE"[^>]*>(\{.*?\})</script>', html_txt, re.S)
    return json.loads(m.group(1)) if m else {}

def fetch_profile_free(username: str) -> dict:
    u = username.lstrip("@")
    url = f"https://www.tiktok.com/@{u}"
    html = _get(url)
    js = _extract_sigi(html)
    out = {"user": {"username": u}, "videos": []}
    try:
        user = next(iter(js["UserModule"]["users"].values()))
        stats = next(iter(js["UserModule"]["stats"].values()))
        out["user"].update({
            "nickname": user.get("nickname"),
            "followerCount": stats.get("followerCount"),
            "followingCount": stats.get("followingCount"),
            "heartCount": stats.get("heartCount"),
            "videoCount": stats.get("videoCount"),
        })
    except Exception:
        pass
    try:
        for k,v in js.get("ItemModule",{}).items():
            out["videos"].append({
                "id": k,
                "title": v.get("desc",""),
                "createTime": v.get("createTime"),
                "stats": v.get("stats",{}),  # playCount, diggCount, shareCount, commentCount
            })
    except Exception:
        pass
    return out

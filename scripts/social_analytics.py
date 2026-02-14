# /scripts/social_analytics.py
from __future__ import annotations
import re, time, logging, requests
from typing import Optional, Dict

UA = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/123 Safari/537.36"}

def _get(url:str, timeout=20) -> str:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status(); return r.text

def parse_compact_number(s:str) -> Optional[int]:
    s = s.strip().lower().replace(",","").replace(" ","")
    m = re.match(r'^(\d+(\.\d+)?)([km]?)$', s)
    if not m: return None
    val=float(m.group(1)); suf=m.group(3)
    if suf=='k': val*=1_000
    elif suf=='m': val*=1_000_000
    return int(val)

def tiktok_profile(username:str) -> Dict[str,Optional[int]]:
    # Public page like https://www.tiktok.com/@FantasyLab.ai
    url = f"https://www.tiktok.com/@{username}"
    try:
        html = _get(url)
        # try structured JSON first
        m = re.search(r'"followerCount":\s*(\d+)', html)
        followers = int(m.group(1)) if m else None
        m = re.search(r'"heartCount":\s*(\d+)', html)
        likes = int(m.group(1)) if m else None
        return {"followers": followers, "likes": likes}
    except Exception as e:
        logging.warning("TikTok scrape failed for %s: %s", username, e)
        return {"followers": None, "likes": None}

def youtube_channel(name_or_handle:str) -> Dict[str,Optional[int]]:
    # Try handle form first: https://www.youtube.com/@FantasyLab_ai
    cand = [f"https://www.youtube.com/@{name_or_handle}",
            f"https://www.youtube.com/c/{name_or_handle}",
            f"https://www.youtube.com/{name_or_handle}"]
    for url in cand:
        try:
            html = _get(url)
            # subscribers
            m = re.search(r'(\d[\d,\.]*\s*[KMkm]?)\s+subscribers', html)
            subs = parse_compact_number(m.group(1)) if m else None
            # total videos (optional)
            mv = re.search(r'(\d[\d,\.]*)\s+videos', html)
            vids = int(float(mv.group(1).replace(",",""))) if mv else None
            return {"subscribers": subs, "videos": vids}
        except Exception:
            continue
    return {"subscribers": None, "videos": None}

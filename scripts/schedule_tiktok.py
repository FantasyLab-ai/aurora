from __future__ import annotations
import os, json, time, webbrowser
from pathlib import Path
from scripts.alerts import notify

QUEUE = Path("/mnt/c/Users/bgrut/Desktop/FantasyAI/outputs/publish_queue")
QUEUE.mkdir(parents=True, exist_ok=True)

def _write_manifest(video_path: str, title: str, tags: list[str], publish_at_utc: str) -> str:
    man = {
        "video_path": video_path, "title": title, "tags": tags,
        "publish_at_utc": publish_at_utc, "created_ts": int(time.time())
    }
    p = QUEUE / f"tiktok_{int(time.time())}.json"
    p.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)

def schedule_stub(video_path: str, title: str, tags: list[str], publish_at_utc: str) -> str:
    # Fallback: write manifest and optionally open upload page
    m = _write_manifest(video_path, title, tags, publish_at_utc)
    try:
        webbrowser.open("https://www.tiktok.com/upload?lang=en")
    except Exception: pass
    notify("Queued TikTok (manual upload likely required).", "info", {"manifest": m})
    return m

def schedule_api(video_path: str, title: str, tags: list[str], publish_at_utc: str) -> str:
    tok = os.getenv("TIKTOK_ACCESS_TOKEN")
    adv = os.getenv("TIKTOK_ADVERTISER_ID") or os.getenv("TIKTOK_BUSINESS_ID")
    if not tok or not adv:
        return schedule_stub(video_path, title, tags, publish_at_utc)
    # ↘️ Placeholder: your app’s approved endpoints go here (init upload, chunk upload, create post with schedule)
    # I’m returning stub until your app has posting scope approved:
    return schedule_stub(video_path, title, tags, publish_at_utc)

import os, json
from pathlib import Path
from datetime import datetime, timezone
from scripts.creds_loader import load_tiktok_creds

SAFE_MODE = os.getenv("FA_SAFE_MODE", "1") != "0"
LOG = Path("/mnt/c/Users/bgrut/Desktop/FantasyAI/outputs/publish_intents.log")

def schedule_video(video_path:str, title:str, description:str, tags:list[str], scheduled_for_utc:str):
    _ = load_tiktok_creds()  # validate readable
    payload = {
        "platform": "tiktok",
        "video_path": video_path,
        "title": title,
        "description": description,
        "tags": tags,
        "scheduled_for_utc": scheduled_for_utc,
        "ts": datetime.now(timezone.utc).isoformat()
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if SAFE_MODE:
        LOG.write_text((LOG.read_text("utf-8") if LOG.exists() else "") + json.dumps(payload)+"\n", encoding="utf-8")
        return {"status":"DRY_RUN_SCHEDULED", "id":"tt_dry_"+datetime.now().strftime("%H%M%S")}
    raise NotImplementedError("Live TikTok scheduling not yet enabled.")

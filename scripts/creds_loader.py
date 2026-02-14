import json, os
from pathlib import Path

def _read_json(path: str):
    # Search ENV then common project locations for convenience
    CANDIDATES = [Path(path)]
    proj = Path("/mnt/c/Users/bgrut/Desktop/FantasyAI")
    CANDIDATES += [
        proj / "creds" / Path(path).name,
        proj / "data" / Path(path).name,
        proj / "config" / Path(path).name,
    ]
    for pth in CANDIDATES:
        if pth.exists():
            return json.loads(pth.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Credentials file not found in {CANDIDATES}")

def load_tiktok_creds():
    path = os.getenv("TIKTOK_CREDS_JSON", "/mnt/data/tiktok_creds.json")
    j = _read_json(path)
    return {
        "client_key": j.get("client_key"),
        "client_secret": j.get("client_secret"),
        "access_token": j.get("access_token"),
    }

def load_youtube_creds():
    path = os.getenv("YT_CREDS_JSON", "/mnt/data/youtube.v3.json")
    j = _read_json(path)
    # For now we only validate the file is readable; live API wiring comes next
    return {"_loaded": bool(j), "path": path}

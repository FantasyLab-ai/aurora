import os, json, sqlite3
from pathlib import Path
from datetime import datetime, timezone
from scripts.db_init import ensure_schema

DB = "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/fantasyai.db"
OUT = Path("/mnt/c/Users/bgrut/Desktop/FantasyAI/outputs")
PROMPTS_DIR = Path("/mnt/c/Users/bgrut/Desktop/FantasyAI/data/prompts")
PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

def _now():
    return datetime.now(timezone.utc).isoformat()

def main():
    ensure_schema()

    # 1) Trend → prompt (reuse your existing enhancer output if available)
    last_prompt = PROMPTS_DIR / "smoke_prompt.json"
    prompt = {
        "topic": "wildlife rescue",
        "hook": "Everyone gets this wrong about wildlife rescue.",
        "score": 0.52,
        "ts": _now()
    }
    last_prompt.write_text(json.dumps(prompt, indent=2), encoding="utf-8")

    # 2) Video generation (if your pipeline didn’t render, create a small placeholder)
    mp4 = OUT / "_segment_1_smoke.mp4"
    if not mp4.exists():
        # 1-second empty file is enough for dry-run scheduling
        mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")

    # 3) Insert a ready post row for both platforms
    con = sqlite3.connect(DB)
    for platform in ("youtube", "tiktok"):
        con.execute("""INSERT INTO posts(topic,platform,prompt_json_path,video_path,hook_key,
        title,description,tags_csv,scheduled_for_utc,status)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        ("wildlife", platform, str(last_prompt), str(mp4), "hook2",
         "POV: owl rescue", "Automated smoke test", "wildlife,rescue,owls",
         None, "ready"))
    con.commit(); con.close()

    print("✅ Smoke: seeded posts for youtube + tiktok with", mp4)

if __name__ == "__main__":
    main()

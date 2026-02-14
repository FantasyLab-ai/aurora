import os, sqlite3, time, json, math, random
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = os.path.expanduser(os.getenv("BASE_DIR", "~/FantasyAI"))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH  = os.path.join(DATA_DIR, "fantasyai.db")
os.makedirs(DATA_DIR, exist_ok=True)

# -------- A/B selection (bandit-ish) ----------
def thompson_pick(candidates):
    """
    candidates: list of tuples like (critic_score, prompt_text, meta_json)
    If your upstream produces plain strings, adapt here.
    """
    if not candidates:
        raise RuntimeError("No candidates to pick from")
    # Sort by score desc; add mild exploration
    ranked = sorted(candidates, key=lambda x: float(x[0]), reverse=True)
    if len(ranked) >= 3 and random.random() < float(os.getenv("BANDIT_EPS", "0.2")):
        return ranked[random.randint(1, min(2, len(ranked)-1))][1]  # explore a non-top candidate
    return ranked[0][1]

# -------- Logging runs into DB + CSV ----------
def _now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _connect():
    return sqlite3.connect(DB_PATH)

def log_run(topic: str, prompt: str, ok: bool, duration_sec: float, video_path: str):
    """
    Persist a minimal row in posts/metrics so future analytics and the orchestrator can find it.
    """
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    con = _connect()
    try:
        # Insert into posts as a ready row if we have a video_path
        if video_path:
            for platform in ("youtube", "tiktok"):
                con.execute("""INSERT INTO posts
                (topic, platform, prompt_json_path, video_path, hook_key, title, description, tags_csv, scheduled_for_utc, status)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (topic, platform, "", video_path, None,
                 f"Auto: {topic}", "Generated via pipeline", f"{topic},fantasylab,auto",
                 None, "ready"))
        # Add a tiny metrics line so your scorer can train later (optional CSV if you want)
        con.execute("""INSERT INTO metrics (platform, topic, post_time_utc, views, likes, comments, avg_view_duration_sec, watch_time_sec, play_rate)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("youtube", topic, _now_utc(), 0, 0, 0, 0.0, 0.0, 0.0))
        con.commit()
    finally:
        con.close()

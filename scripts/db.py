import os, json, sqlite3, hashlib
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", os.path.expanduser("~/FantasyAI/data")))
DB_PATH  = DATA_DIR / "ai_runs.sqlite"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con

def ensure_schema():
    con = _connect()
    cur = con.cursor()
    # runs/prompts
    cur.execute("""
    CREATE TABLE IF NOT EXISTS prompts (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        topic TEXT,
        team_json TEXT,
        final_prompt TEXT NOT NULL,
        prompt_hash TEXT NOT NULL,
        score_proxy REAL DEFAULT 0.0,
        video_path TEXT,
        source TEXT,
        extra_json TEXT,
        UNIQUE(prompt_hash, ts)
    );""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prompts_ts ON prompts(ts);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prompts_topic ON prompts(topic);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prompts_score ON prompts(score_proxy);")

    # social metrics harvested from public pages
    cur.execute("""
    CREATE TABLE IF NOT EXISTS social_metrics (
        id INTEGER PRIMARY KEY,
        platform TEXT NOT NULL,
        post_url TEXT,
        title TEXT,
        caption TEXT,
        hashtags_json TEXT,
        views INTEGER, likes INTEGER, shares INTEGER,
        ts TEXT,
        topic_guess TEXT,
        UNIQUE(platform, post_url)
    );""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sm_ts ON social_metrics(ts);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sm_platform ON social_metrics(platform);")
    con.commit(); con.close()

def _hash_text(t: str) -> str:
    return hashlib.sha256((t or "").encode("utf-8")).hexdigest()

def upsert_prompt_run(*, ts:str|None, topic:str, team:list|None, final_prompt:str,
                      score_proxy:float|int=0.0, video_path:str|None=None,
                      source:str="v2_safe", extra:dict|None=None):
    ensure_schema()
    ts = ts or datetime.utcnow().isoformat()
    team_json = json.dumps(team or [], ensure_ascii=False)
    extra_json = json.dumps(extra or {}, ensure_ascii=False)
    ph = _hash_text(final_prompt or "")
    con = _connect()
    con.execute("""
        INSERT INTO prompts (ts, topic, team_json, final_prompt, prompt_hash, score_proxy, video_path, source, extra_json)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(prompt_hash, ts) DO UPDATE SET
            topic=excluded.topic,
            team_json=excluded.team_json,
            score_proxy=COALESCE(excluded.score_proxy, prompts.score_proxy),
            video_path=COALESCE(excluded.video_path, prompts.video_path),
            source=excluded.source,
            extra_json=excluded.extra_json
    """, (ts, topic, team_json, final_prompt, ph, float(score_proxy or 0.0), video_path, source, extra_json))
    con.commit(); con.close()

def upsert_social_metric(obj: dict):
    """obj: {'platform','post_url','title','caption','hashtags':[...],'views','likes','shares','ts','topic_guess'}"""
    ensure_schema()
    platform = obj.get("platform","")
    post_url = obj.get("post_url","")
    hashtags_json = json.dumps(obj.get("hashtags") or [], ensure_ascii=False)
    con=_connect()
    con.execute("""
        INSERT INTO social_metrics (platform, post_url, title, caption, hashtags_json, views, likes, shares, ts, topic_guess)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(platform, post_url) DO UPDATE SET
            title=excluded.title,
            caption=excluded.caption,
            hashtags_json=excluded.hashtags_json,
            views=excluded.views,
            likes=excluded.likes,
            shares=excluded.shares,
            ts=excluded.ts,
            topic_guess=excluded.topic_guess
    """, (platform, post_url, obj.get("title"), obj.get("caption"), hashtags_json,
          int(obj.get("views") or 0), int(obj.get("likes") or 0), int(obj.get("shares") or 0),
          obj.get("ts") or datetime.utcnow().isoformat(), obj.get("topic_guess")))
    con.commit(); con.close()

from __future__ import annotations
import os, time, sqlite3, datetime
from dateutil import tz
from scripts.yt_public import fetch_via_api, fetch_channel_and_videos_by_handle_free
from scripts.tiktok_public import fetch_profile_free
from scripts.post_time_learner import record_outcome, _key as topic_key

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")
YT_API_KEY = os.getenv("YT_API_KEY", "")
Y_HANDLE = os.getenv("YT_HANDLE", "FantasyLab.ai")
TT_HANDLE = os.getenv("TT_HANDLE", "FantasyLab.ai")
POST_TZ = os.getenv("POST_TZ", "America/Los_Angeles")

os.makedirs(os.path.dirname(DB), exist_ok=True)

def _db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS posts(
      platform TEXT, post_id TEXT, title TEXT, topic_key TEXT,
      published_at_utc TEXT, last_seen_ts INTEGER,
      views INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
      PRIMARY KEY(platform, post_id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS profiles(
      platform TEXT, username TEXT, followers INTEGER, following INTEGER, hearts INTEGER, videos INTEGER,
      last_seen_ts INTEGER, PRIMARY KEY(platform, username)
    )""")
    con.commit()
    return con

def _now(): return int(time.time())

def upsert_profile(con, platform, username, followers=None, following=None, hearts=None, videos=None):
    con.execute("""INSERT INTO profiles(platform,username,followers,following,hearts,videos,last_seen_ts)
      VALUES(?,?,?,?,?,?,?)
      ON CONFLICT(platform,username) DO UPDATE SET
      followers=COALESCE(?,profiles.followers),
      following=COALESCE(?,profiles.following),
      hearts=COALESCE(?,profiles.hearts),
      videos=COALESCE(?,profiles.videos),
      last_seen_ts=?""",
      (platform, username, followers, following, hearts, videos, _now(),
       followers, following, hearts, videos, _now()))

def upsert_post(con, platform, pid, title, tkey, published_at_utc, views=None, likes=None, comments=None, shares=None):
    con.execute("""INSERT INTO posts(platform,post_id,title,topic_key,published_at_utc,last_seen_ts,views,likes,comments,shares)
      VALUES(?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(platform,post_id) DO UPDATE SET
      title=excluded.title,
      topic_key=COALESCE(excluded.topic_key, posts.topic_key),
      published_at_utc=COALESCE(excluded.published_at_utc, posts.published_at_utc),
      last_seen_ts=?,
      views=COALESCE(excluded.views, posts.views),
      likes=COALESCE(excluded.likes, posts.likes),
      comments=COALESCE(excluded.comments, posts.comments),
      shares=COALESCE(excluded.shares, posts.shares)""",
      (platform,pid,title,tkey,published_at_utc,_now(),views,likes,comments,shares,_now()))

def pull_youtube():
    con = _db()
    data = {}
    if YT_API_KEY:
        data = fetch_via_api(Y_HANDLE, YT_API_KEY) or {}
        ch = data.get("channel",{}); vids = data.get("videos",[])
        # profile stat snapshot (subset)
        stats = ch.get("statistics",{})
        upsert_profile(con, "youtube", Y_HANDLE,
                       followers=int(stats.get("subscriberCount","0")) if stats else None,
                       following=None, hearts=None, videos=int(stats.get("videoCount","0")) if stats else None)
        for v in vids:
            pk = topic_key(v["title"])
            upsert_post(con,"youtube", v["id"], v["title"], pk,
                        v.get("published_at"),
                        views=v.get("views"), likes=v.get("likes"), comments=v.get("comments"))
            # teach success heuristic: views over 75th percentile later; for now threshold:
            if v.get("views",0) >= 1000:
                when = v.get("published_at") or datetime.datetime.utcnow().isoformat()+"Z"
                record_outcome("youtube", v["title"], when, True)
    else:
        d = fetch_channel_and_videos_by_handle_free(Y_HANDLE)
        upsert_profile(con, "youtube", Y_HANDLE, followers=None)
        for v in d.get("videos",[]):
            upsert_post(con, "youtube", v["id"], v["title"], topic_key(v["title"]), None,
                        views=v.get("views_est"))
    con.commit(); con.close()

def pull_tiktok():
    con = _db()
    d = fetch_profile_free(TT_HANDLE)
    u = d.get("user",{})
    upsert_profile(con, "tiktok", TT_HANDLE, followers=u.get("followerCount"),
                   following=u.get("followingCount"), hearts=u.get("heartCount"),
                   videos=u.get("videoCount"))
    for v in d.get("videos",[]):
        st = v.get("stats",{})
        created = v.get("createTime")
        published = None
        try:
            # TikTok createTime is epoch seconds
            published = datetime.datetime.utcfromtimestamp(int(created)).isoformat()+"Z" if created else None
        except Exception:
            pass
        upsert_post(con,"tiktok", v["id"], v.get("title",""), topic_key(v.get("title","")),
                    published, views=st.get("playCount"), likes=st.get("diggCount"),
                    comments=st.get("commentCount"), shares=st.get("shareCount"))
        if st.get("playCount",0) and int(st["playCount"]) >= 1000 and published:
            record_outcome("tiktok", v.get("title",""), published, True)
    con.commit(); con.close()

if __name__ == "__main__":
    try: pull_youtube()
    except Exception as e: print("[analytics_pull] youtube warn:", e)
    try: pull_tiktok()
    except Exception as e: print("[analytics_pull] tiktok warn:", e)
    print("✅ analytics pull complete")

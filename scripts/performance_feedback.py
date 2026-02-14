# performance_feedback.py
#
# handles:
#   - recording engagement numbers for a finished video
#   - summarizing what's winning per niche
#
# it is defensive: if schema isn't fully there yet, it will not crash the API.

import sqlite3
import os
from typing import Dict, Any, Optional

DB_PATH = os.path.join(os.getcwd(), "studio.db")


def _table_exists(cur, name: str) -> bool:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def _columns(cur, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def record_metrics(
    video_id: int,
    views: Optional[int] = None,
    likes: Optional[int] = None,
    comments: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Update engagement metrics for a given video row, and refresh its score.

    score = likes + 0.1*comments + 0.01*views
    (cheap proxy we can iterate on)

    Returns the updated row (best effort). Fails soft if schema not ready.
    """

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # 1. sanity: do we even have the table/columns we expect?
    if not _table_exists(cur, "videos"):
        con.close()
        return {"ok": False, "error": "videos table missing"}

    need_cols = {"id", "views", "likes", "comments", "score"}
    have_cols = _columns(cur, "videos")
    if not need_cols.issubset(have_cols):
        con.close()
        return {
            "ok": False,
            "error": "videos schema missing required columns",
            "have_cols": list(have_cols),
        }

    # 2. fetch current values so we can merge partial updates
    cur.execute(
        "SELECT views, likes, comments FROM videos WHERE id=?",
        (video_id,),
    )
    row = cur.fetchone()
    if row is None:
        con.close()
        return {"ok": False, "error": f"video {video_id} not found"}

    cur_views, cur_likes, cur_comments = row

    new_views = views if views is not None else cur_views
    new_likes = likes if likes is not None else cur_likes
    new_comments = comments if comments is not None else cur_comments

    # 3. compute new score
    score = (new_likes or 0) + 0.1 * (new_comments or 0) + 0.01 * (new_views or 0)

    # 4. write back
    cur.execute(
        """
        UPDATE videos
        SET views = ?, likes = ?, comments = ?, score = ?
        WHERE id = ?
        """,
        (new_views, new_likes, new_comments, score, video_id),
    )
    con.commit()

    # 5. return the updated row for convenience
    cur.execute(
        """
        SELECT id, niche_id, topic, file_path,
               views, likes, comments, score, created_at
        FROM videos
        WHERE id=?
        """,
        (video_id,),
    )
    updated = cur.fetchone()
    con.close()

    if not updated:
        return {"ok": True, "video_id": video_id, "updated": None}

    (
        v_id, niche_id, topic, file_path,
        v_views, v_likes, v_comments, v_score, created_at
    ) = updated

    return {
        "ok": True,
        "video": {
            "id": v_id,
            "niche_id": niche_id,
            "topic": topic,
            "file_path": file_path,
            "views": v_views,
            "likes": v_likes,
            "comments": v_comments,
            "score": v_score,
            "created_at": created_at,
        }
    }


def summarize_winners() -> Dict[str, Any]:
    """
    Return a leaderboard of top topics per niche.
    If schema isn't ready yet, fail soft with empty info.
    """

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # table check
    if not _table_exists(cur, "videos"):
        con.close()
        return {
            "message": "No data yet. Keep generating videos.",
            "by_niche": {}
        }

    have_cols = _columns(cur, "videos")
    need_cols = {"niche_id", "topic", "score"}
    if not need_cols.issubset(have_cols):
        con.close()
        return {
            "message": "Schema not fully ready. Generate some videos first.",
            "by_niche": {}
        }

    # pull all rows, highest score first within niche
    cur.execute(
        """
        SELECT niche_id, topic, score
        FROM videos
        ORDER BY niche_id ASC, score DESC
        """
    )
    rows = cur.fetchall()
    con.close()

    by_niche: Dict[str, list[dict[str, Any]]] = {}
    for niche_id, topic, score in rows:
        bucket = by_niche.setdefault(niche_id, [])
        # we only keep top 3 per niche for now
        if len(bucket) < 3:
            bucket.append({
                "topic": topic,
                "score": score,
            })

    if not by_niche:
        return {
            "message": "No performance yet. Post and collect data, then refresh.",
            "by_niche": {}
        }

    return {
        "message": "These niches + topics are spiking. Make more variations NOW.",
        "by_niche": by_niche
    }

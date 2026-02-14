from __future__ import annotations
import os
from scripts.post_time_learner import suggest, record_outcome
from scripts.schedule_youtube import upload_and_schedule
from scripts.schedule_tiktok import schedule_api
from scripts.alerts import notify

def schedule_everywhere(video_path: str, title: str, description: str, tags: list[str], topic: str):
    # Suggest best publish times per platform
    yt_time = suggest("youtube", topic)  # RFC3339 UTC
    tk_time = suggest("tiktok",  topic)

    yt_link = upload_and_schedule(video_path, title, description, tags, yt_time)
    tk_info = schedule_api(video_path, title, tags, tk_time)

    notify("Scheduled video", "info", {"yt_publish_at": yt_time, "yt_link": yt_link,
                                       "tiktok_publish_at": tk_time, "tiktok": tk_info})
    # You’ll later call record_outcome(platform, topic, when, success) after you gather metrics.
    return {"youtube": {"publishAt": yt_time, "link": yt_link},
            "tiktok":  {"publishAt": tk_time, "info": tk_info}}

# /scripts/jobs.py
from __future__ import annotations
import os, time, logging, traceback
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from scripts.social_analytics import tiktok_profile, youtube_channel
from scripts.tiktok_shop_manager import sync_products_from_api
from scripts.contextual_bandit_v2 import refresh_backfill  # assume present in your repo
from scripts.feature_store import fetch_dataset

POST_TZ = os.getenv("POST_TZ","America/Los_Angeles")
JOB_DB = os.getenv("JOBS_DB","sqlite:////mnt/c/Users/bgrut/Desktop/FantasyAI/data/jobs.sqlite")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def _with_backoff(fn, *a, **k):
    delay = 2.0
    for attempt in range(6):
        try:
            return fn(*a, **k)
        except Exception as e:
            logging.warning("Job failed (%s): %s", fn.__name__, e)
            if attempt==5: raise
            time.sleep(delay); delay *= 2

def job_pull_analytics():
    logging.info("Pull analytics for FantasyLab.ai")
    tt = _with_backoff(tiktok_profile, "FantasyLab.ai")
    yt = _with_backoff(youtube_channel, "FantasyLab_ai")
    logging.info("TikTok: %s | YouTube: %s", tt, yt)

def job_shop_sync():
    n = _with_backoff(sync_products_from_api)
    logging.info("Shop sync updated=%s products", n)

def job_bandit_backfill():
    # Let your CB v2 scan metrics.db and update priors
    try:
        _with_backoff(refresh_backfill)
    except Exception:
        logging.warning("Backfill failed:\n%s", traceback.format_exc())

def job_offline_eval_snapshot():
    # lightweight: make sure dataset is loadable
    ds = fetch_dataset(limit=1000)
    logging.info("Offline snapshot rows=%d", len(ds))

def run_scheduler(block=True):
    stores = {"default": SQLAlchemyJobStore(url=JOB_DB)}
    sch = BackgroundScheduler(jobstores=stores, timezone=POST_TZ)
    # every 3h analytics
    sch.add_job(job_pull_analytics, "interval", hours=3, id="analytics")
    # daily shop sync
    sch.add_job(job_shop_sync, "interval", hours=24, id="shop_sync")
    # every 6h bandit backfill
    sch.add_job(job_bandit_backfill, "interval", hours=6, id="bandit_backfill")
    # hourly dataset check
    sch.add_job(job_offline_eval_snapshot, "interval", hours=1, id="offline_eval")
    sch.start()
    logging.info("Scheduler started. Job DB=%s", JOB_DB)
    if block:
        try:
            while True: time.sleep(3600)
        except KeyboardInterrupt:
            sch.shutdown()

if __name__ == "__main__":
    run_scheduler(block=True)

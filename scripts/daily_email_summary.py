from __future__ import annotations
import os, smtplib, sqlite3, pandas as pd
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from scripts.contextual_bandit import suggest_slots

DB = os.getenv("METRICS_DB", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data/metrics.db")
SMTP_HOST = os.getenv("SMTP_HOST","")
SMTP_PORT = int(os.getenv("SMTP_PORT","587"))
SMTP_USER = os.getenv("SMTP_USER","")
SMTP_PASS = os.getenv("SMTP_PASS","")
MAIL_TO   = os.getenv("MAIL_TO","")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)

def _db():
    return sqlite3.connect(DB)

def _fmt_slots(slots):
    return ", ".join(dt.strftime("%a %H:%M") for dt in slots)

def build_report() -> str:
    con = _db()
    prof = pd.read_sql_query("SELECT * FROM profiles", con)
    yt = pd.read_sql_query("SELECT * FROM yt_channel_daily ORDER BY date DESC LIMIT 7", con)
    top = pd.read_sql_query("""
        SELECT platform, title, COALESCE(views,0) v FROM posts
        ORDER BY v DESC LIMIT 5
    """, con)
    con.close()

    lines = []
    lines.append("# FantasyAI — Daily Summary\n")
    if not prof.empty:
        ytf = int(prof.query("platform=='youtube'")["followers"].fillna(0).max() or 0)
        ttf = int(prof.query("platform=='tiktok'")["followers"].fillna(0).max() or 0)
        lines.append(f"- YouTube followers: **{ytf:,}**")
        lines.append(f"- TikTok followers: **{ttf:,}**\n")

    if not yt.empty:
        last7 = int(yt["views"].sum())
        lines.append(f"- YouTube last 7 days views: **{last7:,}**\n")

    lines.append("## Top 5 posts (by views)")
    if top.empty:
        lines.append("_No posts tracked yet_")
    else:
        for _,r in top.iterrows():
            lines.append(f"- [{r['platform']}] {r['title'][:80]} — {int(r['v']):,} views")

    # Suggested slots (by platform, for a dummy 'wildlife' topic)
    lines.append("\n## Suggested post times (next 72h)")
    for plat in ["youtube","tiktok"]:
        slots = suggest_slots(plat, topic_key="wildlife", hours_ahead=72, k=4)
        lines.append(f"- {plat}: { _fmt_slots(slots) }")

    return "\n".join(lines)

def send_email(body_md: str):
    if not SMTP_HOST or not MAIL_TO:
        print("ℹ️ SMTP not configured; printing summary:\n", body_md[:2000])
        return
    msg = MIMEText(body_md, "plain", "utf-8")
    msg["Subject"] = "FantasyAI – Daily Analytics"
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        if SMTP_USER and SMTP_PASS:
            s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(MAIL_FROM, [MAIL_TO], msg.as_string())
    print("✅ Email sent to", MAIL_TO)

if __name__ == "__main__":
    body = build_report()
    send_email(body)

#!/usr/bin/env python3
import os, argparse, datetime as dt
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

def _rfc3339(ts: str|None) -> str|None:
    if not ts: return None
    # Accept "YYYY-mm-dd HH:MM" or full ISO. Treat input as local time.
    try:
        if "T" in ts:
            return dt.datetime.fromisoformat(ts).isoformat()
        else:
            return dt.datetime.strptime(ts, "%Y-%m-%d %H:%M").isoformat()
    except Exception:
        raise SystemExit("publish_at must be 'YYYY-mm-dd HH:MM' or ISO-8601")

def upload_video(video_path, title, description, privacy="unlisted", publish_at=None, tags=None, category_id="22"):
    creds = None
    here = os.path.dirname(os.path.abspath(__file__))
    token_path = os.path.join(here, 'token.json')
    client_secrets = os.path.join(here, 'client_secrets.json')

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    youtube = build('youtube', 'v3', credentials=creds)

    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    # Scheduling trick: set privacy=private + publishAt → Studio shows as “Scheduled”
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = _rfc3339(publish_at)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or ["AI","automation","FantasyLabAI"],
            "categoryId": category_id,  # 22 = People & Blogs
        },
        "status": status
    }

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(video_path)
    )
    response = request.execute()
    vid = response['id']
    print(f"✅ YouTube video queued (id={vid})")
    print(f"https://youtube.com/watch?v={vid}")
    return vid

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--title", required=True)
    ap.add_argument("--desc", default="")
    ap.add_argument("--privacy", default="unlisted", choices=["public","unlisted","private"])
    ap.add_argument("--publish_at", help="Schedule time (local) e.g. '2025-08-23 10:00' or ISO-8601")
    ap.add_argument("--tags", nargs="*", default=None)
    ap.add_argument("--category", default="22")
    args = ap.parse_args()
    upload_video(
        args.video, args.title, args.desc,
        privacy=args.privacy, publish_at=args.publish_at,
        tags=args.tags, category_id=args.category
    )

from __future__ import annotations
import os, datetime, json
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def _service():
    cs = os.getenv("YT_CLIENT_SECRETS")
    tk = os.getenv("YT_TOKEN", os.path.join(os.path.dirname(cs or ""), "yt_token.json"))
    creds = None
    if tk and os.path.exists(tk):
        creds = Credentials.from_authorized_user_file(tk, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())  # type: ignore
        else:
            flow = InstalledAppFlow.from_client_secrets_file(cs, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(tk, "w") as f: f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)

def upload_and_schedule(video_path: str, title: str, description: str, tags: list[str], publish_at_rfc3339_utc: str) -> str:
    yt = _service()
    body = {
        "snippet": {"title": title[:100], "description": description, "tags": tags, "categoryId": "24"},  # Entertainment
        "status":  {"privacyStatus": "private", "publishAt": publish_at_rfc3339_utc,
                    "selfDeclaredMadeForKids": False}
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = req.execute()
    vid = resp["id"]
    return f"https://studio.youtube.com/video/{vid}/edit"

from __future__ import annotations
import os, json, pathlib, datetime
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Where creds live
BASE = pathlib.Path(os.getenv("FA_DATA_DIR", "/mnt/c/Users/bgrut/Desktop/FantasyAI/data"))
CREDS_DIR = BASE / "credentials"; CREDS_DIR.mkdir(parents=True, exist_ok=True)
CLIENT_SECRET = CREDS_DIR / "client_secret.json"   # put your OAuth client JSON here
TOKEN = CREDS_DIR / "yt_token.json"

# Scopes: read-only YouTube + YouTube Analytics
SCOPES = [
  "https://www.googleapis.com/auth/youtube.readonly",
  "https://www.googleapis.com/auth/yt-analytics.readonly"
]

def get_credentials() -> Credentials:
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())  # type: ignore[name-defined]
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        TOKEN.write_text(creds.to_json())
    return creds

def build_services():
    creds = get_credentials()
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    yta = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    return yt, yta

if __name__ == "__main__":
    yt, yta = build_services()
    print("✅ YouTube APIs authorized.")

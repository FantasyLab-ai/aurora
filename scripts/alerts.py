from __future__ import annotations
import os, json, urllib.request, urllib.error

SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")

def _post(url: str, payload: dict):
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type":"application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        print(f"[alerts] warn: {e}")

def notify(msg: str, level: str = "info", extra: dict | None = None):
    extra = extra or {}
    if SLACK_WEBHOOK:
        _post(SLACK_WEBHOOK, {"text": f"[{level.upper()}] {msg}\n{json.dumps(extra, ensure_ascii=False)}"})
    if DISCORD_WEBHOOK:
        _post(DISCORD_WEBHOOK, {"content": f"[{level.upper()}] {msg}\n```{json.dumps(extra, ensure_ascii=False)}```"})

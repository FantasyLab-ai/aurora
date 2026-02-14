#!/usr/bin/env python3
import argparse, subprocess, shlex, os, sys, json

def run(video, caption, creds_path=None, headless=False):
    """
    Uses tiktok_uploader.cli. You can log in once (cookies/session) via its auth CLI.
    This wrapper just forwards args in a reliable way.
    """
    cmd = [sys.executable, "-m", "tiktok_uploader.cli", "-v", video, "-d", caption]
    if headless:
        cmd += ["--headless"]
    # If your local fork/cli supports credentials, you can wire them here:
    # Example (pseudo): cmd += ["--client-key", cfg["client_key"], "--client-secret", cfg["client_secret"], "--access-token", cfg["access_token"]]
    if creds_path and os.path.exists(creds_path):
        try:
            with open(creds_path, "r") as f:
                cfg = json.load(f)
            # Uncomment these if your CLI actually supports them:
            # cmd += ["--client-key", cfg["client_key"], "--client-secret", cfg["client_secret"], "--access-token", cfg["access_token"]]
        except Exception:
            pass
    print("▶︎", " ".join(shlex.quote(c) for c in cmd))
    return subprocess.call(cmd)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--caption", required=True)
    ap.add_argument("--creds", default=os.path.expanduser("~/FantasyAI/scripts/tiktok_creds.json"))
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.video, args.caption, creds_path=args.creds, headless=args.headless))

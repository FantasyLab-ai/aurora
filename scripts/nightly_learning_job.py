from __future__ import annotations
import os, subprocess, sys

BASE = "/mnt/c/Users/bgrut/Desktop/FantasyAI/scripts"

JOBS = [
  f"{BASE}/analytics_pull.py",          # scrape YT/TikTok public (fallback)
  f"{BASE}/yt_analytics_pull.py",       # YouTube Analytics (OAuth)
  f"{BASE}/manifest_joiner.py",         # link mp4 -> posts
  f"{BASE}/daily_email_summary.py"      # send summary + new suggested slots
]

def run(cmd):
    print("→", cmd)
    r = subprocess.run([sys.executable, cmd], capture_output=True, text=True)
    if r.returncode != 0:
        print("  ⚠️", r.stderr.strip())
    else:
        print("  ✅", r.stdout.strip()[:1500])

if __name__ == "__main__":
    for j in JOBS: run(j)
    print("✅ Nightly learning completed.")

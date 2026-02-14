import os
import requests
import pandas as pd
from pathlib import Path

API = "https://api.stlouisfed.org/fred/release/observations"
API_KEY = os.environ.get("FRED_API_KEY")

if not API_KEY:
    raise SystemExit("Missing FRED_API_KEY env var")

params = {
    "api_key": API_KEY,
    "file_type": "json",
    "release_id": "53",  # example: GDP-related release family (change as you like)
    "limit": "100000",
}

r = requests.get(API, params=params, timeout=60)
r.raise_for_status()
data = r.json()

rows = data.get("observations", [])
df = pd.DataFrame(rows)

OUT = Path("fantasyai/data/external/macro_fred/raw")
OUT.mkdir(parents=True, exist_ok=True)
df.to_csv(OUT / "fred_release_53_observations.csv", index=False)

print(f"[OK] wrote {len(df)} rows -> {OUT}")

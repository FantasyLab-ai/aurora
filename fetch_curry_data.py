"""
fetch_curry_data.py
Pulls Stephen Curry's game-by-game stats for the last 3 NBA seasons
and outputs a clean CSV ready for Aurora.

Requirements:
    pip install nba_api pandas

Usage:
    python fetch_curry_data.py
    -> writes curry_3seasons.csv to current directory
"""

from nba_api.stats.endpoints import playergamelog
from nba_api.stats.static import players
import pandas as pd
import time

# Find Stephen Curry
print("Finding Stephen Curry...")
curry = players.find_players_by_full_name("Stephen Curry")[0]
curry_id = curry["id"]
print(f"  Found: {curry['full_name']} (ID: {curry_id})")

# Pull last 3 seasons
seasons = ["2022-23", "2023-24", "2024-25"]
all_games = []

for season in seasons:
    print(f"Fetching season {season}...")
    try:
        log = playergamelog.PlayerGameLog(
            player_id=curry_id,
            season=season
        ).get_data_frames()[0]
        log["SEASON"] = season
        all_games.append(log)
        print(f"  Got {len(log)} games")
    except Exception as e:
        print(f"  ERROR for {season}: {e}")
    # Be polite to NBA's API
    time.sleep(1.5)

# Combine all seasons
df = pd.concat(all_games, ignore_index=True)
print(f"\nTotal games: {len(df)}")

# Parse date
df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], format="%b %d, %Y")

# Sort chronologically (oldest first) — Aurora needs this for time series
df = df.sort_values("GAME_DATE").reset_index(drop=True)

# Select the columns Aurora will analyze
# These are the per-game stats that tell the performance story
columns_for_aurora = [
    "GAME_DATE",
    "PTS",          # Points
    "FG_PCT",       # Field goal percentage
    "FG3_PCT",      # Three-point percentage (Curry's signature)
    "FT_PCT",       # Free throw percentage
    "REB",          # Rebounds
    "AST",          # Assists
    "STL",          # Steals
    "BLK",          # Blocks
    "TOV",          # Turnovers
    "MIN",          # Minutes played
    "PLUS_MINUS",   # Plus/minus (team performance while on court)
]

df_clean = df[columns_for_aurora].copy()

# Clean any nulls (some early-season games have NaN percentages)
df_clean = df_clean.dropna()

# Convert MIN to numeric (sometimes it's a string like "35:20")
df_clean["MIN"] = pd.to_numeric(df_clean["MIN"], errors="coerce")
df_clean = df_clean.dropna()

# Save
output_path = "curry_3seasons.csv"
df_clean.to_csv(output_path, index=False)
print(f"\nSaved: {output_path}")
print(f"Rows: {len(df_clean)}")
print(f"Date range: {df_clean['GAME_DATE'].min()} to {df_clean['GAME_DATE'].max()}")
print(f"\nFirst 3 rows:")
print(df_clean.head(3))
print(f"\nLast 3 rows:")
print(df_clean.tail(3))

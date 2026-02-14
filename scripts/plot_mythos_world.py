# scripts/plot_mythos_world.py
from __future__ import annotations
import json
from pathlib import Path

import sqlite3
import matplotlib.pyplot as plt

from fantasyai.config import DATA_DIR

DB_PATH = DATA_DIR / "fantasyai_core.db"


def fetch_latest_world() -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, biome, mood, time_of_day, weather, json_payload, created_at
        FROM mythos_worlds
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None

    payload = {}
    try:
        payload = json.loads(row["json_payload"])
    except json.JSONDecodeError:
        payload = {}

    return {
        "id": row["id"],
        "biome": row["biome"],
        "mood": row["mood"],
        "time_of_day": row["time_of_day"],
        "weather": row["weather"],
        "payload": payload,
        "created_at": row["created_at"],
    }


def plot_elevation_profile(elev: list[float], title: str) -> None:
    fig, ax = plt.subplots()
    ax.plot(range(len(elev)), elev, marker="o")
    ax.set_title(title)
    ax.set_xlabel("Sample index along path")
    ax.set_ylabel("Normalized elevation (0-1)")
    ax.grid(True)
    plt.tight_layout()
    plt.show()


def plot_balance_curves(balance: dict, biome: str) -> None:
    levels = balance.get("levels", len(balance.get("difficulty_curve", [])))
    diff = balance.get("difficulty_curve", [])
    reward = balance.get("reward_curve", [])
    eff = balance.get("reward_efficiency", [])

    x = list(range(1, levels + 1))

    # Difficulty curve
    fig, ax = plt.subplots()
    ax.plot(x, diff, marker="o")
    ax.set_title(f"Mythos difficulty curve – {biome}")
    ax.set_xlabel("Level")
    ax.set_ylabel("Difficulty")
    ax.grid(True)
    plt.tight_layout()
    plt.show()

    # Reward curve
    if reward:
        fig, ax = plt.subplots()
        ax.plot(x, reward, marker="o")
        ax.set_title(f"Mythos reward curve – {biome}")
        ax.set_xlabel("Level")
        ax.set_ylabel("Reward")
        ax.grid(True)
        plt.tight_layout()
        plt.show()

    # Efficiency curve
    if eff:
        fig, ax = plt.subplots()
        ax.plot(x, eff, marker="o")
        ax.set_title(f"Mythos reward efficiency – {biome}")
        ax.set_xlabel("Level")
        ax.set_ylabel("Reward / Difficulty")
        ax.grid(True)
        plt.tight_layout()
        plt.show()


def main() -> None:
    world = fetch_latest_world()
    if not world:
        print("No Mythos worlds found in DB.")
        return

    biome = world["biome"]
    payload = world["payload"]
    terrain = payload.get("terrain", {})
    elev = terrain.get("elevation_profile", [])
    lore = payload.get("lore", {})
    balance = lore.get("balance_summary", {})

    print(f"World ID: {world['id']}")
    print(f"Biome: {biome}, Mood: {world['mood']}, Time: {world['time_of_day']}")
    print(f"Created at: {world['created_at']}")

    if elev:
        plot_elevation_profile(elev, f"Mythos elevation profile – {biome}")

    if balance:
        plot_balance_curves(balance, biome)


if __name__ == "__main__":
    main()

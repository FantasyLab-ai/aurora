# scripts/test_mythos_balance.py
from __future__ import annotations
import json
import sqlite3

from fantasyai.config import DATA_DIR
from fantasyai.mythos.playtest_sim import simulate_playthrough

DB_PATH = DATA_DIR / "fantasyai_core.db"


def fetch_latest_world_balance() -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT json_payload
        FROM mythos_worlds
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    payload = json.loads(row["json_payload"])
    lore = payload.get("lore", {})
    return lore.get("balance_summary", None)


def main() -> None:
    balance = fetch_latest_world_balance()
    if not balance:
        print("No balance_summary found in latest Mythos world.")
        return

    difficulty = balance.get("difficulty_curve", [])
    summary = simulate_playthrough(
        difficulty_curve=difficulty,
        initial_power=1.0,
        power_growth=0.2,
        randomness=0.15,
        seed=42,
    )

    print(f"Simulated {len(summary.encounters)} encounters.")
    print(f"Win rate: {summary.win_rate:.2%}")
    print(f"Avg margin (power - difficulty): {summary.avg_margin:.3f}")

    for e in summary.encounters:
        print(
            f"Level {e.level:2d}: diff={e.difficulty:.2f}, "
            f"power={e.player_power:.2f}, win_prob={e.win_probability:.2f}, outcome={e.outcome}"
        )


if __name__ == "__main__":
    main()

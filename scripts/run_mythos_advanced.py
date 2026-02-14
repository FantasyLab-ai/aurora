# scripts/run_mythos_advanced.py
from __future__ import annotations
import json
import sqlite3

from fantasyai.mythos.generator import generate_world_schema
from fantasyai.mythos.playtest_sim import (
    simulate_graph_playthrough,
    monte_carlo_graph_playtests,
)
from fantasyai.mythos.world_graph import WorldGraph, WorldNode, WorldEdge
from fantasyai.config import DATA_DIR

DB_PATH = DATA_DIR / "fantasyai_core.db"


def main() -> None:
    world = generate_world_schema()
    print(f"World ID: {world.id}")
    print(f"Biome: {world.biome}, Mood: {world.mood}, Time: {world.time_of_day}")
    print(f"Weather: {world.weather}")

    lore = world.lore
    balance = lore["balance_summary"]
    world_graph_dict = lore["world_graph"]

    # Rebuild WorldGraph dataclass from dict for simulation
    nodes = {
        nid: WorldNode(
            id=nid,
            label=n["label"],
            difficulty_level=n["difficulty_level"],
            is_main_path=n["is_main_path"],
        )
        for nid, n in world_graph_dict["nodes"].items()
    }
    edges = [
        WorldEdge(
            source=e["source"],
            target=e["target"],
            travel_cost=e["travel_cost"],
            kind=e["kind"],
        )
        for e in world_graph_dict["edges"]
    ]
    graph = WorldGraph(nodes=nodes, edges=edges)

    diff_curve = balance["difficulty_curve"]

    # Single playthrough
    summary = simulate_graph_playthrough(
        graph=graph,
        difficulty_curve=diff_curve,
        initial_power=1.0,
        power_growth=0.2,
        randomness=0.15,
        max_steps=15,
        seed=42,
    )
    print(f"Single run win rate: {summary.win_rate:.2%}, avg margin: {summary.avg_margin:.3f}")

    # Monte Carlo
    mc = monte_carlo_graph_playtests(
        graph=graph,
        difficulty_curve=diff_curve,
        runs=100,
        initial_power=1.0,
        power_growth=0.2,
        randomness=0.15,
        max_steps=15,
        seed=123,
    )
    print("Monte Carlo playtest stats:")
    print(json.dumps(mc, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Scenario construction: topology, roles and hourly physics for a micro-grid.

Two named presets and a parametric generator.

  brief       the 3-node reference grid from the project brief. Faithful, and
              useful for watching a negotiation, but it has a single buyer,
              which makes greedy allocation provably optimal — it cannot
              distinguish one method from another. Kept for demonstration.

  contended   6 nodes, 2 prosumers, 3 loads, 1 storage hub, wired so that two
              loads each reach only one prosumer. Supply claimed by the
              well-connected node strands them, so allocation order changes the
              system outcome and there is a real gap for a better method.

  generated   any node count, five topologies, roles and profiles assembled
              from `profiles.py`. This is what scale claims need: three nodes
              cannot demonstrate that decentralisation works, and the
              interesting question — where negotiation stops scaling — only
              appears somewhere north of twenty.

Topology matters more than node count. A ring gives every house exactly two
trading partners, so scarcity is local and information travels slowly. A mesh
lets everyone trade with everyone, which removes the routing problem entirely.
A random geometric graph — houses connect to whoever is physically near — is
the realistic case for a street, and is the default for generated scenarios.
"""

from __future__ import annotations

import math
import random
import string
from dataclasses import dataclass, field
from typing import Optional

import networkx as nx

import profiles as pf

# Roles the simulation understands. Prompts are keyed by these strings.
SOLAR = "solar_prosumer"
CONSUMER = "ev_consumer"
STORAGE = "battery_balancer"

TOPOLOGIES = ("mesh", "ring", "line", "geometric", "star")


# =============================================================================
# Specification
# =============================================================================

@dataclass
class ScenarioSpec:
    """Everything needed to construct a grid. Serialisable into run metadata."""
    name: str = "generated"
    nodes: int = 12
    topology: str = "geometric"

    # Role mix. The remainder after prosumers and storage are consumers.
    prosumer_fraction: float = 0.35
    storage_fraction: float = 0.15

    # Physical sizing
    solar_kw: float = 5.0
    household_kwh_day: float = 12.0
    ev_fraction: float = 0.6          # share of consumers that charge an EV
    battery_capacity_kwh: float = 10.0
    battery_initial_soc: float = 0.6

    # Location and weather for the synthetic solar model
    day_of_year: int = 172
    latitude: float = 40.0
    cloudiness: float = 0.25

    # Network sizing
    line_capacity_kwh: float = 4.0
    base_loss: float = 0.02

    # Economics
    consumer_credits: float = 20.0
    prosumer_credits: float = 10.0

    seed: int = 1

    # Optional measured data: node_id -> Profile, overriding the synthetic model
    measured: dict = field(default_factory=dict)


def node_names(count: int) -> list[str]:
    """A..Z for the first 26 nodes, then N26, N27, ... so ids stay readable."""
    letters = list(string.ascii_uppercase)
    return [letters[i] if i < 26 else f"N{i}" for i in range(count)]


# =============================================================================
# Topology
# =============================================================================

def build_topology(spec: ScenarioSpec, names: list[str],
                   rng: random.Random) -> nx.Graph:
    """
    Wire the houses together and give every line a capacity and a loss.

    For the geometric topology, loss scales with physical distance, which is the
    honest version: a long span up the street costs more to push power through
    than a short one, and that asymmetry is exactly what the agents have to
    discover through price.
    """
    n = len(names)
    if spec.topology == "mesh":
        g = nx.complete_graph(n)
    elif spec.topology == "ring":
        g = nx.cycle_graph(n)
    elif spec.topology == "line":
        g = nx.path_graph(n)
    elif spec.topology == "star":
        g = nx.star_graph(n - 1)
    elif spec.topology == "geometric":
        # Grow the connection radius until the street is one connected grid —
        # an islanded house cannot trade with anyone and is not interesting.
        positions = {i: (rng.random(), rng.random()) for i in range(n)}
        radius = 0.3
        for _ in range(24):
            g = nx.random_geometric_graph(n, radius, pos=positions)
            if nx.is_connected(g):
                break
            radius += 0.05
        else:
            g = nx.cycle_graph(n)   # fall back to a ring rather than fail
            positions = None
    else:
        raise ValueError(f"unknown topology {spec.topology!r}; choose from {TOPOLOGIES}")

    graph = nx.Graph()
    for i, name in enumerate(names):
        graph.add_node(name)

    for u, v in g.edges():
        a, b = names[u], names[v]
        if spec.topology == "geometric":
            pos = g.nodes[u].get("pos"), g.nodes[v].get("pos")
            if all(pos):
                distance = math.dist(pos[0], pos[1])
                loss = min(0.09, spec.base_loss + distance * 0.12)
                capacity = spec.line_capacity_kwh * (1.0 if distance < 0.2 else 0.75)
            else:
                loss, capacity = spec.base_loss, spec.line_capacity_kwh
        else:
            loss = spec.base_loss + rng.uniform(0.0, 0.02)
            capacity = spec.line_capacity_kwh * rng.uniform(0.8, 1.2)
        graph.add_edge(a, b, capacity_kwh=round(capacity, 3), loss=round(loss, 4))

    return graph


# =============================================================================
# Roles and physics
# =============================================================================

def assign_roles(spec: ScenarioSpec, names: list[str], graph: nx.Graph,
                 rng: random.Random) -> dict[str, str]:
    """
    Hand out roles. Storage goes to the best-connected houses, which is both
    realistic (a community battery sits at the substation end of the street) and
    the arrangement most likely to make the grid work — so if allocation still
    fails, it is not because the battery was in a silly place.
    """
    n = len(names)
    n_storage = max(1, round(n * spec.storage_fraction))
    n_prosumer = max(1, round(n * spec.prosumer_fraction))

    by_degree = sorted(names, key=lambda x: (-graph.degree(x), x))
    roles: dict[str, str] = {}
    for name in by_degree[:n_storage]:
        roles[name] = STORAGE

    remaining = [x for x in names if x not in roles]
    rng.shuffle(remaining)
    for name in remaining[:n_prosumer]:
        roles[name] = SOLAR
    for name in remaining[n_prosumer:]:
        roles[name] = CONSUMER

    return {name: roles[name] for name in names}


def build_profiles(spec: ScenarioSpec, roles: dict[str, str],
                   rng: random.Random) -> tuple[dict[str, dict], list[pf.Profile]]:
    """
    Build each node's 24-hour net-energy profile: generation minus consumption.

    Returns per-node hourly series and the flat list of source Profiles, so the
    provenance of the whole scenario can be rolled up and reported.
    """
    per_node: dict[str, dict] = {}
    sources: list[pf.Profile] = []

    for name, role in roles.items():
        measured = spec.measured.get(name)
        if measured is not None:
            per_node[name] = {"net": list(measured.values)}
            sources.append(measured)
            continue

        generation = [0.0] * 24
        if role == SOLAR:
            # Vary array size a little: no two roofs are the same.
            capacity = spec.solar_kw * rng.uniform(0.7, 1.3)
            solar = pf.synthetic_solar(capacity, spec.day_of_year, spec.latitude,
                                       cloudiness=spec.cloudiness, rng=rng)
            sources.append(solar)
            generation = solar.values

        house = pf.synthetic_household(spec.household_kwh_day * rng.uniform(0.7, 1.3),
                                       jitter=0.1, rng=rng)
        sources.append(house)
        consumption = list(house.values)

        if role == CONSUMER and rng.random() < spec.ev_fraction:
            ev = pf.synthetic_ev(arrival_hour=rng.choice([16, 17, 17, 18, 19]),
                                 start_soc=rng.uniform(0.2, 0.45), rng=rng)
            sources.append(ev)
            consumption = [c + e for c, e in zip(consumption, ev.values)]

        per_node[name] = {"net": [round(g - c, 4) for g, c in zip(generation, consumption)]}

    return per_node, sources


# =============================================================================
# Public constructors
# =============================================================================

def generated_scenario(spec: ScenarioSpec, blocks: int, start_hour: int = 15):
    """
    Build a parametric scenario. Returns (graph, node_specs, profiles, meta)
    where node_specs describes each node's role and storage, and profiles is the
    per-block exogenous physics the simulation applies.

    `start_hour` defaults to 15:00 because that is where the problem is actually
    contested. Run at midday and there is a solar glut nobody has to allocate;
    run at 18:00 and the sun is gone, every house is short, and the only
    decision left is how much to buy from the utility. At 15:00 solar is fading
    while the evening ramp begins, and supply sits at roughly 0.85 of demand —
    tight enough that allocation quality changes the outcome, loose enough that
    most demand is servable by someone.
    """
    rng = random.Random(spec.seed)
    names = node_names(spec.nodes)
    graph = build_topology(spec, names, rng)
    roles = assign_roles(spec, names, graph, rng)
    per_node, sources = build_profiles(spec, roles, rng)

    node_specs = {}
    for name, role in roles.items():
        has_pack = role == STORAGE
        node_specs[name] = {
            "role": role,
            "battery_capacity": spec.battery_capacity_kwh if has_pack else 0.0,
            "battery_kwh": (spec.battery_capacity_kwh * spec.battery_initial_soc
                            if has_pack else 0.0),
            "credits": (spec.consumer_credits if role == CONSUMER
                        else spec.prosumer_credits),
        }

    block_profiles = []
    for i in range(blocks):
        hour = (start_hour + i) % 24
        block_profiles.append({
            "hour": hour,
            "exogenous": {name: per_node[name]["net"][hour] for name in names},
        })

    meta = {
        "scenario": spec.name,
        "nodes": spec.nodes,
        "topology": spec.topology,
        "edges": graph.number_of_edges(),
        "mean_degree": round(2.0 * graph.number_of_edges() / max(1, spec.nodes), 2),
        "roles": {r: sum(1 for v in roles.values() if v == r)
                  for r in (SOLAR, CONSUMER, STORAGE)},
        "provenance": pf.summarise_provenance(sources),
        "seed": spec.seed,
    }
    return graph, node_specs, block_profiles, meta


def preset_spec(name: str, **overrides) -> ScenarioSpec:
    """Named parametric presets, for sweeps that vary one dimension at a time."""
    presets = {
        # A street: enough houses for topology to matter, realistic mix.
        "street": ScenarioSpec(name="street", nodes=12, topology="geometric"),
        # A dense block where everyone can reach everyone — routing is trivial,
        # so any remaining gap is pure allocation quality.
        "block": ScenarioSpec(name="block", nodes=12, topology="mesh"),
        # A rural feeder: a line, so energy has to be relayed and the ends are
        # badly served. The hardest topology for a local-only protocol.
        "feeder": ScenarioSpec(name="feeder", nodes=16, topology="line"),
        # Scale test.
        "district": ScenarioSpec(name="district", nodes=32, topology="geometric"),
    }
    if name not in presets:
        raise ValueError(f"unknown preset {name!r}; choose from {sorted(presets)}")
    spec = presets[name]
    for key, value in overrides.items():
        if value is not None and hasattr(spec, key):
            setattr(spec, key, value)
    return spec

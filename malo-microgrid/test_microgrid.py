#!/usr/bin/env python3
"""
Test suite for the MALO micro-grid simulation.

Two things are verified here, and they are the two things that actually decide
whether this research approach is viable:

  1. THE HALLUCINATION FIREWALL. Every parser is exercised against the exact
     garbage a 1B model produces in practice — units inside JSON values, single
     quotes, bare keys, trailing commas, prose-only answers, number words,
     two JSON blocks, physically impossible quantities.

  2. THE FULL NEGOTIATION PATH, without Ollama. A `MockModel` stands in for the
     local LLM and deliberately cycles through clean / messy / prose-only /
     unusable replies, so the whole loop is driven end-to-end and the physics
     invariants are checked on the result.

Run:  python test_microgrid.py      (no pytest required; pytest also works)
"""

from __future__ import annotations

import io
import re
import sys
from contextlib import redirect_stdout

import microgrid_sim as sim


# =============================================================================
# 1. Parser unit tests — real failure modes observed from llama3.2:1b
# =============================================================================

def test_extract_clean_fenced_json():
    text = 'I have 5 kWh spare.\n```json\n{"offer_kwh": 3.5, "price": 0.16}\n```'
    assert sim.extract_json_block(text) == {"offer_kwh": 3.5, "price": 0.16}


def test_extract_prefers_last_block():
    # Small models often restate a corrected answer at the end.
    text = ('```json\n{"offer_kwh": 1.0, "price": 0.1}\n```\n'
            'Wait, I can do better.\n```json\n{"offer_kwh": 4.0, "price": 0.2}\n```')
    assert sim.extract_json_block(text)["offer_kwh"] == 4.0


def test_extract_repairs_dirty_json():
    cases = [
        ("{offer_kwh: 3.0, price: 0.2,}", 3.0),               # bare keys + trailing comma
        ("{'offer_kwh': 3.0, 'price': 0.2}", 3.0),            # single quotes
        ('{"offer_kwh": 3.0, // best I can do\n "price": 0.2}', 3.0),  # comment
        ('{"offer_kwh": 3.0, "price": None}', 3.0),           # Python literal
    ]
    for raw, expected in cases:
        got = sim.extract_json_block(raw)
        assert got is not None, f"failed to parse: {raw}"
        assert sim.coerce_number(got["offer_kwh"]) == expected, raw


def test_extract_ignores_unbalanced_noise():
    assert sim.extract_json_block("here is my answer: { offer") is None
    assert sim.extract_json_block("") is None
    assert sim.extract_json_block("no json at all, sorry") is None


def test_coerce_number_handles_model_noise():
    cases = [
        (5, 5.0), ("5", 5.0), (" 5.0 kWh ", 5.0), ("$0.12", 0.12),
        ("0,12", 0.12), ("four", 4.0), ("none", 0.0), ("about 3-4 kWh", 3.5),
        ("0.18 tokens/kWh", 0.18), ([2.5, 1.0], 2.5), ("-8 kWh", -8.0),
    ]
    for raw, expected in cases:
        assert sim.coerce_number(raw) == expected, f"{raw!r} -> {sim.coerce_number(raw)}"
    # Non-quantities must fall through to the default, not to a bogus number.
    assert sim.coerce_number(True, 9.0) == 9.0
    assert sim.coerce_number("as much as possible", 9.0) == 9.0
    assert sim.coerce_number(None, 9.0) == 9.0


def test_salvage_from_prose_only_reply():
    text = "I can offer 4 kWh at a price of 0.18 per kWh, which seems fair."
    got = sim.salvage_by_regex(text, ["offer_kwh", "price"])
    assert got["price"] == 0.18


def test_clamp_bounds():
    assert sim.clamp(99.0, 0.0, 5.0) == 5.0
    assert sim.clamp(-3.0, 0.0, 5.0) == 0.0


# =============================================================================
# 2. Physics unit tests
# =============================================================================

def test_battery_limits():
    st = sim.NodeState("C", "battery_balancer", net_kwh=0.0,
                       battery_kwh=10.0, battery_capacity=10.0)
    assert st.chargeable() == 0.0                       # full pack absorbs nothing
    assert st.dischargeable() == sim.BATTERY_MAX_RATE_KWH   # rate-limited, not energy-limited

    empty = sim.NodeState("C", "battery_balancer", net_kwh=0.0,
                          battery_kwh=sim.BATTERY_RESERVE_KWH, battery_capacity=10.0)
    assert empty.dischargeable() == 0.0                 # reserve floor is respected


def test_settlement_respects_line_capacity_and_conserves_energy():
    grid = sim.MicroGrid()
    grid.begin_block()
    console, tel = sim.Console(color=False), sim.Telemetry()

    seller = sim.GridAgent(sim.NodeState("A", "solar_prosumer", net_kwh=20.0),
                           sim.OllamaClient(offline=True, console=console), tel, console)
    buyer = sim.GridAgent(sim.NodeState("B", "ev_consumer", net_kwh=-20.0, credits=999.0),
                          sim.OllamaClient(offline=True, console=console), tel, console)

    with redirect_stdout(io.StringIO()):
        rec = sim.settle(grid, seller, buyer, kwh=50.0, price=0.1, console=console, tel=tel)

    cap = grid.g["A"]["B"]["capacity_kwh"]
    assert rec["sent_kwh"] <= cap + sim.EPS, "line capacity was violated"
    assert rec["delivered_kwh"] <= rec["sent_kwh"], "energy was created in transit"
    assert tel.clamped_values >= 1, "an impossible trade size was not flagged"


def test_settlement_respects_buyer_budget():
    grid = sim.MicroGrid()
    grid.begin_block()
    console, tel = sim.Console(color=False), sim.Telemetry()
    seller = sim.GridAgent(sim.NodeState("A", "solar_prosumer", net_kwh=5.0),
                           sim.OllamaClient(offline=True, console=console), tel, console)
    buyer = sim.GridAgent(sim.NodeState("B", "ev_consumer", net_kwh=-5.0, credits=0.10),
                          sim.OllamaClient(offline=True, console=console), tel, console)

    with redirect_stdout(io.StringIO()):
        rec = sim.settle(grid, seller, buyer, kwh=5.0, price=1.0, console=console, tel=tel)

    assert buyer.state.credits >= -sim.EPS, "buyer spent credits it did not have"
    if rec:
        assert rec["credits"] <= 0.10 + sim.EPS


# =============================================================================
# 3. Mock local model — drives the full loop with realistically bad output
# =============================================================================

class MockModel(sim.OllamaClient):
    """
    Stands in for llama3.2:1b. Cycles deliberately through the four reply
    qualities we see in the wild so every branch of `_decide` is exercised:

        0  clean fenced JSON            -> parse="json"
        1  dirty JSON (units, quotes)   -> parse="json" via repair + coercion
        2  prose only, no JSON          -> parse="salvage"
        3  refusal / word salad         -> parse="fallback"
        4  JSON with impossible numbers -> parse="json" then physics clamps
    """

    def __init__(self) -> None:
        super().__init__(offline=False, console=sim.Console(color=False))
        self.available = True
        self.n = 0

    @staticmethod
    def _example_keys(prompt: str) -> list[str]:
        blocks = sim._balanced_objects(prompt)
        if not blocks:
            return []
        return re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', blocks[-1])

    @staticmethod
    def _value_for(key: str, style: int) -> object:
        big = 999.0 if style == 4 else None
        if key == "intent":
            return "sell"
        if key == "accept":
            return True
        if key in ("price", "counter_price", "final_price", "reservation_price"):
            return big or 0.14
        return big or 2.0

    def generate(self, system: str, prompt: str) -> sim.LLMResult:
        style = self.n % 5
        self.n += 1
        keys = self._example_keys(prompt) or ["kwh", "price"]
        vals = {k: self._value_for(k, style) for k in keys}

        if style == 0:
            body = ", ".join(f'"{k}": {v!r}' if isinstance(v, str) else f'"{k}": {v}'
                             for k, v in vals.items())
            text = f"My balance allows this trade.\n```json\n{{{body}}}\n```"
        elif style == 1:
            body = ", ".join(f"'{k}': '{v} kWh'" if not isinstance(v, str) else f"'{k}': '{v}'"
                             for k, v in vals.items())
            text = f"Reasoning: prices are firm.\n```json\n{{{body},}}\n```"
        elif style == 2:
            text = "I think " + ", ".join(f"{k} is {v}" for k, v in vals.items()) + " overall."
        elif style == 3:
            text = "As an AI language model I cannot determine energy prices. Sorry!"
        else:
            body = ", ".join(f'"{k}": {v!r}' if isinstance(v, str) else f'"{k}": {v}'
                             for k, v in vals.items())
            text = f"I will sell everything I have.\n```json\n{{{body}}}\n```"
        return sim.LLMResult(text, True, 0.01)


def test_full_run_with_mock_model():
    """End-to-end: every parse branch fires and the physics still balances."""
    console = sim.Console(color=False)
    tel = sim.Telemetry()
    grid, states, profiles = sim.build_scenario(blocks=3, battery_soc=6.0)
    llm = MockModel()
    agents = {nid: sim.GridAgent(st, llm, tel, console) for nid, st in states.items()}

    import random
    rng = random.Random(7)
    with redirect_stdout(io.StringIO()):
        results = [sim.run_trading_block(i, 3, p, grid, agents, console, tel, rng)
                   for i, p in enumerate(profiles, start=1)]

    assert len(results) == 3
    # All four reply qualities were exercised.
    assert tel.json_ok > 0, "clean JSON path never ran"
    assert tel.json_salvaged > 0, "regex salvage path never ran"
    assert tel.json_lost > 0, "deterministic fallback path never ran"
    assert tel.clamped_values > 0, "impossible LLM numbers were not clamped"

    for r in results:
        assert r["traded_kwh"] >= 0
        assert r["line_loss_kwh"] >= -sim.EPS, "negative line loss = energy created"
        assert r["utility_import_kwh"] >= -sim.EPS
        # No trade may exceed what the seller could physically have produced.
        for t in r["trades"]:
            assert t["delivered_kwh"] <= t["sent_kwh"] + sim.EPS

    for a in agents.values():
        assert a.state.battery_kwh >= -sim.EPS, "battery discharged below empty"
        assert a.state.battery_kwh <= a.state.battery_capacity + sim.EPS, "battery overfilled"


def test_offline_control_arm_runs_clean():
    """The heuristic-only control arm must complete with zero LLM dependency."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = sim.main(["--offline", "--no-color", "--blocks", "3", "--no-export"])
    out = buf.getvalue()
    assert rc == 0
    assert "RUN SUMMARY" in out
    assert "SETTLED" in out, "no trade cleared in the control arm"


def test_agents_cannot_read_peer_state():
    """
    Architectural guard: `GridAgent` decision methods must not accept another
    agent's NodeState. Peers exchange `Message` objects only.
    """
    import inspect
    for name in ("make_offer", "counter_bid", "respond_to_counter",
                 "allocate", "bid_for_surplus", "respond_to_bid"):
        sig = inspect.signature(getattr(sim.GridAgent, name))
        for pname, param in sig.parameters.items():
            ann = param.annotation
            assert "NodeState" not in str(ann), (
                f"GridAgent.{name} takes a peer NodeState ({pname}); "
                "that breaks the decentralization constraint")


# =============================================================================
# 4. Optimal-allocation oracle
# =============================================================================

import oracle


def test_oracle_matches_hand_computed_optimum():
    """
    Block 1 of the brief scenario, worked out by hand:
      A holds 5.0 kWh and its line to B is capped at 5.0 with 2% loss.
      C can discharge 4.75 kWh over a 3%-loss line.
      B needs 8.0 kWh.
    Best play is A sending its full 5.0 (delivering 4.90) and C topping up with
    3.196 (delivering 3.10) — 8.0 kWh served, nothing curtailed, and the least
    possible energy pushed over the wires.
    """
    r = oracle.optimal_allocation(
        max_export={"A": 5.0, "C": 4.75}, deficit={"B": 8.0},
        chargeable={"C": 4.21}, surplus={"A": 5.0},
        links={("A", "B"): (5.0, 0.02), ("A", "C"): (5.0, 0.02), ("B", "C"): (6.0, 0.03)})

    assert abs(r["delivered_to_demand_kwh"] - 8.0) < 1e-3
    assert r["utility_import_kwh"] == 0.0
    assert r["curtailed_kwh"] == 0.0, "an optimal plan never wastes sellable solar"
    assert abs(r["flows"]["A->B:D"] - 5.0) < 1e-3, "A's cheap line should be saturated"


def test_oracle_prefers_at_risk_generation_over_battery():
    """
    Stage 2 of the LP exists for this case. Demand can be met either from solar
    that would otherwise be curtailed, or from a battery that keeps its charge
    regardless. Both serve the same kWh, so stage 1 is indifferent — only the
    curtailment objective breaks the tie the right way.
    """
    r = oracle.optimal_allocation(
        max_export={"A": 4.0, "C": 4.0}, deficit={"B": 3.0},
        chargeable={"C": 0.0}, surplus={"A": 4.0},   # C's export is stored, not at risk
        links={("A", "B"): (5.0, 0.02), ("B", "C"): (5.0, 0.02)})
    assert r["flows"].get("A->B:D", 0.0) > r["flows"].get("C->B:D", 0.0), \
        "solar at risk of curtailment should be drawn before stored energy"


def test_oracle_respects_line_capacity():
    """A seller with plenty of energy but a thin wire cannot serve a big load."""
    r = oracle.optimal_allocation(
        max_export={"A": 50.0}, deficit={"B": 50.0}, chargeable={},
        surplus={"A": 50.0}, links={("A", "B"): (4.0, 0.02)})
    assert r["sent_kwh"] <= 4.0 + 1e-6
    assert r["utility_import_kwh"] > 40.0, "the rest must fall through to the utility"


def test_greedy_fallback_is_valid_but_not_better_than_lp():
    """
    Without scipy the oracle degrades to a greedy fill. That result must remain
    a feasible allocation, and it must never exceed the true optimum — if it
    did, efficiency ratios computed against it would exceed 100%.
    """
    kwargs = dict(
        max_export={"A": 5.0, "C": 4.75}, deficit={"B": 8.0},
        chargeable={"C": 4.21}, surplus={"A": 5.0},
        links={("A", "B"): (5.0, 0.02), ("A", "C"): (5.0, 0.02), ("B", "C"): (6.0, 0.03)})

    exact = oracle.optimal_allocation(**kwargs)
    oracle._HAVE_SCIPY = False
    try:
        approx = oracle.optimal_allocation(**kwargs)
    finally:
        oracle._HAVE_SCIPY = True

    assert approx["exact"] is False, "an approximate bound must declare itself"
    assert approx["delivered_to_demand_kwh"] <= exact["delivered_to_demand_kwh"] + 1e-3


def test_simulation_never_beats_the_oracle():
    """
    The invariant the whole metric rests on. If any run serves more demand than
    the optimum says is possible, the bound is wrong and every efficiency figure
    is meaningless. Checked across both scenarios and several instances.
    """
    console = sim.Console(color=False, quiet=True)
    for scenario in ("brief", "contended"):
        for seed in (1, 2, 3, 4, 5):
            results, _agents, _tel = sim.run_simulation(
                scenario=scenario, offline=True, blocks=3, seed=seed,
                jitter=0.4, console=console)
            for r in results:
                opt = r.get("optimal_utility_import_kwh")
                if opt is None:
                    continue
                assert r["utility_import_kwh"] >= opt - 1e-3, (
                    f"{scenario} seed {seed} block {r['block']}: simulation imported "
                    f"{r['utility_import_kwh']:.3f} kWh but the optimum claims "
                    f"{opt:.3f} kWh was unavoidable — the bound is not a bound")
                assert r["allocation_efficiency_pct"] <= 100.0 + 1e-6


def test_contended_scenario_discriminates():
    """
    The brief's 3-node grid has a single buyer, which makes greedy allocation
    optimal and the benchmark blind. The contended scenario must actually leave
    a gap for a better method to close, or there is nothing to research.
    """
    console = sim.Console(color=False, quiet=True)
    gaps = []
    for seed in range(1, 9):
        results, _a, _t = sim.run_simulation(scenario="contended", offline=True,
                                             blocks=3, seed=seed, jitter=0.4,
                                             console=console)
        eff = [r["allocation_efficiency_pct"] for r in results
               if r["allocation_efficiency_pct"] is not None]
        if eff:
            gaps.append(100.0 - sum(eff) / len(eff))
    assert gaps, "no efficiency measured — is scipy installed?"
    assert max(gaps) > 2.0, (
        "greedy allocation is already near-optimal on every instance; the "
        "benchmark cannot distinguish methods and needs harder scenarios")


# =============================================================================
# Runner (works with or without pytest)
# =============================================================================

def _main() -> int:
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())

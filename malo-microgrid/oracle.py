#!/usr/bin/env python3
"""
Optimal-allocation oracle — the number every LLM result is measured against.

Why this module exists
----------------------
"The agents served 93% of demand locally" means nothing on its own. 93% of
*what*? If the physically optimal allocation was 94%, the LLMs are excellent.
If it was 100%, they left a fifth of the achievable benefit on the table.
Without this oracle there is no research result, only an anecdote.

So: given the exact physical state the agents face at the start of a trading
block, solve for the best allocation any method could possibly achieve, and
report it as an upper bound. `allocation_efficiency = achieved / optimal` is
then the headline metric — comparable across models, scenarios and topologies.

Scope of the bound
------------------
The oracle is restricted to the SAME action space the agents have: single-hop
trades between directly-wired neighbours within one block. It is therefore a
tight upper bound on what the negotiation could have reached, not a fantasy
bound that assumes capabilities the protocol does not offer.

Formulation (linear program, solved lexicographically)
------------------------------------------------------
Decision variables, for every wired pair (s, b) and each of two purposes:
    x_D[s,b]  kWh SENT from s to b to serve b's demand
    x_S[s,b]  kWh SENT from s to b to charge b's battery

    Stage 1   maximize   Σ (1 - loss_sb) · x_D[s,b]     (serve real demand)
    Stage 2   minimize   Σ u[s]                          (waste no clean energy)
    Stage 3   minimize   Σ (x_D + x_S)                   (waste no line losses)

    each stage constrained to preserve the previous stages' optima, with

    s.t.  Σ_b (x_D + x_S)          ≤ max_export[s]     seller cannot oversell
          x_D[s,b] + x_S[s,b]      ≤ capacity[s,b]     line rating (shared)
          Σ_s (1-loss)·x_D[s,b]    ≤ deficit[b]        do not overfill demand
          Σ_s (1-loss)·x_S[s,b]    ≤ chargeable[b]     pack headroom
          u[s] ≥ surplus[s] − Σ_b (x_D + x_S)          curtailment at s
          x, u ≥ 0

Lexicographic ordering encodes the physical priority: serving real demand
displaces a fossil peaker, curtailing clean energy destroys it outright, and
line losses merely cost a few percent. A single weighted objective would
silently trade these against each other at whatever exchange rate the weights
implied.

Stage 2 is not cosmetic. Stage 1 alone is massively degenerate — many
allocations serve the same demand — and an arbitrary optimal vertex will happily
source that demand from a battery while a neighbour's solar goes to waste. The
curtailment stage picks, among all demand-optimal allocations, the ones that
draw from at-risk generation first. Storage charging then falls out of the same
objective, since energy parked in a pack is energy not curtailed.

Solver: scipy.optimize.linprog (HiGHS). If scipy is absent the module falls
back to a greedy low-loss-first fill, which is a valid allocation but only a
LOWER bound on the optimum — results carry `exact: False` so a run can never
quietly report an efficiency ratio against an approximate denominator.
"""

from __future__ import annotations

from typing import Optional

try:
    from scipy.optimize import linprog
    _HAVE_SCIPY = True
except ImportError:  # pragma: no cover - exercised on minimal installs
    _HAVE_SCIPY = False

TOL = 1e-7
SNAP = 1e-4     # LP tolerance dust below this is reported as exactly zero


def _snap(x: float) -> float:
    """Round LP numerical dust to zero so metrics read 0.00, not 1e-06."""
    return 0.0 if abs(x) < SNAP else round(x, 6)


# =============================================================================
# Public result container
# =============================================================================

def _empty_result(exact: bool) -> dict:
    return {
        "delivered_to_demand_kwh": 0.0,
        "delivered_to_storage_kwh": 0.0,
        "sent_kwh": 0.0,
        "utility_import_kwh": 0.0,
        "curtailed_kwh": 0.0,
        "flows": {},
        "exact": exact,
        "method": "linprog" if exact else "greedy",
    }


def optimal_allocation(max_export: dict[str, float],
                       deficit: dict[str, float],
                       chargeable: dict[str, float],
                       surplus: dict[str, float],
                       links: dict[tuple[str, str], tuple[float, float]]) -> dict:
    """
    Solve for the physically optimal single-hop allocation of one trading block.

    Parameters take plain numbers, deliberately: the oracle has no dependency on
    the simulation's classes, so it can be unit-tested in isolation and reused
    against any other implementation of this benchmark.

      max_export  node -> kWh it could put on the wire (generation + discharge)
      deficit     node -> kWh of unmet demand
      chargeable  node -> kWh of battery headroom
      surplus     node -> kWh of *generated* energy at risk of curtailment
                          (battery energy left unsold is not curtailed, it keeps)
      links       (u, v) -> (capacity_kwh, loss_fraction), undirected

    Returns delivered/curtailed/utility totals and the per-arc flows.
    """
    # Undirected links, but flow has a direction: expand to directed arcs.
    arcs: list[tuple[str, str, float, float]] = []   # (seller, buyer, cap, loss)
    for (u, v), (cap, loss) in links.items():
        for s, b in ((u, v), (v, u)):
            if max_export.get(s, 0.0) > TOL and (
                    deficit.get(b, 0.0) > TOL or chargeable.get(b, 0.0) > TOL):
                arcs.append((s, b, float(cap), float(loss)))

    if not arcs:
        result = _empty_result(exact=True)
        result["utility_import_kwh"] = _snap(sum(deficit.values()))
        result["curtailed_kwh"] = _snap(sum(surplus.values()))
        return result

    solve = _solve_lp if _HAVE_SCIPY else _solve_greedy
    flows = solve(arcs, max_export, deficit, chargeable, surplus)

    # --- reduce the flow solution to the reported physical quantities --------
    delivered_demand = sum(f * (1 - loss)
                           for (s, b, purpose), (f, loss) in flows.items()
                           if purpose == "D")
    delivered_storage = sum(f * (1 - loss)
                            for (s, b, purpose), (f, loss) in flows.items()
                            if purpose == "S")
    sent_total = sum(f for (f, _loss) in flows.values())

    sent_by_seller: dict[str, float] = {}
    served_by_buyer: dict[str, float] = {}
    for (s, b, purpose), (f, loss) in flows.items():
        sent_by_seller[s] = sent_by_seller.get(s, 0.0) + f
        if purpose == "D":
            served_by_buyer[b] = served_by_buyer.get(b, 0.0) + f * (1 - loss)

    # Unserved demand falls through to the utility, i.e. to a fossil peaker.
    utility = sum(max(0.0, need - served_by_buyer.get(node, 0.0))
                  for node, need in deficit.items())
    # A seller draws live generation before its battery, so only generation that
    # was never sent is curtailed. Energy left in a pack is not lost.
    curtailed = sum(max(0.0, gen - sent_by_seller.get(node, 0.0))
                    for node, gen in surplus.items())

    result = _empty_result(exact=_HAVE_SCIPY)
    result.update({
        "delivered_to_demand_kwh": _snap(delivered_demand),
        "delivered_to_storage_kwh": _snap(delivered_storage),
        "sent_kwh": _snap(sent_total),
        "utility_import_kwh": _snap(utility),
        "curtailed_kwh": _snap(curtailed),
        "flows": {f"{s}->{b}:{p}": round(f, 4)
                  for (s, b, p), (f, _l) in flows.items() if f > TOL},
    })
    return result


# =============================================================================
# Exact solver — two-stage lexicographic LP
# =============================================================================

def _solve_lp(arcs, max_export, deficit, chargeable, surplus) -> dict:
    """Build and solve the three-stage lexicographic LP from the module docstring."""
    # Variable layout: [x_D per arc] + [x_S per arc] + [u per seller]
    n = len(arcs)
    sellers = sorted({a[0] for a in arcs})
    buyers = sorted({a[1] for a in arcs})
    idx_d = list(range(n))
    idx_s = list(range(n, 2 * n))
    idx_u = {node: 2 * n + i for i, node in enumerate(sellers)}
    nvars = 2 * n + len(sellers)

    def _row() -> list[float]:
        return [0.0] * nvars

    A_ub: list[list[float]] = []
    b_ub: list[float] = []

    # 1. seller export limit: everything it sends, for any purpose
    for node in sellers:
        row = _row()
        for k, (sn, _b, _c, _l) in enumerate(arcs):
            if sn == node:
                row[idx_d[k]] = row[idx_s[k]] = 1.0
        A_ub.append(row)
        b_ub.append(max(0.0, max_export.get(node, 0.0)))

    # 2. line rating: both purposes share the same physical conductor
    for k, (_s, _b, cap, _l) in enumerate(arcs):
        row = _row()
        row[idx_d[k]] = row[idx_s[k]] = 1.0
        A_ub.append(row)
        b_ub.append(max(0.0, cap))

    # 3. buyer demand ceiling (in DELIVERED kWh, so loss applies here)
    for node in buyers:
        row = _row()
        for k, (_s, b, _c, loss) in enumerate(arcs):
            if b == node:
                row[idx_d[k]] = 1.0 - loss
        A_ub.append(row)
        b_ub.append(max(0.0, deficit.get(node, 0.0)))

    # 4. battery headroom ceiling (also in delivered kWh)
    for node in buyers:
        row = _row()
        for k, (_s, b, _c, loss) in enumerate(arcs):
            if b == node:
                row[idx_s[k]] = 1.0 - loss
        A_ub.append(row)
        b_ub.append(max(0.0, chargeable.get(node, 0.0)))

    # 5. curtailment linearisation:  u[s] >= surplus[s] - sent[s]
    #    i.e.  -sent[s] - u[s] <= -surplus[s]
    for node in sellers:
        row = _row()
        for k, (sn, _b, _c, _l) in enumerate(arcs):
            if sn == node:
                row[idx_d[k]] = row[idx_s[k]] = -1.0
        row[idx_u[node]] = -1.0
        A_ub.append(row)
        b_ub.append(-max(0.0, surplus.get(node, 0.0)))

    bounds = [(0.0, None)] * nvars

    # --- Stage 1: maximise delivered energy that serves real demand ----------
    c_demand = _row()
    for k, (_s, _b, _cap, loss) in enumerate(arcs):
        c_demand[idx_d[k]] = -(1.0 - loss)        # linprog minimises
    stage1 = linprog(c_demand, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if not stage1.success:
        return _solve_greedy(arcs, max_export, deficit, chargeable, surplus)
    best_demand = -stage1.fun
    # Lock stage 1 in. We need Σ(1-loss)·x_D >= best - tol, and A_ub rows are
    # "<=" constraints, so negate both sides: -Σ(1-loss)·x_D <= -(best - tol).
    # c_demand already holds the negated coefficients, so it IS that row.
    A_ub.append(list(c_demand))
    b_ub.append(-(best_demand - 1e-6))

    # --- Stage 2: among those, waste the least clean energy ------------------
    c_curtail = _row()
    for node in sellers:
        c_curtail[idx_u[node]] = 1.0
    stage2 = linprog(c_curtail, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    solution = stage2 if stage2.success else stage1
    if stage2.success:
        A_ub.append(list(c_curtail))
        b_ub.append(stage2.fun + 1e-6)

        # --- Stage 3: among those, move the least energy over the wires -----
        c_sent = _row()
        for k in range(n):
            c_sent[idx_d[k]] = c_sent[idx_s[k]] = 1.0
        stage3 = linprog(c_sent, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if stage3.success:
            solution = stage3

    flows: dict[tuple[str, str, str], tuple[float, float]] = {}
    for k, (sn, b, _cap, loss) in enumerate(arcs):
        d_val, s_val = float(solution.x[idx_d[k]]), float(solution.x[idx_s[k]])
        if d_val > TOL:
            flows[(sn, b, "D")] = (d_val, loss)
        if s_val > TOL:
            flows[(sn, b, "S")] = (s_val, loss)
    return flows


# =============================================================================
# Approximate solver — used only when scipy is unavailable
# =============================================================================

def _solve_greedy(arcs, max_export, deficit, chargeable, surplus=None) -> dict:
    """
    Low-loss-first fill: serve all demand before storing anything. Optimal for a
    single buyer, merely good with several — hence `exact: False` upstream.
    """
    remaining_export = {k: float(v) for k, v in max_export.items()}
    remaining_demand = {k: float(v) for k, v in deficit.items()}
    remaining_room = {k: float(v) for k, v in chargeable.items()}
    remaining_line = {(s, b): cap for (s, b, cap, _l) in arcs}
    flows: dict[tuple[str, str, str], tuple[float, float]] = {}

    for purpose, sink in (("D", remaining_demand), ("S", remaining_room)):
        for s, b, _cap, loss in sorted(arcs, key=lambda a: a[3]):   # least lossy first
            want_delivered = sink.get(b, 0.0)
            if want_delivered <= TOL:
                continue
            send = min(remaining_export.get(s, 0.0),
                       remaining_line.get((s, b), 0.0),
                       want_delivered / (1.0 - loss))
            if send <= TOL:
                continue
            flows[(s, b, purpose)] = (send, loss)
            remaining_export[s] -= send
            remaining_line[(s, b)] -= send
            sink[b] -= send * (1.0 - loss)
    return flows


# =============================================================================
# Adapter: build oracle inputs from live simulation state
# =============================================================================

def oracle_for_block(grid, states) -> dict:
    """
    Convenience adapter. `grid` is a MicroGrid, `states` a {node_id: NodeState}
    captured AFTER the block's exogenous physics and BEFORE any trading, so the
    oracle sees exactly the problem the agents were handed.
    """
    links = {(u, v): (float(data["capacity_kwh"]), float(data["loss"]))
             for u, v, data in grid.g.edges(data=True)}

    max_export = {n: s.max_export() for n, s in states.items()}
    deficit = {n: s.deficit for n, s in states.items()}
    chargeable = {n: s.chargeable() for n, s in states.items()}
    surplus = {n: s.surplus for n, s in states.items()}

    # Self-supply is netted out before the trading problem is posed. A node with
    # both a deficit and a charged pack covers itself locally — no wires, no
    # counterparty, no loss — so every optimal solution does it, and the
    # simulation does it too (at block close, as a last resort before the
    # utility). Netting it here keeps the oracle measuring the same problem the
    # agents actually negotiate over, instead of a strictly harder one that the
    # simulation could then appear to "beat".
    self_supplied = {}
    for node, state in states.items():
        own = min(deficit[node], max_export[node])
        if own > TOL:
            deficit[node] -= own
            max_export[node] -= own
            self_supplied[node] = round(own, 6)

    result = optimal_allocation(max_export, deficit, chargeable, surplus, links)
    result["self_supplied_kwh"] = self_supplied
    return result

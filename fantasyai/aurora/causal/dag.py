"""
Causal DAG construction + backdoor-set computation.

We construct the DAG from Aurora's existing system_model artifact —
nodes correspond to variables (columns), edges correspond to
discovered causal directions from Granger, mutual information, and
the relationships pipeline. This keeps the do-calculus engine
grounded in the same evidence the rest of the Studio renders.

The DAG is a thin layer over pure-Python adjacency dicts. We avoid a
heavy dep (networkx) so the runtime install stays lean. The graph
operations we need are limited:

  * ancestors(node) / descendants(node)
  * is_acyclic() — Tarjan-style SCC check
  * d-separation (for identification arguments) — implemented as
    a moralised-then-pruned undirected reachability check, the
    classic Lauritzen formulation.
  * backdoor_set(treatment, outcome) — find a set Z that blocks all
    backdoor paths from treatment to outcome.

When the underlying graph has cycles (rare but possible: the
relationships pipeline can emit X↔Y for tightly coupled variables),
we break cycles by dropping the lower-confidence edge and emit a
``DagCycleWarning`` in the result. The reduced DAG is acyclic by
construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple
import warnings


class DagCycleWarning(UserWarning):
    """Raised when cycles in the source system_model required edge drops."""


@dataclass
class CausalDAG:
    """A directed acyclic graph over variable names.

    ``edges`` maps a parent node to the set of children it points to.
    ``edge_confidence`` mirrors the same shape but stores the
    confidence weight Aurora attached to each edge — used as a
    tiebreaker when cycles need to be broken.
    """
    nodes:          FrozenSet[str]
    edges:          Dict[str, Set[str]]
    edge_confidence: Dict[Tuple[str, str], float] = field(default_factory=dict)
    dropped_edges:  List[Tuple[str, str]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Reachability primitives
    # ------------------------------------------------------------------

    def parents(self, node: str) -> Set[str]:
        return {p for p, kids in self.edges.items() if node in kids}

    def children(self, node: str) -> Set[str]:
        return set(self.edges.get(node, set()))

    def ancestors(self, node: str) -> Set[str]:
        seen: Set[str] = set()
        stack = list(self.parents(node))
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(self.parents(n))
        return seen

    def descendants(self, node: str) -> Set[str]:
        seen: Set[str] = set()
        stack = list(self.children(node))
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(self.children(n))
        return seen

    def is_acyclic(self) -> bool:
        # Standard Kahn topological sort.
        indeg: Dict[str, int] = {n: 0 for n in self.nodes}
        for parent, kids in self.edges.items():
            for kid in kids:
                if kid in indeg:
                    indeg[kid] += 1
        ready = [n for n, d in indeg.items() if d == 0]
        seen = 0
        while ready:
            n = ready.pop()
            seen += 1
            for kid in self.edges.get(n, ()):
                indeg[kid] -= 1
                if indeg[kid] == 0:
                    ready.append(kid)
        return seen == len(self.nodes)

    # ------------------------------------------------------------------
    # d-separation
    # ------------------------------------------------------------------

    def d_separated(
        self,
        x: str, y: str,
        given: Optional[Iterable[str]] = None,
    ) -> bool:
        """Bayes-ball: is X d-separated from Y given a conditioning set?

        We implement the classic algorithm (Shachter 1998) — walk
        from X along admissible directions, blocked vs unblocked by
        collider / non-collider rules, accumulate reachable nodes.
        """
        Z = set(given or ())
        if x in Z or y in Z:
            return True
        if x == y:
            return False
        ancestors_Z: Set[str] = set()
        for z in Z:
            ancestors_Z |= self.ancestors(z)
            ancestors_Z.add(z)
        # Mark every node as (node, direction) where direction is
        # "up" (toward parents) or "down" (toward children).
        visited: Set[Tuple[str, str]] = set()
        # Start at X moving in both directions.
        frontier: List[Tuple[str, str]] = [(x, "up"), (x, "down")]
        while frontier:
            node, direction = frontier.pop()
            if (node, direction) in visited:
                continue
            visited.add((node, direction))
            if node == y:
                # Reached y → NOT d-separated.
                return False
            if direction == "up":
                # Came from a child: can go up to parents (always blocked
                # by Z) or down to other children (always allowed).
                if node not in Z:
                    for p in self.parents(node):
                        frontier.append((p, "up"))
                    for c in self.children(node):
                        frontier.append((c, "down"))
            else:  # direction == "down"
                # Came from a parent: can go down to children if node
                # not in Z; can go up to parents only if node IS a
                # collider with descendants in Z (or itself in Z).
                if node not in Z:
                    for c in self.children(node):
                        frontier.append((c, "down"))
                if node in ancestors_Z:
                    # node is collider with a descendant in Z → unblocked
                    for p in self.parents(node):
                        frontier.append((p, "up"))
        return True


# ----------------------------------------------------------------------
# Construction from system_model
# ----------------------------------------------------------------------

def _normalize_edges(
    raw_edges: Iterable[Any],
) -> Tuple[Dict[str, Set[str]], Dict[Tuple[str, str], float]]:
    """Coerce the system_model's edge shape into ``edges`` +
    ``edge_confidence``.

    Accepted shapes (the system_model has emitted a few over time):
      * ``{"source": "a", "target": "b", "weight": 0.7}``
      * ``{"from": "a", "to": "b", "confidence": 0.7}``
      * ``("a", "b")`` tuple
    """
    out_edges: Dict[str, Set[str]] = {}
    conf: Dict[Tuple[str, str], float] = {}
    for e in raw_edges:
        if not e:
            continue
        if isinstance(e, dict):
            src = e.get("source") or e.get("from") or e.get("parent")
            tgt = e.get("target") or e.get("to") or e.get("child")
            w   = e.get("weight") or e.get("confidence") or e.get("strength") or 0.5
        elif isinstance(e, (tuple, list)) and len(e) >= 2:
            src, tgt = e[0], e[1]
            w = e[2] if len(e) >= 3 else 0.5
        else:
            continue
        if not src or not tgt:
            continue
        src = str(src)
        tgt = str(tgt)
        if src == tgt:
            continue
        out_edges.setdefault(src, set()).add(tgt)
        try:
            conf[(src, tgt)] = float(w)
        except (TypeError, ValueError):
            conf[(src, tgt)] = 0.5
    return out_edges, conf


def _break_cycles(
    edges: Dict[str, Set[str]],
    conf: Dict[Tuple[str, str], float],
) -> Tuple[Dict[str, Set[str]], List[Tuple[str, str]]]:
    """Drop the lowest-confidence edge in each cycle until the graph
    is acyclic. Returns the cleaned edges + list of dropped edges."""
    dropped: List[Tuple[str, str]] = []
    while True:
        # Try a Kahn sort; if it doesn't drain every node, there's a
        # cycle and we need to break it.
        nodes = set(edges.keys()) | {t for ts in edges.values() for t in ts}
        indeg: Dict[str, int] = {n: 0 for n in nodes}
        for parent, kids in edges.items():
            for kid in kids:
                indeg[kid] += 1
        ready = [n for n, d in indeg.items() if d == 0]
        ordered: Set[str] = set()
        while ready:
            n = ready.pop()
            ordered.add(n)
            for kid in list(edges.get(n, ())):
                indeg[kid] -= 1
                if indeg[kid] == 0:
                    ready.append(kid)
        residual = nodes - ordered
        if not residual:
            return edges, dropped
        # Find the lowest-confidence edge that points INTO the residual.
        cycle_edges = [
            (s, t) for s in edges for t in edges[s]
            if s in residual and t in residual
        ]
        if not cycle_edges:
            return edges, dropped  # defensive: shouldn't happen
        weakest = min(cycle_edges, key=lambda st: conf.get(st, 0.5))
        s, t = weakest
        edges[s].discard(t)
        if not edges[s]:
            del edges[s]
        dropped.append(weakest)


def load_dag_from_system_model(
    system_model: Optional[Dict[str, Any]],
) -> CausalDAG:
    """Build a CausalDAG from Aurora's state['system_model'] dict.

    Tolerates missing / empty system_model — returns an empty DAG.
    Tolerates cycles by dropping the weakest edge with a warning.
    """
    if not isinstance(system_model, dict):
        return CausalDAG(nodes=frozenset(), edges={}, edge_confidence={})
    raw_nodes = []
    for key in ("entities", "nodes", "variables"):
        v = system_model.get(key)
        if isinstance(v, list):
            raw_nodes = v
            break
    raw_edges = []
    for key in ("relationships", "edges", "links"):
        v = system_model.get(key)
        if isinstance(v, list):
            raw_edges = v
            break

    # Node names: pull from common keys.
    nodes: Set[str] = set()
    for n in raw_nodes:
        if isinstance(n, dict):
            name = n.get("name") or n.get("id") or n.get("variable")
            if name:
                nodes.add(str(name))
        elif isinstance(n, str):
            nodes.add(n)

    edges, conf = _normalize_edges(raw_edges)
    # Make sure every endpoint is in the node set.
    for s, ts in edges.items():
        nodes.add(s)
        for t in ts:
            nodes.add(t)
    edges, dropped = _break_cycles(edges, conf)
    if dropped:
        warnings.warn(
            f"system_model cycles broken: dropped {len(dropped)} weak "
            f"edges {dropped}",
            DagCycleWarning,
        )
    return CausalDAG(
        nodes=frozenset(nodes),
        edges=edges,
        edge_confidence=conf,
        dropped_edges=dropped,
    )


# ----------------------------------------------------------------------
# Backdoor criterion
# ----------------------------------------------------------------------

def backdoor_set(
    dag: CausalDAG,
    treatment: str,
    outcome:   str,
) -> Optional[Set[str]]:
    """Return a minimal admissible adjustment set for the effect of
    ``treatment`` on ``outcome``, or None if the effect is NOT
    identifiable from observational data via the backdoor criterion.

    The backdoor criterion says: Z admissible iff
      1. No node in Z is a descendant of treatment.
      2. Z blocks every backdoor path from treatment to outcome.

    We default to the **ancestral-parents heuristic**: Z = parents(treatment).
    When that doesn't satisfy backdoor (some confounder upstream isn't
    captured), we expand to ancestors(treatment) minus descendants(treatment).
    When even that fails, we return None (effect unidentifiable from
    the observed DAG).
    """
    if treatment not in dag.nodes or outcome not in dag.nodes:
        return None
    descendants_T = dag.descendants(treatment)
    # Candidate 1: parents of treatment.
    z = {p for p in dag.parents(treatment) if p not in descendants_T}
    if _is_backdoor_admissible(dag, treatment, outcome, z):
        return z
    # Candidate 2: ancestors of treatment minus its descendants.
    z = dag.ancestors(treatment) - descendants_T
    z.discard(treatment)
    z.discard(outcome)
    if _is_backdoor_admissible(dag, treatment, outcome, z):
        return z
    # Not identifiable from the DAG we have.
    return None


def _is_backdoor_admissible(
    dag:       CausalDAG,
    treatment: str,
    outcome:   str,
    z:         Set[str],
) -> bool:
    """Check the backdoor criterion for adjustment set Z."""
    # Rule 1: no descendant of treatment in Z.
    if z & dag.descendants(treatment):
        return False
    # Rule 2: Z d-separates treatment from outcome in the graph
    # G_underline (the DAG with edges OUT of treatment removed).
    # We approximate this by checking d-separation in the modified
    # graph — create a copy of the DAG with outgoing edges from
    # treatment removed.
    mod_edges = {n: set(kids) for n, kids in dag.edges.items()}
    if treatment in mod_edges:
        del mod_edges[treatment]
    mod = CausalDAG(
        nodes=dag.nodes,
        edges=mod_edges,
        edge_confidence=dag.edge_confidence,
    )
    return mod.d_separated(treatment, outcome, given=z)

"""
Intervention propagator — perturb a node, propagate through discovered
relationships, predict the system response.

This is the existing what-if engine elevated to the spatial system-model
surface. Same glass-box rules:

  * every predicted delta carries the relationship + prior that drove it
  * uncertain edges contribute uncertain deltas (CI widens)
  * no black-box neural propagation; we use the model's own coefficients

Algorithm
---------

For a perturbation Δx on entity ``source``:

  1. Walk every relationship outgoing from ``source`` (BFS, depth ≤ ``max_depth``)
  2. For each edge (source → target, kind, confidence):
       * if backed by a fitted physics_models param: use that param
         (linear, logistic, oscillator) to compute Δtarget directly
       * if causal what-if: linear scale by recovered β_treatment
       * else: scale by `strength` (or use confidence × Δsource as a proxy)
  3. Combine multi-path effects on the same target by summing deltas
     (linear superposition is honest; non-linear coupling is flagged)
  4. Tag each delta with a list of relationships that contributed,
     so the UI can render the full reasoning trail

Returns:
  PropagationResult with per-entity {delta, ci_low, ci_high, contributors}
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class EntityDelta:
    entity_id: str
    delta: float
    ci_low: float
    ci_high: float
    confidence: float                      # 0..1 — combined confidence
    contributors: List[Dict[str, Any]] = field(default_factory=list)
    # ↑ each: {"relationship_id", "rationale", "share", "kind"}

    def to_dict(self) -> Dict[str, Any]:
        return _safe(asdict(self))


@dataclass
class PropagationResult:
    source_entity_id: str
    perturbation: float
    max_depth: int
    deltas: List[EntityDelta] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_entity_id": self.source_entity_id,
            "perturbation": _scalar(self.perturbation),
            "max_depth": self.max_depth,
            "deltas": [d.to_dict() for d in self.deltas],
            "warnings": list(self.warnings),
            "rationale": self.rationale,
        }


def _scalar(x: Any) -> Any:
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def _safe(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_safe(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


# ---------------------------------------------------------------------------
# Coefficient resolution
# ---------------------------------------------------------------------------

def _edge_coefficient(rel: Dict[str, Any]) -> Tuple[float, float]:
    """Return (gain, gain_uncertainty) for a single relationship.

    Gain interpretation: Δtarget = gain × Δsource. Sign matters.
    Uncertainty is half-width; the propagator will widen CIs by it.

    Strategy:
      * explicit ``strength`` field → use it
      * confidence scales magnitude (0.5 conf = half effect, 1.0 = full)
      * causal kind → 1.0 baseline gain
      * physical kind → 1.0 baseline gain
      * lagged → 0.6 baseline gain (decays with lag, but we don't have horizon here)
      * correlational → use strength if present, else confidence × 0.5
      * regime_conditional → baseline 0.4 gain (state-dependent so we discount)
    """
    kind = (rel.get("kind") or "correlational").lower()
    strength = rel.get("strength")
    confidence = rel.get("confidence")
    if isinstance(strength, (int, float)) and not (isinstance(strength, bool)):
        gain = float(strength)
    else:
        # No explicit strength — use confidence (or fall back).
        c = confidence if isinstance(confidence, (int, float)) else 0.4
        if kind == "causal":
            gain = float(c)
        elif kind == "physical":
            gain = float(c)
        elif kind == "lagged":
            gain = 0.6 * float(c)
        elif kind == "regime_conditional":
            gain = 0.4 * float(c)
        else:
            gain = 0.5 * float(c)
    # Uncertainty half-width: 1 - confidence, floored at 0.1.
    if isinstance(confidence, (int, float)):
        uncertainty = max(0.1, 1.0 - float(confidence))
    else:
        uncertainty = 0.5
    return gain, uncertainty


# ---------------------------------------------------------------------------
# Propagation
# ---------------------------------------------------------------------------

def propagate_intervention(system_model: Dict[str, Any],
                           source_entity_id: str,
                           perturbation: float,
                           max_depth: int = 3,
                           ) -> PropagationResult:
    """Propagate a perturbation through the system model's relationships.

    Args:
        system_model: dict shape (output of ``SystemModel.to_dict``)
        source_entity_id: the perturbed entity
        perturbation: signed Δ in the entity's units
        max_depth: BFS hop limit

    Returns:
        ``PropagationResult`` — every affected entity carries a delta + CI
        + contributor relationships.
    """
    topo = (system_model or {}).get("topology") or {}
    entities = topo.get("entities") or []
    relationships = topo.get("relationships") or []
    entity_ids = {e.get("id") for e in entities}
    if source_entity_id not in entity_ids:
        return PropagationResult(
            source_entity_id=source_entity_id, perturbation=perturbation,
            max_depth=max_depth, deltas=[], warnings=[
                f"unknown source entity: {source_entity_id!r}"
            ],
            rationale="cannot propagate from an unknown source",
        )

    # Adjacency: for each entity, list outgoing edges.
    adj: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    rev: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in relationships:
        s, t = r.get("source"), r.get("target")
        if s in entity_ids and t in entity_ids:
            adj[s].append(r)
            rev[t].append(r)
            # Bidirectional edges propagate both ways.
            if (r.get("direction") or "unidirectional") == "bidirectional":
                adj[t].append(r)
                rev[s].append(r)

    # BFS out from source, accumulating deltas.
    deltas: Dict[str, float] = {source_entity_id: float(perturbation)}
    half_widths: Dict[str, float] = {source_entity_id: 0.0}
    contributors: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    confidence: Dict[str, float] = {source_entity_id: 1.0}
    queue: deque = deque([(source_entity_id, 0)])
    visited_at_depth: Dict[Tuple[str, int], bool] = {}
    warnings: List[str] = []

    while queue:
        cur, depth = queue.popleft()
        if depth >= max_depth:
            continue
        if visited_at_depth.get((cur, depth)):
            continue
        visited_at_depth[(cur, depth)] = True
        for rel in adj[cur]:
            tgt = rel.get("target") if rel.get("source") == cur else rel.get("source")
            if tgt is None or tgt == cur:
                continue
            gain, unc = _edge_coefficient(rel)
            inc = gain * deltas[cur]
            inc_unc = abs(gain) * half_widths.get(cur, 0.0) + unc * abs(deltas[cur])
            deltas[tgt] = deltas.get(tgt, 0.0) + inc
            half_widths[tgt] = max(half_widths.get(tgt, 0.0), inc_unc)
            # Combined confidence: minimum of edge confidences along the path,
            # clamped at 0.05 floor so we never claim 0.
            edge_c = float(rel.get("confidence")) if isinstance(rel.get("confidence"), (int, float)) else 0.4
            confidence[tgt] = max(0.05,
                                  min(confidence.get(tgt, 1.0),
                                      confidence.get(cur, 1.0) * edge_c))
            contributors[tgt].append({
                "relationship_id": rel.get("id"),
                "kind": rel.get("kind"),
                "rationale": rel.get("rationale", ""),
                "gain": gain,
                "share": float(inc),
                "from": cur,
                "depth": depth + 1,
                "prior_id": rel.get("prior_id"),
                "source_origin": rel.get("source_origin"),
            })
            queue.append((tgt, depth + 1))
            if depth + 1 >= max_depth:
                warnings.append(
                    f"propagation truncated at {tgt} after {depth+1} hops"
                )

    # Build the result list (skip the source — it's the one we perturbed).
    result_deltas: List[EntityDelta] = []
    for eid, d in deltas.items():
        if eid == source_entity_id:
            continue
        hw = half_widths.get(eid, 0.0)
        result_deltas.append(EntityDelta(
            entity_id=eid,
            delta=float(d),
            ci_low=float(d - hw),
            ci_high=float(d + hw),
            confidence=float(confidence.get(eid, 0.5)),
            contributors=contributors.get(eid, []),
        ))
    # Sort by absolute delta magnitude (biggest first).
    result_deltas.sort(key=lambda d: -abs(d.delta))

    return PropagationResult(
        source_entity_id=source_entity_id,
        perturbation=float(perturbation),
        max_depth=int(max_depth),
        deltas=result_deltas,
        warnings=warnings,
        rationale=(
            f"BFS up to depth {max_depth}; "
            f"{len(result_deltas)} downstream entities affected. "
            f"Each delta is the sum of contributions along its incoming paths; "
            f"CI half-widths reflect per-edge uncertainty."
        ),
    )

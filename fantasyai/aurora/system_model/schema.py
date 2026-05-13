"""
System Model schema — the formal anatomy.

A *system model* is a structured executable representation of WHAT KIND OF
SYSTEM the dataset describes. The cube is the analytical surface; the system
model is the synthetic-understanding surface. This module defines the shape
both must agree on.

A system model has seven first-class blocks:

  * ``topology``               — entities (nodes) + relationships (edges)
  * ``state``                  — current regime, key readings, drift status
  * ``constraints``            — hard / soft physical or operational bounds
  * ``characteristic_patterns``— named domain dynamics + signatures
  * ``vocabulary``             — domain language (anomaly_term, regime_term, …)
  * ``action_library``         — canonical responses keyed by state
  * ``provenance``             — template version, priors used, source run

The same schema accommodates every domain (enviro, research, ops, industrial,
finance, medical). Domain specifics live in ``DomainTemplate``; this file is
schema-only.

Glass-box invariants:
* every Entity carries the data column it maps to (or "missing/inferred")
* every Relationship carries the source: data | prior | template_expected
* every action carries: predicted_outcome, confidence_band, falsification
* nothing in here invents knowledge the math doesn't support
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Topology: entities and relationships
# ---------------------------------------------------------------------------

@dataclass
class Entity:
    """A node in the system graph — maps to a data column or derived variable."""
    id: str                                # stable id within the model
    label: str                             # human-readable
    role: str                              # forcing | state | response | indicator | actor
    data_column: Optional[str] = None      # actual column in the run dataset
    unit: Optional[str] = None             # SI string, when known
    dimension: Optional[str] = None        # dimensional bucket (T, L, …)
    typical_range: Optional[Tuple[float, float]] = None
    current_value: Optional[float] = None
    current_drift: Optional[str] = None    # "stable" | "drifting" | "unknown"
    presence: str = "present"              # present | missing | inferred
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _safe(asdict(self))


@dataclass
class Relationship:
    """An edge in the system graph — discovered or template-expected."""
    id: str
    source: str                            # entity id
    target: str                            # entity id
    kind: str                              # causal | physical | correlational | lagged | regime_conditional
    direction: str = "unidirectional"      # unidirectional | bidirectional
    strength: Optional[float] = None       # -1..1 (signed) where applicable
    confidence: Optional[float] = None     # 0..1
    lag: Optional[float] = None            # in seconds, if lagged
    source_origin: str = "template_expected"
    # ↑ "data" | "prior" | "template_expected" | "discovered" | "deviation"
    prior_id: Optional[str] = None         # if backed by a prior
    rationale: str = ""                    # one-line "why we believe this"
    falsification: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _safe(asdict(self))


@dataclass
class Topology:
    entities: List[Entity] = field(default_factory=list)
    relationships: List[Relationship] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities":      [e.to_dict() for e in self.entities],
            "relationships": [r.to_dict() for r in self.relationships],
        }


# ---------------------------------------------------------------------------
# State: where the system currently is
# ---------------------------------------------------------------------------

@dataclass
class StateBlock:
    active_regime: Optional[str] = None    # "STORM" | "STABLE" | "BREACH_PRECURSOR" …
    regime_confidence: Optional[float] = None
    active_patterns: List[Dict[str, Any]] = field(default_factory=list)
    # ↑ each: {"pattern_id", "name", "confidence", "started_at", "expected_duration"}
    key_readings: Dict[str, Any] = field(default_factory=dict)
    # ↑ {"<entity_id>": {"value": .., "z": .., "interpretation": ".."}}
    stability_score: Optional[float] = None    # 0..1; higher = more stable
    drift_indicators: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _safe(asdict(self))


# ---------------------------------------------------------------------------
# Constraints: hard + soft
# ---------------------------------------------------------------------------

@dataclass
class Constraint:
    id: str
    description: str
    kind: str                              # bound | conservation | dimensional | monotonicity
    hardness: str = "soft"                 # hard | soft
    on_entity: Optional[str] = None        # entity id this applies to (optional)
    expression: Optional[str] = None       # human-readable formula
    currently_satisfied: Optional[bool] = None
    deviation_metric: Optional[float] = None    # 0 = perfect; >0 = magnitude

    def to_dict(self) -> Dict[str, Any]:
        return _safe(asdict(self))


# ---------------------------------------------------------------------------
# Characteristic patterns
# ---------------------------------------------------------------------------

@dataclass
class CharacteristicPattern:
    id: str
    name: str                              # "frontal passage", "diurnal cycle"
    signature: str                         # human description of what to look for
    typical_duration_seconds: Optional[float] = None
    typical_successors: List[str] = field(default_factory=list)
    detection_rule: str = ""               # how Aurora detects this in data
    domain_specific_notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _safe(asdict(self))


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

@dataclass
class Vocabulary:
    """How this domain talks. Used by the narrator to re-language findings."""
    anomaly_term: str = "anomaly"
    regime_term: str = "regime"
    pattern_term: str = "pattern"
    action_term: str = "action"
    breach_term: str = "threshold breach"
    advisory_term: str = "advisory"
    extra: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _safe(asdict(self))


# ---------------------------------------------------------------------------
# Action library — canonical responses
# ---------------------------------------------------------------------------

@dataclass
class TemplateAction:
    """A canonical action surface the narrator + recommender can pull from."""
    id: str
    label: str
    description: str
    triggered_when: List[str]              # human conditions
    predicted_outcome_summary: str
    base_rate_success: float = 0.5
    confidence_band_required: str = "medium"   # high | medium | low
    falsification: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    relevant_states: List[str] = field(default_factory=list)
    # ↑ regime ids or pattern ids this action is appropriate for

    def to_dict(self) -> Dict[str, Any]:
        return _safe(asdict(self))


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceBlock:
    template_id: str
    template_version: str
    instantiated_from: Optional[str] = None     # run_dir
    instantiated_at: Optional[str] = None       # ISO ts
    data_columns_used: List[str] = field(default_factory=list)
    priors_used: List[str] = field(default_factory=list)
    confirmed_relationships: List[str] = field(default_factory=list)
    discovered_relationships: List[str] = field(default_factory=list)
    deviation_relationships: List[str] = field(default_factory=list)
    open_assumptions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _safe(asdict(self))


# ---------------------------------------------------------------------------
# The full system model
# ---------------------------------------------------------------------------

@dataclass
class SystemModel:
    """A complete system model — the synthetic-understanding output of a run.

    Same shape across every domain. Use ``validate()`` to check consistency
    before persisting.
    """
    schema_version: str = "system_model_v1"
    topology: Topology = field(default_factory=Topology)
    state: StateBlock = field(default_factory=StateBlock)
    constraints: List[Constraint] = field(default_factory=list)
    characteristic_patterns: List[CharacteristicPattern] = field(default_factory=list)
    vocabulary: Vocabulary = field(default_factory=Vocabulary)
    action_library: List[TemplateAction] = field(default_factory=list)
    provenance: ProvenanceBlock = field(default_factory=lambda: ProvenanceBlock("",""))
    confidence: float = 0.0    # 0..1 — how confident we are in the model overall

    def to_dict(self) -> Dict[str, Any]:
        # Atom 2.1: surface template_id at the top level so the rest of
        # Aurora (state_builder, narrative_engine, frontend chip rail) can
        # read it without diving into provenance. Mirrors what those
        # consumers already expected — they were silently missing it.
        return {
            "schema_version": self.schema_version,
            "template_id":    self.provenance.template_id,
            "template_version": self.provenance.template_version,
            "topology":       self.topology.to_dict(),
            "state":          self.state.to_dict(),
            "constraints":    [c.to_dict() for c in self.constraints],
            "characteristic_patterns":
                              [p.to_dict() for p in self.characteristic_patterns],
            "vocabulary":     self.vocabulary.to_dict(),
            "action_library": [a.to_dict() for a in self.action_library],
            "provenance":     self.provenance.to_dict(),
            "confidence":     _scalar(self.confidence),
        }

    def validate(self) -> List[str]:
        """Return a list of human-readable validation problems. Empty = valid."""
        problems: List[str] = []
        # ids unique within scope
        ent_ids = [e.id for e in self.topology.entities]
        if len(set(ent_ids)) != len(ent_ids):
            problems.append("entity ids are not unique")
        rel_ids = [r.id for r in self.topology.relationships]
        if len(set(rel_ids)) != len(rel_ids):
            problems.append("relationship ids are not unique")
        # all relationships reference existing entities
        ent_set = set(ent_ids)
        for r in self.topology.relationships:
            if r.source not in ent_set:
                problems.append(f"relationship {r.id}: unknown source {r.source!r}")
            if r.target not in ent_set:
                problems.append(f"relationship {r.id}: unknown target {r.target!r}")
        # provenance must declare a template
        if not self.provenance.template_id:
            problems.append("provenance.template_id is empty")
        # state.active_patterns reference real patterns when given
        pat_ids = {p.id for p in self.characteristic_patterns}
        for p in self.state.active_patterns:
            pid = p.get("pattern_id") if isinstance(p, dict) else None
            if pid and pid not in pat_ids:
                problems.append(f"state.active_patterns: unknown pattern {pid!r}")
        # confidence bounded
        if not (0.0 <= self.confidence <= 1.0):
            problems.append(f"confidence out of [0,1]: {self.confidence}")
        return problems


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scalar(x: Any) -> Any:
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
    return x


def _safe(o: Any) -> Any:
    """Recursively convert dataclass dict → JSON-safe (NaN/Inf → None, tuples → lists)."""
    if isinstance(o, dict):
        return {str(k): _safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_safe(v) for v in o]
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
    return o


def system_model_to_json(m: SystemModel) -> str:
    return json.dumps(m.to_dict(), indent=2, default=str)


def system_model_from_dict(d: Dict[str, Any]) -> SystemModel:
    """Inverse of ``SystemModel.to_dict``. Tolerant of missing optional fields."""
    topo_in = d.get("topology") or {}
    topo = Topology(
        entities=[Entity(**_filter_entity(e)) for e in topo_in.get("entities") or []],
        relationships=[Relationship(**_filter_rel(r))
                       for r in topo_in.get("relationships") or []],
    )
    state = StateBlock(**_filter_state(d.get("state") or {}))
    constraints = [Constraint(**_filter_constraint(c))
                   for c in d.get("constraints") or []]
    patterns = [CharacteristicPattern(**_filter_pattern(p))
                for p in d.get("characteristic_patterns") or []]
    vocab = Vocabulary(**_filter_vocab(d.get("vocabulary") or {}))
    actions = [TemplateAction(**_filter_action(a))
               for a in d.get("action_library") or []]
    prov_in = d.get("provenance") or {}
    prov = ProvenanceBlock(**_filter_prov(prov_in))
    return SystemModel(
        schema_version=d.get("schema_version", "system_model_v1"),
        topology=topo, state=state,
        constraints=constraints,
        characteristic_patterns=patterns,
        vocabulary=vocab, action_library=actions,
        provenance=prov,
        confidence=float(d.get("confidence") or 0.0),
    )


# ---- micro-filters: drop unknown keys so loading is forward-compatible ----

def _filter(cls, d: Dict[str, Any]) -> Dict[str, Any]:
    fields = set(getattr(cls, "__dataclass_fields__", {}).keys())
    return {k: v for k, v in d.items() if k in fields}

def _filter_entity(d):     return _filter(Entity, d)
def _filter_rel(d):        return _filter(Relationship, d)
def _filter_state(d):      return _filter(StateBlock, d)
def _filter_constraint(d): return _filter(Constraint, d)
def _filter_pattern(d):    return _filter(CharacteristicPattern, d)
def _filter_vocab(d):      return _filter(Vocabulary, d)
def _filter_action(d):     return _filter(TemplateAction, d)
def _filter_prov(d):       return _filter(ProvenanceBlock, d)

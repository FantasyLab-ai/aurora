"""
InterventionSpec — the structured shape of a what-if query.

We parse user input (natural language or structured form) into this shape
*before* simulating, so the user can confirm the parsed intervention before
Aurora computes the answer. That confirmation step is critical: an
LLM-hallucinated intervention spec is worse than no answer at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class InterventionSpec:
    """A single intervention on the fitted model.

    Attributes:
        variable: the column being intervened on
        kind: "set" (do(X = v)) or "delta" (do(X = X + δ))
        value: the magnitude (the v or δ depending on kind)
        duration_steps: optional duration of the intervention in time-steps;
            None = indefinite (steady-state).
        confidence_level: CI level for the predicted trajectory (default 0.95)
        outcome: optional explicit outcome variable; defaults to the run's target
    """
    variable: str
    kind: str = "delta"
    value: float = 1.0
    duration_steps: Optional[int] = None
    confidence_level: float = 0.95
    outcome: Optional[str] = None
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def display_text(self) -> str:
        """Human-readable rendering of the intervention."""
        op = "+" if self.kind == "delta" and self.value >= 0 else ""
        op_kind = "Δ" if self.kind == "delta" else "="
        dur = f" for {self.duration_steps} steps" if self.duration_steps else ""
        return f"{self.variable} {op_kind} {op}{self.value}{dur}"


@dataclass
class InterventionChain:
    """A sequence of simultaneous interventions (max 3 for v1)."""
    interventions: List[InterventionSpec] = field(default_factory=list)
    horizon_steps: int = 24

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interventions": [i.to_dict() for i in self.interventions],
            "horizon_steps": self.horizon_steps,
        }

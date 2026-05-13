"""
Live what-if engine.

User asks: *"if precip_mm increases by 5 for 12 hours, what happens to temperature_c?"*
Aurora answers with **bounded, falsifiable** predictions — or honestly refuses
when the effect isn't identifiable from the data.

This is the second-most-important differentiator after provenance: cloud LLMs
hallucinate counterfactuals; Aurora computes them from the fitted causal /
physics models in the current run, with explicit identifiability checks.

Public API
----------

    from fantasyai.aurora.whatif import (
        InterventionSpec,         # structured intervention
        parse_intervention_text,  # natural-language → structured (with LLM fallback)
        simulate_intervention,    # apply spec to fitted model → trajectory
        check_identifiability,    # is this estimand identifiable from the data?
    )

The engine reads pre-fitted models from the run dir (no re-running the analysis).
For v1 it uses the linear OLS effect from ``causal_what_if`` for variables that
were fit; future versions will plug in the physics ODE for time-varying
interventions.
"""
from .spec import InterventionSpec
from .parser import parse_intervention_text
from .simulator import simulate_intervention, check_identifiability

__all__ = [
    "InterventionSpec",
    "parse_intervention_text",
    "simulate_intervention",
    "check_identifiability",
]

"""
Explainer layer — answers "why?" for every finding type.

When a user clicks EXPLAIN on a finding card, the frontend calls
``GET /api/explainer/<claim_type>/<claim_id>``. The studio_api routes that
to the appropriate explainer module here, which returns a structured
explanation built from the claim's raw_result + the run's artifacts.

Cloud LLMs paraphrase. Aurora computes the actual decomposition.

Modules
-------

* ``anomaly_explainer``  — feature-attribution decomposition (already 80%
                           there in deep_math.extensions.anomaly_attribution)
* ``regime_explainer``   — pre/post-boundary deltas per variable, alternative
                           cut-points, why this one won
"""
from .anomaly_explainer import explain_anomaly
from .regime_explainer import explain_regime
from .causal_explainer import explain_causal
from .physics_explainer import explain_physics

__all__ = ["explain_anomaly", "explain_regime", "explain_causal", "explain_physics"]

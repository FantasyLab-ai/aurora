"""
Physical-systems prior library.

When ``physics_discovery`` fits a candidate ODE to data, the **prior matcher**
checks whether the fitted form + parameters resemble a known physical law from
this catalogue. If it does, the finding gets upgraded:

  before:   "best fit linear_ode (RMSE 5.83)"
  after:    "best fit linear_ode (RMSE 5.83) — matches Newton's cooling (97% match)"

This is a massive trust signal for researchers and the difference between
"Aurora found a curve" and "Aurora recognized a physical law."

Public API
----------

    from fantasyai.aurora.priors import (
        list_priors,         # → list of all loaded priors
        match_physics_fit,   # → check a fitted candidate against the catalogue
        Prior,
    )
"""
from .registry import Prior, list_priors, get_prior
from .matcher import match_physics_fit, PriorMatch

__all__ = ["Prior", "list_priors", "get_prior", "match_physics_fit", "PriorMatch"]

"""
Aurora composable findings (Q3 Stream A): priors that flow run-to-run.

The Aurora v1 pipeline treats every run as independent. That's safe
and predictable, but it leaves analytical value on the table: a user
running Aurora on the same line every day re-discovers the same
physics model, the same regime structure, the same baseline mean
every time.

This module changes that. After a run finishes, the **extractor**
pulls a small, structured "prior pack" from the bundle:

    {
      "source_run_id":   "20260519_124343__climate_buoy_demo",
      "source_dataset":  "climate_buoy_demo.csv",
      "extracted_at":    1234567890.0,
      "priors": {
        "physics":       {"law": "logistic", "params": {...}, "rmse": ...},
        "regimes":       {"k": 3, "means": [...], "expected_dwells": [...]},
        "baseline":      {"col": "temperature_c", "mean": 22.4, "std": 1.7},
        "anomaly_floor": {"z_warn": 3.0, "z_crit": 5.0},
      }
    }

The pack is small enough to embed in the next run's bundle. The
**applier** wires it back into the next run as initial state +
attaches a ``prior_source`` tag to findings that align with priors.

Tagged findings get a small PRIOR badge in the Studio: "this finding
was validated against the prior from <previous run>".

The whole thing is opt-in. A user who doesn't pass ``inherit_from``
sees v1 behaviour unchanged. The prior pack is stored in
``<run_dir>/priors_pack.json``; it never modifies the source run.

NOTE: we live under ``aurora.composable`` (not ``aurora.priors``)
because ``aurora.priors`` is already used by the physics-law matcher.
That module deals with *static* priors (known physical laws); this
one deals with *dynamic* priors (last run's fitted state).
"""
from __future__ import annotations

from .extractor import (
    extract_prior_pack,
    write_prior_pack,
    PriorPack,
)
from .applier import (
    load_prior_pack,
    apply_prior_pack_to_findings,
    tag_findings_with_prior,
)

__all__ = [
    "extract_prior_pack",
    "write_prior_pack",
    "PriorPack",
    "load_prior_pack",
    "apply_prior_pack_to_findings",
    "tag_findings_with_prior",
]

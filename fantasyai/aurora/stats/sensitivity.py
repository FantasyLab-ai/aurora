"""
Sensitivity analysis — Wave B, Method 10 of the brief.

For every parameter-dependent finding, the synthesis layer should be
able to say one of:

  * "this finding is robust across the standard parameter range"
  * "this finding is sensitive to parameter choice — appears only when
     z > 3.0"

The mechanism is a generic parameter sweep + a robustness profile.

Use cases:
  * z-threshold for univariate anomaly detection (sweep 2.0 → 4.0)
  * k for LOF (sweep 5 → 50)
  * window size for change-point detection (sweep 30 → 200)

The user supplies a callable that runs the analysis at one parameter
value and returns a scalar (e.g., "number of anomalies") or a small
dict (e.g., ``{"n_flagged": 12, "score": 0.78}``). The sweep harness
handles the looping, records the result at each value, classifies the
finding's robustness, and produces a synthesis-ready summary.

Glass-box: every sweep records the parameter range, the step, the
result at each value, and the robustness classification rule.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Sweep harness
# ---------------------------------------------------------------------------

def parameter_sweep(
    fn: Callable[[float], Any],
    parameter_name: str,
    values: Sequence[float],
    *,
    presence_check: Optional[Callable[[Any], bool]] = None,
    canonical_value: Optional[float] = None,
) -> Dict[str, Any]:
    """Run ``fn(value)`` for each ``value`` and record the trajectory.

    Parameters
    ----------
    fn
        Callable that runs the analysis at a single parameter value.
        Must be deterministic if you want the sensitivity profile to
        be meaningful.
    parameter_name
        Human-readable name (e.g. ``"z_threshold"``).
    values
        Sequence of parameter values to sweep.
    presence_check
        Optional predicate ``result -> bool`` that says "the finding
        was present at this parameter value". Default: result is
        truthy.
    canonical_value
        The default / production parameter value for this finding.
        Recorded so the synthesis layer can frame robustness relative
        to it.

    Returns
    -------
    dict::

        {
          "parameter":  parameter_name,
          "canonical":  canonical_value,
          "n_values":   int,
          "trajectory": [{"value": v, "result": r, "present": bool}, ...],
          "present_range":  [v_min, v_max] | None,
          "robust_count":   int,             # n values where present
          "robustness":     "robust" | "fragile" | "absent",
          "notes":         [str],
        }
    """
    if presence_check is None:
        def presence_check(r):
            return bool(r) if r is not None else False
    trajectory: List[Dict[str, Any]] = []
    for v in values:
        try:
            result = fn(v)
            present = bool(presence_check(result))
        except Exception as e:  # never let a failed sweep poison results
            result = {"error": f"{type(e).__name__}: {e}"}
            present = False
        trajectory.append({"value": float(v), "result": result,
                            "present": present})
    present_values = [t["value"] for t in trajectory if t["present"]]
    if not present_values:
        present_range = None
        robustness = "absent"
    else:
        present_range = [min(present_values), max(present_values)]
        # "robust" = present in ≥ 70% of swept values; "fragile" otherwise.
        frac = len(present_values) / max(1, len(values))
        robustness = "robust" if frac >= 0.70 else "fragile"
    notes = []
    if robustness == "fragile" and canonical_value is not None:
        canonical_present = any(
            t["present"] for t in trajectory
            if abs(t["value"] - canonical_value) < 1e-9
        )
        if canonical_present:
            notes.append(
                f"finding is present at canonical {parameter_name}={canonical_value} "
                "but disappears at nearby values — sensitivity to parameter choice"
            )
        else:
            notes.append(
                f"finding is NOT present at canonical {parameter_name}={canonical_value}"
            )
    return {
        "parameter": parameter_name,
        "canonical": canonical_value,
        "n_values": len(values),
        "trajectory": trajectory,
        "present_range": present_range,
        "robust_count": len(present_values),
        "robustness": robustness,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Domain helpers — z-threshold, LOF k, multivariate cutoff
# ---------------------------------------------------------------------------

def sensitivity_z_threshold(
    z_scores: Sequence[float],
    *,
    canonical: float = 3.0,
    sweep: Sequence[float] = (2.5, 2.75, 3.0, 3.25, 3.5, 3.75, 4.0),
) -> Dict[str, Any]:
    """Sweep z-threshold and report how many anomalies flagged at each.

    The robustness verdict tells the synthesis layer whether the
    "anomalies detected" headline number is robust or fragile.
    """
    if np is None:
        raise RuntimeError("numpy required for sensitivity_z_threshold")
    arr = np.abs(np.asarray(z_scores, dtype=float))
    arr = arr[~np.isnan(arr)]

    def _at(z_thr: float) -> Dict[str, Any]:
        n_flagged = int(np.sum(arr > z_thr))
        return {"n_flagged": n_flagged, "z_threshold": z_thr}

    return parameter_sweep(
        _at, "z_threshold", sweep,
        presence_check=lambda r: r["n_flagged"] > 0,
        canonical_value=canonical,
    )


def sensitivity_lof_k(
    X: Sequence[Sequence[float]],
    *,
    canonical_k: int = 20,
    sweep: Sequence[int] = (5, 10, 20, 30, 50),
    threshold: float = 1.5,
) -> Dict[str, Any]:
    """Sweep LOF's k parameter; record the count of flagged points."""
    from .multivariate_outliers import lof_outliers

    def _at(k_val: float) -> Dict[str, Any]:
        out = lof_outliers(X, k=int(k_val), threshold=threshold)
        return {"n_flagged": out["n_outliers"]}

    return parameter_sweep(
        _at, "lof_k", list(sweep),
        presence_check=lambda r: r["n_flagged"] > 0,
        canonical_value=canonical_k,
    )


# ---------------------------------------------------------------------------
# Attach a sensitivity profile to a finding
# ---------------------------------------------------------------------------

def attach_sensitivity_profile(
    finding: Dict[str, Any],
    profile: Dict[str, Any],
    *,
    key: str = "sensitivity_profile",
) -> Dict[str, Any]:
    """Attach a sensitivity profile to a finding, with a short
    human-readable verdict ready for the synthesis layer.

    Adds:
        finding[key]                           — the full profile dict.
        finding[key + "_robustness"]           — "robust" | "fragile" | "absent".
        finding["parameter_robustness"]        — mirrored short label.
    """
    if not isinstance(finding, dict) or not isinstance(profile, dict):
        return finding
    finding[key] = profile
    finding["parameter_robustness"] = profile.get("robustness", "unknown")
    return finding

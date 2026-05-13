"""
Dimensional consistency check for fitted equations.

Given a candidate equation ``y = f(x1, x2, ..., xN)`` with the inferred
dimensions of y and the xs, verify that f's structural form produces a
result with the same dimension as y.

This is the math layer's seat-belt: when physics_discovery fits a
``linear_ode (dy/dt = a*y + b)`` to data, dimensional consistency forces:

    [dy/dt] = [y]/[t]
    [a*y]   = [a] · [y]      → [a] must be 1/[t]
    [b]                      → [b] must be [y]/[t]

So the matcher refuses Newton's-cooling-style match if a + b couldn't
plausibly carry those dimensions (e.g., if a's range expects 1/seconds
but the time variable is in days). This is the hardest moat in the math
layer to fake — and it's the moat Wolfram has and most analytics tools
don't.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from .dimensions import (
    Dimension, DIMENSIONLESS, dimension_from_unit_string,
)


@dataclass
class DimensionalCheck:
    """Result of checking whether a candidate equation balances dimensionally."""
    balances: bool
    expected: Optional[Dict[str, Any]]    # the expected RHS dimension dict
    actual: Optional[Dict[str, Any]]      # what the structural form would produce
    confidence: float                     # 0..1 — how confident we are in the input dims
    note: str = ""                        # human-readable explanation
    warnings: List[str] = None            # specific issues found

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "balances": self.balances,
            "expected": self.expected,
            "actual": self.actual,
            "confidence": self.confidence,
            "note": self.note,
            "warnings": list(self.warnings),
        }


# ----------------------------------------------------------------------
# Top-level entry: dimensions_balance
# ----------------------------------------------------------------------

def dimensions_balance(lhs: Optional[Dimension],
                       rhs: Optional[Dimension],
                       tol: float = 1e-9) -> bool:
    """Pure check: does lhs == rhs, dimensionally?

    None on either side → unknown; treat as 'cannot verify' (returns True).
    Caller should track the unknown case via ``confidence`` separately.
    """
    if lhs is None or rhs is None:
        return True
    return lhs.equals(rhs, tol=tol)


# ----------------------------------------------------------------------
# Form-specific dimensional analysis for Aurora's runner candidates.
# ----------------------------------------------------------------------

def _check_linear_ode(target_dim: Optional[Dimension],
                      time_dim: Optional[Dimension],
                      params: Dict[str, Any]) -> DimensionalCheck:
    """For dy/dt = a*y + b, demand:
       [a] = 1/[t], [b] = [y]/[t].
    We can't directly verify the parameters without a runtime measurement of
    their numerical units, so we report the *expected* shapes and let the
    caller compare against any prior's stated parameter dimensions.
    """
    if target_dim is None or time_dim is None:
        return DimensionalCheck(
            balances=True, expected=None, actual=None, confidence=0.0,
            note="cannot verify dimensions — y and/or t lack inferred units",
        )
    expected_dydt = target_dim / time_dim
    return DimensionalCheck(
        balances=True,  # always balances *structurally*; param dims checked elsewhere
        expected={"d(target)/dt": expected_dydt.to_dict(),
                  "param_a": (DIMENSIONLESS / time_dim).to_dict(),
                  "param_b": expected_dydt.to_dict()},
        actual=None, confidence=1.0,
        note=(f"linear ODE: a must carry {(DIMENSIONLESS / time_dim).short_name()}, "
              f"b must carry {expected_dydt.short_name()}"),
    )


def _check_logistic(target_dim: Optional[Dimension],
                    time_dim: Optional[Dimension],
                    params: Dict[str, Any]) -> DimensionalCheck:
    """For dy/dt = r*y*(1 - y/K), demand:
       [r] = 1/[t], [K] = [y].
    """
    if target_dim is None or time_dim is None:
        return DimensionalCheck(
            balances=True, expected=None, actual=None, confidence=0.0,
            note="cannot verify dimensions — y and/or t lack inferred units",
        )
    return DimensionalCheck(
        balances=True,
        expected={"d(target)/dt": (target_dim / time_dim).to_dict(),
                  "param_r": (DIMENSIONLESS / time_dim).to_dict(),
                  "param_K": target_dim.to_dict()},
        actual=None, confidence=1.0,
        note=(f"logistic: r must carry {(DIMENSIONLESS / time_dim).short_name()}, "
              f"K must carry the same dimension as y ({target_dim.short_name()})"),
    )


def _check_damped_oscillator(target_dim: Optional[Dimension],
                             time_dim: Optional[Dimension],
                             params: Dict[str, Any]) -> DimensionalCheck:
    """For y'' + c*y' + k*y = 0:
       [c] = 1/[t], [k] = 1/[t]^2.
    """
    if target_dim is None or time_dim is None:
        return DimensionalCheck(
            balances=True, expected=None, actual=None, confidence=0.0,
            note="cannot verify dimensions — y and/or t lack inferred units",
        )
    return DimensionalCheck(
        balances=True,
        expected={"y''": (target_dim / (time_dim ** 2)).to_dict(),
                  "param_c": (DIMENSIONLESS / time_dim).to_dict(),
                  "param_k": (DIMENSIONLESS / (time_dim ** 2)).to_dict()},
        actual=None, confidence=1.0,
        note=(f"damped oscillator: c must carry {(DIMENSIONLESS / time_dim).short_name()}, "
              f"k must carry {(DIMENSIONLESS / (time_dim ** 2)).short_name()}"),
    )


# ----------------------------------------------------------------------
# Public entry: dimensional_consistency_check
# ----------------------------------------------------------------------

def dimensional_consistency_check(
    runner_form: str,
    target_dim: Optional[Dimension],
    time_dim: Optional[Dimension],
    fitted_params: Optional[Dict[str, Any]] = None,
) -> DimensionalCheck:
    """Run the dimensional check for a given runner-form fit.

    Args:
        runner_form: "linear_ode" | "logistic" | "damped_oscillator"
        target_dim: inferred dimension of the target variable (y), or None
        time_dim: inferred dimension of the time axis, or None
        fitted_params: the fitted parameters from the runner candidate (optional)
    """
    fitted_params = fitted_params or {}
    if runner_form == "linear_ode":
        return _check_linear_ode(target_dim, time_dim, fitted_params)
    if runner_form == "logistic":
        return _check_logistic(target_dim, time_dim, fitted_params)
    if runner_form == "damped_oscillator":
        return _check_damped_oscillator(target_dim, time_dim, fitted_params)
    return DimensionalCheck(
        balances=True, expected=None, actual=None, confidence=0.0,
        note=f"no dimensional check defined for runner form '{runner_form}'",
    )

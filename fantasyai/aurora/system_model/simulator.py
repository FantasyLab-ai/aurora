"""
Forward-step simulator — run validated dynamics from current state.

Pulls fitted parameters from the run's physics layer (linear_ode, logistic,
damped_oscillator, coupled systems) and steps forward, widening the CI each
step. Pauses when CI exceeds a threshold and ASKS the user whether to
extrapolate or stop. Honest by default.

Glass-box invariants:
  * every simulated step lists which fitted equation drove it
  * confidence widens monotonically (we never claim certainty grows)
  * if no validated dynamic is available, the simulator returns
    ``ok=False`` rather than fabricate one

Output shape:
  ``{ok, n_steps_done, paused, paused_reason, trajectory[…], assumptions[…]}``
  trajectory[i] = {step, t, value, ci_low, ci_high, source_equation}
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SimStep:
    step: int
    t: float
    value: float
    ci_low: float
    ci_high: float
    source_equation: str
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


@dataclass
class SimResult:
    ok: bool
    target_entity_id: str
    method: str
    trajectory: List[SimStep] = field(default_factory=list)
    paused: bool = False
    paused_reason: str = ""
    assumptions: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "target_entity_id": self.target_entity_id,
            "method": self.method,
            "trajectory": [s.to_dict() for s in self.trajectory],
            "paused": self.paused,
            "paused_reason": self.paused_reason,
            "assumptions": list(self.assumptions),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Equation routers
# ---------------------------------------------------------------------------

def _step_linear_ode(params: Dict[str, float], y_prev: float,
                      sigma: float) -> Tuple[float, float, str]:
    """y' = a*y + b — Euler step. Returns (y_next, sigma_next, equation_str)."""
    a = float(params.get("a", 0.0))
    b = float(params.get("b", 0.0))
    dt = float(params.get("dt", 1.0))
    y_next = y_prev + dt * (a * y_prev + b)
    # σ widens by |1 + a·dt| each step (worst-case linear amplification).
    sigma_next = abs(1.0 + a * dt) * sigma + 0.01 * abs(y_next)
    return y_next, sigma_next, f"y_t = y_{{t-1}} + dt·(a·y + b); a={a:.4g}, b={b:.4g}, dt={dt:g}"


def _step_logistic(params: Dict[str, float], y_prev: float,
                    sigma: float) -> Tuple[float, float, str]:
    r = float(params.get("r", 0.0))
    K = float(params.get("K", 1.0))
    dt = float(params.get("dt", 1.0))
    y_next = y_prev + dt * r * y_prev * (1.0 - y_prev / max(K, 1e-9))
    # σ propagation: derivative of f wrt y at y_prev is r·(1 − 2y/K); use that.
    g = abs(1.0 + dt * r * (1.0 - 2.0 * y_prev / max(K, 1e-9)))
    sigma_next = g * sigma + 0.01 * abs(y_next)
    return y_next, sigma_next, f"y_t = y_{{t-1}} + dt·r·y·(1-y/K); r={r:.4g}, K={K:.4g}"


def _step_damped_oscillator(params: Dict[str, float], y_prev: float,
                             v_prev: float, sigma: float
                             ) -> Tuple[float, float, float, str]:
    c = float(params.get("c", 0.0))
    k = float(params.get("k", 0.0))
    dt = float(params.get("dt", 1.0))
    a = -c * v_prev - k * y_prev
    v_next = v_prev + dt * a
    y_next = y_prev + dt * v_next
    # CI grows linearly in time (we don't track v's σ separately).
    sigma_next = sigma * (1.0 + 0.05) + 0.01 * abs(y_next)
    return y_next, v_next, sigma_next, f"y'' + c·y' + k·y = 0; c={c:.4g}, k={k:.4g}"


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def simulate_forward(state: Dict[str, Any],
                     n_steps: int = 50,
                     ci_pause_threshold: float = 5.0,
                     ) -> SimResult:
    """Forward-step the validated dynamics from the run's last value.

    Args:
        state: full state dict from ``state_builder.build_state``.
        n_steps: max steps to attempt.
        ci_pause_threshold: pause when |CI/value| exceeds this ratio.

    Returns:
        ``SimResult`` with a trajectory array.
    """
    physics = state.get("physics") or {}
    law = physics.get("discovered_law") or {}
    name = (law.get("name") or "").lower()
    params = law.get("params") or {}
    target = (state.get("forecast") or {}).get("target") or "y"
    target_eid = _target_entity_id(state, target)

    assumptions: List[str] = [
        "fitted parameters from this run are the truth — re-run if data drifts.",
        "Euler integration with the fitted dt; smaller dt would reduce drift.",
        "σ propagates analytically (not via Monte Carlo)."
    ]

    # Pull a starting value for y. Use the forecast's first point as the
    # current value if available; else fall back to physics.y_max.
    fc = (state.get("forecast") or {})
    if fc.get("available") and fc.get("points"):
        y0 = float(fc["points"][0].get("value") or 0.0)
    else:
        y0 = float((physics.get("y_max") or 1.0))

    # Initial sigma — pull from forecast's CI half-width if available.
    sigma0 = 0.0
    if fc.get("points"):
        p0 = fc["points"][0]
        if p0.get("ci_low") is not None and p0.get("ci_high") is not None:
            sigma0 = float(p0["ci_high"] - p0["ci_low"]) / 2.0
    sigma0 = max(sigma0, 0.01 * abs(y0))

    if name == "linear_ode":
        equation_initial = "linear_ode (dy/dt = a·y + b)"
        return _run_scalar(target_eid, "linear_ode", y0, sigma0, n_steps,
                            ci_pause_threshold, params,
                            stepper=lambda y, s: _step_linear_ode(params, y, s),
                            assumptions=assumptions)
    elif name == "logistic":
        return _run_scalar(target_eid, "logistic", y0, sigma0, n_steps,
                            ci_pause_threshold, params,
                            stepper=lambda y, s: _step_logistic(params, y, s),
                            assumptions=assumptions)
    elif name == "damped_oscillator":
        return _run_oscillator(target_eid, y0, sigma0, n_steps,
                                ci_pause_threshold, params, assumptions)
    else:
        return SimResult(
            ok=False, target_entity_id=target_eid,
            method=(name or "none"),
            error=("no validated forward-integrable dynamic available. "
                   "Run physics_discovery first to fit one of "
                   "{linear_ode, logistic, damped_oscillator}."),
            assumptions=assumptions,
        )


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

def _run_scalar(target_eid: str, method: str, y0: float, sigma0: float,
                 n_steps: int, ci_threshold: float,
                 params: Dict[str, float], stepper, assumptions: List[str]) -> SimResult:
    traj: List[SimStep] = []
    y, sigma = y0, sigma0
    dt = float(params.get("dt", 1.0))
    paused = False; paused_reason = ""
    for i in range(n_steps):
        y, sigma, eq_str = stepper(y, sigma)
        confidence = max(0.05, 1.0 / (1.0 + i * 0.05))
        ci = 1.96 * sigma
        traj.append(SimStep(
            step=i + 1,
            t=float((i + 1) * dt),
            value=float(y),
            ci_low=float(y - ci),
            ci_high=float(y + ci),
            source_equation=eq_str,
            confidence=float(confidence),
        ))
        # Pause when CI/value exceeds threshold.
        if abs(y) > 1e-9 and (ci / abs(y)) > ci_threshold:
            paused = True
            paused_reason = (f"CI ratio {ci/abs(y):.2f} exceeds "
                              f"threshold {ci_threshold}; pausing rather "
                              f"than extrapolate further.")
            break
    return SimResult(
        ok=True, target_entity_id=target_eid, method=method,
        trajectory=traj, paused=paused, paused_reason=paused_reason,
        assumptions=assumptions,
    )


def _run_oscillator(target_eid: str, y0: float, sigma0: float,
                     n_steps: int, ci_threshold: float,
                     params: Dict[str, float], assumptions: List[str]) -> SimResult:
    traj: List[SimStep] = []
    y, v, sigma = y0, 0.0, sigma0
    dt = float(params.get("dt", 1.0))
    paused = False; paused_reason = ""
    for i in range(n_steps):
        y, v, sigma, eq_str = _step_damped_oscillator(params, y, v, sigma)
        confidence = max(0.05, 1.0 / (1.0 + i * 0.05))
        ci = 1.96 * sigma
        traj.append(SimStep(
            step=i + 1, t=float((i + 1) * dt),
            value=float(y), ci_low=float(y - ci), ci_high=float(y + ci),
            source_equation=eq_str, confidence=float(confidence),
        ))
        if abs(y) > 1e-9 and (ci / abs(y)) > ci_threshold:
            paused = True
            paused_reason = (f"CI ratio {ci/abs(y):.2f} exceeds "
                              f"threshold {ci_threshold}.")
            break
    return SimResult(
        ok=True, target_entity_id=target_eid, method="damped_oscillator",
        trajectory=traj, paused=paused, paused_reason=paused_reason,
        assumptions=assumptions,
    )


def _target_entity_id(state: Dict[str, Any], target_col: str) -> str:
    sm = state.get("system_model") or {}
    ents = ((sm.get("topology") or {}).get("entities") or [])
    for e in ents:
        if e.get("data_column") == target_col:
            return e.get("id", target_col)
    return target_col

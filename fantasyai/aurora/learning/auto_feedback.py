"""
Auto-feedback: implicit learning signals derived from each finished run,
no human required.

The intuition: every time the system arrives at an internally-coherent answer
(strong fit + matching prior + signature-fit alignment + cross-run consistency),
that's a self-supervised positive signal we can persist. The meta-learner can
then up-weight priors that consistently survive these gates.

Conversely, when the run produces obviously degenerate results (best fit beats
runner-ups by < 1%, or RMSE > 1× the target's std), that's an implicit 'we
don't trust this' signal worth recording so future runs don't lean on it.

Five signals (each scored 0..1 and stored separately so we can audit which
was firing):

  1. fit_quality        — best fit's RMSE is dramatically lower than runner-ups
  2. prior_strength     — top prior match has confidence ≥ threshold
  3. signature_coherence — calculus shape and best fit's form agree (e.g.
                            'exponential decay' ↔ linear_ode with a<0)
  4. dimensional_pass   — when units are inferred, the dimensional_check passes
  5. cross_run_consistency — same dataset_key produced the same winner before

The composite score is the *minimum* of the active signals (a "weakest link"
view) — we'd rather emit no signal than over-claim. A score ≥ 0.7 emits an
auto-thumbs-up for the prior; ≤ 0.3 emits an auto-thumbs-down.

Glass-box: every auto-feedback row carries a ``signals`` dict so a user can
see exactly why we recorded what we did.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .store import LearningStore


# Thresholds. Documented + tunable; defaults are conservative.
THRESH_AUTO_UP   = 0.70   # composite score ≥ this → auto thumbs-up
THRESH_AUTO_DOWN = 0.30   # composite score ≤ this → auto thumbs-down
PRIOR_CONF_GATE  = 0.70   # min prior confidence to even consider auto-up
FIT_GAP_GATE     = 0.10   # winner must beat 2nd by ≥ 10% RMSE


# Coherence map: which calculus shapes agree with which physics_models forms.
# Conservative — only the unambiguous pairings. When a shape isn't in the map,
# the coherence signal is simply not active (not a negative signal).
_COHERENCE: Dict[str, set] = {
    "exponential decay":          {"linear_ode", "exponential"},
    "exponential-like decay":     {"linear_ode", "exponential"},
    "exponential growth":         {"linear_ode", "exponential", "logistic"},
    "exponential-like growth":    {"linear_ode", "exponential", "logistic"},
    "decelerating growth":        {"logistic", "saturation", "sigmoid"},
    "decelerating decay":         {"saturation"},
    "saturation / sigmoid-like":  {"sigmoid", "saturation", "logistic"},
    "linear-monotonic":           {"linear_ode", "polynomial_2"},
    "oscillatory":                {"damped_oscillator"},
    "noisy / non-stationary":     set(),  # no agreement possible
}


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def compute_signals(state: Dict[str, Any],
                    past_rows: Optional[List[Dict[str, Any]]] = None,
                    ) -> Dict[str, Any]:
    """Score each signal in [0, 1]. Return a dict with each signal + composite.

    Returns:
        {
          "fit_quality": float | None,
          "prior_strength": float | None,
          "signature_coherence": float | None,
          "dimensional_pass": float | None,
          "cross_run_consistency": float | None,
          "composite": float | None,        # min of active signals; None if 0 active
          "n_active": int,
          "rationale": str,
        }
    """
    physics = (state.get("physics") or {})
    law = physics.get("discovered_law") or {}
    calc = physics.get("calculus") or {}
    prior_matches = physics.get("prior_matches") or []

    signals: Dict[str, Optional[float]] = {
        "fit_quality": None,
        "prior_strength": None,
        "signature_coherence": None,
        "dimensional_pass": None,
        "cross_run_consistency": None,
    }
    rationale_bits: List[str] = []

    # Signal 1: fit quality — winner beats runner-ups meaningfully.
    all_cands = physics.get("all_candidates") or []
    rmse_pairs = [(c.get("name"), c.get("rmse_test"))
                  for c in all_cands if isinstance(c, dict)
                  and isinstance(c.get("rmse_test"), (int, float))]
    if len(rmse_pairs) >= 2 and law.get("name"):
        rmse_pairs.sort(key=lambda kv: kv[1])
        winner_rmse = rmse_pairs[0][1]
        runner_rmse = rmse_pairs[1][1]
        if runner_rmse > 0:
            gap = (runner_rmse - winner_rmse) / runner_rmse
            # Map gap > FIT_GAP_GATE to ≥ 0.7. Linear ramp; cap at 1.0.
            signals["fit_quality"] = max(0.0, min(1.0, gap / FIT_GAP_GATE * 0.7))
            rationale_bits.append(f"fit gap = {gap*100:.1f}%")

    # Signal 2: prior strength.
    if prior_matches:
        top_conf = prior_matches[0].get("confidence")
        if isinstance(top_conf, (int, float)):
            signals["prior_strength"] = float(top_conf)
            rationale_bits.append(f"prior conf = {top_conf*100:.0f}%")

    # Signal 3: signature coherence.
    if calc.get("available") and calc.get("likely_shape") and law.get("name"):
        shape = calc["likely_shape"].strip().lower()
        agreed_forms = _COHERENCE.get(shape, set())
        if agreed_forms:
            if law["name"] in agreed_forms:
                conf = float(calc.get("confidence") or 0.5)
                signals["signature_coherence"] = max(0.5, conf)
                rationale_bits.append(f"shape '{shape}' ↔ {law['name']} ✓")
            else:
                # Mismatch — explicit negative signal.
                signals["signature_coherence"] = 0.2
                rationale_bits.append(f"shape '{shape}' disagrees with {law['name']}")

    # Signal 4: dimensional pass.
    dc = physics.get("dimensional_check") or {}
    if isinstance(dc, dict) and dc.get("confidence", 0) > 0.5:
        # 'is_consistent' is the unambiguous indicator; older shapes use 'consistent'.
        ok = dc.get("is_consistent", dc.get("consistent"))
        if ok is True:
            signals["dimensional_pass"] = 1.0
            rationale_bits.append("dimensions ✓")
        elif ok is False:
            signals["dimensional_pass"] = 0.0
            rationale_bits.append("dimensions ✗")

    # Signal 5: cross-run consistency. If we have past rows for the same
    # dataset_name with the same winning prior, that's reinforcement.
    if past_rows is not None and prior_matches:
        top_prior = prior_matches[0].get("prior_id")
        ds_name = (state.get("dataset") or {}).get("name")
        if top_prior and ds_name:
            agreeing = 0
            disagreeing = 0
            for r in past_rows:
                if r.get("row_type") != "run_learning_v1":
                    continue
                if r.get("dataset_name") != ds_name:
                    continue
                bp = (r.get("best_prior") or {}).get("id")
                if not bp:
                    continue
                if bp == top_prior:
                    agreeing += 1
                else:
                    disagreeing += 1
            total = agreeing + disagreeing
            if total >= 1:
                # Bayesian-ish: agree fraction with a uniform prior at 0.5.
                # Smoothing weight 0.5 so 1 agreeing run → 0.75 (not 0.67 with
                # weight 1.0 — that's too harsh on early evidence).
                frac = (agreeing + 0.5) / (total + 1.0)
                signals["cross_run_consistency"] = float(frac)
                rationale_bits.append(
                    f"cross-run: {agreeing}/{total} prior runs agreed"
                )

    # Composite: weakest link of active signals.
    active = [v for v in signals.values() if v is not None]
    composite = min(active) if active else None

    return {
        **signals,
        "composite": composite,
        "n_active": len(active),
        "rationale": "; ".join(rationale_bits) if rationale_bits else "",
    }


# ---------------------------------------------------------------------------
# Persisting auto-feedback rows
# ---------------------------------------------------------------------------

def record_auto_feedback(run_dir: str,
                         state: Dict[str, Any],
                         store: Optional[LearningStore] = None,
                         ) -> Optional[Dict[str, Any]]:
    """Compute auto-feedback for a finished run; if the composite score is
    above/below the auto-up/down thresholds, append a row to the store.

    Returns the row written (None if no signal was strong enough). Failures
    are silent — auto-feedback is enrichment, not blocking.
    """
    try:
        store = store or LearningStore.default()
        # Pull past rows once; both signal #5 and the writer need them.
        past_rows = store.read_all()

        signals = compute_signals(state, past_rows=past_rows)
        composite = signals.get("composite")
        if composite is None or signals["n_active"] < 2:
            # Fewer than 2 active signals → not enough evidence to claim anything.
            return None

        if composite >= THRESH_AUTO_UP:
            verdict = "auto_up"
        elif composite <= THRESH_AUTO_DOWN:
            verdict = "auto_down"
        else:
            return None

        # Gate auto-up specifically on the prior having decent confidence — we
        # never want to up-weight a prior we barely matched.
        physics = state.get("physics") or {}
        prior_matches = physics.get("prior_matches") or []
        top_prior = prior_matches[0] if prior_matches else None
        if verdict == "auto_up":
            if not top_prior or (top_prior.get("confidence") or 0) < PRIOR_CONF_GATE:
                return None

        row = {
            "row_type": "auto_feedback_v1",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "run_dir": str(run_dir),
            "verdict": verdict,
            "composite": float(composite),
            "signals": {k: signals.get(k) for k in
                        ("fit_quality", "prior_strength", "signature_coherence",
                         "dimensional_pass", "cross_run_consistency")},
            "rationale": signals.get("rationale", ""),
            "best_prior_id": (top_prior or {}).get("prior_id"),
            "best_prior_name": (top_prior or {}).get("display_name"),
            "best_fit_name": ((physics.get("discovered_law") or {}).get("name")),
            "dataset_name": (state.get("dataset") or {}).get("name"),
        }
        store.append(row)
        return row
    except Exception:
        return None


def auto_feedback_summary(store: Optional[LearningStore] = None) -> Dict[str, Any]:
    """Aggregate counts: how many runs the system has self-validated, how
    many it's flagged as low-confidence. Used for the 'trained on N runs'
    topbar badge.
    """
    store = store or LearningStore.default()
    rows = store.read_all()
    n_runs = sum(1 for r in rows if r.get("row_type") == "run_learning_v1")
    n_auto_up = sum(1 for r in rows if r.get("row_type") == "auto_feedback_v1"
                    and r.get("verdict") == "auto_up")
    n_auto_down = sum(1 for r in rows if r.get("row_type") == "auto_feedback_v1"
                      and r.get("verdict") == "auto_down")
    n_human_up = sum(1 for r in rows if r.get("row_type") == "feedback_v1"
                     and r.get("verdict") == "up")
    n_human_down = sum(1 for r in rows if r.get("row_type") == "feedback_v1"
                       and r.get("verdict") == "down")
    return {
        "n_runs": n_runs,
        "n_auto_up": n_auto_up,
        "n_auto_down": n_auto_down,
        "n_human_up": n_human_up,
        "n_human_down": n_human_down,
        "n_signals_total": n_auto_up + n_auto_down + n_human_up + n_human_down,
    }

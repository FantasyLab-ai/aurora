"""
Validation changelog — what the user sees each morning.

After a run completes, ``compute_validation_changelog`` compares the prior
library's state before vs. after this run and produces six structured lists:

  * confirmed     — priors whose evidence ticked up (verdict=up)
  * strengthened  — priors that crossed the canonical threshold
  * weakened      — priors whose confidence dropped from prior baseline
  * contradicted  — priors with a verdict=down/contradiction this run
                    (highest-value finding type — surfaced loudly)
  * promoted      — status moves up the ladder (candidate→validated→canonical)
  * deprecated    — status moves to deprecated this run (rare; manual)

Each entry carries the rationale + run_dir + ts so the user can drill in.

Resist the temptation to add black-box ML here. This module is a pure
deterministic diff over the validation_log; every claim is traceable.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from . import validation_log
from .registry import list_priors, get_prior


@dataclass
class ChangelogEntry:
    prior_id: str
    display_name: str
    kind: str                # confirmed | strengthened | weakened | contradicted | promoted | deprecated
    rationale: str
    confidence_before: Optional[float]
    confidence_after: Optional[float]
    n_total: int
    run_dir: Optional[str] = None
    ts: Optional[str] = None
    suggested_status_before: Optional[str] = None
    suggested_status_after: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_RANK = {
    "deprecated": 0,
    "questioned": 1,
    "candidate":  2,
    "validated":  3,
    "canonical":  4,
}


def _is_promotion(before: str, after: str) -> bool:
    return _STATUS_RANK.get(after, -1) > _STATUS_RANK.get(before, -1)


def _is_demotion(before: str, after: str) -> bool:
    return _STATUS_RANK.get(after, -1) < _STATUS_RANK.get(before, -1)


# ---------------------------------------------------------------------------
# Per-run changelog
# ---------------------------------------------------------------------------

def record_run_validations(state: Dict[str, Any],
                           run_dir: Optional[str] = None,
                           log_path=None) -> List[Dict[str, Any]]:
    """Walk the assembled state, append one validation row per matched prior.

    Reads ``state.physics.prior_matches`` + ``state.physics.auto_feedback`` and
    derives a verdict for each matched prior. Returns the rows written.

    Uses:
      * verdict = "up" if auto_feedback.verdict == "auto_up" AND this prior
                   was the top match
      * verdict = "down" if auto_feedback.verdict == "auto_down"
      * verdict = "contradiction" if signature explicitly disagrees
                   (rationale contains "disagrees")
      * verdict = "neutral" otherwise (still recorded so we know it was tried)
    """
    physics = state.get("physics") or {}
    matches = physics.get("prior_matches") or []
    auto = physics.get("auto_feedback") or {}
    if not matches:
        return []

    rationale = auto.get("rationale", "") or ""
    auto_verdict = auto.get("verdict")

    rows: List[Dict[str, Any]] = []
    # Top match gets the strongest signal; subsequent matches get neutral.
    top = matches[0]
    pid = top.get("prior_id")
    if pid:
        if "disagrees" in rationale.lower():
            v = "contradiction"
        elif auto_verdict == "auto_up":
            v = "up"
        elif auto_verdict == "auto_down":
            v = "down"
        else:
            v = "neutral"
        score = float(top.get("confidence") or 0.0)
        fitted = (physics.get("discovered_law") or {}).get("params") or {}
        # In-range check: walk the prior's free_parameters and compare ranges.
        in_range_map: Dict[str, bool] = {}
        prior = get_prior(pid)
        if prior is not None:
            for k, spec in (prior.free_parameters or {}).items():
                rng = spec.get("range") if isinstance(spec, dict) else None
                # Fitted name may differ from spec name; try direct + alias.
                v_actual = None
                if k in fitted:
                    v_actual = fitted[k]
                elif spec.get("name") and spec["name"] in fitted:
                    v_actual = fitted[spec["name"]]
                if v_actual is None or rng is None or len(rng) != 2:
                    continue
                try:
                    lo, hi = float(rng[0]), float(rng[1])
                    in_range_map[k] = (lo <= float(v_actual) <= hi)
                except Exception:
                    continue
        row = validation_log.append_validation(
            prior_id=pid, verdict=v,
            run_dir=run_dir, score=score, via="auto",
            fitted_params={k: float(v) for k, v in fitted.items()
                            if isinstance(v, (int, float))},
            param_in_range=in_range_map or None,
            log_path=log_path,
        )
        rows.append(row)

    # Lower-ranked matches: log as neutral so we know they were considered.
    for m in matches[1:3]:
        pid = m.get("prior_id")
        if not pid:
            continue
        rows.append(validation_log.append_validation(
            prior_id=pid, verdict="neutral",
            run_dir=run_dir,
            score=float(m.get("confidence") or 0.0),
            via="auto",
            note="lower-ranked match",
            log_path=log_path,
        ))
    return rows


def compute_validation_changelog(stats_before: Dict[str, Any],
                                  log_path=None) -> List[Dict[str, Any]]:
    """Compute the full changelog by diffing ``stats_before`` vs the current
    ledger state.

    Args:
        stats_before: dict ``{prior_id: PriorStats-as-dict}`` snapshot taken
                      *before* the run. The diff is between this snapshot
                      and the current ledger state.

    Returns:
        List of ChangelogEntry dicts, sorted by salience (contradictions
        first, then promotions, then weakenings, then confirmations).
    """
    out: List[ChangelogEntry] = []
    seen_now = set(validation_log.all_prior_ids_seen(log_path))
    seen_before = set(stats_before.keys())
    for pid in seen_now | seen_before:
        before = stats_before.get(pid) or {}
        after = validation_log.summarize_prior(pid, log_path)
        if not after:
            continue
        prior = get_prior(pid)
        display = prior.display_name if prior else pid

        c_before = before.get("confidence")
        c_after = after.confidence
        s_before = before.get("suggested_status")
        s_after = after.suggested_status

        # 1. Contradictions: any new contradiction row this run.
        if after.n_contradictions > (before.get("n_contradictions") or 0):
            out.append(ChangelogEntry(
                prior_id=pid, display_name=display, kind="contradicted",
                rationale=(f"This run produced evidence that contradicts "
                           f"{display}. The deviation itself is the result."),
                confidence_before=c_before, confidence_after=c_after,
                n_total=after.n_total, ts=after.last_seen,
                suggested_status_before=s_before,
                suggested_status_after=s_after,
            ))
            continue

        # 2. Promotion / demotion.
        if s_before and s_after and s_before != s_after:
            if _is_promotion(s_before, s_after):
                out.append(ChangelogEntry(
                    prior_id=pid, display_name=display, kind="promoted",
                    rationale=(f"{display}: {s_before} → {s_after}. "
                               f"Confidence {c_before:.2f} → {c_after:.2f} "
                               f"over {after.n_total} validations."
                               if c_before is not None else
                               f"{display}: {s_before} → {s_after}."),
                    confidence_before=c_before, confidence_after=c_after,
                    n_total=after.n_total, ts=after.last_seen,
                    suggested_status_before=s_before,
                    suggested_status_after=s_after,
                ))
                continue
            elif _is_demotion(s_before, s_after):
                out.append(ChangelogEntry(
                    prior_id=pid, display_name=display, kind="weakened",
                    rationale=(f"{display}: {s_before} → {s_after}. "
                               f"Confidence dropped {c_before:.2f} → {c_after:.2f}."
                               if c_before is not None else
                               f"{display}: {s_before} → {s_after}."),
                    confidence_before=c_before, confidence_after=c_after,
                    n_total=after.n_total, ts=after.last_seen,
                    suggested_status_before=s_before,
                    suggested_status_after=s_after,
                ))
                continue

        # 3. Confidence drop without status change → "weakened" (lighter signal).
        if (c_before is not None and c_after is not None
                and c_after < c_before - 0.05):
            out.append(ChangelogEntry(
                prior_id=pid, display_name=display, kind="weakened",
                rationale=(f"{display}: confidence drifted "
                           f"{c_before:.2f} → {c_after:.2f}."),
                confidence_before=c_before, confidence_after=c_after,
                n_total=after.n_total, ts=after.last_seen,
                suggested_status_before=s_before,
                suggested_status_after=s_after,
            ))
            continue

        # 4. Confidence rise / first-time confirmation → "confirmed".
        n_before = before.get("n_total") or 0
        if after.n_total > n_before:
            out.append(ChangelogEntry(
                prior_id=pid, display_name=display, kind="confirmed",
                rationale=(f"{display}: {after.n_total} validations "
                           f"(was {n_before}); confidence "
                           f"{c_after:.2f}." if c_after is not None else
                           f"{display}: validation count rose to {after.n_total}."),
                confidence_before=c_before, confidence_after=c_after,
                n_total=after.n_total, ts=after.last_seen,
                suggested_status_before=s_before,
                suggested_status_after=s_after,
            ))

    # Sort by salience: contradictions > weakened > promoted > confirmed.
    salience = {"contradicted": 0, "weakened": 1, "promoted": 2,
                "deprecated": 3, "strengthened": 4, "confirmed": 5}
    out.sort(key=lambda e: (salience.get(e.kind, 99), -(e.n_total or 0)))
    return [e.to_dict() for e in out]


def snapshot_all_priors(log_path=None) -> Dict[str, Dict[str, Any]]:
    """Capture the current state of every prior the ledger has heard of.

    Returned shape:  {prior_id: PriorStats-as-dict}.

    Use this BEFORE a run to capture the baseline; pass it to
    ``compute_validation_changelog`` AFTER the run.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for pid in validation_log.all_prior_ids_seen(log_path):
        stats = validation_log.summarize_prior(pid, log_path)
        out[pid] = {
            "prior_id": stats.prior_id,
            "confidence": stats.confidence,
            "n_total": stats.n_total,
            "n_up": stats.n_up,
            "n_down": stats.n_down,
            "n_contradictions": stats.n_contradictions,
            "suggested_status": stats.suggested_status,
            "recent_trend": stats.recent_trend,
        }
    return out

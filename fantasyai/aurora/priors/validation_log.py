"""
Per-prior validation ledger — append-only JSONL keyed by prior_id.

Each row records a single observation about a prior:

* a fit confirmed it (auto-up: structural form matched, parameters in range)
* a fit weakened it (auto-down: contradicting evidence)
* a calibration entry: which fitted parameter values were observed, were
  they inside the stated range, by how much did they deviate
* a human verdict: explicit thumbs-up/-down on a finding tied to this prior

Stored at ``~/.aurora/prior_validations.jsonl`` (override via
``AURORA_PRIOR_VALIDATIONS`` env var). The Prior dataclass remains immutable
in code; this ledger is the mutable history.

The continuous-validation loop reads this ledger to:
1. Compute per-prior calibration scores
2. Promote / demote priors between status tiers
3. Detect contradictions worth surfacing to the user
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


def default_log_path() -> Path:
    override = os.environ.get("AURORA_PRIOR_VALIDATIONS")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".aurora" / "prior_validations.jsonl"


# ---------------------------------------------------------------------------
# Append helpers
# ---------------------------------------------------------------------------

def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def _json_safe(o: Any) -> Any:
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
        return o
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if o is None or isinstance(o, (bool, int, str)):
        return o
    try:
        return str(o)
    except Exception:
        return None


def append_validation(prior_id: str, verdict: str, *,
                      run_dir: Optional[str] = None,
                      score: Optional[float] = None,
                      via: str = "auto",
                      fitted_params: Optional[Dict[str, float]] = None,
                      param_in_range: Optional[Dict[str, bool]] = None,
                      note: Optional[str] = None,
                      log_path: Optional[Path] = None) -> Dict[str, Any]:
    """Append one validation row.

    Args:
        prior_id:        which prior the row pertains to
        verdict:         "up"|"down"|"neutral"|"contradiction"
        run_dir:         the run that produced the evidence (for traceability)
        score:           composite score in [0, 1] from auto_feedback (optional)
        via:             "auto" | "human" — distinguishes automated vs explicit feedback
        fitted_params:   fitted parameter values (for calibration)
        param_in_range:  per-parameter pass/fail vs stated range
        note:            optional free text
    """
    if verdict not in ("up", "down", "neutral", "contradiction"):
        raise ValueError(f"invalid verdict: {verdict!r}")
    log_path = log_path or default_log_path()
    _ensure_dir(log_path)
    row = {
        "row_type": "prior_validation_v1",
        "prior_id": str(prior_id),
        "verdict": verdict,
        "via": via,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_dir": run_dir,
        "score": float(score) if score is not None else None,
        "fitted_params": _json_safe(fitted_params) if fitted_params else None,
        "param_in_range": _json_safe(param_in_range) if param_in_range else None,
        "note": note,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_json_safe(row), separators=(",", ":")) + "\n")
    return row


def read_all(log_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    log_path = log_path or default_log_path()
    if not log_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def get_validation_history(prior_id: str,
                           log_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """All ledger rows for one prior, oldest first."""
    rows = [r for r in read_all(log_path)
            if r.get("row_type") == "prior_validation_v1"
            and r.get("prior_id") == prior_id]
    rows.sort(key=lambda r: r.get("ts", ""))
    return rows


# ---------------------------------------------------------------------------
# Per-prior summary stats
# ---------------------------------------------------------------------------

@dataclass
class PriorStats:
    prior_id: str
    n_total: int
    n_up: int
    n_down: int
    n_human_up: int
    n_human_down: int
    n_contradictions: int
    confidence: float                # smoothed up-rate ∈ [0, 1]
    recent_trend: str                # "strengthening" | "stable" | "weakening" | "no-data"
    suggested_status: str            # candidate|validated|canonical|questioned|deprecated
    last_seen: Optional[str]


def summarize_prior(prior_id: str,
                    log_path: Optional[Path] = None) -> PriorStats:
    """Roll the ledger up into one summary row per prior.

    Promotion / demotion thresholds:

    * <  3 evidence rows                           → candidate
    * confidence ≥ 0.75 AND ≥ 3 rows               → validated
    * confidence ≥ 0.90 AND ≥ 8 rows               → canonical
    * confidence ≤ 0.40 AND ≥ 3 rows               → questioned
    * recent contradictions outweigh confirmations → questioned
    """
    rows = get_validation_history(prior_id, log_path)
    n_total = len(rows)
    n_up = sum(1 for r in rows if r.get("verdict") == "up")
    n_down = sum(1 for r in rows if r.get("verdict") == "down")
    n_contra = sum(1 for r in rows if r.get("verdict") == "contradiction")
    n_h_up = sum(1 for r in rows if r.get("verdict") == "up" and r.get("via") == "human")
    n_h_down = sum(1 for r in rows if r.get("verdict") == "down" and r.get("via") == "human")
    last_seen = rows[-1]["ts"] if rows else None

    # Smoothed confidence: Beta-Bernoulli with prior(1, 1).
    pos = n_up + n_h_up + 1
    neg = n_down + n_h_down + n_contra + 1
    conf = pos / (pos + neg)

    # Recent trend: last 5 rows direction.
    recent = rows[-5:]
    if len(recent) < 3:
        trend = "no-data"
    else:
        recent_up = sum(1 for r in recent if r.get("verdict") == "up")
        recent_down = sum(1 for r in recent
                          if r.get("verdict") in ("down", "contradiction"))
        if recent_up > recent_down + 1:
            trend = "strengthening"
        elif recent_down > recent_up:
            trend = "weakening"
        else:
            trend = "stable"

    # Status suggestion.
    if n_total < 3:
        suggested = "candidate"
    elif conf <= 0.40:
        suggested = "questioned"
    elif conf >= 0.90 and n_total >= 8:
        suggested = "canonical"
    elif conf >= 0.75:
        suggested = "validated"
    else:
        suggested = "validated"

    # If recent trend is weakening, override to "questioned".
    if trend == "weakening" and n_total >= 3:
        suggested = "questioned"

    return PriorStats(
        prior_id=prior_id,
        n_total=n_total,
        n_up=n_up,
        n_down=n_down,
        n_human_up=n_h_up,
        n_human_down=n_h_down,
        n_contradictions=n_contra,
        confidence=float(conf),
        recent_trend=trend,
        suggested_status=suggested,
        last_seen=last_seen,
    )


def all_prior_ids_seen(log_path: Optional[Path] = None) -> List[str]:
    return sorted({r.get("prior_id")
                   for r in read_all(log_path)
                   if r.get("row_type") == "prior_validation_v1"
                   and r.get("prior_id")})

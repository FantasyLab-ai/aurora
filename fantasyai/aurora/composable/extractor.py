"""
Extract a compact prior pack from a finished run.

Inputs (all read from a run_dir):
    * ``_RUN_META.json``         — dataset name + identity
    * ``deep_math.json``         — physics_discovery, regimes
    * ``math_results.json``      — top-anomaly z-floors

Output: a ``PriorPack`` dataclass that's both JSON-serialisable AND
small enough (< 2KB typical) to embed in the next run's bundle without
ballooning storage.

What we extract:
    * **physics** — the best-fit law (name + params + rmse) from
      ``physics_discovery.best``. If the run found "logistic, K=42",
      the next run gets logistic-K=42 as a candidate to validate.
    * **regimes** — K and the centroid means + expected dwells from
      ``regimes`` (HMM if available, change-point fallback otherwise).
    * **baseline** — for the target column: mean, std, n_obs. The
      next run's anomaly detector uses these as the z-score reference
      when the dataset is incomplete or noisy.
    * **anomaly_floor** — the z_warn / z_crit thresholds the previous
      run actually triggered on. Carries forward the calibration so a
      noisy-day-followed-by-quiet-day doesn't suddenly explode in
      false positives.

What we DON'T extract:
    * Raw rows / full forecasts (would balloon the pack).
    * Findings text (re-derived from the prior on the next run).
    * KB citations (those are static priors from the KB matcher).
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _safe_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


@dataclass
class PriorPack:
    """Compact summary of a run, ready to seed the next run."""
    source_run_id:   str
    source_dataset:  Optional[str]
    extracted_at:    float
    target_col:      Optional[str]
    # Physics best-fit law (e.g. "logistic", params: {...}, rmse: ...).
    physics:         Optional[Dict[str, Any]] = None
    # Regime structure: k, means, dwells.
    regimes:         Optional[Dict[str, Any]] = None
    # Baseline stats on target column.
    baseline:        Optional[Dict[str, Any]] = None
    # z-score floors that previously triggered.
    anomaly_floor:   Optional[Dict[str, Any]] = None
    # How many sub-priors were populated (cheap "is this useful?" signal).
    n_priors:        int = 0
    # Schema version so the applier can refuse / fall back on stale packs.
    schema_version:  str = "1"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None or k in (
            "physics", "regimes", "baseline", "anomaly_floor",
        )}


def _extract_physics(deep: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pd = (deep.get("physics_discovery") or {}) if isinstance(deep, dict) else {}
    best = pd.get("best") or {}
    if not best:
        return None
    out = {
        "law":    best.get("name") or best.get("model"),
        "params": best.get("params") or {},
        "rmse":   _safe_float(best.get("rmse_test") or best.get("rmse")),
        "aic":    _safe_float(best.get("aic")),
    }
    if not out["law"]:
        return None
    return out


def _extract_regimes(deep: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Prefer HMM (named regime model with means + dwells).
    deep = deep if isinstance(deep, dict) else {}
    hmm = deep.get("hmm") or {}
    if hmm.get("available"):
        means = list(hmm.get("means_sorted") or [])
        dwells = list(hmm.get("expected_dwell_time") or [])
        if means:
            return {
                "method":          "hmm",
                "k":               int(hmm.get("best_K") or len(means)),
                "means":           [_safe_float(m) for m in means],
                "expected_dwells": [_safe_float(d) for d in dwells],
            }
    # Fall back to change-point regimes.
    regs = deep.get("regimes") or {}
    cps = regs.get("change_points") or []
    if cps:
        stats = regs.get("stats") or []
        return {
            "method":          regs.get("method") or "change_point",
            "k":               int(regs.get("regime_count") or len(stats)),
            "means":           [_safe_float((s or {}).get("mean"))
                                  for s in stats],
            "expected_dwells": [],
        }
    return None


def _extract_baseline(
    deep: Dict[str, Any],
    meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Pull the target column + descriptive stats.

    deep_math.json doesn't always carry the descriptive block, but
    when it does we use it; otherwise we fall through to a None
    baseline and the next run computes its own.
    """
    target = (meta or {}).get("target_col")
    if not target:
        return None
    # Aurora's deep_math.physics block sometimes carries y_min/y_max +
    # is_monotonic — close to what we need.
    physics = (deep or {}).get("physics") or {}
    ymin = _safe_float(physics.get("y_min"))
    ymax = _safe_float(physics.get("y_max"))
    if ymin is None or ymax is None:
        return None
    return {
        "col":  str(target),
        "min":  ymin,
        "max":  ymax,
        "span": ymax - ymin,
    }


def _extract_anomaly_floor(mathr: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(mathr, dict):
        return None
    top = mathr.get("top_anomalies") or []
    if not top:
        return None
    # Pull the z-thresholds that distinguished crit vs warn on this run.
    crit_min = None
    warn_min = None
    for a in top:
        sev = str((a or {}).get("severity") or "").lower()
        z = _safe_float((a or {}).get("z"))
        if z is None:
            continue
        if sev == "crit":
            crit_min = z if crit_min is None else min(crit_min, z)
        elif sev == "warn":
            warn_min = z if warn_min is None else min(warn_min, z)
    if crit_min is None and warn_min is None:
        return None
    return {
        "z_warn": warn_min,
        "z_crit": crit_min,
    }


def extract_prior_pack(run_dir: Any) -> PriorPack:
    """Read a run directory and produce a PriorPack.

    Never raises — every sub-section degrades to None if its source
    artifact is missing or malformed. The returned pack may have
    ``n_priors=0`` if the run had nothing reusable.
    """
    rd = Path(run_dir)
    deep         = _read_json(rd / "deep_math.json") or {}
    meta         = _read_json(rd / "_RUN_META.json") or {}
    resolved     = _read_json(rd / "_RESOLVED_CONTRACT.json") or {}
    mathr        = _read_json(rd / "math_results.json") or {}

    target_col = meta.get("target_col") or resolved.get("target_col")
    dataset_name = (
        meta.get("dataset_name")
        or resolved.get("dataset_key")
        or (meta.get("dataset_path") or "").split("/")[-1] or None
    )

    physics       = _extract_physics(deep)
    regimes       = _extract_regimes(deep)
    baseline      = _extract_baseline(deep, meta)
    anomaly_floor = _extract_anomaly_floor(mathr)

    n_priors = sum(1 for x in (physics, regimes, baseline, anomaly_floor)
                    if x is not None)

    return PriorPack(
        source_run_id=str(meta.get("run_id") or rd.name),
        source_dataset=dataset_name,
        extracted_at=time.time(),
        target_col=target_col,
        physics=physics,
        regimes=regimes,
        baseline=baseline,
        anomaly_floor=anomaly_floor,
        n_priors=n_priors,
    )


def write_prior_pack(pack: PriorPack, out_path: Any) -> Path:
    """Persist a PriorPack to disk as JSON. Returns the path written."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(pack.to_dict(), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return p

"""
A/B compare two finished runs.

This is a thin convenience layer over the Q3 multi-dataset joiner: it
loads both runs' summary metrics + delegates to ``compute_join_report``
for the deeper structural compare. The output is a single dict the
Studio renders side-by-side.

Why a separate module: ``joins.compute_join_report`` was scoped to the
"two different datasets joined together" use case. A/B compare is the
adjacent use case — "same dataset, two analytical runs" — and we want
to surface the right things (confidence delta, fabricated counts, new
vs disappeared findings) without forcing the join semantics through.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


def _read_json(p: Path) -> Optional[Dict[str, Any]]:
    try:
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
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
class RunComparison:
    ok: bool
    run_a_id: str
    run_b_id: str
    dataset_a: Optional[str]
    dataset_b: Optional[str]
    same_dataset: bool
    summary_a: Dict[str, Any] = field(default_factory=dict)
    summary_b: Dict[str, Any] = field(default_factory=dict)
    delta:     Dict[str, Any] = field(default_factory=dict)
    new_findings:        List[Dict[str, Any]] = field(default_factory=list)
    disappeared_findings: List[Dict[str, Any]] = field(default_factory=list)
    shared_findings_n:   int = 0
    # Embedded join report (when datasets differ) — surfaces shared
    # keys, schema compatibility, etc.
    join_report: Optional[Dict[str, Any]] = None
    warnings:    List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _load_summary(run_dir: Path) -> Dict[str, Any]:
    meta   = _read_json(run_dir / "_RUN_META.json") or {}
    report = _read_json(run_dir / "report.json")    or {}
    score  = report.get("score") or {}
    mathr  = _read_json(run_dir / "math_results.json") or {}
    deep   = _read_json(run_dir / "deep_math.json")  or {}
    top_anomalies = mathr.get("top_anomalies") or []
    # Coarse severity counts (crit/warn/info/ok).
    sev_counts: Dict[str, int] = {}
    for a in top_anomalies:
        s = str((a or {}).get("severity") or "info").lower()
        sev_counts[s] = sev_counts.get(s, 0) + 1
    physics_law = ((deep.get("physics_discovery") or {})
                    .get("best") or {}).get("name")
    return {
        "run_id":         meta.get("run_id") or run_dir.name,
        "dataset":        meta.get("dataset_name"),
        "target_col":     meta.get("target_col"),
        "n_rows":         meta.get("n_rows"),
        "confidence":     _safe_float(score.get("confidence")),
        "stability_good": score.get("stability_good"),
        "drift_good":     score.get("drift_good"),
        "fabricated":     report.get("fabricated_count"),
        "severity_counts": sev_counts,
        "n_anomalies":    len(top_anomalies),
        "physics_law":    physics_law,
        "hmm_k":          ((deep.get("hmm") or {}).get("best_K")
                            if (deep.get("hmm") or {}).get("available") else None),
    }


def _finding_signatures(run_dir: Path) -> Set[str]:
    """Read the run's findings and produce identity signatures for
    set-arithmetic. We reuse the streaming dedupe hash so the same
    finding gets the same signature in both contexts.
    """
    sigs: Set[str] = set()
    # Aurora doesn't write a top-level findings.json; we approximate
    # from extended_methods.json + top anomalies.
    try:
        from fantasyai.aurora.streaming.dedupe import finding_identity
    except Exception:
        finding_identity = None  # type: ignore

    ext = _read_json(run_dir / "extended_methods.json") or {}
    for f in (ext.get("findings") or []):
        if not isinstance(f, dict):
            continue
        if finding_identity is None:
            sig = f"{f.get('method', '?')}|{f.get('title', '?')}|{f.get('severity', '?')}"
        else:
            sig = finding_identity(f)
        if sig:
            sigs.add(sig)
    mathr = _read_json(run_dir / "math_results.json") or {}
    for a in (mathr.get("top_anomalies") or []):
        if not isinstance(a, dict):
            continue
        synthetic_finding = {
            "method":   "iso-forest+robust-z",
            "title":    f"Anomaly at row {a.get('row')}",
            "severity": a.get("severity") or "info",
            "evidence": {"status": "fit",
                          "n_obs": int(a.get("row") or 0)},
        }
        if finding_identity is None:
            sig = f"anom|row={a.get('row')}|sev={a.get('severity')}"
        else:
            sig = finding_identity(synthetic_finding)
        if sig:
            sigs.add(sig)
    return sigs


def _safe_subtract(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(a - b, 4)


def compare_runs(run_a_dir: Any, run_b_dir: Any) -> RunComparison:
    """Build an A/B comparison between two finished runs."""
    pa = Path(run_a_dir)
    pb = Path(run_b_dir)
    if not pa.exists() or not pb.exists():
        return RunComparison(
            ok=False, run_a_id=pa.name, run_b_id=pb.name,
            dataset_a=None, dataset_b=None, same_dataset=False,
            warnings=["one or both run directories do not exist"],
        )
    if pa.resolve() == pb.resolve():
        return RunComparison(
            ok=False, run_a_id=pa.name, run_b_id=pb.name,
            dataset_a=None, dataset_b=None, same_dataset=False,
            warnings=["cannot compare a run with itself"],
        )

    sum_a = _load_summary(pa)
    sum_b = _load_summary(pb)
    same_dataset = bool(sum_a["dataset"] and sum_a["dataset"] == sum_b["dataset"])

    sigs_a = _finding_signatures(pa)
    sigs_b = _finding_signatures(pb)
    new_sigs        = sigs_b - sigs_a
    disappeared_sigs = sigs_a - sigs_b
    shared_n = len(sigs_a & sigs_b)

    # We attach lightweight refs (signatures) — the frontend can
    # cross-reference with the run's full findings list when needed.
    new_findings = [{"signature": s} for s in sorted(new_sigs)][:20]
    disappeared = [{"signature": s} for s in sorted(disappeared_sigs)][:20]

    # Delta block — coarse but actionable.
    delta = {
        "confidence_delta":   _safe_subtract(
            sum_b.get("confidence"), sum_a.get("confidence")),
        "n_anomalies_delta":  (
            (sum_b.get("n_anomalies") or 0) - (sum_a.get("n_anomalies") or 0)
        ),
        "fabricated_delta": (
            (sum_b.get("fabricated") or 0) - (sum_a.get("fabricated") or 0)
        ),
        "physics_law_changed": bool(
            sum_a.get("physics_law") != sum_b.get("physics_law")
        ),
        "hmm_k_delta": (
            (sum_b.get("hmm_k") or 0) - (sum_a.get("hmm_k") or 0)
            if (sum_a.get("hmm_k") is not None or sum_b.get("hmm_k") is not None)
            else None
        ),
        "same_dataset": same_dataset,
    }

    # When the runs cover different datasets, also surface the join
    # report so the user can see schema compatibility + shared keys.
    join_report = None
    if not same_dataset:
        try:
            from fantasyai.aurora.joins import compute_join_report
            jr = compute_join_report(pa, pb)
            join_report = jr.to_dict()
        except Exception:
            pass

    warnings: List[str] = []
    if not same_dataset:
        warnings.append(
            f"Runs cover different datasets ({sum_a['dataset']} vs "
            f"{sum_b['dataset']}); finding-level compare may not be meaningful."
        )

    return RunComparison(
        ok=True,
        run_a_id=str(sum_a["run_id"]),
        run_b_id=str(sum_b["run_id"]),
        dataset_a=sum_a.get("dataset"),
        dataset_b=sum_b.get("dataset"),
        same_dataset=same_dataset,
        summary_a=sum_a,
        summary_b=sum_b,
        delta=delta,
        new_findings=new_findings,
        disappeared_findings=disappeared,
        shared_findings_n=shared_n,
        join_report=join_report,
        warnings=warnings,
    )

"""
Compute a join report between two finished Aurora runs.

Inputs: two run_dir paths. We need each run's:
    * structure_contract.json — column roles, dtypes, missing_rate
    * _RUN_META.json          — dataset path
    * deep_math.json          — physics_discovery.best + regimes
    * math_results.json       — anomalies (for shared-anomaly check)

Outputs (JoinReport dataclass, JSON-serialisable):

    {
        "ok": True,
        "run_a": {...},
        "run_b": {...},
        "schema_compatibility": {
            "n_cols_a": int, "n_cols_b": int,
            "row_count_a": int, "row_count_b": int,
            "cadence_match": "exact|approx|mismatch",
            "warnings": [...],
        },
        "shared_keys": [
            {"name": "timestamp", "kind": "time",
              "overlap_quality": 0.95, "rationale": "..."},
            ...
        ],
        "cross_correlations": [
            {"col_a": "temp_c", "col_b": "humidity_pct",
              "key": "timestamp", "r": 0.62,
              "n_aligned": 1456},
            ...
        ],
        "inheritance_candidates": [
            {"from": "A", "to": "B", "kind": "physics_law",
              "value": "logistic", "rationale": "..."},
            ...
        ],
    }

Design notes:
    * We don't actually load the CSVs to compute correlations.
      Instead we approximate from existing artifacts (math_results +
      deep_math). This keeps the join report cheap (~50ms) and the
      cross-references safe — we never re-process the raw data.
    * For "true" correlation work the caller hits the joined CSV
      through pandas themselves; this report is the *navigator*.
"""
from __future__ import annotations

import json
import math
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


@dataclass
class JoinKey:
    name: str
    kind: str  # "time" | "id" | "category" | "name_match"
    overlap_quality: float  # 0..1
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CrossCorrelation:
    col_a: str
    col_b: str
    key: Optional[str]
    r: Optional[float]
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class JoinReport:
    ok: bool
    run_a_id: str
    run_b_id: str
    dataset_a: Optional[str]
    dataset_b: Optional[str]
    schema_compatibility: Dict[str, Any] = field(default_factory=dict)
    shared_keys: List[JoinKey] = field(default_factory=list)
    cross_correlations: List[CrossCorrelation] = field(default_factory=list)
    inheritance_candidates: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "run_a_id": self.run_a_id,
            "run_b_id": self.run_b_id,
            "dataset_a": self.dataset_a,
            "dataset_b": self.dataset_b,
            "schema_compatibility": self.schema_compatibility,
            "shared_keys": [k.to_dict() for k in self.shared_keys],
            "cross_correlations": [c.to_dict() for c in self.cross_correlations],
            "inheritance_candidates": list(self.inheritance_candidates),
            "warnings": list(self.warnings),
        }


# ----------------------------------------------------------------------
# Section helpers
# ----------------------------------------------------------------------

def _load_run(run_dir: Path) -> Dict[str, Any]:
    return {
        "meta":      _read_json(run_dir / "_RUN_META.json") or {},
        "resolved":  _read_json(run_dir / "_RESOLVED_CONTRACT.json") or {},
        "structure": _read_json(run_dir / "structure_contract.json") or {},
        "deep":      _read_json(run_dir / "deep_math.json") or {},
        "mathr":     _read_json(run_dir / "math_results.json") or {},
    }


def _cols(run_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    s = run_doc.get("structure") or {}
    return list(s.get("columns") or [])


def _col_names(cols: List[Dict[str, Any]]) -> List[str]:
    return [str(c.get("name") or "") for c in cols if isinstance(c, dict)]


def _classify_kind(col: Dict[str, Any]) -> str:
    role = str(col.get("role") or "").lower()
    dtype = str(col.get("dtype") or "").lower()
    dt_rate = float(col.get("datetime_parse_rate") or 0)
    if dt_rate > 0.5 or "datetime" in dtype or role == "time":
        return "time"
    if "object" in dtype or "category" in dtype or role in ("categorical", "string", "id"):
        return "id"
    return "numeric"


def _find_shared_keys(cols_a: List[Dict[str, Any]],
                       cols_b: List[Dict[str, Any]]) -> List[JoinKey]:
    names_b = {str(c.get("name") or "").lower(): c for c in cols_b}
    out: List[JoinKey] = []
    for a in cols_a:
        an = str(a.get("name") or "").lower()
        if not an:
            continue
        b = names_b.get(an)
        if b is None:
            continue
        # Exact name match: weighted by dtype agreement + missingness similarity.
        ka = _classify_kind(a)
        kb = _classify_kind(b)
        same_kind = ka == kb
        miss_a = float(a.get("missing_rate") or 0)
        miss_b = float(b.get("missing_rate") or 0)
        # Quality bonus when both columns are low-missingness time/id.
        base = 0.5 if same_kind else 0.25
        completeness = 1.0 - max(miss_a, miss_b)
        quality = round(base + 0.5 * completeness, 3)
        rationale = (
            f"Name match on {an!r}; kinds {ka}↔{kb}; "
            f"missingness {miss_a:.2f}/{miss_b:.2f}."
        )
        out.append(JoinKey(
            name=str(a.get("name") or ""),
            kind=ka if same_kind else "name_match",
            overlap_quality=quality,
            rationale=rationale,
        ))
    # Time columns implicitly form a join key even with different names
    # (e.g., "ts" vs "timestamp"). Heuristic: if A has exactly one time
    # column and B has exactly one time column, propose them as a join key.
    a_times = [c for c in cols_a if _classify_kind(c) == "time"]
    b_times = [c for c in cols_b if _classify_kind(c) == "time"]
    if len(a_times) == 1 and len(b_times) == 1:
        na = str(a_times[0].get("name") or "")
        nb = str(b_times[0].get("name") or "")
        if na and nb and na.lower() != nb.lower():
            out.append(JoinKey(
                name=f"{na} ↔ {nb}",
                kind="time",
                overlap_quality=0.70,
                rationale="Inferred time-axis pair (single time column on each side).",
            ))
    # Rank by quality.
    out.sort(key=lambda k: -k.overlap_quality)
    return out[:8]


def _cross_correlations(
    run_a: Dict[str, Any],
    run_b: Dict[str, Any],
    shared_keys: List[JoinKey],
) -> List[CrossCorrelation]:
    """Surface plausible cross-correlations without re-loading raw data.

    We mine correlations that the per-run analytics already computed
    (e.g. anomaly-attribution top_feature_drivers in deep_math). These
    are not joined correlations — they're per-run hints — but pairing
    them with a shared key gives the user a starting point for
    follow-up joined analysis.
    """
    out: List[CrossCorrelation] = []
    key_hint = shared_keys[0].name if shared_keys else None
    # Pull the headline target columns and their dominant drivers
    # from each side.
    for label, run in (("A", run_a), ("B", run_b)):
        deep = run.get("deep") or {}
        attrib = (((deep.get("extensions") or {})
                    .get("anomaly_attribution") or {})
                    .get("points")) or []
        # Take the first attributed driver, if any.
        for p in attrib[:1]:
            drivers = p.get("top_feature_drivers") or []
            for d in drivers[:2]:
                col = d.get("feature") or d.get("col")
                score = d.get("score")
                if not col:
                    continue
                out.append(CrossCorrelation(
                    col_a=col if label == "A" else "(see B)",
                    col_b="(see A)" if label == "A" else col,
                    key=key_hint,
                    r=float(score) if score is not None else None,
                    rationale=(
                        f"Run {label}'s top anomaly driver. "
                        f"Joining on {key_hint!r} would reveal whether "
                        f"the other run's columns track this driver."
                    ),
                ))
    return out


def _inheritance_candidates(
    run_a: Dict[str, Any],
    run_b: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """A's discoveries that B might inherit — and vice versa.

    Concretely: physics-discovery best-law + regime K. The point is
    *suggestion*, not auto-application; the user reviews and (in the
    Studio) clicks "use as prior for B."
    """
    out: List[Dict[str, Any]] = []
    for src, dst, src_doc in (("A", "B", run_a), ("B", "A", run_b)):
        deep = src_doc.get("deep") or {}
        pd = deep.get("physics_discovery") or {}
        best = pd.get("best") or {}
        if best.get("name"):
            out.append({
                "from": src,
                "to":   dst,
                "kind": "physics_law",
                "value": str(best.get("name")),
                "rationale": (
                    f"Run {src} discovered {best.get('name')!r} on its target. "
                    f"Try it as a candidate prior for {dst}."
                ),
            })
        hmm = deep.get("hmm") or {}
        if hmm.get("available"):
            out.append({
                "from": src,
                "to":   dst,
                "kind": "regime_k",
                "value": int(hmm.get("best_K") or 0),
                "rationale": (
                    f"Run {src} found K={hmm.get('best_K')} regimes. "
                    f"Initialise {dst}'s HMM at the same K to test "
                    f"whether the regime structure transfers."
                ),
            })
    return out


def _schema_compatibility(
    run_a: Dict[str, Any],
    run_b: Dict[str, Any],
) -> Dict[str, Any]:
    sa = run_a.get("structure") or {}
    sb = run_b.get("structure") or {}
    shape_a = sa.get("shape") or {}
    shape_b = sb.get("shape") or {}
    rows_a = int(shape_a.get("rows") or 0)
    rows_b = int(shape_b.get("rows") or 0)
    n_cols_a = int(shape_a.get("cols") or 0)
    n_cols_b = int(shape_b.get("cols") or 0)
    # Cadence: read dt_seconds when present.
    dt_a = (run_a.get("meta") or {}).get("dt_seconds") or 0
    dt_b = (run_b.get("meta") or {}).get("dt_seconds") or 0
    cadence_match = "n/a"
    if dt_a and dt_b:
        ratio = max(dt_a, dt_b) / min(dt_a, dt_b)
        if ratio < 1.05:
            cadence_match = "exact"
        elif ratio < 4:
            cadence_match = "approx"
        else:
            cadence_match = "mismatch"
    warnings: List[str] = []
    if rows_a and rows_b and max(rows_a, rows_b) / max(min(rows_a, rows_b), 1) > 10:
        warnings.append(
            f"Row counts differ ~{max(rows_a, rows_b)//max(min(rows_a, rows_b),1)}× "
            f"({rows_a} vs {rows_b}); join needs a window or aggregation strategy."
        )
    if cadence_match == "mismatch":
        warnings.append(
            f"Cadence mismatch (dt {dt_a}s vs {dt_b}s); "
            f"resample to a common grid before joining."
        )
    return {
        "n_cols_a": n_cols_a,
        "n_cols_b": n_cols_b,
        "row_count_a": rows_a,
        "row_count_b": rows_b,
        "dt_seconds_a": dt_a,
        "dt_seconds_b": dt_b,
        "cadence_match": cadence_match,
        "warnings": warnings,
    }


# ----------------------------------------------------------------------
# Top-level entry point
# ----------------------------------------------------------------------

def compute_join_report(run_a_dir: Any, run_b_dir: Any) -> JoinReport:
    """Compute a JoinReport from two finished Aurora run directories."""
    pa = Path(run_a_dir)
    pb = Path(run_b_dir)
    if not pa.exists() or not pb.exists():
        return JoinReport(
            ok=False,
            run_a_id=pa.name, run_b_id=pb.name,
            dataset_a=None, dataset_b=None,
            warnings=["one or both run directories do not exist"],
        )
    if pa.resolve() == pb.resolve():
        return JoinReport(
            ok=False,
            run_a_id=pa.name, run_b_id=pb.name,
            dataset_a=None, dataset_b=None,
            warnings=["cannot join a run with itself"],
        )

    run_a = _load_run(pa)
    run_b = _load_run(pb)

    cols_a = _cols(run_a)
    cols_b = _cols(run_b)

    shared = _find_shared_keys(cols_a, cols_b)
    crosscorr = _cross_correlations(run_a, run_b, shared)
    inherit = _inheritance_candidates(run_a, run_b)
    schema = _schema_compatibility(run_a, run_b)

    return JoinReport(
        ok=True,
        run_a_id=(run_a["meta"].get("run_id") or pa.name),
        run_b_id=(run_b["meta"].get("run_id") or pb.name),
        dataset_a=run_a["meta"].get("dataset_name"),
        dataset_b=run_b["meta"].get("dataset_name"),
        schema_compatibility=schema,
        shared_keys=shared,
        cross_correlations=crosscorr,
        inheritance_candidates=inherit,
        warnings=list(schema.get("warnings") or []),
    )

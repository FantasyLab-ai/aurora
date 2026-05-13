"""
Provenance synthesizer — reads artifacts produced by the runner and emits
a ledger entry for every claim.

Design choice: build the ledger *from the artifacts*, not by instrumenting
each analyzer at run time. Trade-offs:

  + Historical runs become inspectable for free
  + Zero changes to the runner / dataset_runner / orchestrator
  + Forward-compatible: when an analyzer emits new fields, we extend the
    builder and re-synthesize without re-running

  - Per-op granularity isn't possible (only per-finding). For sub-operation
    detail (e.g. each KMeans iteration) we'd need runtime instrumentation.
    That's a v1.5 upgrade.

The builder produces the same provenance entries the frontend displays via
``GET /api/provenance/<claim_id>``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ledger import (
    ProvenanceEntry,
    LEDGER_FILENAME,
    detect_code_version,
    hash_file,
    hash_payload,
    write_ledger,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _read_json(path: Path) -> Optional[Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return None


def _safe_hash_input(run_dir: Path, resolved: Optional[Dict]) -> str:
    """Compute the input data hash if we can find the source CSV."""
    if not resolved:
        return ""
    table = resolved.get("table") or resolved.get("dataset_path")
    if not table:
        return ""
    p = Path(table)
    if p.exists():
        try:
            return hash_file(p)
        except Exception:
            return ""
    return ""


# ----------------------------------------------------------------------
# Per-claim-type synthesizers — each returns a list of ProvenanceEntry
# ----------------------------------------------------------------------

def _entries_for_anomalies(deep: Optional[Dict], inputs_hash: str,
                           code_version: str) -> List[ProvenanceEntry]:
    """One entry per anomaly point in extensions.anomaly_attribution.points."""
    if not deep:
        return []
    aa = (deep.get("extensions") or {}).get("anomaly_attribution") or {}
    points = aa.get("points") or []
    out: List[ProvenanceEntry] = []
    for i, p in enumerate(points):
        if not isinstance(p, dict):
            continue
        drivers = p.get("top_feature_drivers") or []
        head_col = drivers[0]["feature"] if drivers and isinstance(drivers[0], dict) else "?"
        score = p.get("score")
        out.append(ProvenanceEntry(
            claim_id=f"anom-{i:04d}",
            claim_type="anomaly",
            claim_summary=(f"row {p.get('row_id','?')} flagged with score "
                           f"{score:.3f} on {head_col}" if score is not None
                           else f"row {p.get('row_id','?')} flagged on {head_col}"),
            method="robust_z_isolation_forest_attribution",
            method_params={"top_k": aa.get("top_k", 10)},
            inputs_hash=inputs_hash,
            input_subset={"row_id": p.get("row_id"),
                          "columns": [d.get("feature") for d in drivers
                                      if isinstance(d, dict)]},
            code_ref="fantasyai/aurora/math/deep_math.py (anomaly_attribution)",
            code_version=code_version,
            raw_result=p,
            artifact_ref=f"deep_math.json:extensions.anomaly_attribution.points[{i}]",
        ))
    return out


def _entries_for_forecast(deep: Optional[Dict], inputs_hash: str,
                          code_version: str) -> List[ProvenanceEntry]:
    """Single entry summarizing the forecast block."""
    if not deep:
        return []
    fc = deep.get("forecasting") or {}
    if not fc or fc.get("note") not in (None, "ok"):
        return []
    yhat = fc.get("forecast") or []
    if not yhat:
        return []
    target = deep.get("target_col") or "?"
    method = (fc.get("method") or "ar1").upper()
    return [ProvenanceEntry(
        claim_id="forecast-0001",
        claim_type="forecast",
        claim_summary=f"{method} forecast of {target} over {len(yhat)} steps",
        method=method.lower() + "_with_normal_bands",
        method_params={
            "horizon": fc.get("horizon"),
            "alpha": fc.get("alpha"),
            "phi": fc.get("phi"),
            "sigma": fc.get("sigma"),
        },
        inputs_hash=inputs_hash,
        input_subset={"target_col": target},
        code_ref="fantasyai/aurora/math/forecasting.py (ar1_with_bands)",
        code_version=code_version,
        raw_result={"forecast": yhat, "lower": fc.get("lower"),
                    "upper": fc.get("upper")},
        artifact_ref="deep_math.json:forecasting",
    )]


def _entries_for_causal(deep: Optional[Dict], inputs_hash: str,
                        code_version: str) -> List[ProvenanceEntry]:
    """Single entry per causal-what-if estimate (today the runner emits one)."""
    if not deep:
        return []
    cw = deep.get("causal_what_if") or {}
    if not cw or cw.get("note") not in (None, "ok"):
        return []
    treatment = cw.get("treatment_col")
    target = cw.get("target_col")
    if not treatment or not target:
        return []
    return [ProvenanceEntry(
        claim_id="causal-0001",
        claim_type="causal",
        claim_summary=(f"unit increase in {treatment} → {target} changes by "
                       f"{cw.get('estimated_effect'):+.3g}"
                       if cw.get('estimated_effect') is not None else
                       f"effect of {treatment} on {target}"),
        method=cw.get("method", "linear_ols").lower(),
        method_params={
            "delta": cw.get("delta"),
            "covariates_used": cw.get("covariates_used"),
            "n": cw.get("n"),
        },
        inputs_hash=inputs_hash,
        input_subset={"treatment": treatment, "target": target,
                      "covariates": cw.get("covariates_used")},
        code_ref="fantasyai/aurora/math/causal.py (causal_what_if_linear)",
        code_version=code_version,
        raw_result=cw,
        artifact_ref="deep_math.json:causal_what_if",
    )]


def _entries_for_physics(deep: Optional[Dict], inputs_hash: str,
                         code_version: str) -> List[ProvenanceEntry]:
    """Best-fit physics + each runner-up gets an entry."""
    if not deep:
        return []
    pd_ = deep.get("physics_discovery") or {}
    if not pd_:
        return []
    out: List[ProvenanceEntry] = []
    best = pd_.get("best") or {}
    if best:
        out.append(ProvenanceEntry(
            claim_id="physics-best",
            claim_type="physics",
            claim_summary=(f"best fit '{best.get('name')}': "
                           f"{best.get('model','')} (RMSE "
                           f"{best.get('rmse_test') or best.get('rmse_full')})"),
            method="ode_grid_search",
            method_params={"k": best.get("k"), "aic": best.get("aic")},
            inputs_hash=inputs_hash,
            input_subset={"target_col": pd_.get("target_col"),
                          "time_col": pd_.get("time_col")},
            code_ref="fantasyai/aurora/math/physics_discovery.py (discover_physics_models)",
            code_version=code_version,
            raw_result=best,
            artifact_ref="deep_math.json:physics_discovery.best",
        ))
    for i, ru in enumerate(pd_.get("runner_ups") or []):
        if not isinstance(ru, dict):
            continue
        out.append(ProvenanceEntry(
            claim_id=f"physics-ru-{i+1}",
            claim_type="physics",
            claim_summary=f"runner-up '{ru.get('name')}' RMSE {ru.get('rmse_test') or ru.get('rmse_full')}",
            method="ode_grid_search",
            method_params={"k": ru.get("k"), "aic": ru.get("aic")},
            inputs_hash=inputs_hash,
            input_subset={"target_col": pd_.get("target_col")},
            code_ref="fantasyai/aurora/math/physics_discovery.py",
            code_version=code_version,
            raw_result=ru,
            artifact_ref=f"deep_math.json:physics_discovery.runner_ups[{i}]",
        ))
    return out


def _entries_for_regimes(deep: Optional[Dict], inputs_hash: str,
                         code_version: str) -> List[ProvenanceEntry]:
    """One entry per regime segment."""
    if not deep:
        return []
    r = deep.get("regimes") or {}
    if not r or r.get("note") not in (None, "ok"):
        return []
    stats = r.get("stats") or []
    out: List[ProvenanceEntry] = []
    for i, s in enumerate(stats):
        if not isinstance(s, dict):
            continue
        out.append(ProvenanceEntry(
            claim_id=f"regime-{i+1:03d}",
            claim_type="regime",
            claim_summary=(f"regime {i+1}: rows {s.get('start')}-{s.get('end')}, "
                           f"mean={s.get('mean'):.3g}, std={s.get('std'):.3g}"
                           if s.get('mean') is not None else
                           f"regime {i+1}"),
            method=r.get("method", "ruptures_pelt"),
            method_params={"penalty": r.get("penalty"),
                           "n_changepoints": len(r.get("change_points") or [])},
            inputs_hash=inputs_hash,
            input_subset={"target_col": r.get("target_col"),
                          "row_range": [s.get("start"), s.get("end")]},
            code_ref="fantasyai/aurora/math/regimes.py (detect_regimes)",
            code_version=code_version,
            raw_result=s,
            artifact_ref=f"deep_math.json:regimes.stats[{i}]",
        ))
    return out


def _entries_for_motifs(motif_doc: Optional[Dict], inputs_hash: str,
                        code_version: str) -> List[ProvenanceEntry]:
    """One entry per motif."""
    if not motif_doc:
        return []
    motifs = motif_doc.get("motifs") or []
    out: List[ProvenanceEntry] = []
    for i, m in enumerate(motifs):
        if not isinstance(m, dict):
            continue
        out.append(ProvenanceEntry(
            claim_id=f"motif-{i+1:03d}",
            claim_type="motif",
            claim_summary=m.get("title") or f"motif {i+1}: {m.get('kind')}",
            method=f"motif_discovery_{m.get('kind','generic')}",
            method_params={"score": m.get("score")},
            inputs_hash=inputs_hash,
            input_subset=m.get("details") or {},
            code_ref="fantasyai/aurora/discovery/motif_engine.py (discover_motifs)",
            code_version=code_version,
            raw_result=m,
            artifact_ref=f"motif.json:motifs[{i}]",
        ))
    return out


def _entries_for_structure(structure: Optional[Dict], inputs_hash: str,
                           code_version: str) -> List[ProvenanceEntry]:
    """One entry summarizing the structure contract decision."""
    if not structure:
        return []
    eligible = structure.get("eligible") or {}
    # Shape sometimes lives at the top level (n_rows/n_cols) and sometimes
    # in the nested {shape: {rows, cols}} block. Read both.
    shape = structure.get("shape") or {}
    n_rows = structure.get("n_rows") or shape.get("rows")
    n_cols = structure.get("n_cols") or shape.get("cols")
    has_time = bool(structure.get("has_time_axis") or
                    structure.get("time_col") or
                    (structure.get("time") or {}).get("best_time_col"))
    return [ProvenanceEntry(
        claim_id="structure-0001",
        claim_type="structure",
        claim_summary=(
            f"shape {n_rows}×{n_cols}, "
            f"time_axis={'yes' if has_time else 'no'}, "
            f"eligible={eligible}"
        ),
        method="schema_contract_v1",
        method_params={
            "datetime_parse_threshold": 0.6,
            "numeric_density_threshold": 0.6,
        },
        inputs_hash=inputs_hash,
        input_subset={"shape": {"rows": n_rows, "cols": n_cols},
                      "n_columns_described": len(structure.get("columns") or [])},
        code_ref="fantasyai/aurora/contracts/schema_contract.py (build_structure_contract)",
        code_version=code_version,
        raw_result={k: structure.get(k) for k in
                    ("time_col", "target_col", "dt_seconds", "has_time_axis",
                     "eligible", "n_rows", "n_cols", "shape")},
        artifact_ref="structure_contract.json",
    )]


# ----------------------------------------------------------------------
# Public entry: synthesize a full ledger from one run dir
# ----------------------------------------------------------------------

def build_provenance_for_run(run_dir: Path,
                             write: bool = True) -> List[Dict[str, Any]]:
    """Read a run's artifacts and produce ledger entries for every claim.

    If ``write=True``, also writes ``_provenance_ledger.jsonl`` to the run dir.
    Returns the entries as plain dicts (frontend-ready).
    """
    run_dir = Path(run_dir)
    deep      = _read_json(run_dir / "deep_math.json")
    structure = _read_json(run_dir / "structure_contract.json")
    motif_doc = _read_json(run_dir / "motif.json")
    resolved  = _read_json(run_dir / "_RESOLVED_CONTRACT.json")
    tablesel  = _read_json(run_dir / "tableselection.json")

    inputs_hash = _safe_hash_input(run_dir, resolved or tablesel)
    code_version = detect_code_version(run_dir.parents[1] if len(run_dir.parents) >= 2 else None)

    entries: List[ProvenanceEntry] = []
    entries.extend(_entries_for_structure(structure, inputs_hash, code_version))
    entries.extend(_entries_for_anomalies(deep, inputs_hash, code_version))
    entries.extend(_entries_for_forecast(deep, inputs_hash, code_version))
    entries.extend(_entries_for_causal(deep, inputs_hash, code_version))
    entries.extend(_entries_for_physics(deep, inputs_hash, code_version))
    entries.extend(_entries_for_regimes(deep, inputs_hash, code_version))
    entries.extend(_entries_for_motifs(motif_doc, inputs_hash, code_version))

    if write and entries:
        write_ledger(run_dir, entries)

    return [e.to_dict() for e in entries]

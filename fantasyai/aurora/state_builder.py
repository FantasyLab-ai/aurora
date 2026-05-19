"""
state_builder: read the actual artifacts the runner writes today and assemble
the single JSON state the frontend consumes at ``GET /api/state``.

Source-of-truth artifact shapes (verified against real runs in
``outputs/aurora_dataset_runs/``):

* ``_RUN_META.json``         {time_col, target_col, dt_seconds, n_rows, n_cols, numeric_cols}
* ``_RESOLVED_CONTRACT.json`` adds {dataset_key, has_time_axis, ...}
* ``structure_contract.json`` {shape:{rows,cols}, eligible:{...}, columns:[{name,role,dtype,
                               missing_rate,unique_est,datetime_parse_rate}], time:{best_time_col},
                               time_col, target_col, dt_seconds, has_time_axis}
* ``deep_math.json``          {forecasting:{method,horizon,phi,sigma,forecast[],lower[],upper[]},
                               causal_what_if:{treatment_col,target_col,delta,beta_treatment,...},
                               physics:{y_min,y_max,is_monotonic,growth_type,physics_consistency_score,...},
                               physics_discovery:{best:{name,model,params,rmse_test,aic,k},
                                                  runner_ups,all_candidates,...},
                               regimes:{method,change_points[],regime_count,stats:[{start,end,mean,std,min,max}]},
                               extensions:{anomaly_attribution:{points:[{rank,row_id,score,top_feature_drivers:[...]}]}}
                              }
* ``motif.json``              {motifs:[{kind,title,score,details:{col,std,...}}], summary:{n_motifs,...}}
* ``orchestrator.json``       {plan:{actions:[{type,title,why,how}]}, score, warnings}
* ``report.json``             {score:{run_quality,confidence,stability_good,drift_good},
                               events:[{type:"progress"|"warning",msg,payload}],
                               results:[{action,result}], artifacts_present, inputs}
* ``_PLOTS_INDEX.json``       {plots:[{file,title}]}

Rules:
* Every section returns ``{available: bool, ...}``. Frontend renders real data
  when ``available`` is True; otherwise keeps the static mock and shows the
  warning banner.
* No section is allowed to crash the assembly. Per-section try/except.
* No section invents data. Truthful "not available" beats fake sophistication.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ----------------------------------------------------------------------
# IO + scalar helpers
# ----------------------------------------------------------------------

def _read_json(path: Path) -> Optional[Any]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def _safe_int(x: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return default


def _none_unavail(reason: str = "no data") -> Dict[str, Any]:
    return {"available": False, "reason": reason}


def _pick_kind(role: str, dtype: str, dt_parse_rate: float) -> str:
    """Map runner's column metadata to UI's three kinds: time / cat / n."""
    role = (role or "").lower()
    dtype = (dtype or "").lower()
    if dt_parse_rate and dt_parse_rate > 0.5:
        return "time"
    if "datetime" in dtype or "time" in role or role == "time":
        return "time"
    if "object" in dtype or "category" in dtype or "string" in dtype or role in ("categorical", "string", "id"):
        return "cat"
    return "n"


# ----------------------------------------------------------------------
# Dataset / structure
# ----------------------------------------------------------------------

def _build_dataset(meta: Optional[Dict], resolved: Optional[Dict],
                   structure: Optional[Dict], run_dir: Path) -> Dict[str, Any]:
    """Dataset card: filename, rows, cols, size_mb, full path.

    PHASE A1.5 FIX: pick ``dataset_key`` from whichever artifact actually has
    it. Earlier short-circuit (``resolved or meta or structure``) chose the
    first truthy dict, so ``meta`` (which lacks ``dataset_key``) would win
    and ``full_path`` ended up ``None``. That broke worldline rendering in
    SPACETIME view because ``_resolve_dataset_path()`` could not locate the
    CSV. Now we check each source in priority order.
    """
    src = resolved or meta or structure or {}
    rows = _safe_int(src.get("n_rows") or (structure or {}).get("shape", {}).get("rows"), 0) or 0
    cols = _safe_int(src.get("n_cols") or (structure or {}).get("shape", {}).get("cols"), 0) or 0
    # Pick dataset_key from the first artifact that has it (resolved > structure > meta).
    def _pick_key(*dicts):
        for d in dicts:
            if isinstance(d, dict):
                k = d.get("dataset_key")
                if k:
                    return k
        return None
    full_path = _pick_key(resolved, structure, meta)
    raw_name = (full_path
                or (run_dir.name.split("__", 1)[1] if "__" in run_dir.name else run_dir.name))
    # If raw_name happens to be a full path, take the basename.
    name = os.path.basename(str(raw_name).replace("\\", "/")) or str(raw_name)
    return {"available": True, "name": name,
            "path": str(full_path) if full_path else None,
            "rows": rows, "cols": cols, "size_mb": 0.0}


def _build_structure(structure: Optional[Dict], resolved: Optional[Dict]) -> Dict[str, Any]:
    if not structure:
        return _none_unavail("structure_contract.json missing")
    src = resolved or {}
    time_col = structure.get("time_col") or src.get("time_col") or (structure.get("time") or {}).get("best_time_col")
    has_time = bool(structure.get("has_time_axis") or src.get("has_time_axis") or time_col)
    dt = structure.get("dt_seconds") or src.get("dt_seconds")
    cadence = _format_cadence(dt) if dt else ("unknown" if has_time else "—")

    columns_in = structure.get("columns") or []
    columns: List[Dict[str, Any]] = []
    gaps_seen = 0
    # Try to infer units for every column (best-effort; never blocks).
    try:
        from fantasyai.aurora.units import infer_column_unit
    except Exception:
        infer_column_unit = None  # type: ignore
    for c in columns_in:
        if not isinstance(c, dict):
            continue
        kind = _pick_kind(c.get("role", ""), c.get("dtype", ""), c.get("datetime_parse_rate") or 0.0)
        miss = c.get("missing_rate") or 0.0
        if miss > 0:
            gaps_seen += 1
        col_entry: Dict[str, Any] = {
            "name": c.get("name", "?"),
            "kind": kind,
            "fill_pct": (1.0 - float(miss)) * 100.0 if miss is not None else None,
        }
        # Unit inference: parse the column name → SI unit + dimension.
        if infer_column_unit is not None:
            try:
                guess = infer_column_unit(c.get("name", ""))
                if guess.unit and guess.confidence > 0.5:
                    col_entry["unit"] = guess.unit
                    col_entry["dimension"] = guess.short_dim_name
                    col_entry["unit_confidence"] = guess.confidence
            except Exception:
                pass
        columns.append(col_entry)

    # PHASE A1.5: surface dataset_key on the structure block too, so the
    # spacetime/phase-space modules' fallback path (state.structure.dataset_key)
    # can also find the CSV when state.dataset.path is unset for any reason.
    dataset_key = (src.get("dataset_key")
                   or structure.get("dataset_key"))

    return {
        "available": True,
        "time_axis": has_time,
        "time_col": time_col,
        "cadence": cadence,
        "gaps": gaps_seen,           # interpreted as "columns with any missingness"
        "duplicates": 0,             # runner doesn't currently emit this
        "columns": columns,
        "eligibility": structure.get("eligible") or {},
        "dataset_key": dataset_key,
    }


def _format_cadence(dt_seconds: Optional[float]) -> str:
    if dt_seconds is None:
        return "unknown"
    s = float(dt_seconds)
    if s < 60:                     return f"{int(s)}s"
    if s < 3600:                   return f"{int(s/60)}m"
    if s < 86400:                  return f"{int(s/3600)}h"
    return f"{int(s/86400)}d"


# ----------------------------------------------------------------------
# Anomalies — from deep_math.extensions.anomaly_attribution
# ----------------------------------------------------------------------

def _build_anomalies(deep: Optional[Dict],
                       mathr: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """Assemble per-row anomaly findings for the Top Anomalies tile.

    Reads from two possible writer paths and prefers the richer one:

    1. ``deep_math.json["extensions"]["anomaly_attribution"]["points"]`` —
       the original schema produced by ``scripts/pipeline_final.py`` and
       the captured demo (``bin/capture_demo_2026_05_05.py``). Each
       point has ``rank``, ``row_id``, ``score``, ``top_feature_drivers``
       with signed ``robust_z`` and ``direction`` per feature.

    2. ``math_results.json["top_anomalies"]`` — the CURRENT production
       runner's schema (``mathstack_v2.run_mathstack_v2``). Each point
       has ``row_index``, ``score`` (max abs robust_z across columns),
       ``top_contributors`` with unsigned ``abs_robust_z`` per column.

    The current runner does NOT populate ``deep_math.extensions``, so
    without (2) the Top Anomalies tile renders empty even though the
    detector produced canonical findings on disk. Pre-launch contract
    bug — diagnosed in the Task-1 audit. The two schemas are translated
    to a single output shape so the tile and overview-count code paths
    (``_build_anomaly_counts``) agree.

    Threshold (z≥3 warn, z≥5 crit) is preserved per Hampel 1974. Not
    loosened.
    """
    out: List[Dict[str, Any]] = []

    # ---- Primary path: deep_math.extensions.anomaly_attribution.points
    if deep:
        aa = (deep.get("extensions") or {}).get("anomaly_attribution") or {}
        points = aa.get("points") or []
        if points:
            for i, p in enumerate(points):
                if not isinstance(p, dict):
                    continue
                drivers = p.get("top_feature_drivers") or []
                head_driver = drivers[0] if drivers else {}
                column = head_driver.get("feature", "?")
                z = head_driver.get("robust_z")
                if drivers:
                    desc_bits = []
                    for d in drivers[:3]:
                        f = d.get("feature", "?")
                        rz = d.get("robust_z")
                        dirn = d.get("direction", "")
                        if rz is not None:
                            desc_bits.append(f"{f}={dirn} z={rz:+.1f}")
                    description = ", ".join(desc_bits)
                else:
                    description = f"row {p.get('row_id', '?')} flagged"
                s = p.get("score") or 0.0
                if s >= 5.0:
                    severity = "crit"
                elif s >= 3.0:
                    severity = "warn"
                else:
                    severity = "info"
                out.append({
                    "rank": p.get("rank") or len(out) + 1,
                    "when": f"row {p.get('row_id', '?')}",
                    "column": column,
                    "description": description,
                    "z": _safe_float(z),
                    "score": _safe_float(s),
                    "p_value": None,
                    "severity": severity,
                    "drivers": drivers[:3],
                    "provenance_ref": f"anom-{i:04d}",
                    "explainer_ref": {"type": "anomaly", "index": i},
                })
            return out

    # ---- Fallback path: math_results.top_anomalies (current runner).
    # Schema translation:
    #   row_index         → row_id (rendered as "row N" in `when`)
    #   score             → score (max abs robust_z across cols)
    #   top_contributors  → drivers (col → feature, abs_robust_z → robust_z)
    # Direction is not available in this schema (top_contributors carries
    # only the unsigned magnitude), so descriptions show ``|z|=...`` to
    # signal absolute-value semantics rather than fabricating a sign.
    if mathr:
        points = mathr.get("top_anomalies") or []
        for i, p in enumerate(points):
            if not isinstance(p, dict):
                continue
            contribs = p.get("top_contributors") or []
            # Translate to driver shape so downstream consumers (explainer
            # modal, provenance ledger) see a uniform structure.
            drivers: List[Dict[str, Any]] = []
            for c in contribs:
                if not isinstance(c, dict):
                    continue
                drivers.append({
                    "feature": c.get("col", "?"),
                    "robust_z": _safe_float(c.get("abs_robust_z")),
                    "direction": "",   # unknown without the raw row
                })
            head = drivers[0] if drivers else {}
            column = head.get("feature", "?")
            z = head.get("robust_z")
            if drivers:
                desc_bits = []
                for d in drivers[:3]:
                    f = d.get("feature", "?")
                    rz = d.get("robust_z")
                    if rz is not None:
                        desc_bits.append(f"{f}: |z|={rz:.1f}")
                description = ", ".join(desc_bits)
            else:
                description = f"row {p.get('row_index', '?')} flagged"
            s = p.get("score") or 0.0
            if s >= 5.0:
                severity = "crit"
            elif s >= 3.0:
                severity = "warn"
            else:
                severity = "info"
            out.append({
                "rank": len(out) + 1,
                "when": f"row {p.get('row_index', '?')}",
                "column": column,
                "description": description,
                "z": _safe_float(z),
                "score": _safe_float(s),
                "p_value": None,
                "severity": severity,
                "drivers": drivers[:3],
                "provenance_ref": f"anom-{i:04d}",
                "explainer_ref": {"type": "anomaly", "index": i},
            })
        return out

    return out


# ----------------------------------------------------------------------
# Forecast — from deep_math.forecasting
# ----------------------------------------------------------------------

def _build_forecast(deep: Optional[Dict]) -> Dict[str, Any]:
    if not deep:
        return _none_unavail("deep_math.json missing")
    fc = deep.get("forecasting") or {}
    if not fc or fc.get("note") not in (None, "ok"):
        return _none_unavail(fc.get("note") or fc.get("error") or "no forecast")
    yhat = fc.get("forecast") or []
    lo = fc.get("lower") or []
    hi = fc.get("upper") or []
    if not yhat:
        return _none_unavail("forecasting present but empty")

    target = deep.get("target_col") or "?"
    horizon = _safe_int(fc.get("horizon"), len(yhat)) or len(yhat)
    method = (fc.get("method") or "ar1").upper()
    if method == "AR1":
        method = "AR(1)"

    points: List[Dict[str, Any]] = []
    for i, v in enumerate(yhat):
        points.append({
            "step": i + 1,
            "value": _safe_float(v),
            "ci_low": _safe_float(lo[i]) if i < len(lo) else None,
            "ci_high": _safe_float(hi[i]) if i < len(hi) else None,
        })

    # Headline: the maximum predicted value (peak prediction).
    valid = [p for p in points if p["value"] is not None]
    if valid:
        peak = max(valid, key=lambda p: p["value"])
        head_v = peak["value"]
        head_lo = peak["ci_low"]
        head_hi = peak["ci_high"]
    else:
        head_v = head_lo = head_hi = None

    # CRPS-style score is not currently emitted; surface phi as a proxy
    # (closer to 0 = white noise, closer to ±1 = strong AR signal).
    phi = _safe_float(fc.get("phi"))
    sigma = _safe_float(fc.get("sigma"))
    score = abs(phi) if phi is not None else None

    out = {
        "available": True,
        "target": target,
        "horizon": horizon,
        "method": method,
        "headline_value": head_v,
        "headline_ci_low": head_lo,
        "headline_ci_high": head_hi,
        "points": points,
        "score": score,
        "phi": phi,
        "sigma": sigma,
        "warning": {},
        "provenance_ref": "forecast-0001",
    }

    return out


def _attach_advanced_inputs(state: Dict[str, Any],
                              resolved: Optional[Dict],
                              structure: Optional[Dict],
                              deep: Optional[Dict]) -> None:
    """Read the source CSV and attach the numeric-data hooks Brief 2/3
    primitives consume:

      * ``state.dataset_matrix``        — n × p float matrix (NaNs preserved)
      * ``state.dataset_matrix_columns`` — corresponding column names
      * ``state.dataset_dt``             — median Δt (default 1.0)
      * ``state.primary_series``         — target column as a flat list
      * ``state.primary_times``          — time column (or 0..n-1) as flat list

    Cap at 5000 rows for tractability — KSG MI, SINDy, TDA all scale
    poorly. Any failure leaves the state untouched; downstream passes
    skip cleanly.
    """
    try:
        from pathlib import Path as _Path
        import numpy as _np
        import pandas as _pd
    except Exception:
        return
    src = resolved or structure or {}
    dataset_key = src.get("dataset_key")
    if not dataset_key:
        return
    csv_path = _Path(str(dataset_key))
    if not csv_path.exists():
        return
    try:
        df = _pd.read_csv(csv_path, low_memory=False)
    except Exception:
        return
    if df is None or df.empty:
        return
    # v1.1: stash the ORIGINAL CSV row count BEFORE the pipeline-level
    # cap below, so downstream samplers (HMM/wavelet via Fix 1+2) can
    # disclose honestly to the user how their full dataset compares to
    # what each method actually saw. Without this, the v1.1 sampling
    # disclosure would say "HMM analyzed 2000 rows of your 5000-row
    # dataset" on a 100K-row CSV — misleading. With it, the synthesis
    # disclosure can chain the cascade: "of your 100K-row dataset".
    state["dataset_rows_full"] = int(len(df))
    # Cap rows: keep the most recent window so forecasts/SINDy see fresh data.
    MAX_ROWS = 5000
    if len(df) > MAX_ROWS:
        df = df.iloc[-MAX_ROWS:].reset_index(drop=True)

    # Pick the time column (if present) and the target column.
    time_col = (src.get("time_col")
                or (structure or {}).get("time_col")
                or ((structure or {}).get("time") or {}).get("best_time_col"))
    target_col = ((deep or {}).get("target_col")
                   or src.get("target_col"))

    # Build a numeric-only matrix excluding the time column.
    numeric_cols = []
    matrix_cols = []
    for c in df.columns:
        if time_col and str(c) == str(time_col):
            continue
        col_data = _pd.to_numeric(df[c], errors="coerce")
        if col_data.notna().sum() < 4:
            continue
        numeric_cols.append(col_data.to_numpy(dtype=float))
        matrix_cols.append(str(c))
    if numeric_cols:
        matrix = _np.column_stack(numeric_cols)
        state["dataset_matrix"] = matrix.tolist()
        state["dataset_matrix_columns"] = matrix_cols

    # Primary series: target column if numeric; otherwise first numeric column.
    primary = None
    primary_name = None
    if target_col and target_col in df.columns:
        try:
            v = _pd.to_numeric(df[target_col], errors="coerce")
            if v.notna().sum() >= 4:
                primary = v.to_numpy(dtype=float)
                primary_name = str(target_col)
        except Exception:
            pass
    if primary is None and matrix_cols:
        primary = numeric_cols[0]
        primary_name = matrix_cols[0]
    if primary is not None:
        state["primary_series"] = primary.tolist()
        state["primary_series_name"] = primary_name

    # Times — try to parse the time column to a numeric scalar; fall back
    # to integer indices.
    times: Optional[_np.ndarray] = None
    if time_col and time_col in df.columns:
        try:
            ts = _pd.to_datetime(df[time_col], errors="coerce")
            if ts.notna().sum() >= max(4, int(0.5 * len(df))):
                # Convert to seconds-since-epoch (float).
                times = (ts.astype("int64").to_numpy(dtype=float) / 1e9)
            else:
                # Maybe it's already numeric.
                vn = _pd.to_numeric(df[time_col], errors="coerce")
                if vn.notna().sum() >= 4:
                    times = vn.to_numpy(dtype=float)
        except Exception:
            times = None
    if times is None and primary is not None:
        times = _np.arange(len(primary), dtype=float)
    if times is not None:
        state["primary_times"] = times.tolist()

    # dt — prefer the runner-resolved cadence; otherwise median diff.
    dt = src.get("dt_seconds") or (structure or {}).get("dt_seconds")
    if dt is None and times is not None and len(times) > 1:
        diffs = _np.diff(_np.sort(times))
        diffs = diffs[_np.isfinite(diffs) & (diffs > 0)]
        if diffs.size > 0:
            dt = float(_np.median(diffs))
    state["dataset_dt"] = float(dt) if dt else 1.0


def _attach_ml_ensemble(forecast_block: Dict[str, Any],
                         deep: Optional[Dict],
                         resolved: Optional[Dict],
                         structure: Optional[Dict]) -> None:
    """Run KNN-window + AR(2) on the source CSV, attach to forecast_block.

    The runner's AR(1) is the baseline; this adds two more transparent
    forecasters with their full math blocks for comparison. Silent on failure.
    """
    if not forecast_block or not forecast_block.get("available"):
        return
    try:
        from pathlib import Path as _Path
        import numpy as _np
        import pandas as _pd
        from fantasyai.aurora.ml_layer import transparent_forecast

        src = resolved or structure or {}
        dataset_key = src.get("dataset_key")
        target_col = (deep or {}).get("target_col") or src.get("target_col")
        if not dataset_key or not target_col:
            return
        csv_path = _Path(str(dataset_key))
        if not csv_path.exists():
            return
        df = _pd.read_csv(csv_path, low_memory=False)
        cols_lower = {str(c).lower(): c for c in df.columns}
        tcol = (target_col if target_col in df.columns
                else cols_lower.get(str(target_col).lower()))
        if tcol is None:
            return
        y = _pd.to_numeric(df[tcol], errors="coerce").to_numpy(dtype=float)
        y = y[_np.isfinite(y)]
        if y.size < 80:
            return
        # KNN forecaster is O(n²); cap at 10K rows for responsiveness. The
        # tiered orchestrator runs the full pipeline at higher tiers.
        ML_MAX_ROWS = 10000
        if y.size > ML_MAX_ROWS:
            y = y[-ML_MAX_ROWS:]   # use most-recent window for forecast realism
        horizon = int(forecast_block.get("horizon") or 10)
        ensemble = transparent_forecast(y, horizon=horizon)
        forecast_block["alternate_methods"] = ensemble
    except Exception:
        # ML enrichment never blocks the AR(1) baseline.
        pass


# ----------------------------------------------------------------------
# Causal what-ifs — from deep_math.causal_what_if
# ----------------------------------------------------------------------

def _build_causal(deep: Optional[Dict]) -> Dict[str, Any]:
    if not deep:
        return _none_unavail("deep_math.json missing")
    cw = deep.get("causal_what_if") or {}
    if not cw or cw.get("note") not in (None, "ok"):
        return _none_unavail(cw.get("note") or cw.get("error") or "no causal output")

    treatment = cw.get("treatment_col")
    target = cw.get("target_col")
    delta = _safe_float(cw.get("delta"))
    beta = _safe_float(cw.get("beta_treatment"))
    effect = _safe_float(cw.get("estimated_effect"))

    if not treatment or not target or beta is None:
        return _none_unavail("causal artifact present but missing keys")

    # Today the runner emits a single intervention. Wrap it as the items[] the UI expects,
    # with a forward-compatible shape so future multi-intervention runs slot in.
    items = [{
        "if_clause": f"{treatment} increases by {delta if delta is not None else 1.0}",
        "then_clause": f"target {target} changes by ~{effect:+.3g}" if effect is not None else f"effect on {target}",
        "effect_value": effect,
        "ci_low": None,
        "ci_high": None,
    }]
    return {
        "available": True,
        "items": items,
        "method": cw.get("method") or "linear OLS",
        "n": _safe_int(cw.get("n")),
        "provenance_ref": "causal-0001",
        "explainer_ref": {"type": "causal", "index": 0},
    }


# ----------------------------------------------------------------------
# Calculus signature — compute on-demand from the source CSV
# ----------------------------------------------------------------------

def _compute_calculus_block(deep: Optional[Dict],
                            resolved: Optional[Dict],
                            structure: Optional[Dict]) -> Optional[Dict[str, Any]]:
    """Read the source CSV → derive (t, y) → compute calculus signature.

    Returns a dict ready for the frontend, or None when the data isn't
    accessible (e.g. dataset_key missing, file gone, no numeric target).
    Failure is silent — calculus is enrichment, never blocking.
    """
    try:
        from fantasyai.aurora.calculus import calculus_signature
    except Exception:
        return None

    src = resolved or structure or {}
    dataset_key = src.get("dataset_key")
    target_col = (deep or {}).get("target_col") or src.get("target_col")
    time_col = (deep or {}).get("time_col") or src.get("time_col")
    if not dataset_key or not target_col:
        return None
    csv_path = Path(str(dataset_key))
    if not csv_path.exists() or not csv_path.is_file():
        return None
    try:
        import pandas as pd
        import numpy as np
        df = pd.read_csv(csv_path, low_memory=False)
    except Exception:
        return None
    # Case-insensitive column resolution — the runner normalizes column names
    # (e.g. "USA" → "usa") but the original CSV often keeps the original case.
    cols_lower = {str(c).lower(): c for c in df.columns}
    target_resolved = (target_col if target_col in df.columns
                       else cols_lower.get(str(target_col).lower()))
    if target_resolved is None:
        return None
    target_col = target_resolved

    try:
        y = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)
        # Time axis: if a time_col is named and parses, use it; otherwise row index.
        t = None
        time_resolved = None
        if time_col:
            time_resolved = (time_col if time_col in df.columns
                             else cols_lower.get(str(time_col).lower()))
        if time_resolved and time_resolved in df.columns:
            try:
                ts = pd.to_datetime(df[time_resolved], errors="coerce")
                if ts.notna().sum() > 0.5 * len(ts):
                    t = (ts - ts.min()).dt.total_seconds().to_numpy(dtype=float)
            except Exception:
                t = None
            if t is None:
                # Numeric time column?
                t_num = pd.to_numeric(df[time_resolved], errors="coerce").to_numpy(dtype=float)
                if np.isfinite(t_num).sum() > 0.5 * len(t_num):
                    t = t_num
        # If still no time axis, see if the FIRST column looks like a year/numeric
        # axis (common with World Bank style "year, value" CSVs).
        if t is None and len(df.columns) >= 2:
            cand = df.columns[0]
            if cand != target_col:
                t_try = pd.to_numeric(df[cand], errors="coerce").to_numpy(dtype=float)
                if np.isfinite(t_try).sum() > 0.5 * len(t_try):
                    t = t_try
        if t is None:
            t = np.arange(len(y), dtype=float)

        # Drop pairs where either is non-finite.
        mask = np.isfinite(t) & np.isfinite(y)
        if mask.sum() < 8:
            return None
        # Cap the input length — calculus signature scales with n, but FFT
        # for the dominant frequency is O(n log n). For multi-million-row
        # series we evenly subsample to keep state-build fast.
        CALC_MAX_POINTS = 50_000
        t_use = t[mask]
        y_use = y[mask]
        if t_use.size > CALC_MAX_POINTS:
            idx = np.linspace(0, t_use.size - 1, CALC_MAX_POINTS, dtype=int)
            t_use = t_use[idx]
            y_use = y_use[idx]
        sig = calculus_signature(t_use, y_use)
        d = sig.to_dict()
        # Trim noisy float precision for display.
        for k, v in list(d.items()):
            if isinstance(v, float):
                d[k] = round(v, 6)
        d["available"] = True
        d["target_col"] = target_col
        d["time_col"] = time_col
        d["n_points"] = int(t_use.size)
        return d
    except Exception:
        return None


# ----------------------------------------------------------------------
# Physics — from deep_math.physics + physics_discovery
# ----------------------------------------------------------------------

def _build_physics(deep: Optional[Dict], run_dir: Optional[Path] = None,
                   resolved: Optional[Dict] = None,
                   structure: Optional[Dict] = None) -> Dict[str, Any]:
    # Compute calculus signature up-front — it's pure data analysis from the
    # source CSV, doesn't require physics_discovery to have run. We attach it
    # to whatever shape we end up returning so the user always gets calculus
    # stats when there's a numeric target column accessible.
    calculus_block = _compute_calculus_block(deep, resolved, structure)

    if not deep:
        if calculus_block:
            return {"available": True, "discovered_law": None, "checks": [],
                    "invariants": [], "calculus": calculus_block,
                    "reason": "deep_math.json missing — calculus only"}
        return _none_unavail("deep_math.json missing")
    phys_diag = deep.get("physics") or {}
    pd_ = deep.get("physics_discovery") or {}
    invariants_raw = deep.get("physics_invariants") or {}
    consistency = _safe_float(pd_.get("physics_consistency_score") or
                              phys_diag.get("physics_consistency_score"))
    # Match the fitted candidate against the priors catalogue.
    prior_matches: List[Dict[str, Any]] = []
    try:
        from fantasyai.aurora.priors import match_physics_fit
        best_cand = pd_.get("best") or {}
        if best_cand:
            for m in match_physics_fit(best_cand, max_results=3):
                prior_matches.append(m.to_dict())
    except Exception:
        prior_matches = []

    # Dimensional consistency check: if we inferred units for the target +
    # time variables, declare what dimensions the fitted parameters MUST
    # carry. Surfaced as the new "DIMENSIONS" check tile in the physics UI.
    dimensional_check: Optional[Dict[str, Any]] = None
    try:
        from fantasyai.aurora.units import infer_column_unit, dimensional_consistency_check
        target_col = pd_.get("target_col") or deep.get("target_col")
        time_col = pd_.get("time_col") or deep.get("time_col")
        target_guess = infer_column_unit(target_col) if target_col else None
        time_guess = infer_column_unit(time_col) if time_col else None
        target_dim = target_guess.dimension if target_guess and target_guess.confidence > 0.5 else None
        time_dim = time_guess.dimension if time_guess and time_guess.confidence > 0.5 else None
        runner_form = (pd_.get("best") or {}).get("name") or ""
        if runner_form:
            dc = dimensional_consistency_check(runner_form, target_dim, time_dim,
                                                fitted_params=(pd_.get("best") or {}).get("params") or {})
            dimensional_check = dc.to_dict()
    except Exception:
        dimensional_check = None

    # Best discovered law (fitted model).
    best = pd_.get("best") or {}
    if not best:
        # Even with no fitted law, surface the diagnostic signature so the tile isn't blank.
        if not phys_diag:
            # Still emit calculus if we have it — that alone is a useful tile.
            if calculus_block:
                return {"available": True, "discovered_law": None, "checks": [],
                        "invariants": [], "calculus": calculus_block,
                        "all_candidates": [],
                        "reason": "no fitted law (e.g. n < 200) — calculus only"}
            return _none_unavail("no physics output")
        law = {
            "name": "diagnostic_only",
            "equation": f"growth={phys_diag.get('growth_type','?')}, monotonic={phys_diag.get('is_monotonic','?')}",
            "params": {},
            "rmse": None,
            "r2": None,
        }
    else:
        law = {
            "name": best.get("name", "?"),
            "equation": best.get("model", ""),
            "params": best.get("params", {}),
            "rmse": _safe_float(best.get("rmse_test") or best.get("rmse_full") or best.get("rmse")),
            "aic": _safe_float(best.get("aic")),
            "k": _safe_int(best.get("k")),
            "r2": None,  # runner doesn't emit R² yet
        }

    # Constraint checks (from either physics or physics_discovery, both have 'constraints').
    constraints = pd_.get("constraints") or phys_diag.get("constraints") or {}
    checks: List[Dict[str, Any]] = []
    if isinstance(constraints, dict):
        # Common keys observed:
        for key in ("conservation", "dimensional", "energy", "symmetry",
                    "positivity_violation_rate", "monotonicity_score",
                    "physics_consistency_score", "boundedness_score"):
            v = constraints.get(key)
            if v is not None:
                # Some are "rate of violations" — flip to a 0..1 health score.
                if "violation" in key and isinstance(v, (int, float)):
                    score = max(0.0, 1.0 - float(v))
                    checks.append({"name": key.replace("_", " ").upper().replace("RATE", "OK"),
                                   "value": _safe_float(score)})
                else:
                    checks.append({"name": key.replace("_", " ").upper(),
                                   "value": _safe_float(v)})
    if not checks and consistency is not None:
        checks.append({"name": "CONSISTENCY", "value": consistency})

    # Invariants: prefer descriptive, fall back to stats.
    invariants: List[Dict[str, Any]] = []
    if isinstance(invariants_raw, dict):
        for k, v in invariants_raw.items():
            if k in ("note", "error"):
                continue
            if isinstance(v, (int, float)):
                status = "ok" if (k == "finite_fraction" and v > 0.95) or \
                                 (k == "negative_fraction" and v < 0.5) else "warn"
                invariants.append({
                    "text": f"{k.replace('_',' ')} = {v:.3f}",
                    "status": status,
                })
    # Honest annotations from the diagnostic stats.
    if phys_diag.get("growth_type"):
        invariants.append({"text": f"growth pattern: {phys_diag['growth_type']}", "status": "ok"})
    if phys_diag.get("is_monotonic") is True:
        invariants.append({"text": "series is monotonic", "status": "ok"})
    elif phys_diag.get("dy_sign_changes") is not None:
        invariants.append({
            "text": f"derivative sign changes {phys_diag['dy_sign_changes']}×",
            "status": "ok" if phys_diag['dy_sign_changes'] < 50 else "warn",
        })

    # Symbolic discovery + conservation laws — opt-in, reads cached results
    # from a prior /api/symbolic_regression call. Empty when not yet computed.
    symbolic_relations: List[Dict[str, Any]] = []
    discovered_invariants: List[Dict[str, Any]] = []
    if run_dir is not None:
        try:
            sd_path = Path(run_dir) / "symbolic_discovery.json"
            if sd_path.exists():
                sd = _read_json(sd_path) or {}
                symbolic_relations = sd.get("symbolic_relations") or []
                discovered_invariants = sd.get("invariants") or []
        except Exception:
            pass

    # (Calculus signature was computed up-front; it's reused below.)

    # Surface the runner's full candidate list so the UI can show all 8 forms tried,
    # not just the top 3 runner-ups. Each entry: {name, rmse_test, aic, ok, error?}.
    all_candidates: List[Dict[str, Any]] = []
    for c in (pd_.get("all_candidates") or []):
        if not isinstance(c, dict):
            continue
        all_candidates.append({
            "name": c.get("name"),
            "model": c.get("model"),
            "rmse_test": _safe_float(c.get("rmse_test")),
            "aic": _safe_float(c.get("aic")),
            "k": _safe_int(c.get("k")),
            "ok": c.get("ok", c.get("rmse_test") is not None),
            "error": c.get("error"),
        })

    # v2.0 — surface coupled-system + PDE detection blocks. Each is a small
    # summary dict the UI can render conditionally; absent / errored states
    # are kept verbose so the user knows what was tried.
    coupled = pd_.get("coupled_systems") or {}
    coupled_summary: Optional[Dict[str, Any]] = None
    if isinstance(coupled, dict) and coupled.get("note") == "ok" and coupled.get("best"):
        b = coupled["best"]
        coupled_summary = {
            "available": True,
            "name": b.get("name"),
            "model": b.get("model"),
            "params": b.get("params") or {},
            "rmse": _safe_float(b.get("rmse")),
            "x_col": coupled.get("x_col"),
            "y_col": coupled.get("y_col"),
            "all_candidates": [
                {"name": c.get("name"), "rmse": _safe_float(c.get("rmse")),
                 "ok": bool(c.get("ok")), "error": c.get("error")}
                for c in (coupled.get("all_candidates") or [])
            ],
        }
    elif isinstance(coupled, dict) and coupled.get("note") in ("skipped", "error"):
        coupled_summary = {"available": False,
                           "reason": coupled.get("reason") or coupled.get("error") or "n/a"}

    pde = pd_.get("pde_detection") or {}
    pde_summary: Optional[Dict[str, Any]] = None
    if isinstance(pde, dict) and pde.get("note") == "ok" and pde.get("best"):
        b = pde["best"]
        pde_summary = {
            "available": True,
            "name": b.get("name"),
            "model": b.get("model"),
            "params": b.get("params") or {},
            "rmse": _safe_float(b.get("rmse")),
            "r2": _safe_float(b.get("r2")),
            "n_t": pde.get("n_t"),
            "n_x": pde.get("n_x"),
            "spatial_columns": pde.get("spatial_columns") or [],
        }
    elif isinstance(pde, dict) and pde.get("note") in ("skipped", "error"):
        pde_summary = {"available": False,
                       "reason": pde.get("reason") or pde.get("error") or "n/a"}

    return {
        "available": True,
        "discovered_law": law,
        "checks": checks[:4],
        "invariants": invariants[:5],
        "discovered_invariants": discovered_invariants[:5],
        "symbolic_relations": symbolic_relations[:5],
        "consistency_score": consistency,
        "runner_ups": [
            {"name": r.get("name"), "rmse": _safe_float(r.get("rmse_test") or r.get("rmse_full"))}
            for r in (pd_.get("runner_ups") or [])[:3] if isinstance(r, dict)
        ],
        "all_candidates": all_candidates,
        "prior_matches": prior_matches,
        "dimensional_check": dimensional_check,
        "calculus": calculus_block,
        "coupled_systems": coupled_summary,
        "pde_detection": pde_summary,
        "provenance_ref": "physics-best",
        "explainer_ref": {"type": "physics", "index": 0},
    }


# ----------------------------------------------------------------------
# Regimes — from deep_math.regimes
# ----------------------------------------------------------------------

def _build_regimes(deep: Optional[Dict]) -> List[Dict[str, Any]]:
    if not deep:
        return []
    r = deep.get("regimes") or {}
    if not r or r.get("note") not in (None, "ok"):
        return []
    stats = r.get("stats") or []
    if not stats:
        return []

    # Auto-label by mean rank: lowest-mean = "CALM/LOW", highest = "VOLATILE/HIGH",
    # middle by std relative to overall.
    means = [_safe_float(s.get("mean")) for s in stats if isinstance(s, dict)]
    stds = [_safe_float(s.get("std")) for s in stats if isinstance(s, dict)]
    overall_mean = sum(m for m in means if m is not None) / max(1, sum(1 for m in means if m is not None))
    out: List[Dict[str, Any]] = []
    for i, s in enumerate(stats):
        if not isinstance(s, dict):
            continue
        m = _safe_float(s.get("mean"))
        sd = _safe_float(s.get("std"))
        # Simple labelling rule.
        if sd is not None and stds and sd > 1.5 * (sum(x for x in stds if x is not None) / max(1, len(stds))):
            label = "VOLATILE"
        elif m is not None and m < overall_mean - 0.5 * (sd or 0.0):
            label = "LOW"
        elif m is not None and m > overall_mean + 0.5 * (sd or 0.0):
            label = "HIGH"
        else:
            label = "STABLE"
        out.append({
            "label": label,
            "start": str(s.get("start")),
            "end": str(s.get("end")),
            "mean": m,
            "std": sd,
            "min": _safe_float(s.get("min")),
            "max": _safe_float(s.get("max")),
            "weight": _safe_float(s.get("n")),
            "provenance_ref": f"regime-{i+1:03d}",
            "explainer_ref": {"type": "regime", "index": i},
        })
    return out


# ----------------------------------------------------------------------
# Motifs — from motif.json
# ----------------------------------------------------------------------

def _build_motifs(motif_doc: Optional[Dict],
                  deep: Optional[Dict] = None,
                  resolved: Optional[Dict] = None,
                  structure: Optional[Dict] = None) -> List[Dict[str, Any]]:
    # Atom 1.1: emit ALL motifs the runner found. Display layer caps surface.
    out: List[Dict[str, Any]] = []
    motifs = (motif_doc or {}).get("motifs") or []
    for i, m in enumerate(motifs):
        if not isinstance(m, dict):
            continue
        kind = m.get("kind", "")
        details = m.get("details") or {}
        # Build a human-friendly name and description.
        if kind == "high_volatility":
            col = details.get("col", "?")
            std = _safe_float(details.get("std"))
            name = f"High variability: {col}"
            description = f"std = {std:.3g}" if std is not None else ""
        elif kind == "high_correlation":
            a = details.get("col_a") or details.get("a")
            b = details.get("col_b") or details.get("b")
            corr = _safe_float(details.get("corr"))
            name = f"{a} ↔ {b}"
            description = f"ρ = {corr:.3f}" if corr is not None else ""
        elif kind == "monotonic_run":
            col = details.get("col", "?")
            name = f"Monotonic: {col}"
            description = m.get("title", "")
        else:
            name = m.get("title") or kind or f"M{i+1}"
            description = ""
        out.append({
            "id": i,
            "name": name,
            "kind": kind,
            "count": _safe_int(details.get("count")),
            "score": _safe_float(m.get("score")),
            "description": description or m.get("title", ""),
            "details": details,
            "provenance_ref": f"motif-{i+1:03d}",
        })

    # ---- v2.0: matrix-profile motifs from the source CSV.
    # Stricter than the runner's ad-hoc motifs: distance to the nearest
    # non-trivial neighbour is rigorously defined. Adds both motifs (recurring
    # patterns) and discords (uniquely-shaped windows / anomalies).
    try:
        from fantasyai.aurora.matrix_profile import compute_motifs_and_discords
        src = resolved or structure or {}
        dataset_key = src.get("dataset_key")
        target_col = ((deep or {}).get("target_col") or src.get("target_col"))
        if dataset_key and target_col:
            csv_path = Path(str(dataset_key))
            if csv_path.exists():
                import pandas as pd
                import numpy as np
                df = pd.read_csv(csv_path, low_memory=False)
                cols_lower = {str(c).lower(): c for c in df.columns}
                tcol = (target_col if target_col in df.columns
                        else cols_lower.get(str(target_col).lower()))
                if tcol is not None:
                    y = pd.to_numeric(df[tcol], errors="coerce").to_numpy(dtype=float)
                    y = y[np.isfinite(y)]
                    # Matrix profile is O(n²·log n). Cap the input to keep
                    # state-building responsive for large files; this is
                    # only a *summary* — tiered analysis covers the full data.
                    MP_MAX_ROWS = 5000
                    if y.size > MP_MAX_ROWS:
                        # Take an evenly-spaced subsample so we still see
                        # patterns across the series, not just the tail.
                        idx = np.linspace(0, y.size - 1, MP_MAX_ROWS, dtype=int)
                        y = y[idx]
                    if y.size >= 40:
                        mp = compute_motifs_and_discords(
                            y, n_motifs=3, n_discords=3
                        )
                        if mp.get("available"):
                            for j, mm in enumerate(mp.get("motifs") or []):
                                out.append({
                                    "id": len(out),
                                    "name": f"Matrix-profile motif #{mm.get('rank')} ({tcol})",
                                    "kind": "matrix_profile_motif",
                                    "count": 2,
                                    "score": _safe_float(mm.get("distance")),
                                    "description": (
                                        f"Window of {mm.get('window')} samples recurs at "
                                        f"rows {mm.get('start_a')} and {mm.get('start_b')}; "
                                        f"z-distance = {mm.get('distance'):.3g}."
                                    ) if mm.get('distance') is not None else "",
                                    "details": {**mm, "col": tcol,
                                                "method": "matrix_profile"},
                                    "provenance_ref": f"motif-mp-{j+1:03d}",
                                })
                            for j, dd in enumerate(mp.get("discords") or []):
                                out.append({
                                    "id": len(out),
                                    "name": f"Discord (anomalous window) at row {dd.get('start')}",
                                    "kind": "matrix_profile_discord",
                                    "count": 1,
                                    "score": _safe_float(dd.get("distance")),
                                    "description": (
                                        f"Window of {dd.get('window')} samples is uniquely "
                                        f"shaped (z-distance to nearest neighbour "
                                        f"{dd.get('distance'):.3g})."
                                    ) if dd.get('distance') is not None else "",
                                    "details": {**dd, "col": tcol,
                                                "method": "matrix_profile"},
                                    "provenance_ref": f"discord-mp-{j+1:03d}",
                                })
    except Exception:
        # Matrix profile is enrichment — never block state assembly.
        pass

    return out


# ----------------------------------------------------------------------
# Overview tile (synthesized from everything)
# ----------------------------------------------------------------------

def _build_anomaly_counts(deep: Optional[Dict],
                            anomalies: List[Dict]) -> Dict[str, int]:
    """Resolve the HONEST anomaly counts from the runner's
    ``anomaly_attribution`` block when available, falling back to
    ``len(anomalies)`` for legacy artifacts.

    The runner's ``points[]`` is bounded at top_k (default 10) for
    performance — its ``n_total`` field carries the true count of rows
    above the significance threshold. Always prefer that when present.
    """
    aa = ((deep or {}).get("extensions") or {}).get("anomaly_attribution") or {}
    n_total = aa.get("n_total")
    n_crit  = aa.get("n_critical")
    n_warn  = aa.get("n_warn")
    n_info  = aa.get("n_info")
    n_returned = aa.get("n_points_returned") or len(anomalies)
    # Fall back to the truncated `anomalies` list when the runner predates
    # the launch-fix (older deep_math.json files don't have these fields).
    if n_total is None:
        n_total = len(anomalies)
        n_crit = sum(1 for a in anomalies if a.get("severity") == "crit")
        n_warn = sum(1 for a in anomalies if a.get("severity") == "warn")
        n_info = max(0, n_total - n_crit - n_warn)
    return {
        "n_total":    int(n_total or 0),
        "n_critical": int(n_crit or 0),
        "n_warn":     int(n_warn or 0),
        "n_info":     int(n_info or 0),
        "n_detailed": int(n_returned or 0),
    }


def _build_overview(structure: Dict, anomalies: List[Dict],
                    regimes: List[Dict], motifs: List[Dict],
                    forecast: Dict, physics: Dict,
                    report: Optional[Dict],
                    deep: Optional[Dict] = None) -> Dict[str, Any]:
    score = (report or {}).get("score") or {}
    counts = _build_anomaly_counts(deep, anomalies)
    return {
        "available": True,
        "health": _safe_float(score.get("run_quality") or score.get("confidence")),
        "confidence": _safe_float(score.get("confidence")),
        "stability": _safe_float(score.get("stability_good")),
        "drift": _safe_float(score.get("drift_good")),
        # HONEST anomaly count — reflects all rows above the significance
        # threshold, NOT the top-10 detail cap.
        "anomalies_count": counts["n_total"],
        "critical_anomalies": counts["n_critical"],
        "anomalies_warn": counts["n_warn"],
        "anomalies_info": counts["n_info"],
        # Detail-only count: how many anomalies have full driver attribution
        # (i.e. how many can be drilled into via the EXPLAIN modal).
        "anomalies_detailed": counts["n_detailed"],
        "regimes_count": len(regimes),
        "last_regime": regimes[-1].get("label") if regimes else None,
        "motifs_count": len(motifs),
        "top_motif": (motifs[0].get("name") if motifs else None),
        "forecast_score": forecast.get("score") if isinstance(forecast, dict) else None,
        "physics_score": physics.get("consistency_score") if isinstance(physics, dict) else None,
    }


# ----------------------------------------------------------------------
# Findings — synthesized from artifacts + report.events
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Extended methods (v1.2 Stream 1.2) — VAR, DTW, BOCPD, Robust PCA, EMD,
# Kalman, Spectral Entropy. Each method's runner writes an Aurora-shaped
# finding; we expose them here as per-method tile data the frontend can
# render in the ADVANCED METHODS grid.
# ----------------------------------------------------------------------

# Display labels for the 7 v1.2 extended methods. Kept here (not in the
# frontend) so the labels stay in lockstep with the backend method names.
EXTENDED_METHOD_LABELS = {
    "var":              "VAR · multivariate",
    "dtw":              "DTW · pattern match",
    "bocpd":            "BOCPD · change points",
    "robust_pca":       "Robust PCA · L+S",
    "emd":              "EMD · intrinsic modes",
    "kalman":           "Kalman · state filter",
    "spectral_entropy": "Spectral entropy",
}

# Canonical ordering for the frontend grid (matches the v1.2 brief).
EXTENDED_METHOD_ORDER = [
    "var", "dtw", "bocpd", "robust_pca",
    "emd", "kalman", "spectral_entropy",
]


def _build_extended_methods(ext_doc: Optional[Dict]) -> Dict[str, Any]:
    """Project ``extended_methods.json`` into per-method tile data.

    The runner script writes ``extended_methods.json`` with shape::

        {"findings": [<Aurora finding>, ...],
         "per_method": {"var": {"status": "fit", "elapsed_s": 1.23}, ...},
         "n_methods_run": 7,
         "n_findings": 7}

    Frontend wants one entry per method with the headline number already
    extracted (so it can render the tile without re-parsing the
    description text). We pull that from each finding's ``evidence`` block.
    """
    if not isinstance(ext_doc, dict):
        return _none_unavail("extended_methods.json malformed")
    findings = ext_doc.get("findings") or []
    per_method_raw = ext_doc.get("per_method") or {}
    if not isinstance(findings, list):
        findings = []

    # Index findings by method name. Each method emits exactly one
    # finding per run (fit / skipped / failed), so the lookup is 1:1.
    findings_by_method: Dict[str, Dict[str, Any]] = {}
    for f in findings:
        if not isinstance(f, dict):
            continue
        m = str(f.get("method") or "").lower()
        if m and m not in findings_by_method:
            findings_by_method[m] = f

    per_method: Dict[str, Dict[str, Any]] = {}
    n_fit = 0
    for name in EXTENDED_METHOD_ORDER:
        f = findings_by_method.get(name) or {}
        ev = f.get("evidence") or {}
        meta = per_method_raw.get(name) or {}
        status = str(ev.get("status") or meta.get("status") or "missing").lower()
        if status == "fit":
            n_fit += 1
        per_method[name] = {
            "label":        EXTENDED_METHOD_LABELS.get(name, name),
            "method":       name,
            "status":       status,
            "title":        f.get("title") or "",
            "description":  f.get("description") or "",
            "severity":     f.get("severity") or "info",
            "confidence":   f.get("confidence"),
            "claim_id":     f.get("claim_id"),
            "elapsed_s":    meta.get("elapsed_s"),
            "error":        meta.get("error"),
            # Pass through the structured evidence dict so the frontend
            # can render method-specific headline numbers without
            # re-parsing prose. Truncated to a safe subset for size.
            "evidence":     _trim_evidence(ev),
        }
    return {
        "available":      bool(findings),
        "n_methods_run":  int(ext_doc.get("n_methods_run") or len(EXTENDED_METHOD_ORDER)),
        "n_findings":     int(ext_doc.get("n_findings") or len(findings)),
        "n_fit":          n_fit,
        "per_method":     per_method,
        "order":          list(EXTENDED_METHOD_ORDER),
    }


# Evidence blobs can be large (e.g., full IMF arrays from EMD). The
# frontend only needs the scalar headline + a few small lists, so we
# keep a per-key whitelist and truncate the rest.
_EVIDENCE_SAFE_KEYS = {
    # universal
    "status", "reason", "error", "n_obs", "n_vars", "elapsed_s", "target_col",
    # var
    "chosen_lag", "target_cols", "top_cross_coupling",
    "forecast_horizon", "forecast_summary",
    # dtw
    "window", "most_similar", "most_dissimilar",
    # bocpd
    "threshold", "hazard", "change_points",
    # robust_pca
    "low_rank_estimate", "n_outlier_rows", "lam_used",
    "iterations", "converged", "top_outlier_rows",
    # emd
    "n_imfs", "sift_iterations", "imf_stats", "residual_range",
    # kalman
    "noise_reduction_fraction", "forecast",
    # spectral_entropy
    "global_spectral_entropy", "regime_class",
    "window_results", "biggest_window_jump",
}


def _trim_evidence(ev: Any) -> Dict[str, Any]:
    """Keep only the whitelisted, JSON-serializable subset of evidence."""
    if not isinstance(ev, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in ev.items():
        if k not in _EVIDENCE_SAFE_KEYS:
            continue
        # Drop pathologically-large lists (defensive).
        if isinstance(v, list) and len(v) > 64:
            out[k] = v[:64]
        else:
            out[k] = v
    return out


def _build_findings(anomalies: List[Dict], forecast: Dict, regimes: List[Dict],
                    causal: Dict, physics: Dict, motifs: List[Dict],
                    report: Optional[Dict],
                    orchestrator: Optional[Dict]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    # Atom 1.1: filter by severity FIRST, sort, THEN truncate.
    # Old code: anomalies[:5] then severity filter. This silently dropped
    # critical anomalies whenever the top-5 ranked entries happened to be
    # info-level. Fix: filter first → sort by (severity_rank, score) →
    # take top 5. Also record n_total_at_this_severity so the narrative
    # can speak honestly ("5 of 47 critical surfaced").
    crit_warn = [a for a in anomalies
                  if a.get("severity") in ("crit", "warn")]
    sev_rank = {"crit": 0, "warn": 1}
    crit_warn.sort(key=lambda a: (sev_rank.get(a.get("severity"), 9),
                                    -float(a.get("score") or 0.0)))
    n_total_critwarn = len(crit_warn)
    n_total_anomalies = len(anomalies)

    # 1. Top anomalies — top 5 by severity then score.
    for a in crit_warn[:5]:
        sev = a.get("severity", "info")
        score = a.get("score")
        z = a.get("z")
        findings.append({
            "severity": sev,
            "confidence": min(1.0, (score or 0) / 8.0) if score else 0.6,
            "title": f"Anomaly in {a.get('column','?')} at {a.get('when','?')}",
            "description": a.get("description") or "",
            "method": "iso-forest + robust-Z",
            "z": z,
            "actions": ["VIEW EVIDENCE", "PIN ★", "EXPLAIN"],
            # Honest counts so the narrative + UI can say "5 of 47 surfaced".
            "n_total_at_this_severity": n_total_critwarn,
            "n_total_anomalies": n_total_anomalies,
        })

    # 2. Regime shifts (one finding per change-point, latest first).
    if len(regimes) > 1:
        latest = regimes[-1]
        findings.append({
            "severity": "info",
            "confidence": 0.7,
            "title": f"{len(regimes)} regimes detected; latest = {latest.get('label')}",
            "description": (f"mean shifted to {latest.get('mean'):.3g}"
                            if latest.get('mean') is not None else "regime shift detected"),
            "method": "ruptures PELT",
            "actions": ["VIEW REGIMES"],
        })

    # 3. Forecast warning.
    if forecast.get("available") and forecast.get("headline_value") is not None:
        head = forecast["headline_value"]
        ci_low = forecast.get("headline_ci_low")
        ci_high = forecast.get("headline_ci_high")
        ci_str = (f" (95% CI {ci_low:.2g}–{ci_high:.2g})"
                  if ci_low is not None and ci_high is not None else "")
        findings.append({
            "severity": "info",
            "confidence": forecast.get("score") or 0.5,
            "title": f"Forecast peak: {head:.3g}{ci_str}",
            "description": f"Method: {forecast.get('method','')}, horizon {forecast.get('horizon')}",
            "method": forecast.get("method", ""),
            "actions": ["SEE FORECAST", "SET ALERT"],
        })

    # 4. Physics: surface the discovered law (if non-trivial).
    if physics.get("available"):
        law = physics.get("discovered_law", {})
        eq = law.get("equation", "")
        rmse = law.get("rmse")
        if eq and law.get("name") != "diagnostic_only":
            findings.append({
                "severity": "ok",
                "confidence": 0.7,
                "title": f"Physical model fit: {law.get('name')}",
                "description": f"{eq}" + (f" (RMSE {rmse:.3g})" if rmse is not None else ""),
                "method": "physics_discovery",
                "actions": ["VIEW PHYSICS", "EXPLAIN"],
                # Polish: discovered laws are headline findings — the most
                # substantive thing Aurora can report. The frontend uses
                # this flag to render the card with distinct visual
                # prominence (icon + larger title + colored border).
                "headline": True,
                "headline_kind": "physics_match",
            })
        # 4b. Prior match: surface as a finding ONLY when confidence ≥ 0.7.
        #     Lower-confidence matches still appear in the EXPLAIN modal with
        #     full "why not 100%" detail — that honesty is fine in long-form,
        #     but a finding card claiming "matches Newton's cooling 50%" without
        #     the caveats would be misleading. Threshold pushes the marketing
        #     line ("matches a known law") into actually-clean territory.
        FINDING_PROMOTION_THRESHOLD = 0.7
        for pm in (physics.get("prior_matches") or [])[:1]:
            conf = pm.get("confidence")
            if not isinstance(conf, (int, float)) or conf < FINDING_PROMOTION_THRESHOLD:
                continue
            findings.append({
                "severity": "ok",
                "confidence": conf,
                "title": f"Matches known law: {pm.get('display_name')}",
                "description": (f"{pm.get('structural_form')} — "
                                f"{int(round(conf * 100))}% match · "
                                f"domain: {pm.get('domain')} · {pm.get('citation')}"),
                "method": "physics_prior_matcher",
                "actions": ["VIEW PHYSICS", "EXPLAIN"],
                # Polish: prior matches are the most distinctive finding kind —
                # they connect Aurora's discovered model to a citable
                # canonical law. Headline-flag them so the UI lifts these
                # cards above routine anomaly findings.
                "headline": True,
                "headline_kind": "physics_match",
            })

    # 5. Causal what-if as a finding.
    if causal.get("available") and causal.get("items"):
        first = causal["items"][0]
        findings.append({
            "severity": "info",
            "confidence": 0.6,
            "title": f"Causal effect: {first.get('if_clause')}",
            "description": first.get("then_clause", ""),
            "method": causal.get("method", "linear OLS"),
            "actions": ["RUN COUNTERFACTUAL"],
        })

    # 6. Promote runner warnings (report.events of type "warning") as findings.
    for ev in (report or {}).get("events", []):
        if ev.get("type") == "warning":
            msg = ev.get("msg") or ""
            err = (ev.get("payload") or {}).get("error", "")
            findings.append({
                "severity": "warn",
                "confidence": 1.0,
                "title": f"Runner warning: {msg}",
                "description": err[:200] if err else msg,
                "method": "runner",
                "actions": [],
            })

    return findings


# ----------------------------------------------------------------------
# Copilot — from llm_narrative.json or report.events as fallback
# ----------------------------------------------------------------------

def _build_copilot(narrative: Optional[Dict],
                   report: Optional[Dict],
                   anomalies: List[Dict],
                   forecast: Dict,
                   regimes: List[Dict],
                   physics: Dict) -> Dict[str, Any]:
    # Prefer real LLM output if present.
    if narrative and isinstance(narrative, dict):
        text = narrative.get("text") or narrative.get("narrative")
        if text:
            return {
                "available": True,
                "messages": [{"who": "AURORA", "body": text,
                              "citations": narrative.get("citations") or []}],
                "suggestions": narrative.get("suggestions") or _default_suggestions(),
                "model": narrative.get("model") or "aurora-local",
            }

    # Deterministic fallback: synthesize a paragraph from artifacts.
    # Launch fix: read HONEST counts from the runner's anomaly_attribution
    # block (n_total / n_critical) when available. The legacy len(anomalies)
    # bound at the runner's top_k=10 — that path is preserved as a fallback
    # for old artifacts but new runs always report the true count.
    parts: List[str] = []
    # honest counts — populated from overview when present (overview uses
    # the same _build_anomaly_counts helper, so this is consistent).
    n_anom = len(anomalies)
    n_crit = sum(1 for a in anomalies if a.get("severity") == "crit")
    n_warn = sum(1 for a in anomalies if a.get("severity") == "warn")
    n_detailed = n_anom
    # Caller may pass an override via the report.score block? No — we
    # cannot reach `overview` from here cleanly without changing the
    # signature. Recompute the honest counts from `report` if the caller
    # has stashed them; otherwise fall back. We also accept the new
    # `_anomaly_counts` carrier when state-builder propagates it.
    rep_counts = (report or {}).get("_anomaly_counts") if report else None
    if isinstance(rep_counts, dict):
        n_anom = int(rep_counts.get("n_total", n_anom))
        n_crit = int(rep_counts.get("n_critical", n_crit))
        n_warn = int(rep_counts.get("n_warn", n_warn))
        n_detailed = int(rep_counts.get("n_detailed", n_detailed))
    if n_anom:
        # Mention the detail cap honestly when we surfaced fewer than total.
        cap_note = ""
        if n_detailed and n_detailed < n_anom:
            cap_note = f" (driver attribution available for top {n_detailed})"
        if n_anom <= 5:
            parts.append(f"I found {n_anom} anomal{'y' if n_anom == 1 else 'ies'}"
                          + (f" ({n_crit} critical)." if n_crit else "."))
        else:
            parts.append(
                f"I found {n_anom} anomalies — {n_crit} critical, {n_warn} warn."
                f" The most significant are surfaced in the findings panel{cap_note}."
            )
        if anomalies:
            top = anomalies[0]
            parts.append(f"Top: {top.get('description','')}.")
    if regimes and len(regimes) > 1:
        parts.append(f"Detected {len(regimes)} regime segments.")
    if forecast.get("available") and forecast.get("headline_value") is not None:
        parts.append(f"Forecast peaks at {forecast['headline_value']:.3g} (method {forecast.get('method')}).")
    if physics.get("available"):
        law = physics.get("discovered_law", {})
        if law.get("equation") and law.get("name") != "diagnostic_only":
            rmse = law.get("rmse")
            parts.append(f"Best physics fit: {law.get('name')} — {law.get('equation')}"
                         + (f" (RMSE {rmse:.3g})." if rmse is not None else "."))
    if not parts:
        parts.append("This run produced no headline findings — the data may not have a "
                     "valid time axis or sufficient signal. See structure tile for details.")
    return {
        "available": True,
        "messages": [{
            "who": "AURORA",
            "body": " ".join(parts),
            "citations": ["deep_math.json", "report.json"],
        }],
        "suggestions": _default_suggestions(),
        "model": "deterministic-fallback",
    }


def _default_suggestions() -> List[str]:
    return [
        "What's the most surprising finding?",
        "Run a what-if on the top driver",
        "Compare the two largest regimes",
        "Show the forecast uncertainty bands",
    ]


# ----------------------------------------------------------------------
# Public entry
# ----------------------------------------------------------------------

def _build_state_tiered(parent_run_dir: Path) -> Dict[str, Any]:
    """Compose a unified state from a tiered parent run_dir.

    Algorithm:
      1. Load SamplingState; find the highest-complete tier's child run_dir.
      2. Build the state for THAT child run_dir (recursive call to build_state).
      3. If the tier-below also completed, build that state too and call
         ``attach_replication`` to tag every finding with status (confirmed
         /strengthened/weakened/contradicted/novel).
      4. Inject the ``sampling`` block at the top.
      5. Return the unified state.
    """
    from fantasyai.aurora.sampling import (
        SamplingState, read_tier_runs, attach_replication,
    )
    state = SamplingState.load(parent_run_dir)
    if state is None:
        # Shouldn't happen — caller already checked existence — but be honest.
        return {"run_id": parent_run_dir.name, "run_dir": str(parent_run_dir),
                "structure": _none_unavail("could not load _SAMPLING_STATE.json"),
                "anomalies": [], "forecast": _none_unavail(""),
                "causal": _none_unavail(""), "physics": _none_unavail(""),
                "regimes": [], "motifs": [], "findings": [],
                "copilot": _none_unavail(""), "plots": []}

    tier_runs = read_tier_runs(parent_run_dir)
    highest = state.highest_complete_tier()

    # If T0 is the only complete tier, we have structure but no analytical
    # state yet. Return a skeleton that the frontend can render with skeletons.
    if highest <= 0:
        # Read the structure profile from disk so the dataset rail can populate.
        prof_path = parent_run_dir / "tier0" / "structure_profile.json"
        prof = _read_json(prof_path) or {}
        cols = prof.get("columns") or []
        struct_cols = [{
            "name": c.get("name", "?"),
            "kind": ("time" if c.get("is_time") else
                     "n" if c.get("is_numeric") else "cat"),
            "fill_pct": (c.get("completeness") or 0.0) * 100.0,
        } for c in cols]
        return {
            "run_id": parent_run_dir.name,
            "run_dir": str(parent_run_dir),
            "dataset": {"available": True,
                         "name": state.dataset_basename,
                         "path": state.dataset_path,
                         "rows": state.rows_total,
                         "cols": state.cols_total,
                         "size_mb": 0.0},
            "structure": {"available": True,
                           "time_axis": prof.get("has_time_axis", False),
                           "time_col": prof.get("time_col"),
                           "cadence": "—", "gaps": 0, "duplicates": 0,
                           "columns": struct_cols, "eligibility": {}},
            "overview": {"available": True, "anomalies_count": 0,
                          "regimes_count": 0, "motifs_count": 0},
            "anomalies": [], "regimes": [], "motifs": [], "findings": [],
            "forecast": _none_unavail("tier 1 not yet complete"),
            "causal": _none_unavail("tier 1 not yet complete"),
            "physics": _none_unavail("tier 1 not yet complete"),
            "copilot": _none_unavail("tier 1 not yet complete"),
            "plots": [],
            "sampling": state.to_dict(),
        }

    # Build state from the highest-complete tier's child run_dir.
    child = tier_runs.get(highest)
    if not child or not Path(child).exists():
        # Mapping missing — fall back to highest-tier's recorded child path.
        ts = state.tiers[str(highest)]
        if ts.child_run_dir and Path(ts.child_run_dir).exists():
            child = ts.child_run_dir
    if not child or not Path(child).exists():
        return {"run_id": parent_run_dir.name, "run_dir": str(parent_run_dir),
                "structure": _none_unavail("tier child run_dir missing"),
                "anomalies": [], "forecast": _none_unavail(""),
                "causal": _none_unavail(""), "physics": _none_unavail(""),
                "regimes": [], "motifs": [], "findings": [],
                "copilot": _none_unavail(""), "plots": [],
                "sampling": state.to_dict()}

    # Recurse into the child build (legacy code path; child has no _SAMPLING_STATE).
    higher_state = build_state(Path(child))

    # If a lower tier also exists, attach replication status by comparing.
    lower_tier = highest - 1
    if lower_tier >= 1:
        lower_child = tier_runs.get(lower_tier) or state.tiers[str(lower_tier)].child_run_dir
        if lower_child and Path(lower_child).exists() and lower_child != child:
            try:
                lower_state = build_state(Path(lower_child))
                attach_replication(lower_state, higher_state,
                                    tier_lower=lower_tier, tier_higher=highest)
            except Exception:
                pass    # replication is enrichment; never blocks display

    # Final touches: re-key the run_dir to the parent (so the URL/links match).
    higher_state["run_id"] = parent_run_dir.name
    higher_state["run_dir"] = str(parent_run_dir)
    higher_state["sampling"] = state.to_dict()
    higher_state["sampling"]["honest_caveat"] = state.honest_caveat()
    if state.is_small_dataset:
        higher_state["sampling"]["display_mode"] = "single_tier"

    # Re-attach referents now that the parent's sampling block is present —
    # the tiered context can sometimes upgrade an L6 (row-index) finding to
    # L2 (timestamp) by reading the parent dataset_path. Idempotent.
    try:
        from fantasyai.aurora.referential import attach_referents_to_state
        attach_referents_to_state(higher_state)
        _apply_referents_to_findings(higher_state)
    except Exception:
        pass

    # run_meta refresh: the parent run_dir has the authoritative sampling
    # state; re-derive so current_tier / sampling_method / rows_analyzed
    # reflect the parent, not the child.
    try:
        higher_state["run_meta"] = _build_run_meta(parent_run_dir, higher_state)
    except Exception:
        pass
    return higher_state


def _apply_referents_to_findings(state: Dict[str, Any]) -> None:
    """Once referents are attached, rewrite finding titles/descriptions so the
    surface text reads as a real referent instead of a raw row index.

    Rules:
      * If the title contains "at row N" and the finding's referent is NOT
        inspect_only, replace "at row N" with "at {primary_label}".
      * If the title contains "row N flagged" (anomaly description), do
        the same swap inside the description string.
      * Findings with inspect_only=True keep the row-index in title (legacy)
        but gain ``referent.primary_label`` in the finding dict — the
        frontend renders the row index dimmed.
      * Idempotent — if the title already has ``primary_label``, no-op.
    """
    findings = state.get("findings") or []
    anomalies = state.get("anomalies") or []
    regimes = state.get("regimes") or []
    motifs = state.get("motifs") or []

    def _maybe_swap(item: Dict[str, Any], keys: List[str]) -> None:
        ref = item.get("referent") or {}
        label = ref.get("primary_label") or ""
        if not label:
            return
        if ref.get("inspect_only"):
            # Don't replace the row-index string in surface text — but DO
            # mark the item so the frontend can dim the row reference.
            item["referent_inspect_only"] = True
            return
        for k in keys:
            v = item.get(k)
            if not isinstance(v, str) or not v:
                continue
            new = re.sub(r"\bat row\s+\d+\b", f"at {label}", v, flags=re.IGNORECASE)
            new = re.sub(r"\brow\s+\d+\s+flagged\b", f"{label} flagged",
                          new, flags=re.IGNORECASE)
            new = re.sub(r"\b(?:rows?\s+)\d+(?:[–\-]\d+)?", label, new,
                          flags=re.IGNORECASE) if "row" in v.lower() and "rows" in v.lower() else new
            if new != v:
                item[k] = new
                # Preserve the original for explainability.
                item.setdefault("_legacy_" + k, v)

    for f in findings:
        if isinstance(f, dict):
            _maybe_swap(f, ["title", "description"])
    for a in anomalies:
        if isinstance(a, dict):
            _maybe_swap(a, ["when", "description"])
    for r in regimes:
        if isinstance(r, dict):
            _maybe_swap(r, ["start", "end"])
    for m in motifs:
        if isinstance(m, dict):
            _maybe_swap(m, ["name", "description"])


def build_state(run_dir: Path) -> Dict[str, Any]:
    """Read all artifacts from ``run_dir`` and return the frontend state dict.

    Two run-dir flavours:
      * **legacy** — single run, all artifacts in run_dir root (existing behavior)
      * **tiered** — parent dir with _SAMPLING_STATE.json + _TIER_RUNS.json
                     pointing to per-tier child run_dirs

    The tiered case is detected first; if matched, we recurse into the
    highest-complete child, attach replication-status, and return.
    """
    run_dir = Path(run_dir)

    # ---- Tiered run? -----------------------------------------------------
    sampling_state_path = run_dir / "_SAMPLING_STATE.json"
    if sampling_state_path.exists():
        try:
            return _build_state_tiered(run_dir)
        except Exception as e:
            # Fall through to legacy if tiered assembly fails — better to
            # show *something* than a blank screen.
            try:
                from fantasyai.aurora.sampling import SamplingState
                s = SamplingState.load(run_dir)
                return {
                    "run_id": run_dir.name, "run_dir": str(run_dir),
                    "dataset": {"available": True, "name": s.dataset_basename if s else "?"},
                    "structure": _none_unavail(f"tiered assembly failed: {e}"),
                    "anomalies": [], "forecast": _none_unavail("tiered failed"),
                    "causal": _none_unavail("tiered failed"),
                    "physics": _none_unavail("tiered failed"),
                    "regimes": [], "motifs": [], "findings": [],
                    "copilot": _none_unavail("tiered failed"),
                    "plots": [],
                    "sampling": s.to_dict() if s else {},
                    "tiered_error": f"{type(e).__name__}: {e}",
                }
            except Exception:
                pass

    deep         = _read_json(run_dir / "deep_math.json")
    # math_results.json carries the current runner's top_anomalies block.
    # state_builder reads it as a fallback for ``_build_anomalies`` when
    # ``deep_math.extensions.anomaly_attribution.points`` is absent (which
    # is the case for every run produced by ``run_aurora_dataset_runner``
    # / ``mathstack_v2``). Without this read, the Top Anomalies tile
    # renders empty even though the detector produced canonical findings.
    mathr        = _read_json(run_dir / "math_results.json")
    structure    = _read_json(run_dir / "structure_contract.json")
    resolved     = _read_json(run_dir / "_RESOLVED_CONTRACT.json")
    meta         = _read_json(run_dir / "_RUN_META.json")
    motif_doc    = _read_json(run_dir / "motif.json")
    report       = _read_json(run_dir / "report.json")
    orchestrator = _read_json(run_dir / "orchestrator.json")
    plots        = _read_json(run_dir / "_PLOTS_INDEX.json")
    narrative    = (_read_json(run_dir / "llm_narrative.json") or
                    _read_json(run_dir / "llm_narrative_guarded.json"))

    # Independent builders (each catches its own errors).
    structure_out = _build_structure(structure, resolved)
    anomalies     = _build_anomalies(deep, mathr)
    forecast      = _build_forecast(deep)
    # Add transparent ML ensemble (KNN + AR(2)) alongside the AR(1) baseline.
    _attach_ml_ensemble(forecast, deep, resolved, structure)
    causal        = _build_causal(deep)
    physics       = _build_physics(deep, run_dir=run_dir, resolved=resolved, structure=structure)
    regimes       = _build_regimes(deep)
    motifs        = _build_motifs(motif_doc, deep=deep, resolved=resolved, structure=structure)
    overview      = _build_overview(structure_out, anomalies, regimes, motifs,
                                    forecast, physics, report, deep=deep)
    findings      = _build_findings(anomalies, forecast, regimes, causal, physics,
                                    motifs, report, orchestrator)

    # v1.2 Stream 1.2: merge findings from the extended-methods stack
    # (VAR, DTW, BOCPD, Robust PCA, EMD, Kalman, Spectral Entropy).
    # The runner script writes extended_methods.json; each entry is
    # ALREADY in Aurora finding shape so we just append.
    extended_methods_state: Dict[str, Any] = _none_unavail("extended_methods.json missing")
    try:
        ext_doc = _read_json(run_dir / "extended_methods.json") or {}
        ext_findings = ext_doc.get("findings") if isinstance(ext_doc, dict) else None
        if isinstance(ext_findings, list):
            findings.extend([f for f in ext_findings if isinstance(f, dict)])
        # Build a per-method dict the frontend can render as method tiles.
        extended_methods_state = _build_extended_methods(ext_doc)
    except Exception:
        # Extended methods are additive — never block the rest of state assembly.
        pass

    # W10: KB grounding — attempt to attach a citation chip to each finding.
    # Failures are silent (KB is best-effort enrichment, never blocking).
    try:
        from fantasyai.aurora.kb import match_finding_to_kb
        for f in findings:
            try:
                cit = match_finding_to_kb(f)
                if cit:
                    f["kb_citation"] = cit
            except Exception:
                pass
    except Exception:
        pass
    # Stash honest anomaly counts on report so the copilot fallback narrative
    # speaks in true counts, not the truncated len(anomalies).
    try:
        report_with_counts = dict(report or {})
        report_with_counts["_anomaly_counts"] = _build_anomaly_counts(deep, anomalies)
    except Exception:
        report_with_counts = report
    copilot       = _build_copilot(narrative, report_with_counts, anomalies, forecast, regimes, physics)
    dataset       = _build_dataset(meta, resolved, structure, run_dir)

    # Glass-box: ensure the provenance ledger exists for this run. Lazy-build
    # if missing (older runs predate the ledger). Failures are non-fatal.
    ledger_path = run_dir / "_provenance_ledger.jsonl"
    has_ledger = ledger_path.exists()
    if not has_ledger:
        try:
            from fantasyai.aurora.provenance import build_provenance_for_run
            build_provenance_for_run(run_dir, write=True)
            has_ledger = ledger_path.exists()
        except Exception:
            # Don't crash state assembly if the synthesizer hits something it
            # doesn't understand. Findings still render; just no glass-box.
            pass

    state = {
        "run_id":   (resolved or meta or {}).get("run_id") or run_dir.name,
        "run_dir":  str(run_dir),
        "dataset":  dataset,
        "structure": structure_out,
        "overview": overview,
        "anomalies": anomalies,
        "forecast": forecast,
        "causal":   causal,
        "physics":  physics,
        "regimes":  regimes,
        "motifs":   motifs,
        "findings": findings,
        "copilot":  copilot,
        "plots":    (plots or {}).get("plots", []) if isinstance(plots, dict) else [],
        "provenance_available": has_ledger,
        # v1.2 Stream 1.2: per-method state for the 7 new extended methods
        # (VAR / DTW / BOCPD / Robust PCA / EMD / Kalman / Spectral Entropy).
        # The frontend renders one tile per method in the ADVANCED METHODS
        # grid, pulling its headline from this block.
        "extended_methods": extended_methods_state,
    }

    # ---- Hardening sprint Problem 3: run_meta block. Always present on
    #   /api/state responses so the frontend run indicator + dataset rail +
    #   sessions panel can speak with one voice about which run is shown.
    try:
        state["run_meta"] = _build_run_meta(run_dir, state)
    except Exception:
        state["run_meta"] = {"run_id": run_dir.name,
                             "run_dir": str(run_dir),
                             "state": "unknown"}

    # ---- Atom 3.2: SeedRanker. When the user provided a seed_text, attach
    #   relevant_findings + surprise_findings dual sections. The ranker is a
    #   pure function over state["findings"] — never modifies the underlying
    #   findings array, only reorders the surface output. With no seed,
    #   nothing happens (legacy behaviour preserved).
    try:
        from fantasyai.aurora import seed_ranker
        seed_text = (state.get("run_meta") or {}).get("seed_text")
        sm = state.get("system_model") or {}
        template_id = sm.get("template_id") if isinstance(sm, dict) else None
        seed_ranker.attach_to_state(state, seed_text, template_id=template_id)
    except Exception:
        pass

    # ---- Phase A: Referential resolution. Walk every emission site
    #   (anomalies / regimes / motifs / findings) and attach a structured
    #   ``referent`` block with primary_label + level_used + fallback_chain.
    #   The frontend reads referent.primary_label for headlines instead of
    #   parsing "row N" out of titles. Failure is silent — if the resolver
    #   can't help, the legacy strings still render.
    try:
        from fantasyai.aurora.referential import attach_referents_to_state
        attach_referents_to_state(state)
        # After referents are attached, rewrite finding titles + descriptions
        # to consume primary_label so the headline reads as "...at <event>"
        # instead of "...at row 1248".
        _apply_referents_to_findings(state)
    except Exception:
        pass

    # ---- Polish: stable-sort findings so headline-flagged ones (physics
    #   law matches, motif identifications) lift to the top of the list.
    #   Severity rank still applies inside the headline / non-headline
    #   groups so the user sees the most-actionable headline first.
    try:
        sev_rank = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
        def _finding_sort_key(f):
            head_first = 0 if f.get("headline") else 1
            sev = sev_rank.get((f.get("severity") or "info").lower(), 9)
            conf = -float(f.get("confidence") or 0.0)
            return (head_first, sev, conf)
        # Mutate state["findings"] in place so all downstream consumers
        # (narrative_engine, narrator, recommendations) see the new order.
        if isinstance(state.get("findings"), list):
            state["findings"].sort(key=_finding_sort_key)
    except Exception:
        pass

    # ---- Phase 5: attach a decision-aware recommendation to every finding.
    try:
        from fantasyai.aurora.recommendations import build_recommendation
        for f in findings:
            try:
                rec = build_recommendation(f)
                f["recommendation"] = rec.to_dict()
            except Exception:
                pass
    except Exception:
        pass

    # ---- Continuous validation loop (Phase 1) ----
    # Snapshot prior-library state BEFORE writing this run's validation rows
    # so we can compute a clean before/after diff for the morning changelog.
    try:
        from fantasyai.aurora.priors.changelog import (
            snapshot_all_priors, record_run_validations, compute_validation_changelog,
        )
        stats_before = snapshot_all_priors()
        record_run_validations(state, run_dir=str(run_dir))
        validation_changelog = compute_validation_changelog(stats_before)
        if validation_changelog:
            state["validation_changelog"] = validation_changelog
    except Exception:
        # Validation loop is enrichment — never block state assembly.
        pass

    # Persistent learning: append a row about this run to the global JSONL,
    # then attach meta-suggestions ("on similar past data, X usually wins")
    # to the physics tile. Both calls are silent on failure — learning is
    # always enrichment, never blocking.
    try:
        from fantasyai.aurora.learning import (
            append_run, suggest_priors_for_signature,
            record_auto_feedback, auto_feedback_summary,
        )
        # 1. Always-append the run row, even if calculus is None.
        append_run(run_dir, state)

        # 2. Auto-feedback: if the run is internally coherent (strong fit +
        #    matching prior + signature agreement), record an implicit
        #    thumbs-up; if obviously degenerate, record an implicit
        #    thumbs-down. This makes the system *always* learning, no
        #    human click required.
        auto_row = record_auto_feedback(str(run_dir), state)

        # 3. Meta-suggestions ("similar past data → these priors won").
        calc = (physics or {}).get("calculus") if isinstance(physics, dict) else None
        if calc and calc.get("available"):
            domain_hint = None
            if isinstance(physics.get("prior_matches"), list) and physics["prior_matches"]:
                domain_hint = physics["prior_matches"][0].get("domain")
            suggestions = suggest_priors_for_signature(
                calculus_dict=calc, domain_hint=domain_hint, min_n=2, top_k=3
            )
            if suggestions and isinstance(state.get("physics"), dict):
                state["physics"]["meta_suggestions"] = suggestions

        # 4. Surface the auto-feedback verdict + global summary on the state
        #    so the UI can show "trained on N runs · X self-validated" badges.
        if isinstance(state.get("physics"), dict) and auto_row is not None:
            state["physics"]["auto_feedback"] = {
                "verdict": auto_row.get("verdict"),
                "composite": auto_row.get("composite"),
                "rationale": auto_row.get("rationale"),
                "signals": auto_row.get("signals"),
            }
        state["learning_summary"] = auto_feedback_summary()
    except Exception:
        pass

    # ---- Phase 3: morning diary block — what changed in the last 24h.
    try:
        from fantasyai.aurora.agents import build_morning_diary
        diary = build_morning_diary(within_hours=24)
        if diary and diary.get("entries"):
            state["morning_diary"] = diary
    except Exception:
        pass

    # ---- System Model layer (synthetic understanding) ----
    # Identify domain → instantiate the template → write system_model.json
    # next to the run's other artifacts. The state carries the model dict so
    # the frontend system graph can render it without a second API call.
    try:
        from fantasyai.aurora.system_model import (
            instantiate_system_model, system_model_to_json,
        )
        sm = instantiate_system_model(state)
        problems = sm.validate()
        sm_dict = sm.to_dict()
        if problems:
            sm_dict["validation_problems"] = problems
        state["system_model"] = sm_dict
        # Persist alongside the other run artifacts.
        try:
            (run_dir / "system_model.json").write_text(
                system_model_to_json(sm), encoding="utf-8"
            )
        except Exception:
            pass
    except Exception:
        pass

    # ---- System Graph v5: spacetime presentation enrichment ----
    # Adds nodes[i].history (worldlines), causal_lags, future_events,
    # forecast cones, resonances, phase_space — all extracted from
    # already-computed analytical artifacts. Never modifies primitives.
    try:
        from fantasyai.aurora.system_model.spacetime import enrich_state_for_v5
        enrich_state_for_v5(state)
    except Exception:
        # v5 enrichment is presentation-only; never blocks state assembly.
        pass

    # ---- Phase 4: template-aware narrative re-narration on findings.
    try:
        from fantasyai.aurora.system_model.narrator import renarrate_findings
        sm_dict = state.get("system_model") or {}
        if sm_dict and isinstance(state.get("findings"), list):
            renarrate_findings(state["findings"], sm_dict)
    except Exception:
        pass

    # ---- Phase 5: append a template-evidence row so the library evolves.
    try:
        from fantasyai.aurora.system_model.evolution import record_evidence
        record_evidence(state)
    except Exception:
        pass

    # ---- Phase B: narrative engine. Applies the active decision contract
    #   (or a neutral default) to filter / rank / re-render every finding.
    #   The engine is purely deterministic — no LLM — and never invents
    #   claims. Suppressed findings are kept in ``inspected_findings`` so
    #   the user can flip them back from the EXPLAIN modal.
    try:
        from fantasyai.aurora.narrative_engine import render_narrative
        from fantasyai.aurora.decision_contract import (
            get_default_contract, suggest_contracts_for_template,
        )
        # Resolve the active contract for THIS run. Priority order:
        #   1. explicit override stored on disk (active_contract.json)
        #   2. derived from the system_model template's "explore" preset
        #   3. None → engine uses its neutral default
        active_contract = None
        try:
            ac_path = run_dir / "active_contract.json"
            if ac_path.exists():
                from fantasyai.aurora.decision_contract import DecisionContract
                d = _read_json(ac_path) or {}
                if d.get("id"):
                    active_contract = DecisionContract(**{
                        k: v for k, v in d.items()
                        if k in {"id", "template_id", "contract_class",
                                  "objective", "constraints", "available_actions",
                                  "cost_asymmetry", "confidence_floor", "notes",
                                  "source"}
                    })
        except Exception:
            active_contract = None
        if active_contract is None:
            sm = state.get("system_model") or {}
            tid = (sm.get("template_id") or "").lower()
            if tid:
                presets = suggest_contracts_for_template(tid)
                if presets:
                    active_contract = presets[0]
        render_narrative(state, contract=active_contract)
    except Exception:
        # Engine never blocks state assembly — fall back to legacy text.
        pass

    # ---- Credibility Pass (Wave A of the analytical-methods expansion):
    #   bootstrap CIs on point estimates, BH+Bonferroni multiple-
    #   comparisons correction across the findings family, and effect
    #   sizes (Cohen's d / R² / OR / η²) attached where applicable.
    #   Runs BEFORE synthesis so its templates can interpolate q_value,
    #   confidence_interval, effect_size slots. Glass-box: a per-step
    #   audit dict is left at state.credibility, including reasons for
    #   any step that was skipped.
    # ---- Load raw dataset numerics so the credibility + advanced passes
    #   have a numeric matrix + primary series + times to work on. The
    #   primitives upstream of this point only consume the runner-emitted
    #   JSON artifacts — Brief-2/3 methods need the underlying numbers.
    #   Capped at 5000 rows for tractability; SINDy / KSG MI / TDA all
    #   scale super-linearly. Best-effort: failures here just mean the
    #   advanced pass cleanly skips.
    try:
        _attach_advanced_inputs(state, resolved, structure, deep)
    except Exception:
        pass

    try:
        from fantasyai.aurora.stats import apply_credibility_pass
        apply_credibility_pass(state)
    except Exception:
        # Credibility is enrichment; never blocks state assembly.
        pass

    # ---- Advanced Methods Pass (Brief 3): SINDy + HMM + mutual info +
    #   Granger + wavelet + Lomb-Scargle + GP + topology + power +
    #   compositional. Each step gates on data shape and skips cleanly
    #   when its requirements aren't met. Outputs land on dedicated
    #   state.<method> blocks plus emit findings (kind_hint set so the
    #   synthesis matcher can route to method-specific templates).
    try:
        from fantasyai.aurora.stats import apply_advanced_pass
        apply_advanced_pass(state)
    except Exception:
        # Advanced methods are research-grade enrichment; never block.
        pass

    # ---- Strip heavy raw inputs from the API state. The credibility +
    #   advanced passes have already consumed them; the frontend reads
    #   only the derived ``state.advanced_methods`` / ``state.sindy`` /
    #   etc. blocks. Keeping the matrix would balloon the /api/state
    #   payload by 200-500KB on a 5000-row dataset.
    for k in ("dataset_matrix", "dataset_matrix_columns",
                "primary_series", "primary_times", "primary_series_name",
                "dataset_dt"):
        state.pop(k, None)

    # v1.2: surface deep_math method timeouts into a slim summary key
    # so the synthesis disclosure helper (engine._collect_pipeline_disclosure)
    # can route them through the same "Pipeline notes" path as v1.1's
    # advanced_pass timeouts. The raw ``deep`` dict is too large to stash
    # on state (200KB+), so we extract just the timeout markers. Pre-v1.2
    # this key is absent (no timeouts could fire) — backwards compatible.
    if isinstance(deep, dict):
        _dm_timeouts: List[Dict[str, Any]] = []
        for _k, _v in deep.items():
            if isinstance(_v, dict) and _v.get("skipped_reason") == "method_timeout":
                _dm_timeouts.append({
                    "method": _v.get("method") or _k,
                    "block_key": _k,
                    "elapsed_seconds": _v.get("elapsed_seconds"),
                    "timeout_seconds": _v.get("timeout_seconds"),
                })
        if _dm_timeouts:
            state["_deep_math_timeouts"] = _dm_timeouts

    # ---- Synthesis Engine: produce the "WHAT THIS MEANS" interpretive
    #   summary, equation translations, and domain context. Runs LAST
    #   so it has access to the full assembled state (findings, run_meta,
    #   referents, narrative_engine output). Glass-box: every paragraph
    #   it produces is reproducible from templates + this state.
    try:
        from fantasyai.aurora.synthesis import synthesize
        synthesize(state)
    except Exception:
        # Synthesis is presentation-layer; never blocks state assembly.
        pass

    return state


def _build_run_meta(run_dir: Path,
                    state: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesize the ``run_meta`` block for /api/state.

    Per the hardening sprint spec:
      run_id, run_dir, dataset_name, dataset_path, dataset_size_mb,
      started_at, completed_at, elapsed_seconds, current_tier,
      highest_complete_tier, tiered, state, sampling_method,
      rows_total, rows_analyzed, seed, cached, is_active

    The run-registry's _RUN_PROGRESS.json is the source of truth when present;
    otherwise we infer from on-disk artifacts (timestamps, sampling state).
    Best-effort — never blocks state assembly.
    """
    out: Dict[str, Any] = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "dataset_name": None,
        "dataset_path": None,
        "dataset_size_mb": None,
        "started_at": None,
        "completed_at": None,
        "elapsed_seconds": None,
        "current_tier": None,
        "highest_complete_tier": None,
        "tiered": False,
        "state": "complete",
        "sampling_method": None,
        "rows_total": None,
        "rows_analyzed": None,
        "seed": None,
        "cached": False,
        "is_active": False,
        # Atom 3.2: seed text + provenance.
        "seed_text": None,
        "seed_source": None,
        "seed_at": None,
    }
    # 1. Pull whatever we can from the run-registry's persisted progress.
    try:
        from fantasyai.aurora import run_registry as _reg
        prog = _reg._read_persisted_at(run_dir) or _reg.lookup_by_run_dir(str(run_dir))
        if prog:
            for k in ("run_id", "started_at", "completed_at", "state",
                      "current_tier", "elapsed_seconds", "cached",
                      "seed", "tiered",
                      "seed_text", "seed_source", "seed_at"):
                if prog.get(k) is not None:
                    out[k] = prog.get(k)
    except Exception:
        pass

    # 2. Sampling state — for tiered runs, pull rows + method + highest tier.
    try:
        from fantasyai.aurora.sampling import SamplingState
        ss = SamplingState.load(run_dir)
        if ss is not None:
            out["tiered"] = True
            out["rows_total"] = ss.rows_total
            d = ss.to_dict()
            tiers = d.get("tiers") or {}
            out["highest_complete_tier"] = ss.highest_complete_tier()
            # Highest *running-or-complete* tier is current_tier.
            if out["current_tier"] is None:
                cur = 0
                for n in ("0", "1", "2", "3"):
                    s = (tiers.get(n) or {}).get("state")
                    if s in ("running", "complete"):
                        cur = int(n)
                out["current_tier"] = cur
            # rows_analyzed = the highest-complete tier's rows_sampled.
            ht = out["highest_complete_tier"]
            if ht is not None and ht > 0:
                ts = (tiers.get(str(ht)) or {})
                if ts.get("rows_sampled") is not None:
                    out["rows_analyzed"] = ts["rows_sampled"]
                if ts.get("method"):
                    out["sampling_method"] = ts["method"]
            out["dataset_path"] = ss.dataset_path
            out["dataset_name"] = ss.dataset_basename
    except Exception:
        pass

    # 3. Dataset stats from the assembled state if missing.
    ds = state.get("dataset") or {}
    if not out["dataset_name"]:
        out["dataset_name"] = ds.get("name")
    if not out["dataset_path"]:
        out["dataset_path"] = ds.get("path")
    if out["rows_total"] is None:
        out["rows_total"] = ds.get("rows")
    # File size in MB if the path exists.
    if out["dataset_path"]:
        try:
            p = Path(out["dataset_path"])
            if p.exists():
                out["dataset_size_mb"] = round(p.stat().st_size / (1024 * 1024), 2)
        except Exception:
            pass

    # 4. Started/completed timestamps fallback to dir mtime.
    if not out["started_at"]:
        try:
            import datetime as _dt
            out["started_at"] = _dt.datetime.fromtimestamp(
                run_dir.stat().st_mtime, tz=_dt.timezone.utc,
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    # 5. is_active: look at any non-terminal record in the live registry.
    try:
        from fantasyai.aurora import run_registry as _reg
        actives = _reg.list_active()
        for a in actives:
            if a.get("run_dir") and Path(a["run_dir"]) == run_dir:
                out["is_active"] = True
                # Active overrides terminal state inferred from disk.
                out["state"] = a.get("state") or "running"
                if a.get("current_tier") is not None:
                    out["current_tier"] = a["current_tier"]
                break
    except Exception:
        pass

    return out


def find_latest_run(outputs_root: Path,
                       *, state_filter: Optional[str] = "complete_only"
                       ) -> Optional[Path]:
    """Return the most recent run directory.

    Workstream-1 fix F-3: the previous implementation sorted purely by
    directory mtime, which on Windows can lie when files inside an old
    directory are touched (overnight learning loop, antivirus, etc.) —
    that produced the climate_buoy-from-a-different-day ghost on
    refresh after the diabetic_data run.

    Two changes:
      1. Sort newest-first by **run_id timestamp prefix** (the
         ``YYYYMMDD_HHMMSS__...`` directory name is monotonic from
         ``make_run_id``). Falls back to mtime only when the prefix
         doesn't parse.
      2. ``state_filter="complete_only"`` (default) skips dirs whose
         ``_RUN_PROGRESS.json`` reports state != complete (running,
         failed, cancelled). Pass ``state_filter=None`` to disable.

    Returns ``None`` if nothing matches the filter.
    """
    outputs_root = Path(outputs_root)
    if not outputs_root.exists():
        return None
    try:
        candidates = [p for p in outputs_root.iterdir() if p.is_dir()]
    except Exception:
        return None
    if not candidates:
        return None

    def _sort_key(p: Path):
        # Extract leading 15-char timestamp ``YYYYMMDD_HHMMSS`` so newer
        # runs sort first regardless of disk mtime quirks. Older dirs
        # without that pattern fall back to negative mtime.
        name = p.name
        if (len(name) >= 15 and name[:8].isdigit() and name[8] == "_"
                and name[9:15].isdigit()):
            return (0, name)
        return (1, -p.stat().st_mtime)

    candidates.sort(key=_sort_key, reverse=True)

    if state_filter == "complete_only":
        # The F-2 page-load flow handles in-progress runs by hitting
        # /api/runs/active FIRST and resuming polling. /api/state's
        # default fallback is intentionally STRICT: only return a
        # completed run, or None. That way an in-progress run can
        # never be served by the mtime-fallback path with partial
        # artifacts and look like a stale "result".
        for p in candidates:
            if _read_progress_state(p) == "complete":
                return p
        # ``_RUN_PROGRESS.json`` is missing on legacy runs from before
        # B-2 shipped. Walk those once more, trusting the on-disk
        # artifact set as proof of completion: presence of
        # ``deep_math.json`` AND ``report.json`` means the runner ran
        # to the end. This keeps every legacy run that completed
        # before this hardening pass discoverable.
        for p in candidates:
            if _read_progress_state(p) is None:
                if (p / "deep_math.json").exists() and (p / "report.json").exists():
                    return p
        return None

    return candidates[0]


def _read_progress_state(run_dir: Path) -> Optional[str]:
    """Read ``_RUN_PROGRESS.json`` and return its ``state`` field, or None
    if the file is missing/corrupt. Best-effort; never raises."""
    try:
        p = Path(run_dir) / "_RUN_PROGRESS.json"
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        s = d.get("state")
        return str(s) if s else None
    except Exception:
        return None


def list_runs(outputs_root: Path, limit: int = 50) -> List[Dict[str, Any]]:
    outputs_root = Path(outputs_root)
    if not outputs_root.exists():
        return []
    rows: List[Dict[str, Any]] = []
    dirs = [p for p in outputs_root.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for d in dirs[:limit]:
        meta = _read_json(d / "_RUN_META.json") or {}
        report = _read_json(d / "report.json") or {}
        score = report.get("score") or {}
        rows.append({
            "run_id": d.name,
            "run_dir": str(d),
            "dataset": d.name.split("__", 1)[1] if "__" in d.name else d.name,
            "mtime": d.stat().st_mtime,
            "confidence": _safe_float(score.get("confidence")),
            "n_findings": len(report.get("events") or []),
        })
    return rows

"""
System Graph v5 — Spacetime enrichment layer.

This module is presentation-only. It does NOT modify the system_model
analytical primitives (entities, relationships, patterns, regimes).
Instead, it walks the assembled state + system_model dict AFTER they're
built and attaches additional fields the v5 frontend needs:

  - nodes[i].history          per-node time-series sample (B.1)
  - nodes[i].forecast         per-node forecast cone (B.2)
  - causal_lags               leading→lagging lag list (B.3)
  - resonances                cross-coherent oscillator pairs (B.4)
  - phase_space               2-axis projection + attractors (B.5)
  - future_events             threshold crossings + regime entries (B.6)

Every enrichment is graceful: returns early when the underlying data is
unavailable. Frontend handles each missing piece by rendering a
placeholder. No required dependency between sub-atoms; partial state
renders correctly.

Public API (used by state_builder):

    from fantasyai.aurora.system_model.spacetime import enrich_state_for_v5
    enrich_state_for_v5(state)   # mutates state in place
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Tunables (env-overridable)
# ---------------------------------------------------------------------------

WORLDLINE_HOURS = float(os.environ.get("AURORA_WORLDLINE_HOURS", "4.0"))
WORLDLINE_INTERVAL_MIN = float(os.environ.get("AURORA_WORLDLINE_INTERVAL_MIN", "5.0"))
FORECAST_CONE_HOURS = float(os.environ.get("AURORA_FORECAST_CONE_HOURS", "6.0"))
RESONANCE_COHERENCE_FLOOR = 0.70
RESONANCE_TOP_K = 5
RESONANCE_MIN_PERIOD_MIN = 1.0
RESONANCE_MAX_PERIOD_MIN = 24 * 60.0
CAUSAL_LAG_MIN_CONFIDENCE = 0.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def _resolve_dataset_path(state: Dict[str, Any]) -> Optional[Path]:
    """Pull the source CSV path from the dataset / structure block."""
    ds = state.get("dataset") or {}
    p = ds.get("path")
    if p:
        return Path(str(p))
    structure = state.get("structure") or {}
    p = structure.get("dataset_key") or structure.get("path")
    return Path(str(p)) if p else None


def _load_dataset_lazy(state: Dict[str, Any]):
    """Load the dataset CSV once, return (DataFrame, time_col, time_series).
    Returns (None, None, None) when not available — caller skips."""
    p = _resolve_dataset_path(state)
    if p is None or not p.exists() or not p.is_file():
        return None, None, None
    try:
        import pandas as pd
        df = pd.read_csv(p, low_memory=False)
    except Exception:
        return None, None, None
    structure = state.get("structure") or {}
    time_col = structure.get("time_col")
    if not time_col or time_col not in df.columns:
        return df, None, None
    try:
        ts = pd.to_datetime(df[time_col], errors="coerce", utc=False)
        if ts.isna().all():
            return df, time_col, None
        return df, time_col, ts
    except Exception:
        return df, time_col, None


def _nodes_from_system_model(sm: Dict[str, Any]) -> List[Dict[str, Any]]:
    """system_model["topology"]["entities"] — these are the V5 "nodes"."""
    if not isinstance(sm, dict):
        return []
    topo = sm.get("topology") or {}
    return topo.get("entities") or []


# ---------------------------------------------------------------------------
# B.1: Per-node history (worldlines)
# ---------------------------------------------------------------------------

def _sample_history(ts_index, values, n_samples: int = 48) -> List[Dict[str, Any]]:
    """Pick n_samples evenly across the latest WORLDLINE_HOURS slice.

    Falls back to row-index-based sampling when the timestamp index is
    None (no time axis). Returns [{t, v}, ...] from oldest → newest.
    """
    import numpy as np
    if values is None:
        return []
    try:
        arr = np.asarray(values, dtype=float)
    except Exception:
        return []
    n = len(arr)
    if n == 0:
        return []
    finite = np.isfinite(arr)
    if not finite.any():
        return []

    if ts_index is not None:
        # Time-indexed slice: keep only the WORLDLINE_HOURS tail.
        try:
            import pandas as pd
            valid_mask = ts_index.notna() & finite
            if not valid_mask.any():
                return []
            vt = ts_index[valid_mask]
            vv = arr[valid_mask]
            t_max = vt.max()
            t_min = t_max - pd.Timedelta(hours=WORLDLINE_HOURS)
            sl = (vt >= t_min)
            vt = vt[sl]; vv = vv[sl]
            if len(vt) == 0:
                return []
            # Subsample to n_samples evenly.
            step = max(1, len(vt) // n_samples)
            idxs = list(range(0, len(vt), step))[:n_samples]
            out: List[Dict[str, Any]] = []
            for i in idxs:
                t_iso = vt.iloc[i].isoformat() if hasattr(vt.iloc[i], "isoformat") else str(vt.iloc[i])
                out.append({"t": t_iso, "v": float(vv[i])})
            # Always include the last point ("now").
            last_iso = vt.iloc[-1].isoformat() if hasattr(vt.iloc[-1], "isoformat") else str(vt.iloc[-1])
            if not out or out[-1]["t"] != last_iso:
                out.append({"t": last_iso, "v": float(vv[-1])})
            return out
        except Exception:
            pass

    # Row-index fallback — no time axis. Use last n_samples points with
    # synthetic offset markers (negative hours from "now").
    tail = arr[-min(n, n_samples * 4):]
    finite_tail = np.isfinite(tail)
    if not finite_tail.any():
        return []
    tail = tail[finite_tail]
    step = max(1, len(tail) // n_samples)
    idxs = list(range(0, len(tail), step))[:n_samples]
    if idxs and idxs[-1] != len(tail) - 1:
        idxs.append(len(tail) - 1)
    out = []
    n_pts = len(idxs)
    for j, i in enumerate(idxs):
        # Synthesise time offset: oldest = -WORLDLINE_HOURS, newest = 0.
        t_off_hr = -WORLDLINE_HOURS * (1.0 - j / max(1, n_pts - 1))
        out.append({"t_offset_hours": round(t_off_hr, 3),
                    "v": float(tail[i])})
    return out


def _fuzzy_match_column(node: Dict[str, Any],
                          cols_lower: Dict[str, str]) -> Optional[str]:
    """Phase-A2 fallback: when ``node.data_column`` is None or doesn't
    resolve to a real column, try harder. Returns a column name or None.

    Strategy (cheapest first):
      1. node.id (e.g. ``wind_speed`` matches a column called ``wind_speed``)
      2. node.label whole (case-insensitive)
      3. word-tokens of node.label (e.g. label="Wind speed" → ``wind`` + ``speed``)
      4. substring match in either direction with ≥3 chars to avoid
         pathological short-substring hits

    Returns the canonical column name (with original casing) or None.
    """
    candidates: List[str] = []
    nid = str(node.get("id") or "").lower()
    nlabel = str(node.get("label") or "").lower()
    if nid: candidates.append(nid)
    if nlabel: candidates.append(nlabel)
    if " " in nlabel:
        candidates.extend(w for w in nlabel.split() if len(w) >= 3)
    if "_" in nid:
        candidates.extend(w for w in nid.split("_") if len(w) >= 3)
    # Dedupe while preserving order.
    seen = set(); deduped = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c); deduped.append(c)
    for c in deduped:
        if c in cols_lower:
            return cols_lower[c]
    for c in deduped:
        if len(c) < 3:
            continue
        for low, real in cols_lower.items():
            if c in low or (len(low) >= 3 and low in c):
                return real
    return None


def enrich_with_history(state: Dict[str, Any]) -> None:
    """B.1: attach nodes[i].history to system_model.topology.entities.

    Phase-A2 (v1.2.x): when a node's ``data_column`` is None or doesn't
    resolve to a column in the dataframe, run a fuzzy match by
    label/id/word-tokens before giving up. Without this, template
    entities like "Wind speed" missed columns like ``ws_mps`` or
    ``wind_speed_mps`` and showed empty worldlines.
    """
    sm = state.get("system_model")
    if not isinstance(sm, dict):
        return
    nodes = _nodes_from_system_model(sm)
    if not nodes:
        return
    df, time_col, ts_index = _load_dataset_lazy(state)
    if df is None:
        # Mark all nodes as missing history; frontend skips worldlines.
        for n in nodes:
            n["history"] = None
        return
    # Case-insensitive column lookup.
    cols_lower = {str(c).lower(): c for c in df.columns}
    for n in nodes:
        col_name = n.get("data_column") or n.get("id")
        resolved = (col_name if col_name in df.columns
                     else cols_lower.get(str(col_name or "").lower()))
        if not resolved:
            # Phase-A2: last-resort fuzzy match by node label/id tokens.
            resolved = _fuzzy_match_column(n, cols_lower)
            # If we found one via fuzzy match, also write it back to the
            # node so downstream consumers (forecast cones, phase space,
            # SIMULATE target picker) see the same column.
            if resolved:
                n["data_column"] = resolved
                # Mark as fuzzy-matched so the frontend tooltip can
                # explain "matched by label, not exact name" if needed.
                n["data_column_match"] = "fuzzy"
        if not resolved:
            n["history"] = None
            continue
        try:
            import pandas as pd
            series = pd.to_numeric(df[resolved], errors="coerce")
            n["history"] = _sample_history(ts_index, series.values)
        except Exception:
            n["history"] = None


# ---------------------------------------------------------------------------
# B.3: Causal lags surfacing
# ---------------------------------------------------------------------------

def enrich_with_causal_lags(state: Dict[str, Any]) -> None:
    """B.3: attach system_model.causal_lags from existing relationships +
    state.causal block. Filters to lag_minutes > 0 and confidence ≥ 0.5.
    """
    sm = state.get("system_model")
    if not isinstance(sm, dict):
        return
    out: List[Dict[str, Any]] = []

    # Source 1: relationships from the topology with .lag set.
    topo = sm.get("topology") or {}
    for r in (topo.get("relationships") or []):
        if not isinstance(r, dict):
            continue
        lag = r.get("lag")
        conf = r.get("confidence")
        if lag is None:
            continue
        try:
            lag_seconds = float(lag)
        except Exception:
            continue
        if lag_seconds <= 0:
            continue
        if conf is not None and float(conf) < CAUSAL_LAG_MIN_CONFIDENCE:
            continue
        out.append({
            "source_node_id": r.get("source"),
            "target_node_id": r.get("target"),
            "lag_minutes": round(lag_seconds / 60.0, 2),
            "confidence": _safe_float(conf),
            "rationale": r.get("rationale") or "",
            "origin": "topology_relationship",
        })

    # Source 2: state.causal — the runner's cross-correlation finds.
    causal = state.get("causal") or {}
    items = causal.get("items") if isinstance(causal, dict) else None
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            lag_min = item.get("lag_minutes") or item.get("lag")
            if lag_min is None:
                continue
            try:
                lag_min_f = float(lag_min)
            except Exception:
                continue
            if lag_min_f <= 0:
                continue
            conf = item.get("confidence")
            if conf is not None and _safe_float(conf) is not None and \
                    _safe_float(conf) < CAUSAL_LAG_MIN_CONFIDENCE:
                continue
            out.append({
                "source_node_id": item.get("source") or item.get("treatment_col"),
                "target_node_id": item.get("target") or item.get("target_col"),
                "lag_minutes": round(lag_min_f, 2),
                "confidence": _safe_float(conf),
                "rationale": item.get("rationale") or item.get("if_clause") or "",
                "origin": "state_causal",
            })

    # Deduplicate by (source, target, lag) — prefer the higher-confidence one.
    seen: Dict[Tuple[Any, Any, float], Dict[str, Any]] = {}
    for entry in out:
        k = (entry["source_node_id"], entry["target_node_id"],
             round(entry["lag_minutes"], 1))
        if k not in seen or (
            (entry.get("confidence") or 0) > (seen[k].get("confidence") or 0)
        ):
            seen[k] = entry
    sm["causal_lags"] = sorted(
        seen.values(),
        key=lambda r: -(r.get("confidence") or 0.0),
    )


# ---------------------------------------------------------------------------
# B.6: Future events (threshold crossings + regime entries)
# ---------------------------------------------------------------------------

def enrich_with_future_events(state: Dict[str, Any]) -> None:
    """B.6: attach system_model.future_events sorted by time_offset_hours.

    Sources:
      - state.forecast — threshold crossings (when CI upper bound exceeds
        a known operational ceiling).
      - state.regimes — predicted regime transitions.
      - state.physics — bifurcation crossings (best-effort).
    """
    sm = state.get("system_model")
    if not isinstance(sm, dict):
        return
    events: List[Dict[str, Any]] = []

    # ---- Threshold crossings from the forecast block ----
    fc = state.get("forecast") or {}
    if isinstance(fc, dict) and fc.get("available"):
        target = fc.get("target")
        head_v = _safe_float(fc.get("headline_value"))
        head_lo = _safe_float(fc.get("headline_ci_low"))
        head_hi = _safe_float(fc.get("headline_ci_high"))
        if head_v is not None:
            # Find the time-offset of the peak step. The runner emits points
            # with `step` indices (1-based hours from now in many configs).
            # We locate the step whose value most-closely matches head_v;
            # falls back to the last-step offset when the match is ambiguous.
            t_off: Optional[float] = None
            best_diff = float("inf")
            for p in (fc.get("points") or []):
                if not isinstance(p, dict):
                    continue
                v = _safe_float(p.get("value"))
                step = p.get("step")
                if v is None or step is None:
                    continue
                diff = abs(v - head_v)
                if diff < best_diff:
                    best_diff = diff
                    try:
                        t_off = float(step)
                    except Exception:
                        t_off = None
            events.append({
                "kind": "threshold_cross",
                "node_id": target,
                "time_offset_hours": t_off,
                "value": head_v,
                "ci_low": head_lo,
                "ci_high": head_hi,
                "probability": _safe_float(fc.get("score")) or 0.5,
                "rationale": (f"forecast peak = {head_v:.3g}"
                               + (f" (95% CI {head_lo:.3g}–{head_hi:.3g})"
                                  if head_lo is not None and head_hi is not None
                                  else "")),
            })

    # ---- Predicted regime entries ----
    regimes = state.get("regimes") or []
    # If the state carries a regime forecast in physics or system_model.state,
    # surface it. Otherwise just emit "current regime continues".
    sm_state = sm.get("state") or {}
    active_regime = sm_state.get("active_regime")
    pred = sm_state.get("predicted_next_regime") or sm_state.get("next_regime")
    if pred:
        events.append({
            "kind": "regime_entry",
            "node_id": None,
            "time_offset_hours": _safe_float(sm_state.get("predicted_transition_hours")),
            "label": pred,
            "probability": _safe_float(sm_state.get("transition_confidence")) or 0.5,
            "rationale": f"predicted next regime: {pred}"
                          + (f" (from {active_regime})" if active_regime else ""),
        })

    # ---- Sort + truncate ----
    events.sort(key=lambda e: (
        e.get("time_offset_hours") if e.get("time_offset_hours") is not None
        else 9999.0
    ))
    sm["future_events"] = events


# ---------------------------------------------------------------------------
# B.2: Per-node forecast cone
# ---------------------------------------------------------------------------

def _node_forecast_from_state(node_id: str, target_col: Optional[str],
                                fc: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Build a per-node forecast cone array from state.forecast.

    Returns [{t: offset_hours, mean, ci_lo, ci_hi}, ...] or None.

    Aurora's runner produces forecast points keyed by step index. For
    nodes that are NOT the forecast target, we don't have a per-node
    forecast — return None so the frontend skips the cone for that node.
    """
    if not fc or not fc.get("available"):
        return None
    if target_col is None:
        return None
    # Match on either the explicit target string OR the data_column for
    # this node. Case-insensitive.
    fc_target = (fc.get("target") or "").lower()
    if str(node_id).lower() != fc_target and \
            str(target_col).lower() != fc_target:
        return None
    points = fc.get("points") or []
    out: List[Dict[str, Any]] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        step = p.get("step")
        v = _safe_float(p.get("value"))
        ci_low = _safe_float(p.get("ci_low"))
        ci_high = _safe_float(p.get("ci_high"))
        if step is None and v is None:
            continue
        try:
            t_off = float(step) if step is not None else None
        except Exception:
            t_off = None
        # Only emit points that fit inside FORECAST_CONE_HOURS.
        if t_off is not None and t_off > FORECAST_CONE_HOURS:
            continue
        out.append({
            "t": t_off,
            "mean": v,
            "ci_lo": ci_low,
            "ci_hi": ci_high,
        })
    return out or None


def enrich_with_forecast_cones(state: Dict[str, Any]) -> None:
    """B.2: attach nodes[i].forecast cone arrays.

    Cross-references the runner's state.forecast block. Only the node
    whose data_column matches the forecast target receives a cone; others
    get None (frontend skips).
    """
    sm = state.get("system_model")
    if not isinstance(sm, dict):
        return
    nodes = _nodes_from_system_model(sm)
    if not nodes:
        return
    fc = state.get("forecast") or {}
    for n in nodes:
        col_name = n.get("data_column") or n.get("id")
        n["forecast"] = _node_forecast_from_state(n.get("id"), col_name, fc)


# ---------------------------------------------------------------------------
# B.5: Phase space data
# ---------------------------------------------------------------------------

def _pick_phase_space_axes(nodes: List[Dict[str, Any]],
                            df) -> Optional[Tuple[str, str]]:
    """Pick the two most-informative numeric variables for the phase plane.

    Heuristic: pick the two columns with the highest variance among
    columns that have a node in the system_model topology + are present
    in the dataframe + have non-zero variance + finite values.
    """
    import numpy as np
    candidates: List[Tuple[str, float]] = []
    cols_lower = {str(c).lower(): c for c in df.columns}
    for n in nodes:
        col = n.get("data_column") or n.get("id")
        resolved = (col if col in df.columns
                    else cols_lower.get(str(col or "").lower()))
        if not resolved:
            continue
        try:
            import pandas as pd
            s = pd.to_numeric(df[resolved], errors="coerce").dropna()
            if len(s) < 10:
                continue
            v = float(s.var())
            if v <= 0 or not np.isfinite(v):
                continue
            # Normalise variance by mean magnitude so columns with
            # different units don't dominate purely on scale.
            mu = float(abs(s).mean()) or 1.0
            candidates.append((resolved, v / (mu * mu)))
        except Exception:
            continue
    if len(candidates) < 2:
        return None
    candidates.sort(key=lambda c: -c[1])
    return candidates[0][0], candidates[1][0]


def enrich_with_phase_space(state: Dict[str, Any]) -> None:
    """B.5: attach state.system_model.phase_space.

    Picks two informative variables → projects each regime's centroid
    onto that plane → emits historical + projected trajectories.
    """
    sm = state.get("system_model")
    if not isinstance(sm, dict):
        sm = {}
    nodes = _nodes_from_system_model(sm)
    df, time_col, ts_index = _load_dataset_lazy(state)
    if df is None or not nodes:
        sm["phase_space"] = None
        return
    axes = _pick_phase_space_axes(nodes, df)
    if axes is None:
        sm["phase_space"] = None
        return
    x_col, y_col = axes
    try:
        import pandas as pd
        x_series = pd.to_numeric(df[x_col], errors="coerce")
        y_series = pd.to_numeric(df[y_col], errors="coerce")
    except Exception:
        sm["phase_space"] = None
        return

    # Build attractors from the regime stats (state.regimes).
    attractors: List[Dict[str, Any]] = []
    for r in (state.get("regimes") or []):
        if not isinstance(r, dict):
            continue
        # Filter the data to this regime's window when both endpoints look
        # like timestamps; otherwise fall back to the whole series.
        try:
            start = r.get("start"); end = r.get("end")
            if (ts_index is not None and isinstance(start, str)
                    and isinstance(end, str)):
                t0 = pd.to_datetime(start, errors="coerce")
                t1 = pd.to_datetime(end, errors="coerce")
                if pd.notna(t0) and pd.notna(t1):
                    mask = (ts_index >= t0) & (ts_index <= t1)
                    xs = x_series[mask].dropna()
                    ys = y_series[mask].dropna()
                else:
                    xs, ys = x_series.dropna(), y_series.dropna()
            else:
                xs, ys = x_series.dropna(), y_series.dropna()
        except Exception:
            xs, ys = x_series.dropna(), y_series.dropna()
        if len(xs) < 5 or len(ys) < 5:
            continue
        attractors.append({
            "label": r.get("label", "?"),
            "x_center": float(xs.mean()),
            "y_center": float(ys.mean()),
            "x_extent": float(xs.std()) if len(xs) > 1 else 0.0,
            "y_extent": float(ys.std()) if len(ys) > 1 else 0.0,
            "n_points": int(min(len(xs), len(ys))),
        })

    # Bifurcation manifold — line between the two most distant attractor
    # centroids. Frontend draws a dashed separatrix.
    manifold = None
    if len(attractors) >= 2:
        a, b = attractors[0], attractors[1]
        # Use the first two regimes (typically active vs adjacent).
        manifold = {
            "x0": (a["x_center"] + b["x_center"]) / 2.0
                   - (b["y_center"] - a["y_center"]),
            "y0": (a["y_center"] + b["y_center"]) / 2.0
                   + (b["x_center"] - a["x_center"]),
            "x1": (a["x_center"] + b["x_center"]) / 2.0
                   + (b["y_center"] - a["y_center"]),
            "y1": (a["y_center"] + b["y_center"]) / 2.0
                   - (b["x_center"] - a["x_center"]),
            "kind": "midpoint_perpendicular",
        }

    # Historical trajectory — last WORLDLINE_HOURS of (x, y, t).
    history: List[Dict[str, Any]] = []
    try:
        import pandas as pd
        valid = x_series.notna() & y_series.notna()
        if ts_index is not None:
            valid = valid & ts_index.notna()
            t_max = ts_index[valid].max()
            t_min = t_max - pd.Timedelta(hours=WORLDLINE_HOURS)
            sl = (ts_index >= t_min) & valid
            xs = x_series[sl]; ys = y_series[sl]; ts = ts_index[sl]
        else:
            xs = x_series[valid]; ys = y_series[valid]; ts = None
        n_samples = 48
        step = max(1, len(xs) // n_samples)
        idxs = list(range(0, len(xs), step))[:n_samples]
        if idxs and idxs[-1] != len(xs) - 1:
            idxs.append(len(xs) - 1)
        for i in idxs:
            entry: Dict[str, Any] = {
                "x": float(xs.iloc[i]),
                "y": float(ys.iloc[i]),
            }
            if ts is not None:
                tv = ts.iloc[i]
                entry["t"] = tv.isoformat() if hasattr(tv, "isoformat") else str(tv)
            history.append(entry)
    except Exception:
        history = []

    # Predicted future trajectory — only meaningful when the runner's
    # forecast is for one of the two axes. Otherwise emit empty list.
    future: List[Dict[str, Any]] = []
    fc = state.get("forecast") or {}
    if fc.get("available"):
        target = (fc.get("target") or "").lower()
        if target == x_col.lower() or target == y_col.lower():
            for p in (fc.get("points") or []):
                if not isinstance(p, dict):
                    continue
                v = _safe_float(p.get("value"))
                ci_low = _safe_float(p.get("ci_low"))
                ci_high = _safe_float(p.get("ci_high"))
                step_idx = p.get("step")
                t_off = float(step_idx) if step_idx is not None else None
                # The non-target axis stays at the last historical value.
                if not history:
                    continue
                last_x = history[-1]["x"]
                last_y = history[-1]["y"]
                if target == x_col.lower():
                    future.append({
                        "x": v if v is not None else last_x,
                        "y": last_y,
                        "t": t_off,
                        "ci_radius": (
                            (ci_high - ci_low) / 2.0 if ci_high is not None
                                                          and ci_low is not None
                            else None
                        ),
                    })
                else:
                    future.append({
                        "x": last_x,
                        "y": v if v is not None else last_y,
                        "t": t_off,
                        "ci_radius": (
                            (ci_high - ci_low) / 2.0 if ci_high is not None
                                                          and ci_low is not None
                            else None
                        ),
                    })

    sm["phase_space"] = {
        "x_axis": x_col,
        "y_axis": y_col,
        "attractors": attractors,
        "manifold": manifold,
        "history": history,
        "future": future,
        "current": (history[-1] if history else None),
    }


# ---------------------------------------------------------------------------
# B.4: Resonance detection (only new computation in V5.B)
# ---------------------------------------------------------------------------

def _coherence_peak(x, y, sample_rate_per_min: float = 1.0
                     ) -> Optional[Tuple[float, float]]:
    """FFT-based magnitude-squared coherence peak finder.

    Returns (period_minutes, coherence) for the strongest periodic band
    where coherence > RESONANCE_COHERENCE_FLOOR, or None when the pair
    has no strong shared oscillation.

    sample_rate_per_min: samples per minute (1.0 = one sample/min).
    Used to convert FFT bin index → period in minutes.
    """
    import numpy as np
    if x is None or y is None:
        return None
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = min(len(x), len(y))
    # Need enough samples to resolve periodic structure.
    if n < 64:
        return None
    x = x[:n]; y = y[:n]
    # Drop NaNs jointly.
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 64:
        return None
    x = x[mask]; y = y[mask]
    # De-mean.
    x = x - x.mean()
    y = y - y.mean()
    if x.std() < 1e-10 or y.std() < 1e-10:
        return None
    # Window to reduce spectral leakage.
    w = np.hanning(len(x))
    xw = x * w
    yw = y * w
    Pxx = np.abs(np.fft.rfft(xw)) ** 2
    Pyy = np.abs(np.fft.rfft(yw)) ** 2
    Pxy = np.fft.rfft(xw) * np.conj(np.fft.rfft(yw))
    denom = Pxx * Pyy
    # Avoid division by zero / numerical noise at DC.
    coh = np.zeros_like(Pxx, dtype=float)
    valid = denom > 1e-14
    coh[valid] = (np.abs(Pxy[valid]) ** 2) / denom[valid]
    # Search for the strongest peak in the periodic band of interest.
    freqs = np.fft.rfftfreq(len(x), d=1.0 / sample_rate_per_min)  # cycles/min
    best_period = None
    best_coh = 0.0
    for k in range(1, len(coh)):    # skip DC
        if freqs[k] <= 0:
            continue
        period_min = 1.0 / freqs[k]
        if period_min < RESONANCE_MIN_PERIOD_MIN:
            continue
        if period_min > RESONANCE_MAX_PERIOD_MIN:
            continue
        if coh[k] > best_coh:
            best_coh = float(coh[k])
            best_period = float(period_min)
    if best_period is None or best_coh < RESONANCE_COHERENCE_FLOOR:
        return None
    return best_period, best_coh


def enrich_with_resonances(state: Dict[str, Any]) -> None:
    """B.4: cross-coherence between every pair of node time series.

    Emits the top-K strongest resonances on system_model.resonances. Each:
        {node_a, node_b, period_minutes, coherence}

    Capped at RESONANCE_TOP_K (5) to keep the rendering clean. The
    computation is bounded: O(n_nodes² · n_samples · log n_samples), and
    n_samples is already capped to the worldline window.
    """
    sm = state.get("system_model")
    if not isinstance(sm, dict):
        return
    nodes = _nodes_from_system_model(sm)
    if len(nodes) < 2:
        sm["resonances"] = []
        return
    df, _, _ = _load_dataset_lazy(state)
    if df is None:
        sm["resonances"] = []
        return
    # Resolve each node to a numeric array (dataset-column-backed).
    cols_lower = {str(c).lower(): c for c in df.columns}
    series_map: Dict[str, Any] = {}
    try:
        import pandas as pd
        for n in nodes:
            col = n.get("data_column") or n.get("id")
            resolved = (col if col in df.columns
                        else cols_lower.get(str(col or "").lower()))
            if not resolved:
                continue
            s = pd.to_numeric(df[resolved], errors="coerce")
            arr = s.dropna().to_numpy()
            if len(arr) >= 64:
                series_map[n["id"]] = arr
    except Exception:
        sm["resonances"] = []
        return
    # Try to derive sample rate from the structure cadence (seconds).
    structure = state.get("structure") or {}
    cadence = structure.get("cadence") or ""
    sample_rate_per_min = 1.0
    try:
        # Cadence strings observed: "5m", "30s", "1h"
        c = str(cadence).strip().lower()
        if c.endswith("s") and c[:-1].isdigit():
            sec = float(c[:-1])
            if sec > 0:
                sample_rate_per_min = 60.0 / sec
        elif c.endswith("m") and c[:-1].isdigit():
            mn = float(c[:-1])
            if mn > 0:
                sample_rate_per_min = 1.0 / mn
        elif c.endswith("h") and c[:-1].isdigit():
            hr = float(c[:-1])
            if hr > 0:
                sample_rate_per_min = 1.0 / (hr * 60)
    except Exception:
        pass
    # Pairwise coherence search.
    found: List[Dict[str, Any]] = []
    ids = list(series_map.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            res = _coherence_peak(series_map[a], series_map[b],
                                    sample_rate_per_min=sample_rate_per_min)
            if res is None:
                continue
            period, coh = res
            found.append({
                "node_a": a, "node_b": b,
                "period_minutes": round(period, 2),
                "coherence": round(coh, 3),
            })
    found.sort(key=lambda r: -r["coherence"])
    sm["resonances"] = found[:RESONANCE_TOP_K]


# ---------------------------------------------------------------------------
# Public entry: V5.B.1 enrichment (B.1 + B.3 + B.6)
# ---------------------------------------------------------------------------

def enrich_state_for_v5(state: Dict[str, Any], *,
                         enable_history: bool = True,
                         enable_causal_lags: bool = True,
                         enable_future_events: bool = True,
                         enable_forecast_cones: bool = True,
                         enable_phase_space: bool = True,
                         enable_resonances: bool = True) -> None:
    """Run the full V5.B enrichment suite. Each step is independently
    fail-safe; missing upstream data → the field is null/empty and the
    frontend degrades per-feature."""
    if not isinstance(state, dict):
        return
    if state.get("system_model") is None:
        return
    if enable_history:
        try:
            enrich_with_history(state)
        except Exception:
            pass
    if enable_forecast_cones:
        try:
            enrich_with_forecast_cones(state)
        except Exception:
            pass
    if enable_causal_lags:
        try:
            enrich_with_causal_lags(state)
        except Exception:
            pass
    if enable_future_events:
        try:
            enrich_with_future_events(state)
        except Exception:
            pass
    if enable_phase_space:
        try:
            enrich_with_phase_space(state)
        except Exception:
            pass
    if enable_resonances:
        try:
            enrich_with_resonances(state)
        except Exception:
            pass

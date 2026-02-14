from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import numpy as np


def _clip(v: float, lo: Optional[float], hi: Optional[float]) -> float:
    if lo is not None and v < lo:
        return lo
    if hi is not None and v > hi:
        return hi
    return v


def project_forecast_bands(
    forecast: Dict[str, Any],
    lo: Optional[float] = None,
    hi: Optional[float] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    forecast: expects something like:
      { "bands": {"p05":[...], "p50":[...], "p95":[...]}, "horizon": N, ... }
    We clamp each band value into [lo, hi] if provided and report edits.
    """
    out = dict(forecast or {})
    bands = (out.get("bands") or {})
    if not isinstance(bands, dict):
        return out, {"ok": False, "reason": "no_bands_dict"}

    edits = 0
    total = 0

    new_bands: Dict[str, Any] = {}
    for k, arr in bands.items():
        if not isinstance(arr, list):
            new_bands[k] = arr
            continue
        new_arr = []
        for x in arr:
            total += 1
            try:
                xf = float(x)
            except Exception:
                new_arr.append(x)
                continue
            xf2 = _clip(xf, lo, hi)
            if xf2 != xf:
                edits += 1
            new_arr.append(xf2)
        new_bands[k] = new_arr

    out["bands"] = new_bands

    summary = {
        "ok": True,
        "bounds": {"lo": lo, "hi": hi},
        "values_total": int(total),
        "values_clipped": int(edits),
        "fraction_clipped": float(edits / total) if total else 0.0,
        "note": "projection applied post-simulation; model unchanged"
    }
    return out, summary

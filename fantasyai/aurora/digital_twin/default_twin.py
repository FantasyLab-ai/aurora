from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

Json = Dict[str, Any]


def _read_json(p: Path) -> Optional[Json]:
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(p: Path, obj: Json) -> None:
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _first_recommended_target(contract: Json) -> Optional[str]:
    targets = (contract or {}).get("recommended_targets", []) or (contract or {}).get("targets", []) or []
    if not targets:
        return None
    t0 = targets[0]
    if isinstance(t0, str):
        return t0
    if isinstance(t0, dict):
        return t0.get("name") or t0.get("col") or t0.get("target")
    return None


# -------------------------------------------------------------------------
# Existing API (KEEP): this is what your earlier code likely calls.
# -------------------------------------------------------------------------
def run_default_digital_twin(schema_contract: Dict[str, Any], df_numeric: pd.DataFrame) -> Dict[str, Any]:
    """
    Minimal digital twin: build a simple, explainable 'state' from numeric columns
    and schema_contract recommended targets.

    NOTE: This function remains intentionally pure (no disk writes) to avoid breaking
    older call sites. Use run_default_digital_twin_from_run(...) for stateful usage.
    """
    schema_contract = schema_contract or {}
    df_numeric = df_numeric if df_numeric is not None else pd.DataFrame()

    if df_numeric.shape[0] == 0 or df_numeric.shape[1] == 0:
        return {
            "ok": False,
            "reason": "no_numeric_data",
            "rows": int(df_numeric.shape[0]),
            "cols": int(df_numeric.shape[1]),
        }

    # Choose a target
    target = _first_recommended_target(schema_contract)
    if not target or target not in df_numeric.columns:
        # fallback: pick the first numeric col
        target = df_numeric.columns[0]

    y = pd.to_numeric(df_numeric[target], errors="coerce").astype(float)
    y = y.replace([np.inf, -np.inf], np.nan).dropna()

    if len(y) < 10:
        return {
            "ok": False,
            "reason": "target_too_sparse",
            "target": target,
            "n_nonnull": int(len(y)),
        }

    # Simple state estimates (placeholder but stable)
    state = {
        "target": target,
        "n": int(len(y)),
        "mean": float(np.nanmean(y)),
        "std": float(np.nanstd(y)),
        "p05": float(np.nanpercentile(y, 5)),
        "p50": float(np.nanpercentile(y, 50)),
        "p95": float(np.nanpercentile(y, 95)),
        "last_value": float(y.iloc[-1]),
    }

    return {"ok": True, "state": state}


# -------------------------------------------------------------------------
# New stateful wrapper: this is what Task Runtime + your API should call.
# -------------------------------------------------------------------------
def run_default_digital_twin_from_run(
    run_dir: str,
    structure_contract: Optional[Dict[str, Any]] = None,
    max_rows: int = 5000,
) -> Dict[str, Any]:
    """
    Contract-driven, stateful twin update:
      - loads contract from run_dir if not provided
      - loads dataset path from _RUN_META.json if available
      - samples max_rows for safety
      - writes twin_state.json and (optional) active_target.json
    """
    rd = Path(run_dir)
    if not rd.exists():
        return {"ok": False, "error": f"run_dir not found: {run_dir}"}

    contract = structure_contract or _read_json(rd / "structure_contract.json") \
        or _read_json(rd / "schema_contract.json") \
        or _read_json(rd / "structure.json") \
        or {}

    # Load dataset path from meta (best effort)
    meta = _read_json(rd / "_RUN_META.json") or {}
    table_path = meta.get("table") or meta.get("resolved", {}).get("table")

    df: Optional[pd.DataFrame] = None
    load_err: Optional[str] = None

    if table_path and Path(str(table_path)).exists():
        p = Path(str(table_path))
        try:
            # Best effort robust load. Keep it lightweight.
            df = pd.read_csv(p, engine="python", on_bad_lines="skip", nrows=max_rows)
        except Exception as e:
            load_err = f"read_csv_failed:{type(e).__name__}:{e}"

    if df is None:
        # fallback: try to load a cached df_for_engine if you write it later
        load_err = load_err or "no_table_available_in_meta"
        return {"ok": False, "error": load_err, "table_path": table_path}

    # numeric subset for twin
    df_numeric = df.select_dtypes(include=[np.number]).copy()

    twin_out = run_default_digital_twin(schema_contract=contract, df_numeric=df_numeric)

    # Persist state
    _write_json(rd / "twin_state.json", twin_out)

    # Update active_target if possible (helps downstream UI)
    if twin_out.get("ok") and isinstance(twin_out.get("state"), dict):
        tgt = twin_out["state"].get("target")
        if tgt:
            _write_json(rd / "active_target.json", {"active_target": tgt})

    return {"wrote": "twin_state.json", "twin": twin_out}

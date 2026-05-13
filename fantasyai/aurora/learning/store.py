"""
Cross-run learning store: append-only JSONL ledger at ~/.aurora/learning_store.jsonl.

Each row captures everything we'd want to look up later when a new run
arrives with similar properties:

* ``dataset_key`` — original CSV path (lets us identify re-runs)
* ``domain``      — best-guess domain bucket from the connector / column heuristics
* ``target_col`` / ``time_col``
* ``calculus``    — full CalculusSignature dict (shape, derivatives, integral, FFT)
* ``best_fit``    — name + RMSE + AIC of winning physics_models candidate
* ``best_prior``  — top prior match (id + display_name + confidence) if any
* ``n_rows``      — dataset size (for "we trust this signature more on bigger data")
* ``run_dir``     — absolute path back to the run that produced the row
* ``ts``          — append time

The store is a flat JSONL — easy to grep, easy to re-load, never blocks the
main pipeline. We deliberately don't use a DB because:
1. Aurora is local-first; no service dependencies.
2. JSONL gives us trivial provenance + diff visibility.
3. At realistic scale (thousands of runs), reading the whole file is cheap.

If/when the file gets unwieldy, we can layer a SQLite cache on top without
changing the row schema.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def default_store_path() -> Path:
    """~/.aurora/learning_store.jsonl — created on first append.

    Override with ``AURORA_LEARNING_STORE`` env var (mostly for tests).
    """
    override = os.environ.get("AURORA_LEARNING_STORE")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".aurora" / "learning_store.jsonl"


@dataclass
class LearningStore:
    """Thin wrapper around a JSONL file. Stateless; everything operates on the path."""
    path: Path

    @classmethod
    def default(cls) -> "LearningStore":
        return cls(path=default_store_path())

    def ensure_exists(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, row: Dict[str, Any]) -> None:
        self.ensure_exists()
        # Defensive: drop anything that won't survive JSON.
        clean = _json_safe(row)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(clean, separators=(",", ":")) + "\n")

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                # Skip corrupt lines silently; learning is enrichment, not critical.
                continue
        return rows

    def count(self) -> int:
        return len(self.read_all())


def _json_safe(obj: Any) -> Any:
    """Recursively convert obj to something json.dumps can handle.

    Critically: replace NaN/Inf floats with None so the resulting JSONL is
    valid (json.dumps with default settings emits NaN as the string 'NaN'
    which strict parsers reject)."""
    import math
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    # Fall back to str() for everything else (Path, datetime, etc).
    try:
        return str(obj)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Convenience: build a row from the artifacts of a finished run
# ---------------------------------------------------------------------------

def build_run_row(run_dir: Path,
                  state: Dict[str, Any],
                  domain: Optional[str] = None) -> Dict[str, Any]:
    """Turn the assembled state dict into a learning row.

    Pulls only the fields useful for cross-run lookup. Pure function; the
    caller decides whether/where to write.
    """
    physics = (state.get("physics") or {})
    law = physics.get("discovered_law") or {}
    calc = physics.get("calculus") or {}
    prior_matches = physics.get("prior_matches") or []
    best_prior = None
    if prior_matches:
        m0 = prior_matches[0] or {}
        best_prior = {
            "id": m0.get("prior_id"),
            "display_name": m0.get("display_name"),
            "confidence": m0.get("confidence"),
            "domain": m0.get("domain"),
        }
    dataset = state.get("dataset") or {}
    structure = state.get("structure") or {}
    return {
        "row_type": "run_learning_v1",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_dir": str(run_dir),
        "dataset_name": dataset.get("name"),
        "dataset_rows": dataset.get("rows"),
        "dataset_cols": dataset.get("cols"),
        "target_col": calc.get("target_col") or law.get("target_col"),
        "time_col": calc.get("time_col") or structure.get("time_col"),
        "domain": domain or _guess_domain(dataset, best_prior),
        "best_fit": {
            "name": law.get("name"),
            "rmse": law.get("rmse"),
            "aic": law.get("aic"),
        } if law else None,
        "best_prior": best_prior,
        "calculus": {
            "likely_shape": calc.get("likely_shape"),
            "confidence": calc.get("confidence"),
            "monotonic": calc.get("monotonic"),
            "dy_dt_mean": calc.get("dy_dt_mean"),
            "dy_dt_std": calc.get("dy_dt_std"),
            "d2y_dt2_mean": calc.get("d2y_dt2_mean"),
            "inflection_points": calc.get("inflection_points"),
            "dominant_frequency": calc.get("dominant_frequency"),
            "n_points": calc.get("n_points"),
        } if calc.get("available") else None,
    }


def _guess_domain(dataset: Dict[str, Any], best_prior: Optional[Dict[str, Any]]) -> str:
    """Cheap domain heuristic: prefer the prior's domain, else infer from the
    dataset name. Used for filtering meta-learner suggestions."""
    if best_prior and best_prior.get("domain"):
        return str(best_prior["domain"])
    name = (dataset.get("name") or "").lower()
    if any(t in name for t in ("temp", "weather", "climate", "humidity", "pressure")):
        return "enviro"
    if any(t in name for t in ("price", "stock", "gdp", "cpi", "fed", "interest")):
        return "finance"
    if any(t in name for t in ("voltage", "current", "rpm", "torque", "vibration", "bearing")):
        return "industrial"
    if any(t in name for t in ("patient", "clinical", "diabetes", "mimic", "blood", "heart")):
        return "bio"
    if any(t in name for t in ("seismic", "quake", "geo", "magnitude")):
        return "research"
    return "unknown"


# ---------------------------------------------------------------------------
# Module-level conveniences (the common path)
# ---------------------------------------------------------------------------

def append_run(run_dir: Path, state: Dict[str, Any],
               domain: Optional[str] = None,
               store: Optional[LearningStore] = None) -> Optional[Dict[str, Any]]:
    """Build a row from the run state and append it to the global store.

    Returns the row that was written (None on failure). Failures are silent
    by design — learning is enrichment, never blocking.
    """
    try:
        store = store or LearningStore.default()
        row = build_run_row(run_dir, state, domain=domain)
        store.append(row)
        return row
    except Exception:
        return None


def read_all(store: Optional[LearningStore] = None) -> List[Dict[str, Any]]:
    store = store or LearningStore.default()
    return store.read_all()

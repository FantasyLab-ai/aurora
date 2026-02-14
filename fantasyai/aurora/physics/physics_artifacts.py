from __future__ import annotations

from typing import Any, Dict, Optional
from pathlib import Path
import json
import pandas as pd

from .constraints import infer_basic_constraints, check_constraints


def run_physics_checks(df: pd.DataFrame) -> Dict[str, Any]:
    constraints = infer_basic_constraints(df)
    report = check_constraints(df, constraints)

    return {
        "constraints_inferred": [
            {"name": c.name, "columns": c.columns, "kind": c.kind, "params": c.params}
            for c in constraints
        ],
        "report": report,
    }


def write_physics_artifacts(run_dir: str, df: pd.DataFrame) -> Dict[str, str]:
    rd = Path(run_dir)
    rd.mkdir(parents=True, exist_ok=True)

    physics = run_physics_checks(df)

    out_path = rd / "physics_checks.json"
    out_path.write_text(json.dumps(physics, indent=2), encoding="utf-8")

    return {"physics_checks": str(out_path)}

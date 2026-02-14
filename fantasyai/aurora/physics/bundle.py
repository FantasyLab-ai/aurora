from __future__ import annotations

from typing import Any, Dict
from pathlib import Path
import json
import pandas as pd

from .constraints import infer_basic_constraints, check_constraints
from .physics_features import build_physics_features, discover_candidate_invariants
from .mechanistic import try_mechanistic_fit


def write_physics_bundle(run_dir: str, df: pd.DataFrame) -> Dict[str, str]:
    rd = Path(run_dir)
    rd.mkdir(parents=True, exist_ok=True)

    # checks
    constraints = infer_basic_constraints(df)
    checks = {
        "constraints_inferred": [
            {"name": c.name, "columns": c.columns, "kind": c.kind, "params": c.params}
            for c in constraints
        ],
        "report": check_constraints(df, constraints),
    }
    p_checks = rd / "physics_checks.json"
    p_checks.write_text(json.dumps(checks, indent=2), encoding="utf-8")

    # features
    feats = build_physics_features(df)
    p_feats = rd / "physics_features.json"
    p_feats.write_text(json.dumps(feats, indent=2), encoding="utf-8")

    # invariants
    inv = discover_candidate_invariants(df)
    p_inv = rd / "physics_invariants.json"
    p_inv.write_text(json.dumps(inv, indent=2), encoding="utf-8")

    # mechanistic stub
    mech = try_mechanistic_fit(df)
    p_mech = rd / "mechanistic_fit.json"
    p_mech.write_text(json.dumps(mech, indent=2), encoding="utf-8")

    return {
        "physics_checks": str(p_checks),
        "physics_features": str(p_feats),
        "physics_invariants": str(p_inv),
        "mechanistic_fit": str(p_mech),
    }

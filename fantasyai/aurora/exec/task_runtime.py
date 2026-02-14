from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def load_structure_contract(run_dir: str) -> Json:
    """
    Load the best available contract from the run directory.
    We support multiple names because your system evolved over time.
    """
    rd = Path(run_dir)
    for name in ["structure_contract.json", "schema_contract.json", "structure.json", "schema_contract.json"]:
        j = _read_json(rd / name)
        if isinstance(j, dict) and j:
            return j
    return {}


def _first_recommended_target(contract: Json) -> Optional[str]:
    targets = contract.get("recommended_targets") or contract.get("targets") or []
    if not targets:
        return None
    t0 = targets[0]
    if isinstance(t0, str):
        return t0
    if isinstance(t0, dict):
        return t0.get("name") or t0.get("col") or t0.get("target")
    return None


def list_tasks() -> List[Json]:
    """
    Source of truth for what Aurora is allowed to execute.
    Your UI can render these. Your API can validate against these.
    """
    return [
        {"task_id": "set_active_target", "label": "Set active target (contract-driven)"},
        {"task_id": "run_default_digital_twin", "label": "Run digital twin update (stateful)"},
    ]


def suggest_tasks(run_dir: str) -> List[Json]:
    contract = load_structure_contract(run_dir)
    target = _first_recommended_target(contract)

    out: List[Json] = []

    if target:
        out.append({
            "id": "set_active_target",
            "title": f"Set active target → {target}",
            "prompt": f"Set the active target column to '{target}' (from schema/structure contract).",
            "args": {"target": target},
        })

    out.append({
        "id": "run_default_digital_twin",
        "title": "Run digital twin update",
        "prompt": "Run / update the digital twin state from the latest artifacts + contract defaults.",
        "args": {},
    })

    # Always include a freeform slot for UI
    out.append({
        "id": "freeform",
        "title": "Custom request",
        "prompt": "",
        "args": {},
    })

    return out


def execute_task(run_dir: str, task_id: str, args: Optional[Json] = None) -> Json:
    """
    Single official Aurora execution surface.
    Your backend should call THIS (not random internal functions).
    """
    args = args or {}
    rd = Path(run_dir)

    if not rd.exists():
        return {"ok": False, "error": f"run_dir not found: {run_dir}"}

    contract = load_structure_contract(run_dir)
    target_default = _first_recommended_target(contract)

    if task_id == "set_active_target":
        target = args.get("target") or target_default
        if not target:
            return {"ok": False, "error": "No target available (no args.target and no recommended_targets)."}
        _write_json(rd / "active_target.json", {"active_target": target})
        return {"ok": True, "task_id": task_id, "active_target": target}

    if task_id == "run_default_digital_twin":
        from fantasyai.aurora.digital_twin.default_twin import run_default_digital_twin_from_run
        out = run_default_digital_twin_from_run(run_dir=run_dir, structure_contract=contract)
        return {"ok": True, "task_id": task_id, "result": out}

    return {"ok": False, "error": f"Unknown task_id: {task_id}", "known": [t["task_id"] for t in list_tasks()]}

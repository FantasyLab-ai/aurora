from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

DEFAULT_RUNS_DIR = Path("outputs") / "aurora_runs"

def _safe_read_json(p: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def list_runs(runs_dir: Path = DEFAULT_RUNS_DIR) -> List[Dict[str, Any]]:
    runs_dir = Path(runs_dir)
    if not runs_dir.exists():
        return []
    run_dirs = [d for d in runs_dir.iterdir() if d.is_dir()]
    run_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

    out: List[Dict[str, Any]] = []
    for d in run_dirs:
        meta = _safe_read_json(d / "meta.json") or {}
        out.append({
            "run_id": d.name,
            "run_dir": str(d.resolve()),
            "meta": meta,
            "mtime": d.stat().st_mtime,
        })
    return out

def latest_run(runs_dir: Path = DEFAULT_RUNS_DIR) -> Optional[Dict[str, Any]]:
    runs = list_runs(runs_dir=runs_dir)
    return runs[0] if runs else None

def read_run_file(run_id: str, rel_path: str, runs_dir: Path = DEFAULT_RUNS_DIR) -> Path:
    base = (Path(runs_dir) / run_id).resolve()
    target = (base / rel_path).resolve()
    if base not in target.parents and base != target:
        raise ValueError("invalid path traversal")
    if not target.exists():
        raise FileNotFoundError(str(target))
    return target

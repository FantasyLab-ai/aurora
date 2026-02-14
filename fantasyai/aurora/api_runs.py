from __future__ import annotations

import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from fantasyai.aurora.artifacts import default_runs_root, latest_run_dir

router = APIRouter(prefix="/aurora", tags=["aurora"])

@router.get("/runs")
def list_aurora_runs(limit: int = 50):
    root = default_runs_root()
    if not root.exists():
        return {"runs": [], "root": str(root)}
    runs = [p for p in root.iterdir() if p.is_dir()]
    runs = sorted(runs, key=lambda p: p.name, reverse=True)[:limit]
    return {"runs": [p.name for p in runs], "root": str(root)}

@router.get("/runs/latest")
def get_latest_aurora_run():
    p = latest_run_dir()
    if p is None:
        raise HTTPException(status_code=404, detail="No Aurora runs found.")
    return {"run_id": p.name}

@router.get("/runs/{run_id}/summary")
def get_run_summary(run_id: str):
    root = default_runs_root()
    p = root / run_id / "summary.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="summary.json not found.")
    return json.loads(p.read_text(encoding="utf-8"))

@router.get("/runs/{run_id}/report")
def get_run_report(run_id: str):
    root = default_runs_root()
    p = root / run_id / "report.md"
    if not p.exists():
        raise HTTPException(status_code=404, detail="report.md not found.")
    return {"run_id": run_id, "report_md": p.read_text(encoding="utf-8")}

@router.get("/runs/{run_id}/files/{relpath:path}")
def get_run_file(run_id: str, relpath: str):
    root = default_runs_root().resolve()
    p = (root / run_id / relpath).resolve()
    if root not in p.parents:
        raise HTTPException(status_code=400, detail="Invalid path.")
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(str(p))

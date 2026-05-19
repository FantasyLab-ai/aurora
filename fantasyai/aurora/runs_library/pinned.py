"""
Per-workspace pinned-runs tracking.

We persist a small JSON file at
``<workspace_dir>/runs_library/pinned_runs.json``::

    {
        "pinned": [
            {"run_id": "...", "pinned_at": 1716130800.0, "note": "..."},
            ...
        ],
        "schema_version": "1"
    }

The file is workspace-isolated when auth is enabled (Stream 2.4 Phase
2) — each workspace has its own pinned list. Without auth, the
``DEFAULT_WORKSPACE`` is used so single-tenant installs preserve the
pinned list across runs.

Failures are swallowed at the boundary — pinning is a UX nicety, not
a contract-bearing surface. A read error means "no pinned runs", a
write error means "the pin didn't stick" but never raises out.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


# Cap so a runaway script can't pin millions of runs.
MAX_PINNED = 100

_WRITE_LOCK = threading.Lock()


def _workspace_dir(workspace_id: Optional[str]) -> Optional[Path]:
    """Resolve the workspace root via the auth module, with fallback."""
    try:
        from fantasyai.aurora.auth import workspace_dir
        return workspace_dir(workspace_id)
    except Exception:
        # Auth module not available — fall back to the legacy ~/.aurora dir.
        env = os.environ.get("AURORA_DATA_ROOT", "").strip()
        if env:
            return Path(env).expanduser() / "workspaces" / (workspace_id or "default")
        return Path.home() / ".aurora" / "runs_library"


def pinned_file_path(workspace_id: Optional[str] = None) -> Path:
    base = _workspace_dir(workspace_id)
    if base is None:
        base = Path.home() / ".aurora" / "runs_library"
    p = base / "runs_library" / "pinned_runs.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(workspace_id: Optional[str]) -> Dict[str, Any]:
    path = pinned_file_path(workspace_id)
    if not path.exists():
        return {"pinned": [], "schema_version": "1"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"pinned": [], "schema_version": "1"}
        if not isinstance(data.get("pinned"), list):
            data["pinned"] = []
        return data
    except Exception:
        return {"pinned": [], "schema_version": "1"}


def _save(workspace_id: Optional[str], data: Dict[str, Any]) -> None:
    path = pinned_file_path(workspace_id)
    with _WRITE_LOCK:
        try:
            path.write_text(
                json.dumps(data, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass


def list_pinned_runs(workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return the pinned-runs list newest-pin-first.

    Each entry has ``run_id``, ``pinned_at`` (unix seconds), and an
    optional ``note`` string.
    """
    data = _load(workspace_id)
    pinned = list(data.get("pinned") or [])
    pinned.sort(key=lambda e: float(e.get("pinned_at") or 0), reverse=True)
    return pinned


def is_pinned(run_id: str,
              workspace_id: Optional[str] = None) -> bool:
    if not run_id:
        return False
    for e in list_pinned_runs(workspace_id):
        if str(e.get("run_id") or "") == run_id:
            return True
    return False


def pin_run(run_id: str,
            *,
            workspace_id: Optional[str] = None,
            note: Optional[str] = None) -> Dict[str, Any]:
    """Add a run to the pinned list (idempotent).

    Returns the new entry. If already pinned, refreshes ``pinned_at``
    and updates the note (so users can re-pin with a fresh comment).
    """
    if not run_id or not isinstance(run_id, str):
        raise ValueError("run_id is required")
    data = _load(workspace_id)
    pinned: List[Dict[str, Any]] = list(data.get("pinned") or [])
    now = time.time()
    # Update if exists.
    for e in pinned:
        if str(e.get("run_id") or "") == run_id:
            e["pinned_at"] = now
            if note is not None:
                e["note"] = note
            data["pinned"] = pinned
            _save(workspace_id, data)
            return dict(e)
    entry = {
        "run_id":    run_id,
        "pinned_at": now,
        "note":      note,
    }
    pinned.insert(0, entry)
    if len(pinned) > MAX_PINNED:
        # Drop oldest to stay under the cap.
        pinned = sorted(
            pinned, key=lambda e: float(e.get("pinned_at") or 0), reverse=True,
        )[:MAX_PINNED]
    data["pinned"] = pinned
    _save(workspace_id, data)
    return entry


def unpin_run(run_id: str,
              workspace_id: Optional[str] = None) -> bool:
    """Remove a run from the pinned list. Returns True if anything
    actually changed."""
    if not run_id:
        return False
    data = _load(workspace_id)
    pinned = list(data.get("pinned") or [])
    new_pinned = [e for e in pinned if str(e.get("run_id") or "") != run_id]
    if len(new_pinned) == len(pinned):
        return False
    data["pinned"] = new_pinned
    _save(workspace_id, data)
    return True

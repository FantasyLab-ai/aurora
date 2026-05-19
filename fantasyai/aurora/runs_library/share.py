"""
Share-as-bundle — Phase 1 (local file export).

The full v2.0 share story is a public URL hosted by Aurora Cloud, but
that depends on the cloud control plane being live. Phase 1 ships
*today*: extract the run's Aurora Bundle, write a portable
``.aurora.json`` to a workspace-isolated share directory, and surface
the local file path. Any teammate with Aurora installed can
``aurora_sdk.Bundle.load(path).verify()`` against it.

When cloud sharing lights up in Aurora Cloud Phase 3, this module will
gain an ``upload`` mode that mirrors the bundle to the cloud and
returns a public URL. The local-export path stays available either
way.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class ShareInfo:
    ok: bool
    run_id: str
    local_path: Optional[str] = None
    bundle_hash: Optional[str] = None
    n_bytes: Optional[int] = None
    shared_at: Optional[float] = None
    note: Optional[str] = None
    public_url: Optional[str] = None       # populated when cloud upload lights up
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _share_root(workspace_id: Optional[str]) -> Path:
    """Where shared bundles land. Per-workspace isolated."""
    try:
        from fantasyai.aurora.auth import workspace_dir
        base = workspace_dir(workspace_id)
    except Exception:
        import os
        env = os.environ.get("AURORA_DATA_ROOT", "").strip()
        if env:
            base = Path(env).expanduser() / "workspaces" / (workspace_id or "default")
        else:
            base = Path.home() / ".aurora"
    return base / "runs_library" / "shared"


def share_run_bundle(run_dir: Any,
                      *,
                      workspace_id: Optional[str] = None,
                      note: Optional[str] = None) -> ShareInfo:
    """Export the run's Aurora Bundle to a portable file.

    Failure modes:
      * Run dir doesn't exist → returns ok=False with error
      * Bundle file isn't found in the run dir → tries to construct one
        in-memory via the SDK; if that also fails, returns ok=False
      * Write failure → returns ok=False with error
    """
    rd = Path(run_dir)
    if not rd.exists():
        return ShareInfo(ok=False, run_id=rd.name,
                          error=f"run directory not found: {rd}")

    # Step 1: locate an existing bundle in the run dir (the runner
    # writes one as part of its export step). Walk a few common names.
    bundle_doc: Optional[Dict[str, Any]] = None
    for candidate in ("aurora.bundle.json", "bundle.aurora.json",
                       "_BUNDLE.json"):
        p = rd / candidate
        if p.exists():
            try:
                bundle_doc = json.loads(p.read_text(encoding="utf-8"))
                break
            except Exception:
                continue
    # Step 2: if none on disk, ask the SDK to build one.
    if bundle_doc is None:
        try:
            from aurora_sdk import bundle as _sdk_bundle
            built = _sdk_bundle.build_from_run_dir(rd)
            if isinstance(built, dict):
                bundle_doc = built
            elif hasattr(built, "doc"):
                bundle_doc = built.doc
        except Exception:
            pass
    if bundle_doc is None or not isinstance(bundle_doc, dict):
        return ShareInfo(ok=False, run_id=rd.name,
                          error="no bundle present and SDK build failed")

    # Step 3: write the export.
    root = _share_root(workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    out_name = f"{rd.name}__{ts}.aurora.json"
    out_path = root / out_name
    try:
        text = json.dumps(bundle_doc, indent=2, sort_keys=True, default=str)
        out_path.write_text(text, encoding="utf-8")
    except Exception as e:
        return ShareInfo(ok=False, run_id=rd.name,
                          error=f"write failed: {type(e).__name__}: {e}")

    bundle_hash = (bundle_doc.get("integrity") or {}).get("content_hash") \
                  or (bundle_doc.get("integrity") or {}).get("sha256")
    return ShareInfo(
        ok=True,
        run_id=rd.name,
        local_path=str(out_path),
        bundle_hash=bundle_hash,
        n_bytes=out_path.stat().st_size,
        shared_at=time.time(),
        note=note,
        public_url=None,        # filled in by Aurora Cloud Phase 3
    )

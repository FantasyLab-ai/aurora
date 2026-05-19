"""
Workspace = "where this user's data lives on disk."

A workspace_id is a short, opaque, filesystem-safe slug (lowercase
alpha/numeric/underscore). Each workspace gets its own subdirectory
under ``AURORA_DATA_ROOT``::

    /var/aurora/workspaces/<workspace_id>/

with this layout::

    workspaces/alice/
      runs/                    ← aurora_dataset_runs/
      kb/                      ← knowledge bank
      contracts/               ← decision contract output
      uploads/                 ← per-session data uploads
      usage.jsonl              ← per-workspace usage log (Phase 2 billing)

The phase-1 single-tenant Aurora wrote to ``./aurora-data/`` /
``./outputs/`` directly. Phase 2 routes those through
``workspace_dir(...)`` so each tenant gets isolated storage.

When auth is disabled (or no token is supplied), everyone shares the
``DEFAULT_WORKSPACE`` — preserving Phase 1 behaviour.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


DEFAULT_WORKSPACE = "default"

# Filesystem-safe workspace id pattern. We tolerate hyphens and
# underscores; reject everything else so a stray "../" can't escape.
_VALID_WORKSPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def data_root() -> Path:
    """Root directory under which workspace subdirs live.

    Falls back to ``./aurora-data`` (matches docker-compose's bind
    mount) when ``AURORA_DATA_ROOT`` is unset.
    """
    env = os.environ.get("AURORA_DATA_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    return Path("./aurora-data").resolve()


def _validate(workspace_id: str) -> str:
    wid = (workspace_id or "").strip().lower()
    if not _VALID_WORKSPACE_RE.match(wid):
        # Don't echo the input back to the user — it might be a
        # path-injection attempt. Just refuse.
        raise ValueError("invalid workspace_id")
    return wid


def workspace_dir(workspace_id: Optional[str] = None,
                    *, create: bool = True) -> Path:
    """Return ``<data_root>/workspaces/<workspace_id>/``.

    Creates the directory tree by default. Pass ``create=False`` for
    pure-read paths where you don't want the side effect.
    """
    wid = _validate(workspace_id or DEFAULT_WORKSPACE)
    p = data_root() / "workspaces" / wid
    if create:
        for sub in ("runs", "kb", "contracts", "uploads"):
            (p / sub).mkdir(parents=True, exist_ok=True)
    return p


def workspace_subdir(workspace_id: str, sub: str, *,
                      create: bool = True) -> Path:
    """Resolve one of the canonical subdirectories under a workspace.

    ``sub`` must be one of ``runs / kb / contracts / uploads``.
    Anything else raises (no arbitrary path joins from caller input).
    """
    if sub not in ("runs", "kb", "contracts", "uploads"):
        raise ValueError(f"unknown workspace subdir {sub!r}")
    base = workspace_dir(workspace_id, create=create)
    p = base / sub
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p

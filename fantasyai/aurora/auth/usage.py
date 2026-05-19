"""
Per-workspace usage log.

Every authenticated endpoint that costs compute or storage appends a
single JSON line to ``<workspace_dir>/usage.jsonl``. The shape::

    {
        "ts": 1716130800.123,        # unix seconds
        "workspace_id": "alice",
        "endpoint": "/api/run/start",
        "method": "POST",
        "kind": "run_started",       # short canonical event type
        "meta": {...},               # endpoint-specific payload
    }

The aggregator that rolls these up into hourly/daily counters lives in
Aurora Cloud Phase 3 (the managed offering). This module is the
recorder + reader so on-prem fleets can write their own billing
adapters today.

Why JSONL not a database: append-only, durable, trivial to ship to S3
/ external billing systems with logrotate. A DB would couple
everything to a schema and migration story this early in the product.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .workspaces import workspace_dir, DEFAULT_WORKSPACE


_USAGE_WRITE_LOCK = threading.Lock()


def usage_log_path(workspace_id: Optional[str] = None) -> Path:
    """Where this workspace's usage log lives."""
    wid = workspace_id or DEFAULT_WORKSPACE
    # Allow override via env for callers that ship logs centrally.
    env = os.environ.get("AURORA_USAGE_LOG", "").strip()
    if env:
        return Path(env).expanduser()
    return workspace_dir(wid, create=True) / "usage.jsonl"


def record_usage(
    workspace_id: Optional[str],
    endpoint: str,
    *,
    method: str = "GET",
    kind: str = "request",
    meta: Optional[Dict[str, Any]] = None,
    ts: Optional[float] = None,
) -> None:
    """Append a single usage event.

    Failures are swallowed — usage logging MUST NEVER take down a
    request. If the disk is full / read-only / the path is bad, we
    just drop the event.
    """
    try:
        path = usage_log_path(workspace_id)
        line = json.dumps({
            "ts":           float(ts) if ts is not None else time.time(),
            "workspace_id": workspace_id or DEFAULT_WORKSPACE,
            "endpoint":     str(endpoint),
            "method":       str(method).upper(),
            "kind":         str(kind),
            "meta":         meta or {},
        }, default=str)
        with _USAGE_WRITE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def read_usage_events(
    workspace_id: Optional[str] = None,
    *,
    since_ts: Optional[float] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Read usage events for a workspace, newest-last.

    Args:
        workspace_id: workspace to filter. None → DEFAULT_WORKSPACE.
        since_ts:     only return events with ``ts >= since_ts``.
        limit:        maximum events to return (None = all).
    """
    path = usage_log_path(workspace_id)
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if since_ts is not None and float(ev.get("ts") or 0) < since_ts:
                    continue
                out.append(ev)
    except Exception:
        return out
    if limit is not None and limit >= 0:
        return out[-int(limit):]
    return out


def summarize_usage(
    workspace_id: Optional[str] = None,
    *,
    since_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Compact per-endpoint + per-kind counts. Useful for status pages."""
    events = read_usage_events(workspace_id, since_ts=since_ts)
    per_endpoint: Dict[str, int] = {}
    per_kind: Dict[str, int] = {}
    for ev in events:
        ep = str(ev.get("endpoint") or "")
        kd = str(ev.get("kind") or "")
        per_endpoint[ep] = per_endpoint.get(ep, 0) + 1
        per_kind[kd] = per_kind.get(kd, 0) + 1
    return {
        "n_events": len(events),
        "per_endpoint": per_endpoint,
        "per_kind": per_kind,
        "first_ts": events[0]["ts"] if events else None,
        "last_ts":  events[-1]["ts"] if events else None,
    }

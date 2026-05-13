"""
In-process run registry — tracks the lifecycle of every analysis kicked off
through ``POST /api/run`` so the frontend can poll without blocking.

The registry persists progress to ``_RUN_PROGRESS.json`` next to the run's
artifacts so a freshly-loaded server (or a test) can resume reading status.
The in-memory dict is the source of truth while the run is in-flight; it
falls back to disk for completed runs.

Public API:

    run_id = make_run_id(dataset_path)
    register(run_id, dataset_path)                    # state=PENDING
    set_state(run_id, RunState.RUNNING)
    set_tier(run_id, tier=1, state=RunState.RUNNING, progress_pct=...)
    set_run_dir(run_id, run_dir)
    complete(run_id, run_dir=..., cached=False)
    fail(run_id, error="...")
    cancel(run_id)
    snapshot(run_id) -> dict                          # for /api/run/status

The lifecycle vocabulary (state strings) matches the polling-state-machine
spec delivered before code:

  pending → running → tier1_complete → tier2_running → tier2_complete →
  tier3_running → complete | failed | cancelled

Rules
-----
* All mutations are thread-safe (single mutex over the registry dict).
* Writing to disk is best-effort — the in-memory state is authoritative.
* The registry is process-local. Restarting the server resets in-flight
  status; on-disk completed runs remain inspectable via build_state.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_log = logging.getLogger("aurora.registry")


# Default outputs root used by both rehydrate-on-import (B-1) and the
# placeholder run_dir derivation (B-2). Honors the same env var the
# orchestrator and state_builder use so tests can isolate.
def _default_outputs_root() -> Path:
    return Path(os.environ.get("AURORA_OUTPUTS_ROOT")
                or "outputs/aurora_dataset_runs")


# ---------------------------------------------------------------------------
# State vocabulary
# ---------------------------------------------------------------------------

class RunState:
    PENDING = "pending"
    RUNNING = "running"                 # generic "started, no tier yet"
    TIER0_RUNNING = "tier0_running"     # structure inference
    TIER1_RUNNING = "tier1_running"
    TIER1_COMPLETE = "tier1_complete"
    TIER2_RUNNING = "tier2_running"
    TIER2_COMPLETE = "tier2_complete"
    TIER3_RUNNING = "tier3_running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED}
RUNNING_STATES = {
    RunState.RUNNING, RunState.PENDING,
    RunState.TIER0_RUNNING, RunState.TIER1_RUNNING,
    RunState.TIER2_RUNNING, RunState.TIER3_RUNNING,
}


# ---------------------------------------------------------------------------
# Run record
# ---------------------------------------------------------------------------

@dataclass
class RunRecord:
    run_id: str
    dataset_path: str
    state: str = RunState.PENDING
    current_tier: Optional[int] = None
    progress_pct: float = 0.0
    message: str = ""
    started_at: str = ""
    completed_at: Optional[str] = None
    run_dir: Optional[str] = None
    cached: bool = False
    tiered: bool = False
    error: Optional[str] = None
    seed: Optional[int] = None
    flags: Dict[str, Any] = field(default_factory=dict)
    # Atom 3.2: user-provided seed text — what they're trying to figure out.
    # When set, state_builder invokes the SeedRanker so findings split into
    # "relevant_findings" + "surprise_findings". Affects ordering, never
    # exclusion. Source tracks who set the seed (user, copilot, none).
    seed_text: Optional[str] = None
    seed_source: Optional[str] = None     # "user" | "copilot" | "none"
    seed_at: Optional[str] = None
    # Per-tier history so the UI can show transitions in order.
    transitions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def elapsed_seconds(self) -> Optional[float]:
        try:
            t0 = datetime.fromisoformat(self.started_at.replace("Z", ""))
        except Exception:
            return None
        end = (datetime.fromisoformat(self.completed_at.replace("Z", ""))
               if self.completed_at else datetime.utcnow())
        return max(0.0, (end - t0).total_seconds())


# ---------------------------------------------------------------------------
# Registry — module-level singleton
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()
_RUNS: Dict[str, RunRecord] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(dataset_path: str) -> str:
    """Deterministic-ish: timestamp + dataset basename suffix."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    base = Path(str(dataset_path)).stem
    # Sanitize basename for use in filesystem paths.
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)
    return f"{ts}__{safe}"


def register(run_id: str, dataset_path: str, *,
             seed: Optional[int] = None,
             flags: Optional[Dict[str, Any]] = None,
             seed_text: Optional[str] = None) -> RunRecord:
    """Create a new RunRecord in PENDING state.

    Atom 3.2: ``seed_text`` is the user's optional natural-language
    statement of what they're trying to figure out. It's stored on the
    record and persisted to disk so the SeedRanker can pick it up.
    """
    with _LOCK:
        rec = RunRecord(
            run_id=run_id,
            dataset_path=str(dataset_path),
            state=RunState.PENDING,
            started_at=_now_iso(),
            seed=seed,
            flags=flags or {},
        )
        if seed_text and seed_text.strip():
            rec.seed_text = seed_text.strip()
            rec.seed_source = "user"
            rec.seed_at = rec.started_at
        _RUNS[run_id] = rec
        rec.transitions.append({"ts": rec.started_at, "state": rec.state})
        # B-2: persist immediately so the run is recoverable from disk
        # even before the orchestrator has chosen a real run_dir. Uses
        # the placeholder ``<outputs_root>/<run_id>/`` derived in
        # _resolve_persist_dir.
        _persist(rec)
        return rec


def set_state(run_id: str, state: str, *,
              message: str = "",
              progress_pct: Optional[float] = None,
              current_tier: Optional[int] = None,
              tiered: Optional[bool] = None) -> None:
    """Update a record's state. No-op if the run isn't registered."""
    with _LOCK:
        rec = _RUNS.get(run_id)
        if rec is None:
            return
        rec.state = state
        if message:
            rec.message = message
        if progress_pct is not None:
            rec.progress_pct = float(progress_pct)
        if current_tier is not None:
            rec.current_tier = int(current_tier)
        if tiered is not None:
            rec.tiered = bool(tiered)
        rec.transitions.append({
            "ts": _now_iso(), "state": state,
            "tier": rec.current_tier, "pct": rec.progress_pct,
        })
        if state in TERMINAL_STATES and rec.completed_at is None:
            rec.completed_at = _now_iso()
        _persist(rec)


def set_seed(run_id: str, seed_text: Optional[str],
             source: str = "user") -> None:
    """Atom 3.2: persist the user's seed text on the run record.

    Empty / None clears the seed. Source describes the path it came in
    on so /learning/admin can audit auto-updates from the copilot.
    """
    with _LOCK:
        rec = _RUNS.get(run_id)
        if rec is None:
            return
        rec.seed_text = (seed_text or None) if (seed_text and seed_text.strip()) else None
        rec.seed_source = source if rec.seed_text else None
        rec.seed_at = _now_iso() if rec.seed_text else None
        rec.transitions.append({
            "ts": _now_iso(), "state": "seed_updated",
            "seed_text": rec.seed_text, "source": source,
        })
        _persist(rec)


def set_run_dir(run_id: str, run_dir: str) -> None:
    """Once the run has a concrete output dir, record it."""
    with _LOCK:
        rec = _RUNS.get(run_id)
        if rec is None:
            return
        rec.run_dir = str(run_dir)
        _persist(rec)


def complete(run_id: str, *, run_dir: Optional[str] = None,
             cached: bool = False) -> None:
    with _LOCK:
        rec = _RUNS.get(run_id)
        if rec is None:
            return
        if run_dir:
            rec.run_dir = str(run_dir)
        rec.cached = bool(cached)
        rec.state = RunState.COMPLETE
        rec.progress_pct = 100.0
        rec.completed_at = _now_iso()
        rec.message = "complete"
        rec.transitions.append({"ts": rec.completed_at,
                                 "state": rec.state, "tier": rec.current_tier})
        _persist(rec)


def fail(run_id: str, error: str) -> None:
    with _LOCK:
        rec = _RUNS.get(run_id)
        if rec is None:
            return
        rec.state = RunState.FAILED
        rec.error = str(error)
        rec.message = f"failed: {error[:140]}"
        rec.completed_at = _now_iso()
        rec.transitions.append({"ts": rec.completed_at,
                                 "state": rec.state})
        _persist(rec)


def cancel(run_id: str) -> None:
    with _LOCK:
        rec = _RUNS.get(run_id)
        if rec is None:
            return
        if rec.state in TERMINAL_STATES:
            return
        rec.state = RunState.CANCELLED
        rec.message = "cancelled"
        rec.completed_at = _now_iso()
        rec.transitions.append({"ts": rec.completed_at,
                                 "state": rec.state})
        _persist(rec)


def snapshot(run_id: str) -> Optional[Dict[str, Any]]:
    """Return the current record as a JSON-friendly dict, or None."""
    with _LOCK:
        rec = _RUNS.get(run_id)
        if rec is None:
            # Fall back to disk: maybe the server restarted but the run
            # completed earlier in this filesystem.
            return _read_persisted_by_id(run_id)
        out = rec.to_dict()
        out["elapsed_seconds"] = rec.elapsed_seconds()
        return out


def lookup_by_run_dir(run_dir: str) -> Optional[Dict[str, Any]]:
    """Find a record whose run_dir matches; useful when only run_dir is known."""
    with _LOCK:
        for rec in _RUNS.values():
            if rec.run_dir and Path(rec.run_dir) == Path(run_dir):
                return snapshot(rec.run_id)
    return _read_persisted_at(Path(run_dir))


def list_active() -> List[Dict[str, Any]]:
    """Return all non-terminal runs (for debugging)."""
    with _LOCK:
        return [r.to_dict() for r in _RUNS.values()
                if r.state not in TERMINAL_STATES]


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------

PROGRESS_FILE = "_RUN_PROGRESS.json"


def _resolve_persist_dir(rec: "RunRecord") -> Optional[Path]:
    """Resolve where to write _RUN_PROGRESS.json for this record.

    Brief: B-2 — persist on every state mutation, even before the
    runner has set ``run_dir``. We derive a placeholder under
    ``<outputs_root>/<run_id>/`` so polls during a run can survive a
    server restart. The orchestrator will eventually overwrite the
    placeholder with the real run_dir; we then continue persisting
    there.
    """
    if rec.run_dir:
        return Path(rec.run_dir)
    if not rec.run_id:
        return None
    return _default_outputs_root() / rec.run_id


def _persist(rec: RunRecord) -> None:
    """Atomic write of the record to ``_RUN_PROGRESS.json``.

    Brief: B-3 — write-temp-then-rename so a crash mid-write can never
    leave a truncated marker. Brief constraint — no silent failures:
    every catch logs ``traceback.format_exc()`` to ``aurora.registry``.
    """
    target_dir = _resolve_persist_dir(rec)
    if target_dir is None:
        return
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        out = rec.to_dict()
        out["elapsed_seconds"] = rec.elapsed_seconds()
        body = json.dumps(out, indent=2, default=str)
        final = target_dir / PROGRESS_FILE
        tmp = target_dir / (PROGRESS_FILE + ".tmp")
        # Write tmp + atomic rename. On Windows, os.replace is atomic
        # within the same volume; on POSIX it's the same call.
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, final)
    except Exception:
        _log.error(
            "registry persist failed for run_id=%s dir=%s\n%s",
            rec.run_id, target_dir, traceback.format_exc(),
        )


def _read_persisted_at(run_dir: Path) -> Optional[Dict[str, Any]]:
    try:
        p = Path(run_dir) / PROGRESS_FILE
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_persisted_by_id(run_id: str,
                          search_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Walk the outputs root looking for a _RUN_PROGRESS.json with this run_id."""
    if not search_root:
        # Default outputs locations Aurora writes into.
        candidates = [
            Path("outputs/aurora_dataset_runs"),
            Path.cwd() / "outputs" / "aurora_dataset_runs",
        ]
    else:
        candidates = [Path(search_root)]
    for root in candidates:
        try:
            if not root.exists():
                continue
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                p = child / PROGRESS_FILE
                if not p.exists():
                    continue
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    if d.get("run_id") == run_id:
                        return d
                except Exception:
                    continue
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Disk rehydration on import (B-1)
# ---------------------------------------------------------------------------

def rehydrate_from_disk(outputs_root: Optional[Path] = None,
                          *, demote_non_terminal: bool = True) -> Dict[str, int]:
    """Walk every run dir under ``outputs_root`` and load its
    ``_RUN_PROGRESS.json`` into the in-memory registry.

    Behaviour:
      * Terminal records (complete / failed / cancelled) are loaded
        as-is. After a restart, /api/run/status?run_id=X returns the
        right answer immediately rather than 'run not found'.
      * Non-terminal records (pending / running / tier*_running) found
        on disk belong to a previous process — that process is dead
        and the analysis is no longer running. We demote them to
        ``failed`` with reason ``server_restart_lost_run`` so the
        frontend stops polling forever and surfaces an honest message.
        Set ``demote_non_terminal=False`` to skip that pass (mainly
        for tests that want to inspect raw on-disk state).

    Returns a counts dict ``{loaded_terminal, demoted, total_seen,
    skipped_invalid}``.
    """
    counts = {"loaded_terminal": 0, "demoted": 0,
                "total_seen": 0, "skipped_invalid": 0}
    root = Path(outputs_root) if outputs_root else _default_outputs_root()
    if not root.exists():
        return counts
    try:
        children = [p for p in root.iterdir() if p.is_dir()]
    except Exception:
        _log.error("rehydrate: could not list outputs root\n%s",
                     traceback.format_exc())
        return counts
    with _LOCK:
        for child in children:
            counts["total_seen"] += 1
            p = child / PROGRESS_FILE
            if not p.exists():
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                counts["skipped_invalid"] += 1
                _log.warning("rehydrate: skipped invalid %s\n%s",
                              p, traceback.format_exc())
                continue
            run_id = d.get("run_id")
            if not run_id:
                counts["skipped_invalid"] += 1
                continue
            if run_id in _RUNS:
                # In-memory record always wins (fresher).
                continue
            try:
                rec = RunRecord(
                    run_id=run_id,
                    dataset_path=d.get("dataset_path") or "",
                    state=d.get("state") or RunState.PENDING,
                    started_at=d.get("started_at") or _now_iso(),
                    completed_at=d.get("completed_at"),
                    progress_pct=float(d.get("progress_pct") or 0.0),
                    current_tier=d.get("current_tier"),
                    tiered=bool(d.get("tiered") or False),
                    cached=bool(d.get("cached") or False),
                    error=d.get("error"),
                    message=d.get("message") or "",
                    run_dir=d.get("run_dir") or str(child),
                    seed=d.get("seed"),
                    seed_text=d.get("seed_text"),
                    seed_source=d.get("seed_source"),
                    seed_at=d.get("seed_at"),
                    flags=d.get("flags") or {},
                    transitions=list(d.get("transitions") or []),
                )
            except Exception:
                counts["skipped_invalid"] += 1
                _log.warning("rehydrate: skipped malformed %s\n%s",
                              p, traceback.format_exc())
                continue
            if rec.state in TERMINAL_STATES:
                _RUNS[run_id] = rec
                counts["loaded_terminal"] += 1
                continue
            if not demote_non_terminal:
                _RUNS[run_id] = rec
                continue
            # Non-terminal on disk = orphaned by a dead process.
            rec.state = RunState.FAILED
            rec.error = "server_restart_lost_run"
            rec.message = ("the server restarted while this run was "
                            "in progress; the analysis is no longer "
                            "running and must be re-launched")
            rec.completed_at = _now_iso()
            rec.transitions.append({
                "ts": rec.completed_at,
                "state": rec.state,
                "reason": "server_restart_lost_run",
            })
            _RUNS[run_id] = rec
            counts["demoted"] += 1
            try:
                _persist(rec)
            except Exception:
                _log.error("rehydrate: re-persist demoted record failed\n%s",
                             traceback.format_exc())
    return counts


# Auto-rehydrate on import. Test isolation via the
# ``AURORA_REGISTRY_NO_AUTOLOAD`` env var (tests set this to disable).
if not os.environ.get("AURORA_REGISTRY_NO_AUTOLOAD"):
    try:
        rehydrate_from_disk()
    except Exception:
        _log.error("rehydrate-on-import failed\n%s",
                     traceback.format_exc())


# ---------------------------------------------------------------------------
# Test helper — clear registry between tests
# ---------------------------------------------------------------------------

def _clear_for_tests() -> None:
    with _LOCK:
        _RUNS.clear()

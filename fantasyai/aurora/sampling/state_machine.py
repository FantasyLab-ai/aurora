"""
Tiered analysis state machine — single source of truth on disk.

The orchestrator + state_builder + frontend all read/write
``<run_dir>/_SAMPLING_STATE.json``. This module owns its schema,
persistence, atomic updates, and state transitions.

States per tier:  pending | running | complete | cancelled | failed |
                  resumable (only T2/T3, after server restart with state=running)
                  available (only T3, after T2 complete)

Tier-3 starts as ``available`` (waiting for user) once T2 completes;
all other tiers auto-progress.

The file layout per parent run_dir:
    _SAMPLING_STATE.json         — this dataclass, persisted atomically
    _TIER_RUNS.json              — tier → child run_dir path mapping
    tier0/                       — T0 streaming structure inference output
    (tier1/, tier2/, tier3/ live in their own child run_dirs, not here)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


SAMPLING_STATE_FILE = "_SAMPLING_STATE.json"
TIER_RUNS_FILE = "_TIER_RUNS.json"

VALID_STATES = {"pending", "running", "complete",
                "cancelled", "failed", "resumable", "available"}


@dataclass
class TierStatus:
    """One tier's status + metadata."""
    state: str = "pending"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    elapsed_s: Optional[float] = None
    method: Optional[str] = None         # sampling method id
    rows_sampled: Optional[int] = None
    cols_sampled: Optional[int] = None
    rows_total: Optional[int] = None
    cols_total: Optional[int] = None
    seed: Optional[str] = None
    n_strata: Optional[int] = None
    progress_pct: Optional[float] = None
    estimated_completion_s: Optional[float] = None
    child_run_dir: Optional[str] = None  # the actual runner output dir
    error: Optional[str] = None
    note: Optional[str] = None
    # Internal: pid of the worker thread (for cancellation; not persisted as PID
    # but used as a thread-id key inside the orchestrator).

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SamplingState:
    """The full tiered-run state."""
    schema: str = "sampling_v1"
    parent_run_dir: str = ""
    dataset_path: str = ""
    dataset_basename: str = ""
    shape_detected: Optional[str] = None      # time_series | panel | wide | very_wide | tall_sparse | small
    rows_total: Optional[int] = None
    cols_total: Optional[int] = None
    is_small_dataset: bool = False            # bypass tiering for small files
    tiers: Dict[str, TierStatus] = field(default_factory=lambda: {
        "0": TierStatus(),
        "1": TierStatus(),
        "2": TierStatus(),
        "3": TierStatus(state="pending"),     # bumped to available when T2 done
    })
    current_tier: int = 0
    overall_started_at: str = ""
    overall_state: str = "initializing"       # initializing | running | complete | cancelled | failed

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "schema": self.schema,
            "parent_run_dir": self.parent_run_dir,
            "dataset_path": self.dataset_path,
            "dataset_basename": self.dataset_basename,
            "shape_detected": self.shape_detected,
            "rows_total": self.rows_total,
            "cols_total": self.cols_total,
            "is_small_dataset": self.is_small_dataset,
            "current_tier": self.current_tier,
            "overall_started_at": self.overall_started_at,
            "overall_state": self.overall_state,
            "tiers": {k: v.to_dict() for k, v in self.tiers.items()},
        }
        return d

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, parent_run_dir: Path) -> Optional["SamplingState"]:
        """Read the state JSON; return None if it doesn't exist."""
        path = Path(parent_run_dir) / SAMPLING_STATE_FILE
        if not path.exists():
            return None
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        s = cls()
        s.schema = d.get("schema", "sampling_v1")
        s.parent_run_dir = d.get("parent_run_dir", str(parent_run_dir))
        s.dataset_path = d.get("dataset_path", "")
        s.dataset_basename = d.get("dataset_basename", "")
        s.shape_detected = d.get("shape_detected")
        s.rows_total = d.get("rows_total")
        s.cols_total = d.get("cols_total")
        s.is_small_dataset = bool(d.get("is_small_dataset", False))
        s.current_tier = int(d.get("current_tier", 0))
        s.overall_started_at = d.get("overall_started_at", "")
        s.overall_state = d.get("overall_state", "initializing")
        for k, v in (d.get("tiers") or {}).items():
            s.tiers[str(k)] = TierStatus(**{
                key: val for key, val in (v or {}).items()
                if key in TierStatus.__dataclass_fields__
            })
        return s

    def save(self) -> None:
        """Atomic write: tmp + os.replace. Threads can call concurrently."""
        path = Path(self.parent_run_dir) / SAMPLING_STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------
    def begin_tier(self, n: int, *, method: str, rows: int, cols: int,
                    seed: str, rows_total: Optional[int] = None) -> None:
        t = self.tiers[str(n)]
        t.state = "running"
        t.started_at = _ts()
        t.method = method
        t.rows_sampled = rows
        t.cols_sampled = cols
        t.rows_total = rows_total
        t.seed = seed
        self.current_tier = n
        if self.overall_state == "initializing":
            self.overall_state = "running"
            self.overall_started_at = _ts()
        self.save()

    def complete_tier(self, n: int, *, child_run_dir: Optional[str] = None,
                      note: Optional[str] = None) -> None:
        t = self.tiers[str(n)]
        t.state = "complete"
        t.completed_at = _ts()
        if t.started_at:
            try:
                t.elapsed_s = round(_epoch(t.completed_at) - _epoch(t.started_at), 2)
            except Exception:
                pass
        if child_run_dir:
            t.child_run_dir = str(child_run_dir)
        if note:
            t.note = note
        # T3 doesn't auto-start; once T2 completes, T3 becomes "available".
        if n == 2 and self.tiers["3"].state == "pending":
            self.tiers["3"].state = "available"
        # Mark overall complete when the highest needed tier is done.
        if self.is_small_dataset and n == 0:
            self.overall_state = "complete"
        elif n == 2 or n == 3:
            self.overall_state = "complete"
        self.save()

    def fail_tier(self, n: int, error: str) -> None:
        t = self.tiers[str(n)]
        t.state = "failed"
        t.error = (error or "")[:1000]
        t.completed_at = _ts()
        # Failures DOWNSTREAM are blocked; UI shows what's available.
        # Don't auto-cascade; the user can retry.
        self.overall_state = "failed" if n in (0, 1) else "running"
        self.save()

    def cancel_tier(self, n: int, note: Optional[str] = None) -> None:
        t = self.tiers[str(n)]
        if t.state == "running":
            t.state = "cancelled"
            t.completed_at = _ts()
            if note:
                t.note = note
            self.save()

    def update_progress(self, n: int, *, pct: float,
                        estimated_completion_s: Optional[float] = None) -> None:
        t = self.tiers[str(n)]
        if t.state != "running":
            return
        t.progress_pct = round(float(pct), 1)
        if estimated_completion_s is not None:
            t.estimated_completion_s = round(float(estimated_completion_s), 1)
        # Don't save on every tick — orchestrator decides cadence.

    # ------------------------------------------------------------------
    # Read-only helpers
    # ------------------------------------------------------------------
    def highest_complete_tier(self) -> int:
        for n in (3, 2, 1, 0):
            if self.tiers[str(n)].state == "complete":
                return n
        return -1

    def is_tier_runnable(self, n: int) -> bool:
        """T(n) can start only if T(n-1) is complete (or n == 0)."""
        if n == 0:
            return self.tiers["0"].state in ("pending", "failed")
        prev = self.tiers[str(n - 1)]
        cur = self.tiers[str(n)]
        return prev.state == "complete" and cur.state in ("pending", "available", "failed")

    def honest_caveat(self) -> str:
        """One-line explanation of what's currently displayed."""
        h = self.highest_complete_tier()
        if h < 0:
            return "Analysis is initializing — structure inference in progress."
        if self.is_small_dataset:
            return f"Analysis ran on the full file ({self.rows_total or '?'} rows)."
        t = self.tiers[str(h)]
        if h == 1:
            return (f"Findings reflect a {(t.rows_sampled or 0):,}-row {t.method} "
                    f"sample ({_fraction(t.rows_sampled, self.rows_total)} of "
                    f"full data). Confirmation pass on a larger sample is "
                    f"running.")
        if h == 2:
            return (f"Findings confirmed on a {(t.rows_sampled or 0):,}-row "
                    f"{t.method} sample ({_fraction(t.rows_sampled, self.rows_total)} "
                    f"of full data). Run on full data is available.")
        if h == 3:
            return f"Analysis ran on the full file ({self.rows_total or '?'} rows)."
        return ""


# ---------------------------------------------------------------------------
# Tier-runs mapping (separate from state for clarity)
# ---------------------------------------------------------------------------

def write_tier_runs(parent_run_dir: Path, mapping: Dict[int, str]) -> None:
    """Persist {tier_n: child_run_dir_path}."""
    path = Path(parent_run_dir) / TIER_RUNS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({str(k): str(v) for k, v in mapping.items()}, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def read_tier_runs(parent_run_dir: Path) -> Dict[int, str]:
    path = Path(parent_run_dir) / TIER_RUNS_FILE
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return {int(k): str(v) for k, v in d.items()}
    except Exception:
        return {}


def update_tier_run(parent_run_dir: Path, tier: int, child_run_dir: str) -> None:
    mapping = read_tier_runs(parent_run_dir)
    mapping[int(tier)] = str(child_run_dir)
    write_tier_runs(parent_run_dir, mapping)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _epoch(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def _fraction(part: Optional[int], total: Optional[int]) -> str:
    if not part or not total or total <= 0:
        return "?"
    f = part / total
    if f >= 0.5:
        return f"{int(f * 100)}%"
    if f >= 0.01:
        return f"{f * 100:.1f}%"
    return f"{f * 100:.3f}%"

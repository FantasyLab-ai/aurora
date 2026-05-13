"""
Tier orchestrator — runs T0/T1/T2/T3 in sequence (T2/T3 can be backgrounded).

Public entry:
    start_tiered_run(file_path, parent_run_dir, runner_callable,
                     small_run_callable=None, on_tier_complete=None) -> SamplingState

The orchestrator owns the state machine. It:

  1. Performs T0 streaming structure inference, decides shape, and decides
     whether the file is "small" (skip tiering — call small_run_callable).
  2. For T1 (foreground) and T2/T3 (background): generates a sample, calls
     ``runner_callable(sampled_csv_path) -> child_run_dir``, captures the
     child run dir into _TIER_RUNS.json.
  3. Updates state machine + persists at every transition.
  4. Computes replication status across tier outputs (delegated to
     ``replication.attach_replication`` at state-build time).
  5. Cancellation honoured via a per-run threading.Event.

Background execution: T2 runs in a daemon thread. State file is the cross-
process source of truth; the API + frontend poll it.
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .shape_detector import (
    StructureProfile, infer_structure,
    SMALL_DATASET_ROW_THRESHOLD, SMALL_DATASET_BYTE_THRESHOLD,
    VERY_WIDE_COL_THRESHOLD,
)
from .samplers import (
    SampleManifest, run_sampler, select_sampling_method,
)
from .state_machine import (
    SamplingState, TierStatus, update_tier_run, write_tier_runs,
)


# ---------------------------------------------------------------------------
# Tier budgets
# ---------------------------------------------------------------------------

TIER_BUDGETS = {
    1: {"rows": 100_000,    "max_cols": 200},
    2: {"rows": 1_000_000,  "max_cols": 1000},
    3: {"rows": None,       "max_cols": None},   # full data
}


# ---------------------------------------------------------------------------
# Cancellation registry — one Event per parent_run_dir
# ---------------------------------------------------------------------------

_cancels: Dict[str, threading.Event] = {}
_lock = threading.Lock()


def _cancel_event(parent_run_dir: Path) -> threading.Event:
    key = str(Path(parent_run_dir).resolve())
    with _lock:
        ev = _cancels.get(key)
        if ev is None:
            ev = threading.Event()
            _cancels[key] = ev
        return ev


def cancel_run(parent_run_dir: Path) -> None:
    _cancel_event(parent_run_dir).set()


def is_cancelled(parent_run_dir: Path) -> bool:
    return _cancel_event(parent_run_dir).is_set()


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

# Type signatures for the runner hooks (kept loose to avoid circular imports).
RunnerFn = Callable[[Path, Path], Optional[Path]]
"""runner(sampled_csv_path, parent_run_dir) -> child_run_dir or None"""

OnTierFn = Callable[[int, SamplingState], None]


def start_tiered_run(
    file_path: Path,
    parent_run_dir: Path,
    runner: RunnerFn,
    *,
    small_runner: Optional[RunnerFn] = None,
    on_tier_complete: Optional[OnTierFn] = None,
    background_t2: bool = True,
    user_hints: Optional[List[str]] = None,
) -> SamplingState:
    """Run T0 + T1 synchronously; spawn T2 in the background (if requested).

    Returns AFTER T1 completes (or fails). T2 continues in a daemon thread.
    """
    file_path = Path(file_path).resolve()
    parent_run_dir = Path(parent_run_dir).resolve()
    parent_run_dir.mkdir(parents=True, exist_ok=True)

    # Reset cancellation event for this run.
    ev = _cancel_event(parent_run_dir)
    ev.clear()

    # Initial state.
    state = SamplingState(
        parent_run_dir=str(parent_run_dir),
        dataset_path=str(file_path),
        dataset_basename=file_path.name,
    )
    state.save()

    # ---------- T0: structure inference ----------
    state.tiers["0"].state = "running"
    state.tiers["0"].started_at = _now()
    state.current_tier = 0
    state.overall_state = "running"
    state.overall_started_at = _now()
    state.save()

    try:
        profile = infer_structure(file_path)
    except Exception as e:
        state.fail_tier(0, f"structure inference failed: {e}")
        return state

    # Persist T0 artifacts under <parent_run_dir>/tier0/
    tier0_dir = parent_run_dir / "tier0"
    tier0_dir.mkdir(parents=True, exist_ok=True)
    (tier0_dir / "structure_profile.json").write_text(
        json.dumps(profile.to_dict(), indent=2), encoding="utf-8")

    state.shape_detected = profile.shape
    state.rows_total = profile.rows_total
    state.cols_total = profile.cols_total
    state.is_small_dataset = profile.is_small
    state.tiers["0"].rows_sampled = profile.rows_total
    state.tiers["0"].cols_sampled = profile.cols_total
    state.tiers["0"].rows_total = profile.rows_total
    state.tiers["0"].cols_total = profile.cols_total
    state.tiers["0"].method = "streaming_structure"
    state.complete_tier(0, note=f"shape={profile.shape}")
    if on_tier_complete:
        try: on_tier_complete(0, state)
        except Exception: pass

    # ---------- Small dataset short-circuit ----------
    if profile.is_small and small_runner is not None:
        # Call the legacy single-shot runner directly on the original file.
        state.tiers["1"].state = "running"
        state.tiers["1"].started_at = _now()
        state.tiers["1"].method = "full_data"
        state.tiers["1"].rows_sampled = profile.rows_total
        state.tiers["1"].cols_sampled = profile.cols_total
        state.tiers["1"].rows_total = profile.rows_total
        state.tiers["1"].cols_total = profile.cols_total
        state.current_tier = 1
        state.save()
        try:
            child = small_runner(file_path, parent_run_dir)
        except Exception as e:
            state.fail_tier(1, f"runner failed (small dataset): {e}")
            return state
        if is_cancelled(parent_run_dir):
            state.cancel_tier(1, note="cancelled by user")
            return state
        if child is None:
            state.fail_tier(1, "runner returned no run_dir")
            return state
        update_tier_run(parent_run_dir, 1, str(child))
        state.complete_tier(1, child_run_dir=str(child),
                             note="small dataset — full file analyzed at tier 1")
        # Mark T2/T3 as not-applicable.
        state.tiers["2"].state = "complete"
        state.tiers["2"].note = "skipped — small dataset"
        state.tiers["2"].child_run_dir = str(child)  # reuse
        state.tiers["3"].state = "complete"
        state.tiers["3"].note = "skipped — small dataset"
        state.tiers["3"].child_run_dir = str(child)
        update_tier_run(parent_run_dir, 2, str(child))
        update_tier_run(parent_run_dir, 3, str(child))
        state.save()
        if on_tier_complete:
            try: on_tier_complete(1, state)
            except Exception: pass
        return state

    # ---------- T1: foreground sample + run ----------
    if not _run_tier(1, file_path, parent_run_dir, profile, runner,
                     state, user_hints, on_tier_complete):
        return state

    # ---------- T2: background ----------
    if background_t2:
        thread = threading.Thread(
            target=_run_tier_in_background,
            args=(2, file_path, parent_run_dir, profile, runner,
                  user_hints, on_tier_complete),
            daemon=True,
        )
        thread.start()
    else:
        _run_tier(2, file_path, parent_run_dir, profile, runner,
                  state, user_hints, on_tier_complete)

    return state


def start_tier_3(parent_run_dir: Path, runner: RunnerFn,
                 *, on_tier_complete: Optional[OnTierFn] = None,
                 background: bool = True) -> SamplingState:
    """User-initiated full-data run."""
    parent_run_dir = Path(parent_run_dir).resolve()
    state = SamplingState.load(parent_run_dir)
    if state is None:
        raise RuntimeError(f"no _SAMPLING_STATE.json at {parent_run_dir}")
    if state.tiers["2"].state != "complete":
        raise RuntimeError("Tier 2 must be complete before Tier 3 can run")
    file_path = Path(state.dataset_path)
    profile_path = parent_run_dir / "tier0" / "structure_profile.json"
    if profile_path.exists():
        try:
            from .shape_detector import StructureProfile
            d = json.loads(profile_path.read_text(encoding="utf-8"))
            profile = StructureProfile()
            for k, v in d.items():
                if hasattr(profile, k) and k != "columns":
                    setattr(profile, k, v)
        except Exception:
            profile = infer_structure(file_path)
    else:
        profile = infer_structure(file_path)

    if background:
        threading.Thread(
            target=_run_tier_in_background,
            args=(3, file_path, parent_run_dir, profile, runner,
                  None, on_tier_complete),
            daemon=True,
        ).start()
    else:
        _run_tier(3, file_path, parent_run_dir, profile, runner,
                  state, None, on_tier_complete)
    return state


# ---------------------------------------------------------------------------
# Internal: per-tier execution
# ---------------------------------------------------------------------------

def _run_tier(n: int, file_path: Path, parent_run_dir: Path,
              profile: StructureProfile, runner: RunnerFn,
              state: SamplingState,
              user_hints: Optional[List[str]],
              on_tier_complete: Optional[OnTierFn]) -> bool:
    """Run tier n synchronously. Returns True on success, False on fail/cancel."""
    if is_cancelled(parent_run_dir):
        state.cancel_tier(n, note="cancelled before start")
        return False

    budget = TIER_BUDGETS.get(n) or {}
    rows_target = budget.get("rows") or profile.rows_total
    max_cols = budget.get("max_cols") or profile.cols_total

    method = ("full_data" if rows_target >= profile.rows_total
              else select_sampling_method(profile.shape))

    # Sample.
    samples_dir = parent_run_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    sample_path = samples_dir / f"tier{n}_sample.csv"

    state.begin_tier(n, method=method,
                      rows=min(rows_target, profile.rows_total),
                      cols=min(max_cols, profile.cols_total),
                      seed="pending",
                      rows_total=profile.rows_total)

    try:
        if method == "full_data":
            sample_path = file_path           # use original
            manifest = SampleManifest(
                tier=n, method="full_data",
                rows_sampled=profile.rows_total, cols_sampled=profile.cols_total,
                rows_total=profile.rows_total, cols_total=profile.cols_total,
                rows_fraction=1.0, cols_fraction=1.0,
                seed="full",
            )
        else:
            manifest = run_sampler(method, file_path, profile=profile,
                                    rows_target=rows_target, max_cols=max_cols,
                                    tier=n, out_path=sample_path,
                                    user_hints=user_hints)
        # Persist manifest.
        (parent_run_dir / f"tier{n}_sample_manifest.json").write_text(
            json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
        # Update state with manifest details.
        ts = state.tiers[str(n)]
        ts.rows_sampled = manifest.rows_sampled
        ts.cols_sampled = manifest.cols_sampled
        ts.seed = manifest.seed
        ts.n_strata = manifest.n_strata
        state.save()
    except Exception as e:
        state.fail_tier(n, f"sampling failed: {e}")
        return False

    if is_cancelled(parent_run_dir):
        state.cancel_tier(n, note="cancelled before runner")
        return False

    # Run the runner.
    try:
        child = runner(sample_path, parent_run_dir)
    except Exception as e:
        state.fail_tier(n, f"runner failed: {e}\n{traceback.format_exc()[:600]}")
        return False

    if is_cancelled(parent_run_dir):
        state.cancel_tier(n, note="cancelled mid-runner",)
        return False
    if child is None:
        state.fail_tier(n, "runner returned no run_dir")
        return False

    update_tier_run(parent_run_dir, n, str(child))
    state.complete_tier(n, child_run_dir=str(child))
    if on_tier_complete:
        try: on_tier_complete(n, state)
        except Exception: pass
    return True


def _run_tier_in_background(n: int, file_path: Path, parent_run_dir: Path,
                            profile: StructureProfile, runner: RunnerFn,
                            user_hints: Optional[List[str]],
                            on_tier_complete: Optional[OnTierFn]) -> None:
    """Daemon-thread wrapper that re-loads state to avoid cross-thread races."""
    try:
        state = SamplingState.load(parent_run_dir)
        if state is None:
            return
        _run_tier(n, file_path, parent_run_dir, profile, runner,
                  state, user_hints, on_tier_complete)
    except Exception:
        # Background failures already get caught inside _run_tier; this is a
        # belt-and-braces guard so a daemon thread never silently dies.
        pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")

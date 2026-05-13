"""
Overnight learning scheduler — Atom 4.1 of the pre-launch sprint.

Reads ``~/.aurora/overnight_schedule.json``, walks the configured trusted
public sources at the scheduled local time, runs Aurora on each, and
records the run for the validation loop (Atom 4.2) to consume.

Catch-up policy (decided in the spec):
  - "never": missed = skipped.
  - "next_eligible_window" (default): if we wake within tolerance_hours of
    the last scheduled fire AND haven't run too recently AND haven't
    exceeded max_consecutive_missed_runs, fire one catch-up. After it
    finishes, resume normal schedule.
  - "always": catch up every missed night.

State persists at ``~/.aurora/scheduler/state.json`` so a daemon restart
doesn't lose its place.

This module never does HTTP itself — it delegates to the existing
connectors and the run_registry. Failures on one source never block the
others; each source has its own try/except.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_SCHEDULE_PATH = Path.home() / ".aurora" / "overnight_schedule.json"
SCHEDULER_STATE_FILE = Path.home() / ".aurora" / "scheduler" / "state.json"


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

@dataclass
class SourceConfig:
    id: str                         # connector id (must match connectors/registry)
    query: str                      # query string passed to the connector
    active: bool = True
    seed_text: Optional[str] = None # optional default seed for this source
    notes: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SourceConfig":
        return cls(
            id=d.get("id", ""),
            query=d.get("query", ""),
            active=bool(d.get("active", True)),
            seed_text=d.get("seed_text"),
            notes=d.get("notes", ""),
        )


@dataclass
class CatchUpConfig:
    mode: str = "next_eligible_window"     # never | next_eligible_window | always
    tolerance_hours: float = 6.0
    max_consecutive_missed_runs: int = 3
    skip_if_recent_run_within_hours: float = 12.0


@dataclass
class SchedulerConfig:
    active: bool = True
    run_at_local: str = "03:00"            # HH:MM local time
    sources: List[SourceConfig] = field(default_factory=list)
    catch_up: CatchUpConfig = field(default_factory=CatchUpConfig)
    max_runs_per_night: int = 7
    max_runtime_per_run_min: int = 30

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "run_at_local": self.run_at_local,
            "sources": [asdict(s) for s in self.sources],
            "catch_up": asdict(self.catch_up),
            "max_runs_per_night": self.max_runs_per_night,
            "max_runtime_per_run_min": self.max_runtime_per_run_min,
        }


def _default_config() -> SchedulerConfig:
    """v1: NOAA only. Atom 4.5 adds FRED, BLS/BEA, sports."""
    return SchedulerConfig(
        sources=[
            SourceConfig(id="noaa", query="recent_us_climate", active=True,
                          notes="National Weather Service buoy + station data"),
        ],
    )


def load_config(path: Optional[Path] = None) -> SchedulerConfig:
    """Load + parse the overnight schedule. Materialises a v1 default
    pointing at NOAA only when no file exists (does NOT write it — caller
    decides via save_config)."""
    p = Path(path) if path else DEFAULT_SCHEDULE_PATH
    if not p.exists():
        return _default_config()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return _default_config()
    cfg = SchedulerConfig(
        active=bool(d.get("active", True)),
        run_at_local=str(d.get("run_at_local", "03:00")),
        sources=[SourceConfig.from_dict(s) for s in (d.get("sources") or [])],
        catch_up=CatchUpConfig(**(d.get("catch_up") or {})),
        max_runs_per_night=int(d.get("max_runs_per_night", 7)),
        max_runtime_per_run_min=int(d.get("max_runtime_per_run_min", 30)),
    )
    return cfg


def save_config(cfg: SchedulerConfig, path: Optional[Path] = None) -> None:
    p = Path(path) if path else DEFAULT_SCHEDULE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg.to_dict(), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Scheduler state (catch-up tracking)
# ---------------------------------------------------------------------------

@dataclass
class SchedulerState:
    last_scheduled_fire: Optional[str] = None       # ISO ts (UTC)
    last_completed_at: Optional[str] = None         # ISO ts (UTC)
    missed_count: int = 0
    skipped_dates: List[str] = field(default_factory=list)


def _load_state(path: Path = SCHEDULER_STATE_FILE) -> SchedulerState:
    if not path.exists():
        return SchedulerState()
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return SchedulerState(
            last_scheduled_fire=d.get("last_scheduled_fire"),
            last_completed_at=d.get("last_completed_at"),
            missed_count=int(d.get("missed_count", 0)),
            skipped_dates=list(d.get("skipped_dates") or []),
        )
    except Exception:
        return SchedulerState()


def _save_state(state: SchedulerState,
                path: Path = SCHEDULER_STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _parse_hhmm(s: str) -> Tuple[int, int]:
    parts = (s or "03:00").split(":")
    return int(parts[0]), int(parts[1] if len(parts) > 1 else 0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _next_scheduled(cfg: SchedulerConfig,
                    after: Optional[datetime] = None) -> datetime:
    """Compute the next scheduled fire time (UTC) in the local clock's
    HH:MM. We assume the current process's local timezone."""
    h, m = _parse_hhmm(cfg.run_at_local)
    now_local = (after or _now()).astimezone()
    target = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now_local:
        target = target + timedelta(days=1)
    return target.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Catch-up policy
# ---------------------------------------------------------------------------

def should_fire_now(cfg: SchedulerConfig,
                    state: Optional[SchedulerState] = None,
                    now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Return (should_fire, reason). Used by both the watch loop and
    catch_up_check — same policy decision either way.
    """
    if not cfg.active:
        return False, "scheduler.active=false"
    state = state or _load_state()
    now = now or _now()
    h, m = _parse_hhmm(cfg.run_at_local)

    # 1. Check if it's currently within +/- 30 min of the scheduled local time.
    now_local = now.astimezone()
    target_today_local = now_local.replace(hour=h, minute=m, second=0,
                                             microsecond=0)
    delta_min = abs((now_local - target_today_local).total_seconds() / 60.0)

    # 2. Don't re-fire if we already completed within the last
    #    skip_if_recent_run_within_hours.
    if state.last_completed_at:
        try:
            last = datetime.fromisoformat(state.last_completed_at.replace("Z", "+00:00"))
            hours_since = (now - last).total_seconds() / 3600.0
            if hours_since < cfg.catch_up.skip_if_recent_run_within_hours:
                return False, (f"recent run {hours_since:.1f}h ago < "
                                f"{cfg.catch_up.skip_if_recent_run_within_hours}h skip threshold")
        except Exception:
            pass

    # 3. Within the daily window?
    if delta_min <= 30:
        return True, "in scheduled window"

    # 4. Catch-up?
    return _catch_up_decision(cfg, state, now)


def _catch_up_decision(cfg: SchedulerConfig,
                        state: SchedulerState,
                        now: datetime) -> Tuple[bool, str]:
    mode = cfg.catch_up.mode
    if mode == "never":
        return False, "catch_up=never"
    if mode == "always":
        # Only fire if the last completed is more than 24h ago.
        if state.last_completed_at:
            try:
                last = datetime.fromisoformat(
                    state.last_completed_at.replace("Z", "+00:00"))
                if (now - last).total_seconds() / 3600.0 > 24.0:
                    return True, "catch_up=always; last >24h ago"
            except Exception:
                pass
        return False, "catch_up=always; recent run within 24h"

    # next_eligible_window (default)
    if state.missed_count >= cfg.catch_up.max_consecutive_missed_runs:
        return False, (f"missed_count {state.missed_count} >= "
                         f"max {cfg.catch_up.max_consecutive_missed_runs}")
    # If the last scheduled fire was within tolerance_hours and we never
    # ran for it, fire now.
    if state.last_scheduled_fire:
        try:
            last_sched = datetime.fromisoformat(
                state.last_scheduled_fire.replace("Z", "+00:00"))
            hours_since_scheduled = (now - last_sched).total_seconds() / 3600.0
            if 0 < hours_since_scheduled <= cfg.catch_up.tolerance_hours:
                # And we DIDN'T complete since then.
                if state.last_completed_at:
                    try:
                        last_comp = datetime.fromisoformat(
                            state.last_completed_at.replace("Z", "+00:00"))
                        if last_comp >= last_sched:
                            return False, "already ran for last scheduled fire"
                    except Exception:
                        pass
                return True, (f"catch_up=next_eligible_window; "
                                f"{hours_since_scheduled:.1f}h since scheduled fire")
        except Exception:
            pass
    return False, "no eligible catch-up window"


def catch_up_check(cfg: SchedulerConfig,
                   state: Optional[SchedulerState] = None,
                   now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Convenience wrapper for the daemon's wake-up handler. Same logic as
    should_fire_now but the caller is asking specifically about catch-up
    (e.g. just woke from sleep)."""
    return should_fire_now(cfg, state, now)


# ---------------------------------------------------------------------------
# Source dispatch
# ---------------------------------------------------------------------------

def list_supported_sources() -> List[str]:
    """v1 set; Atom 4.5 expands."""
    return ["noaa", "usgs", "world_bank", "openml", "fred", "bls", "sports"]


def _connector_for(source_id: str):
    """Return the connector class/instance for a given source id, or None.

    Lazy imports — connectors aren't loaded until the scheduler actually
    needs them. Each connector exposes a ``fetch(query, ...)`` method that
    writes a CSV to disk and returns its path.
    """
    try:
        if source_id == "noaa":
            from fantasyai.aurora.connectors.noaa import NOAAConnector
            return NOAAConnector
        if source_id == "usgs":
            from fantasyai.aurora.connectors.usgs_earthquake import USGSEarthquakeConnector
            return USGSEarthquakeConnector
        if source_id == "world_bank":
            from fantasyai.aurora.connectors.world_bank import WorldBankConnector
            return WorldBankConnector
        if source_id == "openml":
            from fantasyai.aurora.connectors.openml import OpenMLConnector
            return OpenMLConnector
        if source_id == "fred":
            from fantasyai.aurora.connectors.fred import FREDConnector
            return FREDConnector
        if source_id == "bls":
            from fantasyai.aurora.connectors.bls import BLSConnector
            return BLSConnector
        if source_id == "sports":
            from fantasyai.aurora.connectors.sports import SportsConnector
            return SportsConnector
        return None
    except Exception:
        return None


def fetch_and_run_one_source(source: SourceConfig,
                              *, max_runtime_sec: int = 1800,
                              outputs_root: Optional[Path] = None,
                              trigger_run_fn=None) -> Dict[str, Any]:
    """Fetch one source's data and kick off Aurora on it. Returns a dict
    summarising the outcome (ok, run_id, run_dir, error, elapsed_sec).

    ``trigger_run_fn`` is dependency-injected for tests; in production the
    caller passes ``studio_api._trigger_run``. Decoupling means the
    scheduler can be unit-tested without spinning up Flask.
    """
    t0 = time.time()
    out: Dict[str, Any] = {
        "source": source.id,
        "query": source.query,
        "ok": False,
        "run_id": None,
        "run_dir": None,
        "error": None,
        "elapsed_sec": 0,
        "csv_path": None,
    }
    try:
        Connector = _connector_for(source.id)
        if Connector is None:
            out["error"] = f"unsupported source: {source.id}"
            return out
        conn = Connector() if isinstance(Connector, type) else Connector
        # Connector contract is non-uniform: BaseConnector.fetch(item_id)
        # returns a ConnectorResult with a .csv_path attribute. Older /
        # standalone connectors may return the path directly. Handle both.
        csv_path = None
        try:
            res = conn.fetch(source.query)
        except TypeError:
            res = conn.fetch(query=source.query)
        # Normalise return shape.
        if hasattr(res, "csv_path"):
            csv_path = res.csv_path
        elif isinstance(res, (str, Path)):
            csv_path = res
        elif isinstance(res, dict) and res.get("csv_path"):
            csv_path = res["csv_path"]
        out["csv_path"] = str(csv_path) if csv_path else None
        if not csv_path or not Path(csv_path).exists():
            out["error"] = "connector did not produce a CSV"
            return out

        # Now run Aurora on the dataset. trigger_run_fn defaults to the
        # studio_api one if not injected (lazy import to avoid cycles).
        if trigger_run_fn is None:
            from studio_api import _trigger_run as trigger_run_fn  # type: ignore
        result = trigger_run_fn(
            dataset=str(csv_path), seed=42,
            also_math=True, math_v2=True, deep_math=True,
            also_llm=False,                # KEEP DETERMINISTIC for overnight
            also_motif=True,
            also_orchestrator=True,
            timeout_seconds=max_runtime_sec,
        )
        out["ok"] = bool(result.get("ok"))
        out["run_dir"] = result.get("run_dir")
        out["run_id"] = (Path(result["run_dir"]).name
                         if result.get("run_dir") else None)
        if not out["ok"]:
            out["error"] = result.get("error_summary") or "runner returned ok=false"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        out["elapsed_sec"] = round(time.time() - t0, 1)
    return out


def run_overnight_pass(cfg: Optional[SchedulerConfig] = None,
                        *, trigger_run_fn=None,
                        outputs_root: Optional[Path] = None,
                        max_total_runtime_sec: int = 14_400,
                        ) -> Dict[str, Any]:
    """Run the full overnight pass: walk active sources serially, fetch +
    analyze each, persist scheduler state. Returns a per-source result
    summary. The validation loop (Atom 4.2) consumes this output.
    """
    cfg = cfg or load_config()
    state = _load_state()
    state.last_scheduled_fire = _now().isoformat()
    _save_state(state)

    started = _now()
    results: List[Dict[str, Any]] = []
    total_runs = 0
    deadline = time.time() + max_total_runtime_sec

    for src in cfg.sources:
        if not src.active:
            continue
        if total_runs >= cfg.max_runs_per_night:
            break
        if time.time() > deadline:
            break
        per_run_budget = int(min(cfg.max_runtime_per_run_min * 60,
                                   max(60, deadline - time.time())))
        r = fetch_and_run_one_source(
            src, max_runtime_sec=per_run_budget,
            outputs_root=outputs_root,
            trigger_run_fn=trigger_run_fn,
        )
        results.append(r)
        total_runs += 1

    # Update scheduler state.
    state = _load_state()      # re-read in case run mutated it
    state.last_completed_at = _now().isoformat()
    state.missed_count = 0     # successful pass clears the missed counter
    _save_state(state)

    return {
        "started_at": started.isoformat(),
        "completed_at": _now().isoformat(),
        "elapsed_sec": round((_now() - started).total_seconds(), 1),
        "n_sources_attempted": len(results),
        "n_ok": sum(1 for r in results if r.get("ok")),
        "n_failed": sum(1 for r in results if not r.get("ok")),
        "results": results,
    }

"""
IncrementalRunner — drives Aurora analyses in response to streaming
events.

Composes ``FileWatcher`` + ``RollingWindowState`` + the standard
Aurora pipeline. When a watched file changes (or a new row arrives
via direct ingestion), the runner:

  1. Reads the latest data
  2. Updates the rolling window
  3. Re-runs Aurora on the windowed slice
  4. Publishes ``new_finding`` / ``regime_changed`` events to the bus

Phase 1 = simple "re-run on change". Phase 2+ will add genuinely
incremental detectors (BOCPD-driven, partial-recompute) that avoid
the full pipeline restart.

Why the simple version still matters: it's the simplest thing that
demonstrably works end-to-end. A user with hourly NOAA buoy CSV
drops gets fresh findings on the cube as each new hour's data
appears, without manually clicking Run.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .watcher import (
    FileWatcher,
    WatcherEvent,
    WATCHER_EVENT_FILE_ADDED,
    WATCHER_EVENT_FILE_MODIFIED,
)
from .rolling_window import RollingWindowState
from .events import (
    StreamEventBus,
    StreamEvent,
    EVENT_NEW_FINDING,
    EVENT_REGIME_CHANGED,
    EVENT_WINDOW_ADVANCED,
)

LOG = logging.getLogger(__name__)


@dataclass
class IncrementalResult:
    """The most-recent run's summary. Kept in memory so the SSE
    handler / Studio can query "what's the latest?" without re-running."""
    run_count: int = 0
    last_run_at: float = 0.0
    last_findings_count: int = 0
    last_severity_rollup: Dict[str, int] = field(default_factory=dict)
    last_run_dir: Optional[str] = None
    errors: List[str] = field(default_factory=list)


# Type of the analysis function the runner invokes. Takes a DataFrame,
# returns a dict-like result with a ``findings`` key. Defaults to a
# closure over the v1.2 extended_methods runner, but any callable that
# matches this shape works (lets tests inject a stub).
AnalysisFn = Callable[[Any], Dict[str, Any]]


class IncrementalRunner:
    """Pulls together watcher + window + analysis into a continuous loop.

    Usage:

        bus = StreamEventBus()
        runner = IncrementalRunner(
            data_path="/data/feed/",
            file_glob="*.csv",
            event_bus=bus,
            window=RollingWindowState(max_rows=10_000),
        )
        runner.start()

        # Elsewhere, an SSE handler subscribes to bus and streams.

        runner.stop()
    """

    def __init__(
        self,
        *,
        data_path: Any,
        event_bus: StreamEventBus,
        window: Optional[RollingWindowState] = None,
        file_glob: str = "*.csv",
        poll_interval_s: float = 2.0,
        analysis_fn: Optional[AnalysisFn] = None,
    ):
        self.data_path = Path(data_path).expanduser()
        self.bus = event_bus
        self.window = window
        self.file_glob = file_glob
        self.poll_interval_s = poll_interval_s
        self.analysis_fn = analysis_fn or _default_analysis
        self.result = IncrementalResult()

        self._watcher: Optional[FileWatcher] = None
        self._lock = threading.Lock()
        self._running = False
        # Track the last regime "state" we published so we only fire
        # regime_changed when the state actually transitions.
        self._last_regime: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._watcher = FileWatcher(
                path=self.data_path,
                callback=self._on_watcher_event,
                glob=self.file_glob,
                poll_interval_s=self.poll_interval_s,
            )
            self._watcher.start()
            self._running = True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            if self._watcher:
                self._watcher.stop()
            self._watcher = None
            self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Manual / programmatic ingestion (bypasses the watcher)
    # ------------------------------------------------------------------

    def ingest(self, df: Any, *, source: str = "manual") -> Dict[str, Any]:
        """Run analysis on a DataFrame directly. Useful when data comes
        from a non-file source (Kafka, Postgres, an MCP tool call).

        Returns the analysis result. Also publishes events to the bus."""
        return self._analyse_and_publish(df, source=source)

    # ------------------------------------------------------------------
    # Watcher callback
    # ------------------------------------------------------------------

    def _on_watcher_event(self, ev: WatcherEvent) -> None:
        if ev.kind not in (WATCHER_EVENT_FILE_ADDED, WATCHER_EVENT_FILE_MODIFIED):
            return
        try:
            df = self._read_file(ev.path)
        except Exception as e:
            self.result.errors.append(f"read {ev.path}: {e}")
            LOG.exception(f"could not read watched file {ev.path}")
            return
        try:
            self._analyse_and_publish(df, source=str(ev.path))
        except Exception as e:
            self.result.errors.append(f"analyse {ev.path}: {e}")
            LOG.exception(f"analysis failed on {ev.path}")

    def _read_file(self, path: Path):
        """Load a CSV (or Parquet) into a DataFrame."""
        try:
            import pandas as pd
        except ImportError as e:
            raise RuntimeError(f"pandas required: {e}")
        if path.suffix.lower() in (".parquet", ".pq"):
            return pd.read_parquet(path)
        return pd.read_csv(path)

    # ------------------------------------------------------------------
    # Core: run analysis + publish events
    # ------------------------------------------------------------------

    def _analyse_and_publish(self, df: Any, *, source: str) -> Dict[str, Any]:
        """Update the rolling window (if any), run the analysis fn,
        publish events for newly-emerged findings or regime changes."""
        if self.window is not None:
            now = time.time()
            try:
                # Append each row to the rolling window.
                for _, row in df.iterrows():
                    self.window.append(now, dict(row))
                window_df = self.window.to_dataframe()
            except Exception:
                window_df = df
            self.bus.publish(StreamEvent(
                kind=EVENT_WINDOW_ADVANCED,
                payload={"window_size": len(self.window),
                          "source": source},
                source=source,
            ))
        else:
            window_df = df

        result = self.analysis_fn(window_df)
        findings = result.get("findings") or []

        # Update the runner's memo.
        sev_counts: Dict[str, int] = {}
        for f in findings:
            sev = str(f.get("severity", "info")).lower()
            sev_counts[sev] = sev_counts.get(sev, 0) + 1

        self.result.run_count += 1
        self.result.last_run_at = time.time()
        self.result.last_findings_count = len(findings)
        self.result.last_severity_rollup = sev_counts

        # Publish a new-finding event for any crit/warn that wasn't
        # in the previous run. Phase 1 = simple "publish the whole
        # severity rollup"; phase 2 will dedupe at the per-finding level.
        if findings:
            self.bus.publish(StreamEvent(
                kind=EVENT_NEW_FINDING,
                payload={
                    "n_findings": len(findings),
                    "severity_rollup": sev_counts,
                    "source": source,
                },
                source=source,
            ))

        # Detect "regime changed" — any finding with kind_hint that
        # mentions regimes/state-shift.
        regime_finding = next(
            (f for f in findings
             if "regime" in str(f.get("kind_hint", "")).lower()
             or "regime" in str(f.get("title", "")).lower()),
            None,
        )
        if regime_finding:
            new_regime = str(regime_finding.get("title", ""))
            if new_regime != self._last_regime:
                self._last_regime = new_regime
                self.bus.publish(StreamEvent(
                    kind=EVENT_REGIME_CHANGED,
                    payload={
                        "title": new_regime,
                        "method": regime_finding.get("method"),
                        "source": source,
                    },
                    source=source,
                ))

        return result


# ---------------------------------------------------------------------------
# Default analysis function — uses the v1.2 extended-methods runner
# ---------------------------------------------------------------------------

def _default_analysis(df: Any) -> Dict[str, Any]:
    """Run the extended-methods stack on a DataFrame.

    This is what the runner uses when no ``analysis_fn`` is provided.
    It mirrors what the batch pipeline does in
    ``run_aurora_dataset_runner.py`` for the post-deep-math stage —
    runs the 7 v1.2 methods and returns Aurora-shaped findings.
    """
    try:
        from fantasyai.aurora.math.methods import run_extended_methods
        return run_extended_methods(df)
    except Exception as e:
        LOG.exception("default streaming analysis failed")
        return {"findings": [], "error": f"{type(e).__name__}: {e}"}

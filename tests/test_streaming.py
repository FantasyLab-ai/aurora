"""
Tests for the streaming-mode foundation (Stream 1.4 Phase 1).

Covers FileWatcher (polling), RollingWindowState, StreamEventBus, and
IncrementalRunner end-to-end. Hermetic — no real file watching, we
manipulate file modtimes directly to drive the watcher's polling loop.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.streaming import (
    FileWatcher,
    FileWatcherError,
    WatcherEvent,
    WATCHER_EVENT_FILE_ADDED,
    WATCHER_EVENT_FILE_MODIFIED,
    WATCHER_EVENT_FILE_REMOVED,
    RollingWindowState,
    StreamEvent,
    StreamEventBus,
    EVENT_NEW_FINDING,
    EVENT_HEARTBEAT,
    IncrementalRunner,
)


# ===========================================================================
# FileWatcher
# ===========================================================================

class TestFileWatcher:

    def test_refuses_missing_path(self, tmp_path):
        w = FileWatcher(tmp_path / "missing", lambda ev: None,
                          poll_interval_s=0.1)
        with pytest.raises(FileWatcherError):
            w.start()

    def test_directory_watcher_fires_on_added_file(self, tmp_path):
        events = []
        w = FileWatcher(tmp_path, events.append, glob="*.csv",
                          poll_interval_s=0.2)
        w.start()
        try:
            # Sleep past initial snapshot.
            time.sleep(0.3)
            (tmp_path / "new.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            time.sleep(0.6)
        finally:
            w.stop(join_timeout_s=2.0)

        added = [e for e in events if e.kind == WATCHER_EVENT_FILE_ADDED]
        assert added, f"expected an ADDED event, got: {[e.kind for e in events]}"
        assert added[0].path.name == "new.csv"

    def test_file_watcher_fires_on_modification(self, tmp_path):
        f = tmp_path / "watched.csv"
        f.write_text("a,b\n", encoding="utf-8")
        events = []
        w = FileWatcher(f, events.append, poll_interval_s=0.2,
                          watch_self_only=True)
        w.start()
        try:
            time.sleep(0.3)
            # Modify modtime explicitly so the watcher's polling sees it.
            new_t = time.time() + 5
            import os as _os
            _os.utime(f, (new_t, new_t))
            f.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
            time.sleep(0.6)
        finally:
            w.stop(join_timeout_s=2.0)

        modified = [e for e in events if e.kind == WATCHER_EVENT_FILE_MODIFIED]
        assert modified

    def test_glob_filter_excludes_other_extensions(self, tmp_path):
        events = []
        w = FileWatcher(tmp_path, events.append, glob="*.csv",
                          poll_interval_s=0.2)
        w.start()
        try:
            time.sleep(0.3)
            (tmp_path / "x.txt").write_text("nope", encoding="utf-8")
            (tmp_path / "y.csv").write_text("yes", encoding="utf-8")
            time.sleep(0.6)
        finally:
            w.stop(join_timeout_s=2.0)
        added = [e for e in events if e.kind == WATCHER_EVENT_FILE_ADDED]
        assert any(e.path.name == "y.csv" for e in added)
        assert not any(e.path.name == "x.txt" for e in added)

    def test_stop_is_clean(self, tmp_path):
        w = FileWatcher(tmp_path, lambda ev: None, poll_interval_s=0.2)
        w.start()
        assert w.is_running
        w.stop()
        assert not w.is_running


# ===========================================================================
# RollingWindowState
# ===========================================================================

class TestRollingWindow:

    def test_count_based_eviction(self):
        win = RollingWindowState(max_rows=3)
        for i in range(5):
            win.append(float(i), {"v": i})
        assert len(win) == 3
        snap = win.snapshot()
        assert [r["v"] for _, r in snap] == [2, 3, 4]

    def test_time_based_eviction(self):
        win = RollingWindowState(max_age_seconds=5.0)
        now = time.time()
        for offset in (-10, -8, -3, -1, 0):
            win.append(now + offset, {"v": offset})
        # Only rows within 5s of newest (now) should remain.
        snap = win.snapshot()
        assert len(snap) >= 3
        assert all(t >= now - 5.0 for t, _ in snap)

    def test_extend(self):
        win = RollingWindowState(max_rows=5)
        rows = [(float(i), {"v": i}) for i in range(10)]
        win.extend(rows)
        assert len(win) == 5

    def test_to_dataframe(self):
        win = RollingWindowState(max_rows=10)
        win.append(1.0, {"a": 1})
        win.append(2.0, {"a": 2})
        df = win.to_dataframe()
        assert list(df.columns) == ["ts", "a"]
        assert len(df) == 2

    def test_invalid_config_raises(self):
        with pytest.raises(ValueError):
            RollingWindowState()


# ===========================================================================
# StreamEventBus
# ===========================================================================

class TestStreamEventBus:

    def test_publish_to_one_subscriber(self):
        bus = StreamEventBus()
        q = bus.subscribe()
        n = bus.publish(StreamEvent(kind="test", payload={"x": 1}))
        assert n == 1
        ev = q.get(timeout=1.0)
        assert ev.kind == "test"
        assert ev.payload == {"x": 1}

    def test_publish_to_multiple_subscribers(self):
        bus = StreamEventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.publish(StreamEvent(kind="hello"))
        assert q1.get(timeout=1.0).kind == "hello"
        assert q2.get(timeout=1.0).kind == "hello"

    def test_unsubscribe(self):
        bus = StreamEventBus()
        q = bus.subscribe()
        assert bus.subscriber_count() == 1
        bus.unsubscribe(q)
        assert bus.subscriber_count() == 0

    def test_iterate_yields_heartbeats_on_idle(self):
        bus = StreamEventBus()
        q = bus.subscribe()
        # iterate with short timeout so heartbeat fires fast in tests.
        gen = bus.iterate(q, timeout_s=0.1)
        hb = next(gen)
        assert hb.kind == EVENT_HEARTBEAT


# ===========================================================================
# IncrementalRunner — end-to-end (with stubbed analysis fn)
# ===========================================================================

class TestIncrementalRunner:

    def _stub_analysis(self, df):
        """Return one warn finding so we can verify event publication."""
        return {
            "findings": [{
                "severity": "warn", "confidence": 0.9,
                "title": "Synthetic finding",
                "method": "stub", "fabricated": False,
                "kind_hint": "test",
            }],
        }

    def test_ingest_publishes_new_finding(self):
        bus = StreamEventBus()
        q = bus.subscribe()
        runner = IncrementalRunner(
            data_path=".", event_bus=bus,
            analysis_fn=self._stub_analysis,
        )
        df = pd.DataFrame({"x": [1, 2, 3]})
        runner.ingest(df, source="test")
        # Pull events; we expect at least one new_finding.
        events = []
        for _ in range(4):
            try:
                events.append(q.get(timeout=0.5))
            except Exception:
                break
        kinds = [e.kind for e in events]
        assert EVENT_NEW_FINDING in kinds

    def test_runner_stops_cleanly(self, tmp_path):
        bus = StreamEventBus()
        runner = IncrementalRunner(
            data_path=tmp_path, event_bus=bus,
            analysis_fn=self._stub_analysis,
            poll_interval_s=0.2,
        )
        runner.start()
        assert runner.is_running
        runner.stop()
        assert not runner.is_running

    def test_runner_tracks_run_count(self):
        bus = StreamEventBus()
        runner = IncrementalRunner(
            data_path=".", event_bus=bus,
            analysis_fn=self._stub_analysis,
        )
        df = pd.DataFrame({"x": [1, 2]})
        runner.ingest(df)
        runner.ingest(df)
        assert runner.result.run_count == 2
        assert runner.result.last_findings_count == 1

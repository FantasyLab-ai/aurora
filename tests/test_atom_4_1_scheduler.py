"""
Atom 4.1: Overnight scheduler — config + catch-up policy + per-source
dispatch. No live HTTP; trigger_run is dependency-injected.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasyai.aurora.scheduler.overnight import (   # noqa: E402
    SchedulerConfig, SourceConfig, CatchUpConfig,
    SchedulerState, _save_state, _load_state,
    load_config, save_config,
    should_fire_now, fetch_and_run_one_source,
    run_overnight_pass, list_supported_sources,
)


# ============================================================
# Config load / save
# ============================================================

class TestConfigLoad:

    def test_default_when_missing(self, tmp_path):
        p = tmp_path / "missing.json"
        cfg = load_config(p)
        assert cfg.active is True
        assert cfg.run_at_local == "03:00"
        # v1 defaults to NOAA only.
        assert len(cfg.sources) == 1
        assert cfg.sources[0].id == "noaa"

    def test_round_trip(self, tmp_path):
        p = tmp_path / "schedule.json"
        cfg = SchedulerConfig(
            active=True, run_at_local="04:30",
            sources=[SourceConfig(id="usgs", query="mag>=4", active=True)],
            catch_up=CatchUpConfig(mode="never"),
        )
        save_config(cfg, p)
        loaded = load_config(p)
        assert loaded.active is True
        assert loaded.run_at_local == "04:30"
        assert loaded.sources[0].id == "usgs"
        assert loaded.catch_up.mode == "never"

    def test_corrupted_file_falls_back_to_default(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        cfg = load_config(p)
        assert cfg.active is True
        # Default sources only.
        assert len(cfg.sources) == 1


# ============================================================
# Catch-up policy
# ============================================================

class TestShouldFireNow:

    def _utc(self, hh, mm=0, day_offset=0):
        now = datetime.now(timezone.utc) + timedelta(days=day_offset)
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    def test_inactive_never_fires(self):
        cfg = SchedulerConfig(active=False, sources=[
            SourceConfig(id="noaa", query="x")])
        fire, reason = should_fire_now(cfg, SchedulerState())
        assert fire is False
        assert "active=false" in reason

    def test_recent_run_blocks_within_skip_threshold(self):
        cfg = SchedulerConfig(
            active=True, run_at_local="03:00",
            sources=[SourceConfig(id="noaa", query="x")],
            catch_up=CatchUpConfig(skip_if_recent_run_within_hours=12.0),
        )
        # Pretend we ran 1h ago.
        last = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        state = SchedulerState(last_completed_at=last)
        fire, reason = should_fire_now(cfg, state)
        assert fire is False
        assert "recent run" in reason

    def test_catch_up_never_skips_outside_window(self):
        # Schedule at 03:00 local; we're now noon; mode=never; no recent run.
        # Should not fire.
        cfg = SchedulerConfig(
            active=True, run_at_local="03:00",
            sources=[SourceConfig(id="noaa", query="x")],
            catch_up=CatchUpConfig(mode="never"),
        )
        fire, reason = should_fire_now(cfg, SchedulerState(),
                                          now=self._utc(12))
        assert fire is False
        assert reason in ("catch_up=never", "no eligible catch-up window")

    def test_catch_up_next_eligible_within_tolerance(self):
        # Last scheduled fire was 4h ago and we never completed it.
        cfg = SchedulerConfig(
            active=True, run_at_local="03:00",
            sources=[SourceConfig(id="noaa", query="x")],
            catch_up=CatchUpConfig(mode="next_eligible_window",
                                    tolerance_hours=6.0),
        )
        last_sched = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        state = SchedulerState(last_scheduled_fire=last_sched,
                                last_completed_at=None)
        fire, reason = should_fire_now(cfg, state)
        # Outside the daily window but inside the catch-up tolerance.
        assert fire is True
        assert "catch_up" in reason

    def test_catch_up_max_consecutive_missed_blocks(self):
        cfg = SchedulerConfig(
            active=True, run_at_local="03:00",
            sources=[SourceConfig(id="noaa", query="x")],
            catch_up=CatchUpConfig(mode="next_eligible_window",
                                    max_consecutive_missed_runs=3),
        )
        state = SchedulerState(missed_count=3,
                                last_scheduled_fire=(
                                    datetime.now(timezone.utc) - timedelta(hours=2)
                                ).isoformat())
        fire, reason = should_fire_now(cfg, state)
        assert fire is False
        assert "missed_count" in reason


# ============================================================
# Per-source dispatch (no live HTTP — trigger_run is injected)
# ============================================================

class TestDispatch:

    def test_unsupported_source_fails_cleanly(self):
        out = fetch_and_run_one_source(
            SourceConfig(id="not_a_real_source", query="x"),
        )
        assert out["ok"] is False
        assert "unsupported source" in (out.get("error") or "")

    def test_connector_error_does_not_crash(self, monkeypatch):
        # Stub the NOAA connector to raise.
        from fantasyai.aurora.scheduler import overnight
        def fake_connector():
            class C:
                def fetch(self, q): raise RuntimeError("network down")
            return C
        monkeypatch.setattr(overnight, "_connector_for",
                             lambda sid: fake_connector())
        out = fetch_and_run_one_source(
            SourceConfig(id="noaa", query="x"),
        )
        assert out["ok"] is False
        assert "network down" in out["error"]

    def test_dependency_injection_path(self, monkeypatch, tmp_path):
        # Make _connector_for return a class whose fetch writes a CSV,
        # and inject a fake trigger_run.
        from fantasyai.aurora.scheduler import overnight
        csv_path = tmp_path / "fake.csv"
        csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
        class FakeConn:
            def fetch(self, q):
                return csv_path
        monkeypatch.setattr(overnight, "_connector_for",
                             lambda sid: FakeConn)
        called = {"count": 0}
        def fake_trigger_run(**kwargs):
            called["count"] += 1
            assert kwargs.get("dataset") == str(csv_path)
            # Critical: also_llm must be False for overnight runs.
            assert kwargs.get("also_llm") is False
            return {"ok": True, "run_dir": str(tmp_path / "run_x")}
        out = fetch_and_run_one_source(
            SourceConfig(id="noaa", query="x"),
            trigger_run_fn=fake_trigger_run,
        )
        assert out["ok"] is True
        assert called["count"] == 1
        assert out["run_dir"] == str(tmp_path / "run_x")


class TestOvernightPass:

    def test_walks_all_active_sources(self, monkeypatch, tmp_path):
        from fantasyai.aurora.scheduler import overnight
        # Two fake connectors.
        csv_path = tmp_path / "fake.csv"
        csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
        class FakeConn:
            def fetch(self, q): return csv_path
        monkeypatch.setattr(overnight, "_connector_for",
                             lambda sid: FakeConn)
        run_count = {"n": 0}
        def fake_trigger(**k):
            run_count["n"] += 1
            return {"ok": True, "run_dir": str(tmp_path / f"run_{run_count['n']}")}
        cfg = SchedulerConfig(
            sources=[
                SourceConfig(id="noaa", query="q1", active=True),
                SourceConfig(id="usgs", query="q2", active=True),
                SourceConfig(id="world_bank", query="q3", active=False),  # off
            ],
        )
        result = run_overnight_pass(cfg, trigger_run_fn=fake_trigger)
        assert result["n_sources_attempted"] == 2  # world_bank skipped
        assert result["n_ok"] == 2
        # Both run_ids surface.
        ids = [r["run_id"] for r in result["results"]]
        assert all(ids)


# ============================================================
# Sanity
# ============================================================

class TestSupportedSources:
    def test_v1_includes_noaa(self):
        assert "noaa" in list_supported_sources()

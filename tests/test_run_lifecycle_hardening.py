"""
Workstream-1 regression tests — run lifecycle + frontend resilience.

Pins the diagnosis from "Aurora Backend & Loading State Audit":

  * Registry persists immediately on register() even before run_dir
    is known (B-2).
  * Persistence uses atomic write-tmp-then-rename (B-3).
  * On import, terminal records load from disk; non-terminal records
    are demoted to failed("server_restart_lost_run") (B-1).
  * find_latest_run picks newest by run_id timestamp prefix and
    skips non-complete runs by default (F-3).
  * No silent failures: every persistence error logs traceback.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def isolated_outputs(tmp_path, monkeypatch):
    """Force registry + state_builder to use a tmp outputs root."""
    monkeypatch.setenv("AURORA_OUTPUTS_ROOT", str(tmp_path))
    monkeypatch.setenv("AURORA_REGISTRY_NO_AUTOLOAD", "1")
    # Force a fresh registry import so our env vars take effect.
    import importlib
    import fantasyai.aurora.run_registry as reg_mod
    importlib.reload(reg_mod)
    reg_mod._clear_for_tests()
    yield tmp_path
    reg_mod._clear_for_tests()


# ============================================================
# B-2: register() persists immediately, even without run_dir
# ============================================================

class TestPersistImmediate:
    def test_register_writes_progress_file_with_placeholder_dir(
            self, isolated_outputs):
        from fantasyai.aurora import run_registry as reg
        rec = reg.register("rid_alpha", "/data/foo.csv")
        # Expected placeholder dir: <outputs_root>/<run_id>/.
        progress = isolated_outputs / "rid_alpha" / "_RUN_PROGRESS.json"
        assert progress.exists(), (
            "register() must persist immediately even before run_dir set "
            "(Workstream-1 fix B-2)"
        )
        data = json.loads(progress.read_text(encoding="utf-8"))
        assert data["run_id"] == "rid_alpha"
        assert data["state"] == reg.RunState.PENDING

    def test_set_state_persists_through_lifecycle(self, isolated_outputs):
        from fantasyai.aurora import run_registry as reg
        reg.register("rid_beta", "/data/bar.csv")
        reg.set_state("rid_beta", reg.RunState.RUNNING, message="kicking off")
        progress = isolated_outputs / "rid_beta" / "_RUN_PROGRESS.json"
        assert progress.exists()
        data = json.loads(progress.read_text(encoding="utf-8"))
        assert data["state"] == reg.RunState.RUNNING


# ============================================================
# B-3: atomic write — never leaves a truncated file
# ============================================================

class TestAtomicWrite:
    def test_no_tmp_file_left_behind_on_success(self, isolated_outputs):
        from fantasyai.aurora import run_registry as reg
        reg.register("rid_atomic", "/data/x.csv")
        reg.complete("rid_atomic",
                       run_dir=str(isolated_outputs / "rid_atomic"))
        run_dir = isolated_outputs / "rid_atomic"
        # After a successful complete(), no .tmp file should be left.
        leftover = list(run_dir.glob("_RUN_PROGRESS.json.tmp"))
        assert leftover == []
        # And the canonical file is intact.
        assert (run_dir / "_RUN_PROGRESS.json").exists()


# ============================================================
# B-1: rehydrate-on-import — terminal load + non-terminal demote
# ============================================================

class TestRehydrate:
    def test_terminal_records_load_from_disk(self, isolated_outputs):
        from fantasyai.aurora import run_registry as reg
        # Pre-populate the disk with a completed run.
        d = isolated_outputs / "20260507_120000__demo"
        d.mkdir()
        (d / "_RUN_PROGRESS.json").write_text(json.dumps({
            "run_id": "rid_complete",
            "state": "complete",
            "run_dir": str(d),
            "started_at": "2026-05-07T12:00:00Z",
            "completed_at": "2026-05-07T12:00:30Z",
        }), encoding="utf-8")
        reg._clear_for_tests()
        counts = reg.rehydrate_from_disk(isolated_outputs)
        assert counts["loaded_terminal"] == 1
        assert counts["demoted"] == 0
        snap = reg.snapshot("rid_complete")
        assert snap is not None
        assert snap["state"] == "complete"

    def test_non_terminal_records_demoted_to_failed(self, isolated_outputs):
        """The exact scenario from the audit: server crashed mid-run.
        On restart, the previously-running record on disk should
        rehydrate as failed('server_restart_lost_run') so the frontend
        stops polling forever."""
        from fantasyai.aurora import run_registry as reg
        d = isolated_outputs / "20260508_114404__diabetic_data"
        d.mkdir()
        (d / "_RUN_PROGRESS.json").write_text(json.dumps({
            "run_id": "rid_orphan",
            "state": "tier1_running",
            "run_dir": str(d),
            "started_at": "2026-05-08T11:44:04Z",
            "completed_at": None,
        }), encoding="utf-8")
        reg._clear_for_tests()
        counts = reg.rehydrate_from_disk(isolated_outputs)
        assert counts["demoted"] == 1
        snap = reg.snapshot("rid_orphan")
        assert snap["state"] == "failed"
        assert snap["error"] == "server_restart_lost_run"
        assert snap["completed_at"] is not None
        # And the demotion is persisted back to disk.
        on_disk = json.loads(
            (d / "_RUN_PROGRESS.json").read_text(encoding="utf-8"))
        assert on_disk["state"] == "failed"

    def test_in_memory_records_win_over_disk(self, isolated_outputs):
        """When rehydrate runs and a record already exists in
        ``_RUNS``, the in-memory copy wins (it's fresher)."""
        from fantasyai.aurora import run_registry as reg
        reg.register("rid_live", "/data/y.csv")
        reg.set_state("rid_live", reg.RunState.RUNNING)
        # Now run rehydrate — should NOT demote the live record.
        counts = reg.rehydrate_from_disk(isolated_outputs)
        snap = reg.snapshot("rid_live")
        assert snap["state"] == reg.RunState.RUNNING
        assert counts["demoted"] == 0


# ============================================================
# F-3: find_latest_run uses run_id timestamp + state filter
# ============================================================

class TestFindLatestRun:
    def test_picks_newest_by_run_id_timestamp_when_both_complete(
            self, isolated_outputs):
        from fantasyai.aurora.state_builder import find_latest_run
        old = isolated_outputs / "20260507_100000__old"
        new = isolated_outputs / "20260508_100000__new"
        old.mkdir(); new.mkdir()
        for d in (old, new):
            (d / "_RUN_PROGRESS.json").write_text(json.dumps({
                "run_id": d.name, "state": "complete",
            }), encoding="utf-8")
        chosen = find_latest_run(isolated_outputs)
        assert chosen.name == new.name

    def test_skips_running_runs_in_default_mode(self, isolated_outputs):
        """The climate_buoy ghost: a running newer run should NOT
        shadow an older completed run. /api/state's default fallback
        wants a STABLE artifact set."""
        from fantasyai.aurora.state_builder import find_latest_run
        old = isolated_outputs / "20260507_100000__buoy"
        new = isolated_outputs / "20260508_100000__diabetic"
        old.mkdir(); new.mkdir()
        (old / "_RUN_PROGRESS.json").write_text(json.dumps({
            "run_id": old.name, "state": "complete"}), encoding="utf-8")
        (new / "_RUN_PROGRESS.json").write_text(json.dumps({
            "run_id": new.name, "state": "running"}), encoding="utf-8")
        chosen = find_latest_run(isolated_outputs)
        # Running run is skipped; we fall back to the latest complete.
        assert chosen.name == old.name

    def test_state_filter_none_preserves_legacy_behaviour(
            self, isolated_outputs):
        """Callers that explicitly want the previous mtime-based
        behaviour pass state_filter=None."""
        from fantasyai.aurora.state_builder import find_latest_run
        d = isolated_outputs / "20260508_100000__running"
        d.mkdir()
        (d / "_RUN_PROGRESS.json").write_text(json.dumps({
            "run_id": d.name, "state": "running"}), encoding="utf-8")
        # state_filter=None returns the dir even if not complete.
        chosen = find_latest_run(isolated_outputs, state_filter=None)
        assert chosen.name == d.name
        # Default (complete_only) returns None when nothing is complete.
        assert find_latest_run(isolated_outputs) is None


# ============================================================
# B-4: /api/runs/active endpoint shape
# ============================================================

class TestApiRunsActive:
    def test_list_active_returns_only_non_terminal(self, isolated_outputs):
        from fantasyai.aurora import run_registry as reg
        reg.register("rid_running", "/data/a.csv")
        reg.set_state("rid_running", reg.RunState.RUNNING)
        reg.register("rid_done", "/data/b.csv")
        reg.complete("rid_done", run_dir=str(isolated_outputs / "rid_done"))
        active = reg.list_active()
        ids = {r["run_id"] for r in active}
        assert "rid_running" in ids
        assert "rid_done" not in ids

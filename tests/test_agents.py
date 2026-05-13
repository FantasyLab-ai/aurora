"""
Tests for ``fantasyai/aurora/agents``: base + data acquisition + analysis.

We do NOT make live HTTP calls. Network-bound agents are tested via the
no-key gate (returns "blocked"/"no_op") and the run-log audit trail.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_AGENT_RUNLOG", str(tmp_path / "runlog.jsonl"))
    monkeypatch.setenv("AURORA_LEARNING_STORE", str(tmp_path / "learn.jsonl"))
    monkeypatch.setenv("AURORA_PRIOR_VALIDATIONS", str(tmp_path / "prior_v.jsonl"))
    yield


# ============================================================
# Base agent: timing + run-log + benign failure
# ============================================================

class TestBaseAgent:

    def test_run_returns_agentresult_and_logs(self):
        from fantasyai.aurora.agents.base import BaseAgent, AgentResult, read_runlog

        class TestAgent(BaseAgent):
            agent_id = "test_simple"
            display_name = "Test agent"

            def _do_run(self, task):
                return AgentResult(
                    agent_id=self.agent_id, task_id="", ts="",
                    status="ok", summary="hello",
                )

        a = TestAgent()
        res = a.run({"k": "v"})
        assert res.status == "ok"
        assert res.summary == "hello"
        # Windows time.time() resolution can be ~15ms, so a no-op may clock 0.
        assert res.duration_ms >= 0
        # Should have one row in the run-log.
        rows = read_runlog()
        assert any(r["agent_id"] == "test_simple" and r["status"] == "ok"
                   for r in rows)

    def test_failure_is_caught_and_logged(self):
        from fantasyai.aurora.agents.base import BaseAgent, read_runlog

        class FlakyAgent(BaseAgent):
            agent_id = "test_flaky"
            display_name = "Flaky"

            def _do_run(self, task):
                raise RuntimeError("kaboom")

        a = FlakyAgent()
        res = a.run({})
        assert res.status == "failed"
        assert "kaboom" in res.summary
        rows = read_runlog()
        assert any(r["status"] == "failed" for r in rows)

    def test_subclass_must_return_agentresult(self):
        from fantasyai.aurora.agents.base import BaseAgent

        class BrokenAgent(BaseAgent):
            agent_id = "test_broken"
            display_name = "B"

            def _do_run(self, task):
                return {"oops": "wrong type"}

        a = BrokenAgent()
        res = a.run({})
        assert res.status == "failed"
        assert "must return AgentResult" in (res.summary or "")


# ============================================================
# Data acquisition: blocked / no-op behavior
# ============================================================

class TestDataAcquisition:

    def test_noaa_blocked_without_key(self, monkeypatch):
        monkeypatch.delenv("NOAA_CDO_API_KEY", raising=False)
        from fantasyai.aurora.agents.data_acquisition import NOAAAcquisitionAgent
        a = NOAAAcquisitionAgent()
        res = a.run({"query": "Central Park"})
        assert res.status == "blocked"
        assert "Missing API key" in res.summary

    def test_fred_blocked_without_key(self, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        from fantasyai.aurora.agents.data_acquisition import FREDAcquisitionAgent
        res = FREDAcquisitionAgent().run({"query": "GDP"})
        assert res.status == "blocked"

    def test_csv_url_no_op_without_url(self):
        from fantasyai.aurora.agents.data_acquisition import CSVURLAcquisitionAgent
        res = CSVURLAcquisitionAgent().run({})
        assert res.status == "no_op"
        assert "missing" in res.summary

    def test_csv_url_blocked_in_offline_mode(self, tmp_path):
        from fantasyai.aurora.agents.data_acquisition import CSVURLAcquisitionAgent
        a = CSVURLAcquisitionAgent(cache_dir=tmp_path / "cache")
        res = a.run({"url": "https://example.com/foo.csv", "offline": True})
        assert res.status == "blocked"


# ============================================================
# Analysis agents: orchestrator + triage
# ============================================================

class TestAnalysisAgents:

    def test_orchestrator_no_op_when_no_candidates(self):
        from fantasyai.aurora.agents.analysis import RunOrchestrationAgent
        res = RunOrchestrationAgent().run({"candidates": []})
        assert res.status == "no_op"

    def test_orchestrator_ranks_candidates(self):
        from fantasyai.aurora.agents.analysis import RunOrchestrationAgent
        candidates = [
            {"path": "/c/big_new.csv", "rows": 5000, "cols": 5,
             "ts": "2026-05-04T08:00:00", "agent_id": "agent_noaa"},
            {"path": "/c/small_old.csv", "rows": 30, "cols": 2,
             "ts": "2026-04-01T08:00:00", "agent_id": "agent_csv_url"},
        ]
        res = RunOrchestrationAgent().run({"candidates": candidates, "top_k": 1})
        assert res.status == "ok"
        assert "selected 1" in res.summary
        # Decisions present + glass-box (carry "why").
        assert res.decisions
        for d in res.decisions:
            assert "why" in d
            assert "confidence" in d

    def test_triage_returns_diary(self):
        from fantasyai.aurora.agents.analysis import build_morning_diary
        out = build_morning_diary(within_hours=24)
        assert "entries" in out
        # Empty store → no entries (or a single confirmation line). Both fine.
        assert isinstance(out["entries"], list)

    def test_triage_surfaces_contradiction(self):
        """Seed a contradiction in the validation log; diary must surface it."""
        from fantasyai.aurora.priors.validation_log import append_validation
        from fantasyai.aurora.agents.analysis import build_morning_diary
        append_validation("newton_cooling", "contradiction",
                          run_dir="/r1", note="explicit disagreement")
        out = build_morning_diary(within_hours=24)
        kinds = {e.get("kind") for e in out.get("entries", [])}
        assert "contradiction" in kinds


# ============================================================
# Scheduler tick
# ============================================================

class TestScheduler:

    def test_tick_runs_all_registered(self):
        from fantasyai.aurora.agents.base import (
            BaseAgent, AgentResult, AgentScheduler,
        )

        class NoOpAgent(BaseAgent):
            agent_id = "test_noop"
            display_name = "NoOp"

            def _do_run(self, task):
                return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                                    status="no_op", summary="nothing to do")

        s = AgentScheduler()
        s.register(NoOpAgent()).register(NoOpAgent())
        results = s.tick()
        assert len(results) == 2
        assert all(r.status == "no_op" for r in results)

    def test_default_scheduler_loads(self):
        from fantasyai.aurora.agents import default_scheduler
        s = default_scheduler()
        agents = s.list_agents()
        ids = {a["agent_id"] for a in agents}
        assert "agent_noaa" in ids
        assert "agent_fred" in ids
        assert "agent_csv_url" in ids
        assert "agent_orchestrator" in ids
        assert "agent_triage" in ids

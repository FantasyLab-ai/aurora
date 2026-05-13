"""
Agent base class — bounded, inspectable, fails benignly.

The strategic invariant: agents in Aurora are NEVER black-box. Every agent:

  1. Takes a bounded task (find data matching this description; queue this
     dataset for analysis; …) — no open-ended objectives
  2. Carries full inspectable state — every input, output, decision is
     written to a JSONL run-log we can read back later
  3. Fails benignly — agent failure NEVER corrupts user data, blocks the
     pipeline, or escalates. The default failure is "no progress this tick"
  4. Is dependency-free at the Aurora level: agents call existing connectors
     and analysis modules; they don't reach for new packages
  5. Reports verdicts as structured findings, not free text

This module ships the abstract base class + run-log machinery + a tiny
scheduler. Concrete agents live in:

    agents/data_acquisition.py    (NOAA, FRED, generic CSV-from-URL)
    agents/analysis.py            (orchestrate runs, triage findings, morning diary)
    agents/curation.py            (prior library housekeeping)
"""
from __future__ import annotations

import json
import math
import os
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """What an agent.run() returns: a structured verdict + audit trail.

    Every field is required; agents that have nothing useful to report still
    emit an AgentResult with ``status='no_op'`` so the run-log captures the
    fact that the agent ran and chose to do nothing.
    """
    agent_id: str                  # connector_name or analysis_orchestrator etc.
    task_id: str                   # unique id for this invocation
    ts: str                        # ISO timestamp
    status: str                    # ok | no_op | failed | blocked
    summary: str                   # one-line human readable
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    # ↑ each entry: {kind, path, hash, rows?, cols?}
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    # ↑ each entry: {decision, why, confidence, alternatives_considered}
    error: Optional[str] = None
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                d[k] = None
        return d


# ---------------------------------------------------------------------------
# Run log (per-agent JSONL)
# ---------------------------------------------------------------------------

def default_runlog_path() -> Path:
    override = os.environ.get("AURORA_AGENT_RUNLOG")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".aurora" / "agent_runlog.jsonl"


def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def _json_safe(o: Any) -> Any:
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return None
        return o
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if o is None or isinstance(o, (bool, int, str)):
        return o
    try:
        return str(o)
    except Exception:
        return None


def append_runlog(result: AgentResult, log_path: Optional[Path] = None) -> None:
    log_path = log_path or default_runlog_path()
    _ensure_dir(log_path)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_json_safe(result.to_dict()),
                           separators=(",", ":")) + "\n")


def read_runlog(limit: int = 200,
                agent_id: Optional[str] = None,
                log_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    log_path = log_path or default_runlog_path()
    if not log_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if agent_id and r.get("agent_id") != agent_id:
            continue
        rows.append(r)
    rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
# Base agent
# ---------------------------------------------------------------------------

class BaseAgent(ABC):
    """ABC for all Aurora agents.

    Subclasses implement ``_do_run(task)`` returning an ``AgentResult``. The
    base class wraps that with timing + run-log persistence + benign-failure
    handling so every agent automatically gets the same audit trail.
    """

    # Subclass overrides
    agent_id: str = "base"
    display_name: str = "Base agent"
    description: str = ""
    bounded_task_description: str = "Generic bounded task"

    def __init__(self, log_path: Optional[Path] = None):
        self._log_path = log_path

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "description": self.description,
            "bounded_task_description": self.bounded_task_description,
        }

    # ---- lifecycle ----
    def run(self, task: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Public entry — wraps _do_run with timing + benign-failure trap."""
        start = time.time()
        task_id = f"{self.agent_id}-{int(start*1000)}"
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            result = self._do_run(task or {})
            # Allow subclasses to return either a fully-formed AgentResult or
            # (status, summary, artifacts, decisions) tuples for convenience.
            if not isinstance(result, AgentResult):
                raise TypeError(
                    f"{self.agent_id}._do_run must return AgentResult, "
                    f"got {type(result).__name__}"
                )
            # Stamp identifying fields the subclass might have skipped.
            result.agent_id = result.agent_id or self.agent_id
            result.task_id = result.task_id or task_id
            result.ts = result.ts or ts
            result.duration_ms = (time.time() - start) * 1000.0
        except Exception as e:
            result = AgentResult(
                agent_id=self.agent_id, task_id=task_id, ts=ts,
                status="failed",
                summary=f"{type(e).__name__}: {e}",
                artifacts=[], decisions=[],
                error=traceback.format_exc(limit=4),
                duration_ms=(time.time() - start) * 1000.0,
            )
        try:
            append_runlog(result, log_path=self._log_path)
        except Exception:
            pass  # Run-log is enrichment; never block.
        return result

    @abstractmethod
    def _do_run(self, task: Dict[str, Any]) -> AgentResult:
        """Subclass implementation. Must return AgentResult."""
        ...


# ---------------------------------------------------------------------------
# Tiny scheduler — wakes up, runs all registered agents once.
# ---------------------------------------------------------------------------

class AgentScheduler:
    """A pull-based scheduler.

    Aurora doesn't ship a daemon — instead, ``schedule_tick()`` is meant to be
    called from a cron / Task Scheduler / GitHub Action / user click. Each
    tick walks every registered agent, runs it once, and appends the result.
    The scheduler itself is stateless beyond the run-log.

    Phase 0's two-week observation period is just a cron that calls
    ``schedule_tick()`` hourly or daily; no separate plumbing required.
    """

    def __init__(self):
        self._agents: List[BaseAgent] = []

    def register(self, agent: BaseAgent) -> "AgentScheduler":
        self._agents.append(agent)
        return self

    def list_agents(self) -> List[Dict[str, Any]]:
        return [a.to_metadata() for a in self._agents]

    def tick(self, task_for: Optional[Dict[str, Dict[str, Any]]] = None
              ) -> List[AgentResult]:
        """Run every registered agent once. Returns each result in order.

        Args:
            task_for: optional per-agent task overrides
                       ``{agent_id: {…task fields…}}``
        """
        task_for = task_for or {}
        out: List[AgentResult] = []
        for agent in self._agents:
            task = task_for.get(agent.agent_id, {})
            out.append(agent.run(task))
        return out


# ---------------------------------------------------------------------------
# Convenience: produce a simple "what happened on the last tick" summary
# ---------------------------------------------------------------------------

def latest_tick_summary(log_path: Optional[Path] = None,
                        within_seconds: int = 3600) -> Dict[str, Any]:
    """Aggregate the last hour of runlog rows into a tick-summary."""
    rows = read_runlog(limit=500, log_path=log_path)
    if not rows:
        return {"n_results": 0, "by_status": {}, "agents": []}
    cutoff = time.time() - within_seconds
    recent = [r for r in rows if _ts_to_epoch(r.get("ts", "")) >= cutoff]
    by_status: Dict[str, int] = {}
    by_agent: Dict[str, Dict[str, int]] = {}
    for r in recent:
        s = r.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
        a = r.get("agent_id", "unknown")
        by_agent.setdefault(a, {"ok": 0, "no_op": 0, "failed": 0, "blocked": 0})
        by_agent[a][s] = by_agent[a].get(s, 0) + 1
    return {
        "n_results": len(recent),
        "by_status": by_status,
        "agents": [{"agent_id": k, **v} for k, v in by_agent.items()],
    }


def _ts_to_epoch(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0

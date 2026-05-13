"""
Aurora agents — bounded, inspectable, fail benignly.

Three agent families:

  * Data acquisition  — find new data, validate it, queue it for analysis
  * Analysis          — orchestrate runs, triage findings, build morning diary
  * Curation          — prior-library housekeeping (Phase 4)

Use ``default_scheduler()`` to get a tick-runnable bundle of all the
agents shipped today; or instantiate individual agents directly.
"""
from __future__ import annotations

from typing import List

from .base import (
    BaseAgent, AgentResult, AgentScheduler,
    append_runlog, read_runlog, latest_tick_summary, default_runlog_path,
)
from .data_acquisition import (
    NOAAAcquisitionAgent, FREDAcquisitionAgent, CSVURLAcquisitionAgent,
)
from .analysis import (
    RunOrchestrationAgent, FindingTriageAgent, build_morning_diary,
)
from .action_agents import (
    EnviroAdvisoryAgent, ResearchFollowUpAgent, OpsRemediationAgent,
    IndustrialMaintenanceAgent, FinanceHedgeAgent, MedicalSurveillanceAgent,
    all_action_agents,
)


def default_scheduler() -> AgentScheduler:
    """Return a scheduler with all shipped agents registered.

    Order: acquire → orchestrate → triage → action. Action agents fire last
    because they read the latest run's system_model.json which is written by
    state_builder during the analysis phase.
    """
    s = AgentScheduler()
    # Tier 1: data acquisition
    s.register(NOAAAcquisitionAgent())
    s.register(FREDAcquisitionAgent())
    s.register(CSVURLAcquisitionAgent())
    # Tier 2: analysis
    s.register(RunOrchestrationAgent())
    s.register(FindingTriageAgent())
    # Tier 3: action drafting (one per domain template)
    for a in all_action_agents():
        s.register(a)
    return s


__all__ = [
    "BaseAgent", "AgentResult", "AgentScheduler",
    "append_runlog", "read_runlog", "latest_tick_summary", "default_runlog_path",
    "NOAAAcquisitionAgent", "FREDAcquisitionAgent", "CSVURLAcquisitionAgent",
    "RunOrchestrationAgent", "FindingTriageAgent", "build_morning_diary",
    "EnviroAdvisoryAgent", "ResearchFollowUpAgent", "OpsRemediationAgent",
    "IndustrialMaintenanceAgent", "FinanceHedgeAgent", "MedicalSurveillanceAgent",
    "all_action_agents",
    "default_scheduler",
]

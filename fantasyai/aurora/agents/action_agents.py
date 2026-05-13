"""
Action-specific agents — bounded, inspectable, fail benignly.

Where data-acquisition agents fetch and analysis agents triage, **action**
agents *propose* concrete next steps tied to the system-model's action_library.
They never execute irreversible actions on external systems; they draft and
log actionable recommendations the user can approve.

Each action agent:
  * reads the latest run's system_model + findings
  * filters the action_library by current state (regime / active patterns)
  * produces an ``AgentResult`` whose ``decisions`` list is a glass-box
    ranked list of recommended actions, each with predicted_outcome,
    base_rate, falsification, and assumptions

Six concrete agents — one per shipped domain.

Glass-box rule: every action carries the math it depends on. The agent
never invents a reason; it only routes the template's prescribed reasoning.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent, AgentResult


# ---------------------------------------------------------------------------
# Shared draft helper
# ---------------------------------------------------------------------------

def _load_latest_system_model() -> Optional[Dict[str, Any]]:
    """Read the freshest run's system_model.json. Returns None if not found."""
    try:
        from ..state_builder import find_latest_run
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[3]
        outputs = repo_root / "outputs" / "aurora_dataset_runs"
        latest = find_latest_run(outputs)
        if latest is None:
            return None
        sm_path = latest / "system_model.json"
        if not sm_path.exists():
            return None
        import json
        return json.loads(sm_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _draft_actions_for_domain(domain_tag: str,
                               sm: Optional[Dict[str, Any]]
                               ) -> Tuple[List[Dict[str, Any]], str]:
    """Pull actions from the system model, filter by domain + current state.

    Returns (decisions, summary_text).
    """
    if sm is None:
        return [], "no system_model.json available — run an analysis first"
    prov = sm.get("provenance") or {}
    model_domain = prov.get("template_id")
    if model_domain != domain_tag:
        return [], (f"latest run is template={model_domain!r}; this agent "
                    f"only fires for {domain_tag!r} runs")

    actions = sm.get("action_library") or []
    state = sm.get("state") or {}
    active_pids = {p.get("pattern_id") for p in (state.get("active_patterns") or [])
                   if isinstance(p, dict)}
    confidence = float(sm.get("confidence") or 0.0)

    decisions: List[Dict[str, Any]] = []
    for a in actions:
        rel = set(a.get("relevant_states") or [])
        # Filter: keep an action if it's relevant to an active pattern, OR
        # has no specific state requirement (always-applicable).
        if rel and not (rel & active_pids):
            continue
        # Confidence-band filter: a "high" action requires model_confidence ≥ 0.7
        band = (a.get("confidence_band_required") or "medium").lower()
        if band == "high" and confidence < 0.7:
            continue
        if band == "medium" and confidence < 0.4:
            continue
        decisions.append({
            "decision": "draft_action",
            "why": (f"action {a.get('id')} is relevant under active patterns "
                    f"{list(rel & active_pids) or '(any)'}, "
                    f"model confidence {confidence:.2f} ≥ {band} band"),
            "confidence": confidence,
            "alternatives_considered": [b.get("id") for b in actions
                                         if b is not a][:3],
            "action_id": a.get("id"),
            "action_label": a.get("label"),
            "predicted_outcome": a.get("predicted_outcome_summary"),
            "base_rate_success": a.get("base_rate_success"),
            "falsification": a.get("falsification") or [],
            "assumptions": a.get("assumptions") or [],
            "triggered_when": a.get("triggered_when") or [],
        })

    if not decisions:
        return [], (f"no {domain_tag} actions matched the current state — "
                    f"either patterns aren't active or model confidence "
                    f"is below the required band")
    summary = f"{domain_tag}: drafted {len(decisions)} candidate actions"
    return decisions, summary


# ---------------------------------------------------------------------------
# Concrete domain agents
# ---------------------------------------------------------------------------

class _BaseActionAgent(BaseAgent):
    """Common implementation shared by every domain action agent."""
    domain_tag: str = "general"

    def _do_run(self, task: Dict[str, Any]) -> AgentResult:
        sm = _load_latest_system_model()
        decisions, summary = _draft_actions_for_domain(self.domain_tag, sm)
        return AgentResult(
            agent_id=self.agent_id, task_id="", ts="",
            status="ok" if decisions else "no_op",
            summary=summary, decisions=decisions,
            artifacts=([{"kind": "system_model_used",
                          "template_id": (sm.get("provenance") or {}).get("template_id"),
                          "confidence": (sm.get("confidence")
                                          if isinstance(sm.get("confidence"), (int, float))
                                          else None)}]
                       if sm else []),
        )


class EnviroAdvisoryAgent(_BaseActionAgent):
    agent_id = "agent_action_enviro"
    display_name = "Environmental advisory action agent"
    description = ("Drafts operational advisories (issue advisory, raise "
                   "monitoring cadence, scenario forecasts) from the enviro template.")
    bounded_task_description = "Draft enviro actions; never executes them."
    domain_tag = "enviro"


class ResearchFollowUpAgent(_BaseActionAgent):
    agent_id = "agent_action_research"
    display_name = "Research follow-up action agent"
    description = ("Drafts hypothesis prioritization, multiple-test corrections, "
                   "and replication plans from the research template.")
    bounded_task_description = "Draft research actions."
    domain_tag = "research"


class OpsRemediationAgent(_BaseActionAgent):
    agent_id = "agent_action_ops"
    display_name = "Ops remediation action agent"
    description = ("Drafts SRE remediations (scale up, rollback, throttle, "
                   "circuit breaker) from the ops template.")
    bounded_task_description = "Draft ops remediations."
    domain_tag = "ops"


class IndustrialMaintenanceAgent(_BaseActionAgent):
    agent_id = "agent_action_industrial"
    display_name = "Industrial maintenance action agent"
    description = ("Drafts calibration scheduling, preventive maintenance, "
                   "batch quarantine, process tuning from the industrial template.")
    bounded_task_description = "Draft industrial maintenance actions."
    domain_tag = "industrial"


class FinanceHedgeAgent(_BaseActionAgent):
    agent_id = "agent_action_finance"
    display_name = "Finance hedge action agent"
    description = ("Drafts vol-targeted sizing, hedge construction, risk-budget "
                   "enforcement, stress-test triggers from the finance template.")
    bounded_task_description = "Draft finance hedging / risk actions."
    domain_tag = "finance"


class MedicalSurveillanceAgent(_BaseActionAgent):
    agent_id = "agent_action_medical"
    display_name = "Medical surveillance action agent"
    description = ("Drafts surveillance escalation, differential workup, "
                   "treatment review, alert thresholding from the medical template.")
    bounded_task_description = "Draft clinical surveillance actions."
    domain_tag = "medical"


def all_action_agents() -> List[BaseAgent]:
    return [
        EnviroAdvisoryAgent(),
        ResearchFollowUpAgent(),
        OpsRemediationAgent(),
        IndustrialMaintenanceAgent(),
        FinanceHedgeAgent(),
        MedicalSurveillanceAgent(),
    ]

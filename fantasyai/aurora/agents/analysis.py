"""
Analysis-running agents — orchestrate runs and triage findings.

Two concrete agents:

* ``RunOrchestrationAgent`` — given a list of recently-acquired CSVs,
  decides which to run analysis on this tick (priority order with rationale)
* ``FindingTriageAgent`` — reads runs from the last 24h and produces the
  "morning research diary": confirmations, contradictions, novel patterns,
  things to look at first

Glass-box rule (strict): every prioritization decision is a structured
``decision`` entry with ``why``, ``confidence``, and ``alternatives_considered``.
If the decision logic ever becomes opaque, the agent is broken.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent, AgentResult


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------

class RunOrchestrationAgent(BaseAgent):
    """Pick which acquired datasets to analyze this tick.

    Bounded task: take a list of CSV paths (or read them from the recent
    runlog), score them, return the top-K with rationale.

    Scoring rule (deterministic + inspectable):

      score =  rows_factor + numeric_factor + novelty_factor − staleness_penalty
      rows_factor       = log10(max(rows, 10)) × 0.2     (bigger data → higher)
      numeric_factor    = min(numeric_cols, 8) × 0.10     (more numeric → higher)
      novelty_factor    = +0.5 if dataset_key never analyzed, else 0
      staleness_penalty = (days_since_acquired / 30)      (recent > stale)

    Easy to tune. Easy to explain to the user why X was picked over Y.
    """

    agent_id = "agent_orchestrator"
    display_name = "Run orchestration agent"
    description = "Decides which acquired datasets to run analysis on this tick."
    bounded_task_description = (
        "Score and rank candidate datasets by analysis priority."
    )

    def _do_run(self, task: Dict[str, Any]) -> AgentResult:
        # Candidates can be passed in directly OR pulled from the recent runlog.
        candidates: List[Dict[str, Any]] = (task or {}).get("candidates") or []
        if not candidates:
            from .base import read_runlog
            recent = read_runlog(limit=200)
            for r in recent:
                if r.get("status") != "ok":
                    continue
                for art in (r.get("artifacts") or []):
                    if art.get("kind") == "csv" and art.get("path"):
                        candidates.append({
                            "path": art["path"],
                            "rows": art.get("rows"),
                            "cols": art.get("cols"),
                            "ts": r.get("ts"),
                            "agent_id": r.get("agent_id"),
                        })
                        break    # one CSV per run-result
        # Deduplicate by path.
        seen, deduped = set(), []
        for c in candidates:
            p = c.get("path")
            if not p or p in seen:
                continue
            seen.add(p); deduped.append(c)
        if not deduped:
            return AgentResult(agent_id=self.agent_id, task_id="", ts="",
                               status="no_op",
                               summary="no candidate datasets in run-log")

        # Get the set of dataset keys we've already analyzed (from learning store).
        known_keys: set = set()
        try:
            from ..learning import read_all
            for r in read_all():
                if r.get("row_type") == "run_learning_v1":
                    n = r.get("dataset_name") or ""
                    known_keys.add(n)
        except Exception:
            pass

        ranked: List[Tuple[float, Dict[str, Any]]] = []
        now = time.time()
        for c in deduped:
            rows = c.get("rows") or 100
            cols = c.get("cols") or 2
            ts_str = c.get("ts") or ""
            from .base import _ts_to_epoch
            age_days = max(0.0, (now - _ts_to_epoch(ts_str)) / 86400.0)
            import math as _m, os as _os
            rows_factor = _m.log10(max(int(rows), 10)) * 0.2
            numeric_factor = min(int(cols), 8) * 0.10
            basename = _os.path.basename(c.get("path", ""))
            novelty_factor = 0.5 if basename not in known_keys else 0.0
            staleness = age_days / 30.0
            score = rows_factor + numeric_factor + novelty_factor - staleness
            c["_score"] = score
            c["_score_breakdown"] = {
                "rows_factor": rows_factor,
                "numeric_factor": numeric_factor,
                "novelty_factor": novelty_factor,
                "staleness_penalty": staleness,
            }
            ranked.append((score, c))

        ranked.sort(key=lambda kv: -kv[0])
        top_k = int((task or {}).get("top_k", 3))
        chosen = [r[1] for r in ranked[:top_k]]

        decisions = []
        for r in chosen:
            decisions.append({
                "decision": "run_analysis",
                "why": (f"score = {r['_score']:.2f}: "
                        + ", ".join(f"{k}={v:+.2f}" for k, v
                                    in r["_score_breakdown"].items())),
                "confidence": 0.7,
                "alternatives_considered":
                    [c.get("path") for _, c in ranked[top_k:top_k+3]],
                "candidate_path": r.get("path"),
            })

        return AgentResult(
            agent_id=self.agent_id, task_id="", ts="", status="ok",
            summary=(f"orchestrator selected {len(chosen)} of "
                     f"{len(deduped)} candidates"),
            artifacts=[{"kind": "ranking", "candidates": deduped}],
            decisions=decisions,
        )


# ---------------------------------------------------------------------------
# Morning diary
# ---------------------------------------------------------------------------

@dataclass
class DiaryEntry:
    rank: int
    headline: str
    detail: str
    kind: str                    # confirmation | contradiction | novelty | warning
    salience: float              # 0..1; higher = "look at this first"
    run_dir: Optional[str]
    prior_id: Optional[str]
    citations: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FindingTriageAgent(BaseAgent):
    """Read the last 24h of runs + validation changelog, produce the morning diary.

    The diary is what the user wakes up to: ranked list of what changed,
    what's new, what to look at first. Every entry one click from full
    provenance.

    Salience is computed deterministically:
      contradictions = 1.0
      promotions     = 0.85
      weakenings     = 0.75
      novel patterns = 0.65
      confirmations  = 0.40

    The agent never invents content; it only re-shapes existing ledger rows.
    """

    agent_id = "agent_triage"
    display_name = "Finding triage agent"
    description = "Builds the morning research diary from the last day's evidence."
    bounded_task_description = (
        "Read recent runs and produce a ranked, glass-box diary."
    )

    def _do_run(self, task: Dict[str, Any]) -> AgentResult:
        within_hours = int((task or {}).get("within_hours", 24))

        # Pull recent runs from the learning store.
        try:
            from ..learning import read_all as read_learning
            learning_rows = read_learning() or []
        except Exception:
            learning_rows = []

        # Pull the prior validation log to spot recent contradictions.
        try:
            from ..priors.validation_log import read_all as read_validations
            valid_rows = read_validations() or []
        except Exception:
            valid_rows = []

        cutoff = time.time() - within_hours * 3600
        from .base import _ts_to_epoch
        recent_runs = [r for r in learning_rows
                       if r.get("row_type") == "run_learning_v1"
                       and _ts_to_epoch(r.get("ts", "")) >= cutoff]
        recent_valid = [r for r in valid_rows
                        if _ts_to_epoch(r.get("ts", "")) >= cutoff]

        entries: List[DiaryEntry] = []

        # 1. Contradictions — surface every recent contradiction explicitly.
        for r in recent_valid:
            if r.get("verdict") == "contradiction":
                entries.append(DiaryEntry(
                    rank=0,    # filled below
                    headline=(f"⚠ Contradiction: {r.get('prior_id')} "
                               f"violated by recent run"),
                    detail=(r.get("note")
                            or f"Run at {r.get('run_dir')} contradicted prior "
                               f"{r.get('prior_id')}."),
                    kind="contradiction",
                    salience=1.0,
                    run_dir=r.get("run_dir"),
                    prior_id=r.get("prior_id"),
                    citations=["prior_validations.jsonl"],
                ))

        # 2. Promotions / demotions — read the prior summaries.
        try:
            from ..priors.validation_log import (
                all_prior_ids_seen, summarize_prior,
            )
            for pid in all_prior_ids_seen():
                s = summarize_prior(pid)
                if s.recent_trend == "weakening" and s.suggested_status == "questioned":
                    entries.append(DiaryEntry(
                        rank=0,
                        headline=(f"Prior weakening: {pid} now status "
                                   f"'{s.suggested_status}'"),
                        detail=(f"Confidence {s.confidence:.2f} over "
                                f"{s.n_total} validations; recent trend "
                                f"is {s.recent_trend}. Investigate."),
                        kind="warning",
                        salience=0.75,
                        run_dir=None,
                        prior_id=pid,
                        citations=["prior_validations.jsonl"],
                    ))
                elif s.recent_trend == "strengthening" and s.suggested_status == "canonical":
                    entries.append(DiaryEntry(
                        rank=0,
                        headline=(f"Prior strengthened: {pid} → canonical "
                                   f"({s.confidence:.2f} confidence)"),
                        detail=(f"{s.n_total} validations; confidence "
                                f"{s.confidence:.2f}; recent trend "
                                f"{s.recent_trend}."),
                        kind="confirmation",
                        salience=0.85,
                        run_dir=None,
                        prior_id=pid,
                        citations=["prior_validations.jsonl"],
                    ))
        except Exception:
            pass

        # 3. Novelty: runs whose calculus.likely_shape is unusual vs the rest.
        if recent_runs:
            shape_counts: Dict[str, int] = {}
            for r in recent_runs:
                sh = (r.get("calculus") or {}).get("likely_shape")
                if sh:
                    shape_counts[sh] = shape_counts.get(sh, 0) + 1
            for r in recent_runs:
                sh = (r.get("calculus") or {}).get("likely_shape")
                if sh and shape_counts.get(sh, 0) == 1 and len(recent_runs) >= 3:
                    # Singleton shape among multiple recent runs → novel.
                    entries.append(DiaryEntry(
                        rank=0,
                        headline=(f"Novel pattern: {r.get('dataset_name')} "
                                   f"showed unique '{sh}' shape"),
                        detail=(f"This run produced a calculus signature "
                                f"distinct from all other recent runs in the "
                                f"24h window."),
                        kind="novelty",
                        salience=0.65,
                        run_dir=r.get("run_dir"),
                        prior_id=None,
                        citations=["learning_store.jsonl"],
                    ))

        # 4. Run summary line if nothing else fired.
        if not entries and recent_runs:
            entries.append(DiaryEntry(
                rank=0,
                headline=f"{len(recent_runs)} runs in the last {within_hours}h, no anomalies",
                detail=("All recent runs validated existing priors with no "
                        "contradictions and no novel signatures."),
                kind="confirmation",
                salience=0.30,
                run_dir=None, prior_id=None,
                citations=["learning_store.jsonl"],
            ))

        # Rank by salience (descending).
        entries.sort(key=lambda e: -e.salience)
        for i, e in enumerate(entries):
            e.rank = i + 1

        return AgentResult(
            agent_id=self.agent_id, task_id="", ts="", status="ok",
            summary=(f"diary: {len(entries)} entries from "
                     f"{len(recent_runs)} runs + "
                     f"{len(recent_valid)} validations in last {within_hours}h"),
            artifacts=[{"kind": "diary",
                        "entries": [e.to_dict() for e in entries[:25]]}],
            decisions=[{
                "decision": "ranked_by_salience",
                "why": ("contradictions=1.0 > promotions=0.85 > "
                        "weakenings=0.75 > novelty=0.65 > confirmations=0.40"),
                "confidence": 1.0,
                "alternatives_considered": ["chronological", "by-confidence"],
            }],
        )


def build_morning_diary(within_hours: int = 24) -> Dict[str, Any]:
    """Convenience: run the triage agent and return the diary directly."""
    agent = FindingTriageAgent()
    res = agent.run({"within_hours": within_hours})
    diary = next((a.get("entries") for a in (res.artifacts or [])
                  if a.get("kind") == "diary"), [])
    return {
        "ts": res.ts,
        "status": res.status,
        "summary": res.summary,
        "entries": diary,
    }

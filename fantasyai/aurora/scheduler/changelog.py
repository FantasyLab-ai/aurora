"""
Atom 4.2: Validation loop + draft changelog writer.

After each overnight pass writes runs to disk, this module:

  1. For every overnight run, compares its findings against the prior
     library and classifies each as: strengthened | confirmed |
     weakened | contradicted | candidate | promoted | deprecated.
  2. Writes a draft changelog entry to
     ~/.aurora/learning/changelog/draft/<YYYY-MM-DD>.json
  3. Persists candidate priors but does NOT auto-publish — the maintainer
     reviews via /learning/admin (Atom 4.3) before anything appears on
     the public /learning page.

The validation logic itself is deterministic (z-tests / sign tests /
threshold rules). No NN, no LLM. Glass-box throughout: every event
carries the run_id(s) that contributed.

Library on disk:
    ~/.aurora/priors/<domain>/<prior_id>.json

Per-prior schema:
    {
      "id": str,
      "domain": str,
      "display_name": str,
      "structural_form": str,
      "status": "candidate" | "canonical" | "deprecated",
      "support_n": int,
      "confidence": float,
      "validation_runs": [run_id, ...],
      "provenance": [{run_id, finding_index, status, delta, ts}, ...],
      "versions": [{ts, status, confidence, support_n}, ...],
      "deprecation_history": [{ts, reason, run_ids}, ...]
    }
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_PRIORS_ROOT = Path.home() / ".aurora" / "priors"
DEFAULT_CHANGELOG_ROOT = Path.home() / ".aurora" / "learning" / "changelog"
DRAFT_DIR_NAME = "draft"
PUBLISHED_DIR_NAME = "published"

# Promotion / deprecation thresholds.
PROMOTION_MIN_VALIDATIONS = 3
PROMOTION_MIN_CONSENSUS = 0.60
DEPRECATION_THRESHOLD = 3        # contradictions
DEPRECATION_WINDOW_DAYS = 14

# Trusted-source whitelist — only these contribute to the prior library
# in v1 (per the brief).
TRUSTED_SOURCES = {
    "noaa", "usgs", "world_bank", "openml", "fred", "bls", "sports",
}


# ---------------------------------------------------------------------------
# Time helper
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Prior library load / save
# ---------------------------------------------------------------------------

def _prior_path(prior_id: str, domain: str,
                root: Path = DEFAULT_PRIORS_ROOT) -> Path:
    return root / domain / f"{prior_id}.json"


def load_prior(prior_id: str, domain: str,
               root: Path = DEFAULT_PRIORS_ROOT) -> Optional[Dict[str, Any]]:
    p = _prior_path(prior_id, domain, root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_prior(prior: Dict[str, Any],
               root: Path = DEFAULT_PRIORS_ROOT) -> Path:
    p = _prior_path(prior["id"], prior["domain"], root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prior, indent=2, default=str), encoding="utf-8")
    return p


def list_priors(domain: Optional[str] = None,
                status: Optional[str] = None,
                root: Path = DEFAULT_PRIORS_ROOT) -> List[Dict[str, Any]]:
    if not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    domains = [domain] if domain else [d.name for d in root.iterdir()
                                          if d.is_dir()]
    for d in domains:
        ddir = root / d
        if not ddir.exists():
            continue
        for f in ddir.glob("*.json"):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                if status is None or p.get("status") == status:
                    out.append(p)
            except Exception:
                continue
    return out


def library_summary(root: Path = DEFAULT_PRIORS_ROOT) -> Dict[str, Any]:
    by_domain: Dict[str, Dict[str, int]] = {}
    total = {"canonical": 0, "candidate": 0, "deprecated": 0}
    for p in list_priors(root=root):
        d = p.get("domain", "unknown")
        s = p.get("status", "candidate")
        by_domain.setdefault(d, {"canonical": 0, "candidate": 0, "deprecated": 0})
        if s in by_domain[d]:
            by_domain[d][s] += 1
        if s in total:
            total[s] += 1
    return {"total": total, "by_domain": by_domain}


# ---------------------------------------------------------------------------
# Validation classification
# ---------------------------------------------------------------------------

@dataclass
class ValidationEvent:
    kind: str                       # strengthened | confirmed | weakened |
                                     # contradicted | candidate | promoted |
                                     # deprecated
    prior_id: str
    display_name: str
    domain: str
    delta: str
    rationale: str
    run_ids: List[str] = field(default_factory=list)
    support_n_before: Optional[int] = None
    support_n_after: Optional[int] = None
    confidence_before: Optional[float] = None
    confidence_after: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_form(s: str) -> str:
    """Normalise a structural-form expression for matching: strip whitespace,
    lowercase, collapse symbol variants. Cheap fingerprint — not a parser."""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\s+", "", s)
    s = s.replace("·", "*").replace("×", "*").replace("∝", "~")
    return s


def _match_finding_to_prior(finding: Dict[str, Any],
                              priors: List[Dict[str, Any]]
                              ) -> Optional[Dict[str, Any]]:
    """Return the prior whose structural_form best matches a finding's
    physics/law match. Findings without an equation match are unmatched.
    """
    title = (finding.get("title") or "").lower()
    method = (finding.get("method") or "").lower()
    if not (("matches known law" in title) or ("physical model fit" in title)
            or ("physics_prior" in method)):
        return None
    desc = (finding.get("description") or "").lower()
    title_full = title + " " + desc
    title_norm = _normalize_form(title_full)
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for pr in priors:
        f = _normalize_form(pr.get("structural_form", "")) or \
            _normalize_form(pr.get("display_name", ""))
        if not f:
            continue
        # Cheap substring match — robust enough for v1.
        if f in title_norm:
            score = len(f) / max(1, len(title_norm))
            if score > best_score:
                best, best_score = pr, score
    return best


def _classify_run(state: Dict[str, Any],
                   priors_root: Path = DEFAULT_PRIORS_ROOT
                   ) -> List[Dict[str, Any]]:
    """For a single run's state dict, walk findings and classify each
    against the existing prior library. Returns a list of per-finding
    {prior_id, kind, delta, rationale} dicts.

    Kind:
      strengthened — same prior, increased confidence.
      confirmed    — same prior, similar confidence (within ±0.05).
      weakened     — same prior, lower confidence.
      contradicted — finding directly contradicts a canonical prior.
      candidate    — no matching prior; new candidate.
    """
    findings = state.get("findings") or []
    sm = state.get("system_model") or {}
    domain = sm.get("template_id") or "unknown"
    priors = list_priors(domain=domain, root=priors_root)
    out: List[Dict[str, Any]] = []
    for fi in findings:
        if not isinstance(fi, dict):
            continue
        match = _match_finding_to_prior(fi, priors)
        f_conf = float(fi.get("confidence") or 0.0)
        if match:
            old_conf = float(match.get("confidence") or 0.0)
            delta = f_conf - old_conf
            if delta > 0.05:
                kind = "strengthened"
            elif delta < -0.20:
                kind = "weakened"
            elif delta < -0.40:
                kind = "contradicted"
            else:
                kind = "confirmed"
            out.append({
                "kind": kind,
                "prior_id": match["id"],
                "domain": match["domain"],
                "display_name": match.get("display_name", match["id"]),
                "delta": delta,
                "old_confidence": old_conf,
                "new_confidence": f_conf,
                "support_n": match.get("support_n", 0),
            })
        else:
            # Promote a candidate ONLY when the finding looks law-shaped.
            title = (fi.get("title") or "").lower()
            if ("matches known law" in title) or ("physical model fit" in title):
                # Use the title as a stable id-able fingerprint.
                pid = re.sub(r"[^a-z0-9_]", "_", title.split(":")[-1].strip())[:60]
                out.append({
                    "kind": "candidate",
                    "prior_id": pid,
                    "domain": domain,
                    "display_name": title.split(":")[-1].strip().title(),
                    "delta": 0.0,
                    "old_confidence": None,
                    "new_confidence": f_conf,
                    "support_n": 0,
                })
    return out


# ---------------------------------------------------------------------------
# Library mutation
# ---------------------------------------------------------------------------

def _record_validation(prior: Dict[str, Any], run_id: str,
                        kind: str, delta: float,
                        finding_index: int = -1) -> None:
    prior.setdefault("validation_runs", []).append(run_id)
    prior.setdefault("provenance", []).append({
        "run_id": run_id, "finding_index": finding_index,
        "status": kind, "delta": float(delta),
        "ts": _now_iso(),
    })
    prior.setdefault("versions", [])


def _bump_confidence(prior: Dict[str, Any],
                      kind: str, new_conf: float) -> None:
    """Apply a small confidence change based on the validation kind."""
    cur = float(prior.get("confidence") or 0.5)
    if kind == "strengthened":
        prior["confidence"] = min(0.99, cur + 0.04)
    elif kind == "confirmed":
        prior["confidence"] = min(0.99, cur + 0.02)
    elif kind == "weakened":
        prior["confidence"] = max(0.01, cur - 0.06)
    elif kind == "contradicted":
        prior["confidence"] = max(0.01, cur - 0.12)
    prior.setdefault("versions", []).append({
        "ts": _now_iso(),
        "status": prior.get("status"),
        "confidence": prior["confidence"],
        "support_n": prior.get("support_n", 0),
    })


def update_priors_from_run(state: Dict[str, Any],
                            run_id: str,
                            *,
                            priors_root: Path = DEFAULT_PRIORS_ROOT,
                            ) -> List[ValidationEvent]:
    """For one overnight run, mutate the prior library based on the run's
    findings, and return the validation events generated. The events are
    NOT yet a public changelog — the changelog writer aggregates events
    from a whole night's pass.
    """
    events: List[ValidationEvent] = []
    classifications = _classify_run(state, priors_root=priors_root)
    sm = state.get("system_model") or {}
    domain = sm.get("template_id") or "unknown"

    for cl in classifications:
        kind = cl["kind"]
        if kind in ("strengthened", "confirmed", "weakened", "contradicted"):
            prior = load_prior(cl["prior_id"], cl["domain"], priors_root)
            if prior is None:
                continue
            old_n = int(prior.get("support_n", 0))
            old_conf = float(prior.get("confidence", 0.5))
            _record_validation(prior, run_id, kind, cl["delta"])
            prior["support_n"] = old_n + 1
            _bump_confidence(prior, kind, cl["new_confidence"])
            save_prior(prior, priors_root)
            events.append(ValidationEvent(
                kind=kind,
                prior_id=prior["id"],
                domain=prior["domain"],
                display_name=prior.get("display_name", prior["id"]),
                delta=f"{cl['delta']:+.3f} confidence "
                       f"({old_conf:.2f} → {prior['confidence']:.2f})",
                rationale=f"Run {run_id} {kind} this prior.",
                run_ids=[run_id],
                support_n_before=old_n,
                support_n_after=prior["support_n"],
                confidence_before=old_conf,
                confidence_after=prior["confidence"],
            ))
        elif kind == "candidate":
            # Create OR update a candidate prior.
            existing = load_prior(cl["prior_id"], cl["domain"], priors_root)
            if existing:
                old_n = int(existing.get("support_n", 0))
                old_conf = float(existing.get("confidence", 0.5))
                _record_validation(existing, run_id, "candidate", 0.0)
                existing["support_n"] = old_n + 1
                _bump_confidence(existing, "strengthened",
                                  cl["new_confidence"])
                save_prior(existing, priors_root)
                events.append(ValidationEvent(
                    kind="strengthened",
                    prior_id=existing["id"],
                    domain=existing["domain"],
                    display_name=existing.get("display_name", existing["id"]),
                    delta=f"+1 supporting run "
                           f"({old_conf:.2f} → {existing['confidence']:.2f})",
                    rationale=f"Run {run_id} added to candidate's evidence.",
                    run_ids=[run_id],
                    support_n_before=old_n,
                    support_n_after=existing["support_n"],
                    confidence_before=old_conf,
                    confidence_after=existing["confidence"],
                ))
            else:
                new_prior = {
                    "id": cl["prior_id"],
                    "domain": cl["domain"],
                    "display_name": cl["display_name"],
                    "structural_form": cl["display_name"],
                    "status": "candidate",
                    "support_n": 1,
                    "confidence": float(cl["new_confidence"]),
                    "validation_runs": [run_id],
                    "provenance": [{
                        "run_id": run_id, "status": "candidate",
                        "delta": 0.0, "ts": _now_iso(),
                    }],
                    "versions": [{
                        "ts": _now_iso(), "status": "candidate",
                        "confidence": float(cl["new_confidence"]),
                        "support_n": 1,
                    }],
                    "deprecation_history": [],
                }
                save_prior(new_prior, priors_root)
                events.append(ValidationEvent(
                    kind="candidate",
                    prior_id=new_prior["id"],
                    domain=new_prior["domain"],
                    display_name=new_prior["display_name"],
                    delta="new candidate, 1 confirmation",
                    rationale=f"First sighting in run {run_id}.",
                    run_ids=[run_id],
                    support_n_after=1,
                    confidence_after=new_prior["confidence"],
                ))
    return events


# ---------------------------------------------------------------------------
# Promotion / deprecation gates
# ---------------------------------------------------------------------------

def promote_candidates_if_ready(
        priors_root: Path = DEFAULT_PRIORS_ROOT,
        min_validations: int = PROMOTION_MIN_VALIDATIONS,
        min_consensus: float = PROMOTION_MIN_CONSENSUS,
        ) -> List[ValidationEvent]:
    """Walk all candidate priors; if any has ≥ min_validations and
    ≥ min_consensus agreement, promote to canonical."""
    events: List[ValidationEvent] = []
    for pr in list_priors(status="candidate", root=priors_root):
        n = int(pr.get("support_n", 0))
        if n < min_validations:
            continue
        prov = pr.get("provenance") or []
        # Consensus: fraction of provenance entries that are confirming
        # (not contradicted / weakened).
        if not prov:
            continue
        confirming = sum(1 for p in prov
                          if p.get("status") in
                              ("candidate", "strengthened", "confirmed"))
        consensus = confirming / len(prov)
        if consensus < min_consensus:
            continue
        old_status = pr.get("status")
        pr["status"] = "canonical"
        pr.setdefault("versions", []).append({
            "ts": _now_iso(), "status": "canonical",
            "confidence": pr.get("confidence"),
            "support_n": n,
        })
        save_prior(pr, priors_root)
        events.append(ValidationEvent(
            kind="promoted",
            prior_id=pr["id"], domain=pr["domain"],
            display_name=pr.get("display_name", pr["id"]),
            delta=f"{old_status} → canonical (n={n}, consensus={consensus:.0%})",
            rationale=f"{n} validations with {confirming}/{len(prov)} confirming "
                       f"reached promotion threshold.",
            run_ids=pr.get("validation_runs", [])[-min_validations:],
            support_n_before=n, support_n_after=n,
        ))
    return events


def deprecate_if_contradicted(
        priors_root: Path = DEFAULT_PRIORS_ROOT,
        threshold: int = DEPRECATION_THRESHOLD,
        window_days: int = DEPRECATION_WINDOW_DAYS,
        ) -> List[ValidationEvent]:
    """Walk canonical priors; if a prior has ≥ threshold contradictions
    in the last ``window_days``, mark deprecated (kept, never deleted)."""
    events: List[ValidationEvent] = []
    cutoff = (datetime.now(timezone.utc).timestamp()
               - window_days * 86400)
    for pr in list_priors(status="canonical", root=priors_root):
        prov = pr.get("provenance") or []
        contradictions = []
        for p in prov:
            if p.get("status") != "contradicted":
                continue
            try:
                ts = datetime.fromisoformat(
                    str(p.get("ts", "")).replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if ts >= cutoff:
                contradictions.append(p)
        if len(contradictions) < threshold:
            continue
        old_status = pr["status"]
        pr["status"] = "deprecated"
        pr.setdefault("deprecation_history", []).append({
            "ts": _now_iso(),
            "reason": f"{len(contradictions)} contradictions in last "
                       f"{window_days} days",
            "run_ids": [c.get("run_id") for c in contradictions
                          if c.get("run_id")],
        })
        pr.setdefault("versions", []).append({
            "ts": _now_iso(), "status": "deprecated",
            "confidence": pr.get("confidence"),
            "support_n": pr.get("support_n", 0),
        })
        save_prior(pr, priors_root)
        events.append(ValidationEvent(
            kind="deprecated",
            prior_id=pr["id"], domain=pr["domain"],
            display_name=pr.get("display_name", pr["id"]),
            delta=f"{old_status} → deprecated "
                   f"({len(contradictions)} contradictions / {window_days}d)",
            rationale="Marked deprecated; not deleted.",
            run_ids=[c.get("run_id") for c in contradictions if c.get("run_id")],
            support_n_before=pr.get("support_n", 0),
            support_n_after=pr.get("support_n", 0),
        ))
    return events


# ---------------------------------------------------------------------------
# Draft changelog writer
# ---------------------------------------------------------------------------

def collect_synthesis_template_fires(
        state: Dict[str, Any],
        run_id: str,
        ) -> Optional[Dict[str, Any]]:
    """Extract a single-run record of which synthesis template fired.

    The synthesis engine writes ``state.synthesis_meta`` containing the
    chosen template id, score, specificity, active domain, and the
    finding-kind set that matched. This helper turns that into a tidy
    record the changelog can aggregate.

    Returns ``None`` when synthesis didn't run for this state — e.g.
    pre-synthesis-engine runs or runs that crashed before assembly.
    """
    sm = state.get("synthesis_meta") or {}
    if not sm or not sm.get("template_id"):
        return None
    return {
        "run_id": run_id,
        "template_id": sm.get("template_id"),
        "score": sm.get("score"),
        "specificity": sm.get("specificity"),
        "active_domain": sm.get("active_domain"),
        "kinds_seen": list(sm.get("kinds_seen") or []),
        "n_domain_notes": len(state.get("domain_context") or []),
        "n_equation_translations": len(state.get("equation_translations") or {}),
        "ts": _now_iso(),
    }


def aggregate_synthesis_fires(
        fires: List[Dict[str, Any]],
        ) -> Dict[str, Any]:
    """Build a draft-changelog-ready summary from a list of per-run
    synthesis-template-fire records. Counts per template_id, per domain,
    and per kind. Glass-box: every aggregate field is reproducible from
    the raw ``fires`` list."""
    if not fires:
        return {
            "n_runs_with_synthesis": 0,
            "by_template": {},
            "by_domain": {},
            "by_kind": {},
        }
    by_template: Dict[str, int] = {}
    by_domain: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    for f in fires:
        tid = f.get("template_id") or "unknown"
        by_template[tid] = by_template.get(tid, 0) + 1
        dom = f.get("active_domain") or "none"
        by_domain[dom] = by_domain.get(dom, 0) + 1
        for k in (f.get("kinds_seen") or []):
            by_kind[k] = by_kind.get(k, 0) + 1
    return {
        "n_runs_with_synthesis": len(fires),
        "by_template": by_template,
        "by_domain": by_domain,
        "by_kind": by_kind,
    }


def write_draft_changelog(events: List[ValidationEvent],
                            *,
                            sources_run: Optional[List[Dict[str, Any]]] = None,
                            priors_root: Path = DEFAULT_PRIORS_ROOT,
                            changelog_root: Path = DEFAULT_CHANGELOG_ROOT,
                            date: Optional[str] = None,
                            synthesis_fires: Optional[List[Dict[str, Any]]] = None,
                            ) -> Path:
    """Atom 4.2 + 4.3: write a draft changelog. Goes to draft/<date>.json.
    Maintainer review (Atom 4.3) moves entries to published/<date>.json.

    ``synthesis_fires`` is the per-run telemetry from
    :func:`collect_synthesis_template_fires`. When present, the draft
    changelog includes both the raw list and an aggregate, so the
    overnight loop can see which synthesis templates are firing on
    which domains and adjust priors / templates accordingly.
    """
    date = date or _today_iso()
    draft_dir = Path(changelog_root) / DRAFT_DIR_NAME
    draft_dir.mkdir(parents=True, exist_ok=True)
    summary = library_summary(priors_root)
    payload = {
        "date": date,
        "started_at": _now_iso(),
        "completed_at": _now_iso(),
        "review_required": True,
        "published": False,
        "sources": sources_run or [],
        "priors_summary": {
            "canonical_count_after": summary["total"]["canonical"],
            "candidate_count_after": summary["total"]["candidate"],
            "deprecated_count_after": summary["total"]["deprecated"],
            "by_domain_after": summary["by_domain"],
        },
        "events": [e.to_dict() for e in events],
        "synthesis_template_fires": synthesis_fires or [],
        "synthesis_template_summary": aggregate_synthesis_fires(
            synthesis_fires or []),
    }
    out_path = draft_dir / f"{date}.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str),
                         encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Public entry: run the validation loop on a list of overnight run results
# ---------------------------------------------------------------------------

def run_validation_loop(run_results: List[Dict[str, Any]],
                         *,
                         priors_root: Path = DEFAULT_PRIORS_ROOT,
                         changelog_root: Path = DEFAULT_CHANGELOG_ROOT,
                         build_state_fn=None,
                         is_trusted_source_fn=None) -> Dict[str, Any]:
    """End-to-end validation loop. ``run_results`` is the list returned by
    ``run_overnight_pass``. For each successful trusted-source run,
    classify findings against priors, mutate the library, then write a
    draft changelog. Returns a summary dict.

    Untrusted sources (user uploads etc.) are skipped — per the brief,
    only trusted public sources contribute to the prior library in v1.
    """
    if build_state_fn is None:
        from fantasyai.aurora.state_builder import build_state as build_state_fn  # type: ignore
    if is_trusted_source_fn is None:
        is_trusted_source_fn = lambda src: src in TRUSTED_SOURCES

    all_events: List[ValidationEvent] = []
    sources_summary: List[Dict[str, Any]] = []
    # Per-run synthesis telemetry. We collect for every successful state
    # build (trusted or not) — synthesis voice doesn't depend on the
    # trusted-source whitelist. This lets the learning loop see which
    # templates fire most often, on which domains, etc.
    synthesis_fires: List[Dict[str, Any]] = []
    for r in run_results:
        if not r.get("ok"):
            sources_summary.append({**r, "validated": False,
                                      "skip_reason": "run_failed"})
            continue
        if not is_trusted_source_fn(r.get("source")):
            sources_summary.append({**r, "validated": False,
                                      "skip_reason": "untrusted_source"})
            continue
        rd = r.get("run_dir")
        if not rd or not Path(rd).exists():
            sources_summary.append({**r, "validated": False,
                                      "skip_reason": "run_dir_missing"})
            continue
        try:
            state = build_state_fn(Path(rd))
        except Exception as e:
            sources_summary.append({**r, "validated": False,
                                      "skip_reason": f"state_build_failed: {e}"})
            continue
        run_id = r.get("run_id") or Path(rd).name
        events = update_priors_from_run(state, run_id,
                                          priors_root=priors_root)
        all_events.extend(events)
        # Capture synthesis fire (best-effort — never blocks validation).
        try:
            fire = collect_synthesis_template_fires(state, run_id)
            if fire:
                synthesis_fires.append(fire)
        except Exception:
            pass
        sources_summary.append({**r, "validated": True,
                                  "n_events": len(events)})

    # After per-run mutations, run the global gates.
    all_events.extend(promote_candidates_if_ready(priors_root=priors_root))
    all_events.extend(deprecate_if_contradicted(priors_root=priors_root))

    draft_path = write_draft_changelog(
        all_events, sources_run=sources_summary,
        priors_root=priors_root, changelog_root=changelog_root,
        synthesis_fires=synthesis_fires,
    )
    return {
        "ok": True,
        "draft_changelog": str(draft_path),
        "n_events": len(all_events),
        "events_by_kind": _count_by(all_events, "kind"),
        "sources_summary": sources_summary,
        "synthesis_template_fires": synthesis_fires,
        "synthesis_template_summary": aggregate_synthesis_fires(synthesis_fires),
    }


def _count_by(events: List[ValidationEvent], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for e in events:
        v = getattr(e, key, "?")
        out[v] = out.get(v, 0) + 1
    return out

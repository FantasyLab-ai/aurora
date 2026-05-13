"""
System Model instantiator.

Given a finished Aurora run (state dict from ``state_builder.build_state``)
this module:

  1. Identifies the domain by scoring against every loaded template
     (or honours an explicit user-selected template_id)
  2. Instantiates the topology: maps real columns into the template's entity
     slots; entities without a matching column are flagged ``presence="missing"``
  3. Populates relationships from the run's outputs:
        - causal what-if edges → kind=causal
        - lagged correlations from motifs → kind=lagged
        - physics matches → kind=physical (with prior_id)
     Anything the template expected but the data didn't confirm is recorded
     as ``source_origin="template_expected"`` with confidence=None.
     Anything the data shows that the template didn't expect is recorded
     as ``source_origin="discovered"``.
     Anything the data contradicts is ``source_origin="deviation"``.
  4. Fills the state block from the run's regimes / patterns / calculus / overview
  5. Pulls the template's action_library, constraints, vocabulary verbatim
     (we don't fabricate; templates own these).
  6. Computes an overall confidence (slot-fill rate × prior matches × constraint
     satisfaction).

Output: ``SystemModel`` ready for ``system_model_to_json`` / persistence.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .schema import (
    SystemModel, Topology, StateBlock, Constraint,
    CharacteristicPattern, Vocabulary, TemplateAction, ProvenanceBlock,
    Entity, Relationship,
)
from .templates_loader import (
    DomainTemplate, list_templates, get_template, best_template_for,
)


# ---------------------------------------------------------------------------
# Domain identification (wraps templates_loader.best_template_for + run stats)
# ---------------------------------------------------------------------------

def identify_domain(state: Dict[str, Any],
                    explicit_template_id: Optional[str] = None
                    ) -> Tuple[Optional[DomainTemplate], List[Dict[str, Any]]]:
    """Decide which template to use for this run.

    Args:
        state: assembled state dict from ``state_builder.build_state``.
        explicit_template_id: when set, returns that template (still scores
            others so we can surface the second-best for transparency).

    Returns:
        (chosen_template, ranked_scores) where ranked_scores is a list of
        ``{template_id, score, rationale}`` dicts.
    """
    columns = [
        c.get("name") for c in (state.get("structure") or {}).get("columns") or []
        if isinstance(c, dict)
    ]
    cadence = (state.get("structure") or {}).get("cadence")
    # Approx numeric ranges for each numeric column (cheap sniff).
    numeric_ranges: Dict[str, Tuple[float, float]] = {}
    # If structure carries fill_pct only, we can still score on names alone.
    best, scores = best_template_for(columns, cadence=cadence,
                                      numeric_ranges=numeric_ranges or None)
    ranked = [{"template_id": s.template_id, "score": s.score,
                "rationale": s.rationale} for s in scores]
    if explicit_template_id:
        explicit = get_template(explicit_template_id)
        if explicit is not None:
            return explicit, ranked
    return best, ranked


# ---------------------------------------------------------------------------
# Instantiate
# ---------------------------------------------------------------------------

# v1.2.x: thresholds for falling back from a matched template to the
# generic numeric-columns model. Triggered when BOTH conditions hold:
#   * Template's matching score is below LOW_CONFIDENCE_FALLBACK_THRESHOLD
#   * Fewer than LOW_MATCH_RATIO_FALLBACK_THRESHOLD of the template's
#     entity slots actually mapped to real columns in the dataset.
# A template with high confidence (>= 0.40) is trusted regardless of
# slot mapping. A template with strong slot-coverage (>= 30%) is also
# trusted even at lower confidence. Only when BOTH signals say "this
# template doesn't fit" do we fall back — which is exactly the case
# the user reported (enviro at 22% on patient_cohort = wrong + no
# slots map; the user's actual data should be the nodes).
LOW_CONFIDENCE_FALLBACK_THRESHOLD = 0.40
LOW_MATCH_RATIO_FALLBACK_THRESHOLD = 0.30


def _template_slot_match_ratio(template, cols_lower: Dict[str, str]) -> float:
    """Quick pre-check: how many of the template's entity slots map to
    a real column? Returns a ratio in [0.0, 1.0]. Used by the gate
    below; doesn't mutate any state."""
    slots = template.topology_skeleton.get("entities") or []
    if not slots:
        return 0.0
    matched = 0
    for slot in slots:
        eid = slot.get("id", "")
        label = slot.get("label", eid)
        if _match_entity_to_column(eid, label, cols_lower):
            matched += 1
    return matched / max(1, len(slots))


def instantiate_system_model(state: Dict[str, Any],
                              explicit_template_id: Optional[str] = None
                              ) -> SystemModel:
    """Build a ``SystemModel`` from a run's state dict + a matched template.

    Phase-A1 (v1.2.x): when no explicit template is requested AND the
    auto-matched template is weak in BOTH dimensions (confidence and
    slot-mapping ratio), use the generic numeric-columns fallback
    instead. The template's score is still surfaced via ``ranked`` so
    the frontend can show "tried <template> at N% — used generic
    instead" for transparency. Explicit user selections (e.g. domain
    pill click) always win — the threshold only gates the implicit
    auto-pick.

    Why BOTH dimensions: factory_bearing matches ``industrial`` at
    0.318 confidence (under 0.40), but the slot-mapping is only 1/9
    (vibration only) so we WANT the fallback. patient_cohort matches
    ``enviro`` at 0.22 with 0/9 slot-mapping — also fallback. A
    hypothetical dataset that matches ``industrial`` at 0.35 with
    5/9 slots mapped should KEEP the template — its slots are
    actually filling in.
    """
    template, ranked = identify_domain(state, explicit_template_id)
    if template is None:
        # Empty fallback model — keeps shape stable for downstream consumers.
        return _empty_fallback_model(state, ranked)
    # Confidence + slot-mapping gate. Both must indicate weakness.
    if not explicit_template_id and ranked:
        top_score = float((ranked[0] or {}).get("score") or 0.0)
        if top_score < LOW_CONFIDENCE_FALLBACK_THRESHOLD:
            cols_in_run_pre = [
                c.get("name") for c in (state.get("structure") or {}).get("columns") or []
                if isinstance(c, dict)
            ]
            cols_lower_pre = {str(c).lower(): c for c in cols_in_run_pre if c}
            match_ratio = _template_slot_match_ratio(template, cols_lower_pre)
            if match_ratio < LOW_MATCH_RATIO_FALLBACK_THRESHOLD:
                # Weak in both — use the generic fallback.
                return _empty_fallback_model(state, ranked)

    # ---- topology ----
    cols_in_run = [
        c.get("name") for c in (state.get("structure") or {}).get("columns") or []
        if isinstance(c, dict)
    ]
    cols_lower = {str(c).lower(): c for c in cols_in_run if c}

    entities: List[Entity] = []
    entity_id_to_data_col: Dict[str, Optional[str]] = {}
    for slot in (template.topology_skeleton.get("entities") or []):
        eid = slot.get("id", "")
        label = slot.get("label", eid)
        role = slot.get("role", "state")
        unit = slot.get("unit")
        # Try to map this entity slot to a real column by keyword + lower-case name.
        match_col = _match_entity_to_column(eid, label, cols_lower)
        presence = "present" if match_col else "missing"
        entities.append(Entity(
            id=eid, label=label, role=role, unit=unit,
            data_column=match_col, presence=presence,
        ))
        entity_id_to_data_col[eid] = match_col

    relationships: List[Relationship] = []
    confirmed_ids: List[str] = []
    discovered_ids: List[str] = []
    deviation_ids: List[str] = []
    expected_rels = template.topology_skeleton.get("expected_relationships") or []

    # 1. Walk template-expected relationships; tag each by whether the data
    # confirms / leaves silent / contradicts.
    physics = state.get("physics") or {}
    causal = state.get("causal") or {}
    motifs = state.get("motifs") or []
    prior_matches = physics.get("prior_matches") or []
    top_prior = prior_matches[0] if prior_matches else None
    for rel in expected_rels:
        rid = rel.get("id", f"rel-{len(relationships)}")
        src, tgt = rel.get("source"), rel.get("target")
        kind = rel.get("kind", "correlational")
        # Confirmation logic: if a prior match maps to this kind+pair, mark confirmed.
        confidence: Optional[float] = None
        source_origin = "template_expected"
        prior_id = None
        rationale = rel.get("rationale", "")
        if top_prior and kind in ("physical", "causal"):
            confidence = float(top_prior.get("confidence") or 0.0)
            prior_id = top_prior.get("prior_id")
            source_origin = "prior" if confidence and confidence > 0.5 else "template_expected"
            rationale = (rationale or
                         f"matches {top_prior.get('display_name')} at "
                         f"{int((confidence or 0)*100)}% confidence")
        # Causal what-if edge (only one in current Aurora) → if treatment/target match.
        if (causal.get("available") and causal.get("items")):
            try:
                cw = causal["items"][0]
                t_col = (causal.get("treatment_col") if "treatment_col" in causal
                         else (cw.get("if_clause") or ""))
                if (entity_id_to_data_col.get(src) and
                        str(t_col or "").lower().find(
                            entity_id_to_data_col[src].lower()) >= 0):
                    confidence = max(confidence or 0.0, 0.6)
                    source_origin = "data"
                    rationale = rationale or "supported by causal what-if estimate"
            except Exception:
                pass
        confirmed = confidence is not None and confidence >= 0.5
        relationships.append(Relationship(
            id=rid, source=src, target=tgt, kind=kind,
            strength=None, confidence=confidence,
            source_origin=source_origin,
            prior_id=prior_id, rationale=rationale,
        ))
        if confirmed:
            confirmed_ids.append(rid)

    # 2. Walk motifs for *discovered* lagged-correlation edges between entities.
    for m in motifs:
        details = m.get("details") or {}
        if details.get("method") == "matrix_profile":
            continue   # matrix-profile motifs are within a single col
        if m.get("kind") == "high_correlation":
            a = (details.get("col_a") or details.get("a") or "").lower()
            b = (details.get("col_b") or details.get("b") or "").lower()
            src_id = _entity_for_col(entities, a)
            tgt_id = _entity_for_col(entities, b)
            if src_id and tgt_id and src_id != tgt_id:
                rid = f"discovered_corr_{src_id}_{tgt_id}"
                if not any(r.id == rid for r in relationships):
                    relationships.append(Relationship(
                        id=rid, source=src_id, target=tgt_id,
                        kind="correlational",
                        strength=float(details.get("corr") or 0.0),
                        confidence=min(1.0, abs(float(details.get("corr") or 0.0))),
                        source_origin="discovered",
                        rationale=("data shows |ρ| = "
                                    f"{abs(float(details.get('corr') or 0.0)):.2f} "
                                    "(no template-expected edge here)"),
                    ))
                    discovered_ids.append(rid)

    # 3. Deviations — when a hard physics check failed.
    checks = physics.get("checks") or []
    for c in checks:
        if (isinstance(c, dict) and c.get("name") and
                isinstance(c.get("value"), (int, float)) and
                c["value"] is not None and c["value"] < 0.5):
            rid = f"deviation_check_{c['name']}"
            if not any(r.id == rid for r in relationships):
                relationships.append(Relationship(
                    id=rid, source=entities[0].id if entities else "",
                    target=entities[0].id if entities else "",
                    kind="correlational", source_origin="deviation",
                    confidence=float(c["value"]),
                    rationale=(f"check {c['name']} = {c['value']:.2f} below "
                                f"the soft-pass threshold of 0.5"),
                ))
                deviation_ids.append(rid)

    # ---- state ----
    regimes = state.get("regimes") or []
    overview = state.get("overview") or {}
    active_regime = (regimes[-1].get("label") if regimes else None)
    state_block = StateBlock(
        active_regime=active_regime,
        regime_confidence=(0.7 if active_regime else None),
        active_patterns=_active_patterns_from_state(state, template),
        key_readings=_key_readings(state, entities),
        stability_score=overview.get("stability"),
        drift_indicators=_drift_indicators(state),
    )

    # ---- constraints ----
    constraints: List[Constraint] = []
    for c in (template.constraints or []):
        constraints.append(Constraint(
            id=c.get("id", f"constraint-{len(constraints)}"),
            description=c.get("description", ""),
            kind=c.get("kind", "bound"),
            hardness=c.get("hardness", "soft"),
            on_entity=c.get("on_entity"),
            expression=c.get("expression"),
            currently_satisfied=None,    # filled later by validator
            deviation_metric=None,
        ))

    # ---- characteristic patterns ----
    patterns = [CharacteristicPattern(
        id=p.get("id", f"pattern-{i}"),
        name=p.get("name", ""),
        signature=p.get("signature", ""),
        typical_duration_seconds=p.get("typical_duration_seconds"),
        typical_successors=p.get("typical_successors") or [],
        detection_rule=p.get("detection_rule", ""),
    ) for i, p in enumerate(template.characteristic_patterns or [])]

    # ---- vocabulary ----
    vocab = Vocabulary(
        anomaly_term=template.vocabulary.get("anomaly_term", "anomaly"),
        regime_term=template.vocabulary.get("regime_term", "regime"),
        pattern_term=template.vocabulary.get("pattern_term", "pattern"),
        action_term=template.vocabulary.get("action_term", "action"),
        breach_term=template.vocabulary.get("breach_term", "breach"),
        advisory_term=template.vocabulary.get("advisory_term", "advisory"),
        extra=template.vocabulary.get("extra") or {},
    )

    # ---- action library ----
    actions = [TemplateAction(
        id=a.get("id", f"action-{i}"),
        label=a.get("label", ""),
        description=a.get("description", ""),
        triggered_when=a.get("triggered_when") or [],
        predicted_outcome_summary=a.get("predicted_outcome_summary", ""),
        base_rate_success=float(a.get("base_rate_success") or 0.5),
        confidence_band_required=a.get("confidence_band_required", "medium"),
        falsification=a.get("falsification") or [],
        assumptions=a.get("assumptions") or [],
        relevant_states=a.get("relevant_states") or [],
    ) for i, a in enumerate(template.action_library or [])]

    # ---- provenance ----
    prov = ProvenanceBlock(
        template_id=template.id,
        template_version=template.version,
        instantiated_from=state.get("run_dir"),
        instantiated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        data_columns_used=[c for c in cols_in_run if c],
        priors_used=[m.get("prior_id") for m in prior_matches if m.get("prior_id")],
        confirmed_relationships=confirmed_ids,
        discovered_relationships=discovered_ids,
        deviation_relationships=deviation_ids,
        open_assumptions=_open_assumptions(template, entities),
    )

    # ---- overall confidence ----
    n_total = max(1, len(entities))
    n_present = sum(1 for e in entities if e.presence == "present")
    fill_rate = n_present / n_total
    rel_confidences = [r.confidence for r in relationships
                       if isinstance(r.confidence, (int, float))]
    avg_rel_conf = sum(rel_confidences) / max(1, len(rel_confidences)) if rel_confidences else 0.0
    confidence = 0.5 * fill_rate + 0.5 * avg_rel_conf
    confidence = float(min(1.0, max(0.0, confidence)))

    model = SystemModel(
        topology=Topology(entities=entities, relationships=relationships),
        state=state_block,
        constraints=constraints,
        characteristic_patterns=patterns,
        vocabulary=vocab,
        action_library=actions,
        provenance=prov,
        confidence=confidence,
    )

    # Stash the ranked scores on provenance.open_assumptions for transparency.
    if ranked:
        top3 = ranked[:3]
        prov.open_assumptions.append(
            "domain identified by template scores: " +
            "; ".join(f"{r['template_id']}={r['score']:.2f}" for r in top3)
        )
    return model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _match_entity_to_column(entity_id: str, label: str,
                             cols_lower: Dict[str, str]) -> Optional[str]:
    """Cheap mapper: try the entity_id, the label, and word-tokens from the label."""
    candidates = [entity_id.lower(), str(label).lower()]
    if " " in str(label):
        candidates.extend(str(label).lower().split())
    if "_" in entity_id:
        candidates.extend(entity_id.lower().split("_"))
    for c in candidates:
        if c in cols_lower:
            return cols_lower[c]
        # partial substring match.
        for low, real in cols_lower.items():
            if c and (c in low or low in c) and len(c) > 2:
                return real
    return None


def _entity_for_col(entities: List[Entity], col: str) -> Optional[str]:
    col = col.lower()
    for e in entities:
        if e.data_column and e.data_column.lower() == col:
            return e.id
    return None


def _active_patterns_from_state(state: Dict[str, Any],
                                 template: DomainTemplate) -> List[Dict[str, Any]]:
    """Heuristic: if calculus.likely_shape suggests a pattern, surface it."""
    out: List[Dict[str, Any]] = []
    physics = state.get("physics") or {}
    calc = physics.get("calculus") or {}
    shape = (calc.get("likely_shape") or "").lower()
    # Map calculus shapes to template pattern ids by keyword match.
    for p in template.characteristic_patterns:
        sig = (p.get("signature") or "").lower()
        name = (p.get("name") or "").lower()
        if not shape:
            continue
        score = 0.0
        if shape and shape.split()[0] in sig:
            score += 0.4
        if shape and shape.split()[0] in name:
            score += 0.4
        if score > 0:
            out.append({
                "pattern_id": p.get("id"),
                "name": p.get("name"),
                "confidence": min(1.0, 0.4 + score),
                "rationale": f"calculus shape '{shape}' overlaps signature",
            })
    # Also: oscillatory shape → diurnal-style patterns
    if "oscill" in shape:
        for p in template.characteristic_patterns:
            if "diurnal" in (p.get("name") or "").lower():
                out.append({
                    "pattern_id": p.get("id"),
                    "name": p.get("name"),
                    "confidence": 0.6,
                    "rationale": "calculus signature is oscillatory",
                })
                break
    return out[:3]


def _key_readings(state: Dict[str, Any],
                  entities: List[Entity]) -> Dict[str, Any]:
    overview = state.get("overview") or {}
    out: Dict[str, Any] = {}
    if overview.get("anomalies_count"):
        out["anomalies_count"] = {"value": overview["anomalies_count"],
                                  "interpretation": "number of flagged anomalies"}
    if overview.get("regimes_count"):
        out["regimes_count"] = {"value": overview["regimes_count"],
                                "interpretation": "regime segments identified"}
    return out


def _drift_indicators(state: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    physics = state.get("physics") or {}
    calc = physics.get("calculus") or {}
    if calc.get("monotonic"):
        out.append("target series is monotonic")
    if calc.get("dy_dt_sign_changes") and calc["dy_dt_sign_changes"] > 0:
        out.append(f"derivative flips sign {calc['dy_dt_sign_changes']}×")
    return out


def _open_assumptions(template: DomainTemplate,
                      entities: List[Entity]) -> List[str]:
    out: List[str] = []
    missing = [e.id for e in entities if e.presence == "missing"]
    if missing:
        out.append(f"missing template slots: {', '.join(missing[:6])}")
    return out


def _empty_fallback_model(state: Dict[str, Any],
                           ranked: List[Dict[str, Any]]) -> SystemModel:
    """When no template matches, emit a *generic* fallback model:
    every numeric column → entity, plus discovered correlation edges from
    motifs / causal output. The user still sees their data in graph form.

    Honest signalling: ``template_id="(none)"``, confidence reflects only
    the strength of the discovered relationships (no template prior).
    """
    cols = [
        c for c in (state.get("structure") or {}).get("columns") or []
        if isinstance(c, dict)
    ]
    # Pick numeric columns; skip pure time/categorical.
    num_cols = [c for c in cols
                if c.get("kind") not in ("time", "cat")
                and c.get("name")]
    entities: List[Entity] = []
    for c in num_cols[:14]:
        nm = c.get("name") or ""
        # Heuristic role assignment when we have no domain prior:
        # the target column (from physics layer) is "response", a single
        # time-like column is "forcing", everything else is "state".
        role = "state"
        target_col = ((state.get("physics") or {}).get("discovered_law") or {}).get(
            "target_col") or (state.get("forecast") or {}).get("target")
        if target_col and nm == target_col:
            role = "response"
        entities.append(Entity(
            id=_safe_eid(nm),
            label=nm,
            role=role,
            data_column=nm,
            unit=c.get("unit"),
            dimension=c.get("dimension"),
            presence="present",
        ))
    relationships: List[Relationship] = []
    # Pull in correlation edges from motifs.
    for m in state.get("motifs") or []:
        details = (m or {}).get("details") or {}
        if (m or {}).get("kind") == "high_correlation":
            a = details.get("col_a") or details.get("a")
            b = details.get("col_b") or details.get("b")
            if not a or not b:
                continue
            sa, sb = _safe_eid(a), _safe_eid(b)
            ent_ids = {e.id for e in entities}
            if sa in ent_ids and sb in ent_ids and sa != sb:
                rid = f"discovered_corr_{sa}_{sb}"
                if not any(r.id == rid for r in relationships):
                    corr = float(details.get("corr") or 0.0)
                    relationships.append(Relationship(
                        id=rid, source=sa, target=sb,
                        kind="correlational",
                        strength=corr,
                        confidence=min(1.0, abs(corr)),
                        source_origin="discovered",
                        rationale=f"|ρ| = {abs(corr):.2f} from motif scan",
                    ))
    # Causal what-if edge — only one in current Aurora.
    causal = state.get("causal") or {}
    if causal.get("available") and causal.get("items"):
        cw = causal
        treatment = cw.get("treatment_col")
        target = cw.get("target_col")
        if treatment and target:
            sa, sb = _safe_eid(treatment), _safe_eid(target)
            ent_ids = {e.id for e in entities}
            if sa in ent_ids and sb in ent_ids:
                rid = f"discovered_causal_{sa}_{sb}"
                if not any(r.id == rid for r in relationships):
                    relationships.append(Relationship(
                        id=rid, source=sa, target=sb,
                        kind="causal",
                        confidence=0.6,
                        source_origin="discovered",
                        rationale="causal what-if estimate from this run",
                    ))

    # Vocabulary fallback — generic but honest.
    vocab = Vocabulary(
        anomaly_term="anomaly",
        regime_term="regime",
        pattern_term="pattern",
        action_term="action",
        breach_term="threshold breach",
        advisory_term="alert",
        extra={"high_state": "high", "low_state": "low",
                "transition": "shift", "growth_term": "growth", "decay_term": "decay"},
    )

    prov = ProvenanceBlock(
        template_id="(none)",
        template_version="(none)",
        instantiated_from=state.get("run_dir"),
        instantiated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        data_columns_used=[e.data_column for e in entities if e.data_column],
        discovered_relationships=[r.id for r in relationships
                                   if r.source_origin == "discovered"],
    )
    prov.open_assumptions.append(
        "no domain template matched the dataset's columns; rendering as a "
        "generic numeric-column graph. Add a domain template under "
        "system_model/templates/<name>/v1.json to enable richer behaviour."
    )
    if ranked:
        top3 = ranked[:3]
        prov.open_assumptions.append(
            "best template scores: " +
            "; ".join(f"{r['template_id']}={r['score']:.2f}" for r in top3)
        )
    # Confidence: only from discovered relationships (no template prior).
    if relationships:
        avg = sum(r.confidence or 0.0 for r in relationships) / len(relationships)
        confidence = float(0.3 * avg)    # capped low because we have no template
    else:
        confidence = 0.0

    return SystemModel(
        topology=Topology(entities=entities, relationships=relationships),
        state=StateBlock(),
        constraints=[],
        characteristic_patterns=[],
        vocabulary=vocab,
        action_library=[],
        provenance=prov,
        confidence=confidence,
    )


def _safe_eid(name: str) -> str:
    """Normalise a column name to a stable entity id: lowercase, snake-case."""
    import re
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name or "")).strip("_").lower()
    return s or "col"

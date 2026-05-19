"""
Bridge between streaming findings and the Decision Contracts engine.

Stream 1.4 Phase 2 deliverable: when the streaming runner emits a
genuinely-new finding, evaluate every loaded Decision Contract against
a synthetic single-finding bundle and fire those whose trigger
predicates are satisfied.

The synthetic bundle mirrors the shape that ``fire_contract`` expects
in the batch flow — same field paths, same severities — so existing
contracts work unchanged.

Design notes:
  * Contracts have built-in rate limiting; the streaming flow doesn't
    add its own. The engine's per-contract rate limit is enough.
  * Failures are swallowed and logged. A contract crash NEVER takes
    down the streaming runner.
  * The bridge is opt-in. Callers register it via
    ``IncrementalRunner.attach_contracts_bridge``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)


def _build_streaming_bundle(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap a single finding in the minimal bundle shape contracts
    expect. We populate the findings list with just this one finding
    plus a coarse anomaly count derived from severity, so
    severity-counting triggers (e.g. ``count_crit > 0``) still work."""
    sev = str(finding.get("severity") or "info").lower()
    return {
        "findings": [finding],
        # The contracts engine supports `count_<sev>` field paths that
        # resolve to "how many findings of this severity are in the
        # bundle." With one finding in the synthetic bundle, those
        # paths return 1 if the severity matches and 0 otherwise.
        "_streaming": True,
        "_source": "stream_bridge",
        "anomalies": [] if sev not in ("crit", "warn") else [finding],
    }


def evaluate_finding_against_contracts(
    finding: Dict[str, Any],
    *,
    contracts: Optional[List[Any]] = None,
    contracts_dir: Optional[Any] = None,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Evaluate every loaded contract against the synthetic bundle for
    this finding. Fire those that match.

    Returns a list of FiringRecord-as-dict for the contracts that fired.
    """
    try:
        from fantasyai.aurora.decision_contracts.engine import (
            load_contracts, fire_contract, evaluate_contract,
        )
    except Exception as e:
        LOG.warning(f"decision_contracts unavailable: {e}")
        return []

    try:
        contracts_to_eval = contracts if contracts is not None else load_contracts(contracts_dir)
    except Exception as e:
        LOG.warning(f"loading contracts failed: {e}")
        return []

    if not contracts_to_eval:
        return []

    bundle = _build_streaming_bundle(finding)
    fired: List[Dict[str, Any]] = []
    for c in contracts_to_eval:
        try:
            satisfied, _ = evaluate_contract(c, bundle)
            if not satisfied:
                continue
            rec = fire_contract(c, bundle, dry_run=dry_run)
            fired.append({
                "contract_id": getattr(c, "id", None),
                "satisfied": True,
                "actions_attempted": getattr(rec, "actions_attempted", 0),
                "actions_succeeded": getattr(rec, "actions_succeeded", 0),
                "fired_at": getattr(rec, "fired_at", None),
                "errors":   list(getattr(rec, "errors", []) or []),
            })
        except Exception as ce:
            LOG.exception(
                f"contract evaluation failed for contract "
                f"{getattr(c, 'id', '?')}: {ce}"
            )
    return fired


def make_bridge(
    *,
    contracts_dir: Optional[Any] = None,
    dry_run: bool = False,
):
    """Factory: return a callable suitable for
    ``IncrementalRunner.attach_contracts_bridge``.

    The returned callable takes a single finding dict and evaluates
    every loaded contract. Returns nothing — side effects only.
    """
    def _bridge(finding: Dict[str, Any]) -> None:
        evaluate_finding_against_contracts(
            finding,
            contracts_dir=contracts_dir,
            dry_run=dry_run,
        )
    return _bridge

"""
SSE adapter — placeholder module so app.py can `from .adapters import sse_adapter`.

The actual broadcast happens in app.py (`_broadcast_sse`), which holds
the list of subscriber queues directly. This module exists so the
adapter surface is uniform (every adapter is a sibling module) and
so future SSE-specific helpers can live here.
"""
from __future__ import annotations

from typing import Any, Dict


def envelope_to_card(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Project a fire envelope to the minimum the overlay needs.

    The relay broadcasts the FULL envelope by default; this helper
    exists so future card-only consumers (e.g. a thinner mobile
    overlay) can subscribe to a stripped projection.
    """
    return {
        "severity":      envelope.get("severity"),
        "contract_name": envelope.get("contract_name"),
        "method":        envelope.get("method"),
        "row":           envelope.get("row"),
        "z_score":       envelope.get("z_score"),
        "fabricated":    envelope.get("fabricated"),
        "trigger_field": envelope.get("trigger_field"),
        "trigger_value": envelope.get("trigger_value"),
    }

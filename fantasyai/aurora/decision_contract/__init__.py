"""
Decision contract — Phase B of the "Human-Readable Output" brief.

A *decision contract* captures what a user is trying to decide BEFORE the
narrative engine renders findings. It encodes:

  - objective       : the goal in one phrase ("avoid breach", "discover law")
  - constraints     : hard limits ("budget < $X", "no irreversible actions")
  - available_actions : what the user can actually do
  - cost_asymmetry  : which kind of error hurts more (false positive vs false negative)

Public API:

    from fantasyai.aurora.decision_contract import (
        DecisionContract, ContractClass,
        list_default_contracts, get_default_contract,
        suggest_contracts_for_template,
    )

The contract drives:
  - which findings are surfaced (filter)
  - which language frames them (template)
  - whether suppression rules fire (confidence × cost-asymmetry matrix)
"""
from __future__ import annotations

from .schema import (
    DecisionContract,
    ContractClass,
    CostAsymmetry,
    ConfidenceBand,
    confidence_band,
    suppression_decision,
    list_default_contracts,
    get_default_contract,
    suggest_contracts_for_template,
)

__all__ = [
    "DecisionContract",
    "ContractClass",
    "CostAsymmetry",
    "ConfidenceBand",
    "confidence_band",
    "suppression_decision",
    "list_default_contracts",
    "get_default_contract",
    "suggest_contracts_for_template",
]

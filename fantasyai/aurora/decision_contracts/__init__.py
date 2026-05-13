"""
Aurora Decision Contracts — executable predicates that fire actions
when an Aurora bundle satisfies a condition.

Distinct from ``fantasyai.aurora.contracts`` (which holds *schema*
contracts — column-type validation for ingest); this module is about
*decision* contracts that turn analytical findings into automation.

A contract is a declarative JSON document:

    {
      "id": "factory-line-3-bearing-watch",
      "name": "Alert on critical bearing anomalies",
      "trigger": {
        "field":   "findings.crit_count",
        "op":      ">=",
        "value":   3
      },
      "actions": [
        {"type": "webhook", "url": "https://example.com/hook"},
        {"type": "log",     "level": "warn"}
      ],
      "rate_limit": {"max_per_hour": 12},
      "metadata":   {"owner": "ops@example.com"}
    }

Triggers are pure-data predicate expressions; the engine evaluates
them against an Aurora bundle's deterministic JSON shape. No code
execution. Field paths are resolved via a documented dotted-path
walker (see ``engine._resolve_path``).

Actions are restricted to an audited set — webhook (with SSRF guards),
log, file (append-only to designated output paths). Webhook URLs are
validated against private-IP / localhost targets unless the user
explicitly opts in via ``ALLOW_LOCAL_WEBHOOKS``. Every firing produces
a structured audit record.

Security stance — see actions.py for the SSRF / rate-limit / secret-
handling details.
"""
from __future__ import annotations

from .engine import (
    Contract,
    Trigger,
    FiringRecord,
    ContractEvaluationError,
    evaluate_contract,
    fire_contract,
    list_contracts,
    save_contract,
    load_contracts,
    DEFAULT_CONTRACTS_DIR,
)
from .actions import (
    Action,
    ALLOW_LOCAL_WEBHOOKS,
    WebhookAction,
    LogAction,
    FileAction,
    InvalidActionError,
    WebhookSecurityError,
    build_action,
)

__all__ = [
    "Contract",
    "Trigger",
    "FiringRecord",
    "ContractEvaluationError",
    "evaluate_contract",
    "fire_contract",
    "list_contracts",
    "save_contract",
    "load_contracts",
    "DEFAULT_CONTRACTS_DIR",
    "Action",
    "ALLOW_LOCAL_WEBHOOKS",
    "WebhookAction",
    "LogAction",
    "FileAction",
    "InvalidActionError",
    "WebhookSecurityError",
    "build_action",
]

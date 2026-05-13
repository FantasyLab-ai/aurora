"""
Decision-contract engine — predicate evaluation + firing orchestration.

The engine is intentionally tiny and pure: given a contract document
and an Aurora bundle, it tells you whether the trigger is satisfied
and (when called via ``fire_contract``) runs the contract's actions.

Triggers are *closed* predicate expressions: one field path, one
comparison operator, one literal value. No boolean composition (yet)
— that keeps the surface area small and audit-friendly. If a future
contract needs AND/OR, we add a ``conditions: [..., "all"]`` shape
that composes individual triggers; current shape stays a strict subset.

Supported operators:
  ==, !=, >, >=, <, <=, in, not_in, contains, regex

Supported field paths:
  Any dotted path through the bundle dict. Special aggregate suffixes:
    ``findings.crit_count`` → count of crit-severity findings
    ``findings.warn_count`` → count of warn-severity findings
    ``findings.count`` → total findings
    ``findings.methods`` → list of method strings (use with ``contains``)
    ``confidence`` → system_model.confidence (shortcut)
    ``forecast.peak`` → max value across forecast points

Path walking is read-only: a contract can NEVER write to the bundle.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .actions import Action, build_action, InvalidActionError


# Where contracts live on disk by default. Configurable via env var so
# multi-tenant deployments can scope it per-user/per-project.
DEFAULT_CONTRACTS_DIR = Path(
    os.environ.get("AURORA_CONTRACTS_DIR")
    or Path.home() / ".aurora" / "decision_contracts"
)


class ContractEvaluationError(ValueError):
    """Raised when a contract document is malformed or a path cannot be
    resolved against the bundle. We use a typed error so the API layer
    can distinguish user errors (bad contract) from system errors."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Trigger:
    """A single predicate expression."""
    field: str
    op: str
    value: Any

    def to_dict(self) -> Dict[str, Any]:
        return {"field": self.field, "op": self.op, "value": self.value}


@dataclass
class Contract:
    """A decision contract: trigger + actions + metadata."""
    id: str
    name: str
    trigger: Trigger
    actions: List[Action] = field(default_factory=list)
    rate_limit: Optional[Dict[str, int]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":          self.id,
            "name":        self.name,
            "description": self.description,
            "enabled":     self.enabled,
            "trigger":     self.trigger.to_dict(),
            "actions":     [a.to_dict() for a in self.actions],
            "rate_limit":  self.rate_limit,
            "metadata":    self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Contract":
        if not isinstance(d, dict):
            raise ContractEvaluationError("contract must be a dict")
        cid = d.get("id")
        if not cid or not isinstance(cid, str):
            raise ContractEvaluationError("contract.id is required (string)")
        if not re.match(r"^[a-zA-Z0-9_\-\.]{1,128}$", cid):
            raise ContractEvaluationError(
                "contract.id must match [a-zA-Z0-9_-.]{1,128}"
            )
        name = d.get("name") or cid
        trig_d = d.get("trigger") or {}
        if not isinstance(trig_d, dict):
            raise ContractEvaluationError("trigger must be a dict")
        for k in ("field", "op", "value"):
            if k not in trig_d:
                raise ContractEvaluationError(f"trigger.{k} is required")
        actions = []
        for a in (d.get("actions") or []):
            try:
                actions.append(build_action(a))
            except InvalidActionError as e:
                raise ContractEvaluationError(
                    f"contract has invalid action: {e}"
                ) from e
        return cls(
            id=cid, name=str(name),
            description=str(d.get("description") or ""),
            enabled=bool(d.get("enabled", True)),
            trigger=Trigger(
                field=str(trig_d["field"]),
                op=str(trig_d["op"]),
                value=trig_d["value"],
            ),
            actions=actions,
            rate_limit=d.get("rate_limit"),
            metadata=dict(d.get("metadata") or {}),
        )


@dataclass
class FiringRecord:
    """Audit record produced every time a contract fires."""
    contract_id: str
    fired_at: str
    bundle_run_id: Optional[str]
    bundle_hash: Optional[str]
    trigger_value: Any   # the resolved bundle value that matched
    actions_attempted: int
    actions_succeeded: int
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Path resolver
# ---------------------------------------------------------------------------

def _resolve_path(bundle: Dict[str, Any], path: str) -> Any:
    """Walk a dotted path through the bundle. Supports the special
    aggregates documented at module top. Returns ``None`` for missing
    paths (the caller decides whether None is a match or not).

    Examples:
        _resolve_path(b, "system_model.confidence")
        _resolve_path(b, "findings.crit_count")
        _resolve_path(b, "dataset.rows")
        _resolve_path(b, "forecast.peak")
    """
    if not isinstance(path, str) or not path:
        raise ContractEvaluationError("trigger.field must be a non-empty string")

    # Special aggregates over findings.
    if path == "findings.crit_count":
        return _count_by_sev(bundle, ("crit", "critical"))
    if path == "findings.warn_count":
        return _count_by_sev(bundle, ("warn", "warning"))
    if path == "findings.info_count":
        return _count_by_sev(bundle, ("info", "informational"))
    if path == "findings.count":
        return len(bundle.get("findings") or [])
    if path == "findings.methods":
        return [str(f.get("method") or "") for f in (bundle.get("findings") or [])
                if isinstance(f, dict)]
    if path == "confidence":
        sm = bundle.get("system_model") or {}
        return sm.get("confidence")
    if path == "forecast.peak":
        fc = bundle.get("forecast") or {}
        pts = fc.get("points") or []
        vals = [p.get("value") for p in pts
                if isinstance(p, dict) and p.get("value") is not None]
        return max(vals) if vals else None
    if path == "fabricated_count":
        return bundle.get("fabricated_count")

    # Generic dotted walk.
    node: Any = bundle
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node


def _count_by_sev(bundle: Dict[str, Any], severities: Tuple[str, ...]) -> int:
    n = 0
    for f in bundle.get("findings") or []:
        if isinstance(f, dict) and str(f.get("severity") or "") in severities:
            n += 1
    return n


# ---------------------------------------------------------------------------
# Predicate evaluation
# ---------------------------------------------------------------------------

_OPS = {"==", "!=", ">", ">=", "<", "<=", "in", "not_in", "contains", "regex"}


def _apply_op(lhs: Any, op: str, rhs: Any) -> bool:
    """Evaluate ``lhs OP rhs``. Returns False on incompatible-type
    comparisons rather than raising — a contract that asks "is None
    >= 3" should simply not fire, not crash."""
    if op == "==":  return lhs == rhs
    if op == "!=":  return lhs != rhs
    if op == "in":
        if not isinstance(rhs, (list, tuple, set, str)):
            return False
        return lhs in rhs
    if op == "not_in":
        if not isinstance(rhs, (list, tuple, set, str)):
            return False
        return lhs not in rhs
    if op == "contains":
        if isinstance(lhs, (list, tuple, set)):
            return rhs in lhs
        if isinstance(lhs, str):
            return str(rhs) in lhs
        return False
    if op == "regex":
        if not isinstance(lhs, str) or not isinstance(rhs, str):
            return False
        try:
            return bool(re.search(rhs, lhs))
        except re.error:
            return False
    # Numeric comparisons — coerce both sides where possible.
    try:
        l = float(lhs); r = float(rhs)
    except (TypeError, ValueError):
        return False
    if op == ">":   return l > r
    if op == ">=":  return l >= r
    if op == "<":   return l < r
    if op == "<=":  return l <= r
    return False


def evaluate_contract(contract: Contract,
                       bundle: Dict[str, Any]
                       ) -> Tuple[bool, Any]:
    """Return ``(satisfied, resolved_value)``.

    ``resolved_value`` is whatever the trigger field resolved to in
    this bundle — useful for the firing record / audit trail. Does NOT
    fire actions; that's ``fire_contract``.
    """
    if not isinstance(contract, Contract):
        raise ContractEvaluationError("contract must be a Contract instance")
    if not isinstance(bundle, dict):
        raise ContractEvaluationError("bundle must be a dict")
    if not contract.enabled:
        return False, None
    if contract.trigger.op not in _OPS:
        raise ContractEvaluationError(
            f"unsupported operator {contract.trigger.op!r}; "
            f"supported: {sorted(_OPS)}"
        )
    val = _resolve_path(bundle, contract.trigger.field)
    return _apply_op(val, contract.trigger.op, contract.trigger.value), val


# ---------------------------------------------------------------------------
# Rate limiting (in-memory, per-process)
# ---------------------------------------------------------------------------

# {contract_id: [unix_ts, ...]} — append-only, pruned on lookup.
_FIRING_LOG: Dict[str, List[float]] = {}


def _rate_limit_ok(contract: Contract) -> bool:
    """Check the contract's rate limit. ``rate_limit`` is a dict with
    one of:
        max_per_minute, max_per_hour, max_per_day

    Returns True when the next firing is allowed; False to suppress."""
    rl = contract.rate_limit or {}
    if not rl:
        return True
    now = time.time()
    window_s = 0
    cap = None
    for key, sec in (("max_per_minute", 60),
                     ("max_per_hour", 3600),
                     ("max_per_day", 86400)):
        if key in rl and isinstance(rl[key], int):
            window_s = sec
            cap = int(rl[key])
            break
    if cap is None:
        return True
    log = _FIRING_LOG.setdefault(contract.id, [])
    # Prune past the window.
    cutoff = now - window_s
    while log and log[0] < cutoff:
        log.pop(0)
    return len(log) < cap


def _record_firing(contract: Contract) -> None:
    _FIRING_LOG.setdefault(contract.id, []).append(time.time())


# ---------------------------------------------------------------------------
# Fire (evaluate + run actions when satisfied)
# ---------------------------------------------------------------------------

def fire_contract(contract: Contract,
                   bundle: Dict[str, Any],
                   *,
                   dry_run: bool = False,
                   ) -> FiringRecord:
    """Evaluate and, if satisfied, run the actions. Returns a
    FiringRecord regardless (a satisfied-but-rate-limited firing still
    produces a record with ``actions_attempted=0``)."""
    satisfied, resolved = evaluate_contract(contract, bundle)
    bundle_run = (bundle.get("run") or {}).get("run_id")
    bundle_hash = (bundle.get("integrity") or {}).get("content_hash")

    record = FiringRecord(
        contract_id=contract.id,
        fired_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        bundle_run_id=bundle_run,
        bundle_hash=bundle_hash,
        trigger_value=resolved,
        actions_attempted=0,
        actions_succeeded=0,
        errors=[],
    )

    if not satisfied:
        record.errors.append("not_satisfied")
        return record

    if not _rate_limit_ok(contract):
        record.errors.append("rate_limited")
        return record

    if dry_run:
        record.errors.append("dry_run")
        return record

    for action in contract.actions:
        record.actions_attempted += 1
        try:
            action.run({
                "contract_id": contract.id,
                "contract_name": contract.name,
                "trigger_field": contract.trigger.field,
                "trigger_value": resolved,
                "bundle_run_id": bundle_run,
                "bundle_hash": bundle_hash,
                "metadata": contract.metadata,
            })
            record.actions_succeeded += 1
        except Exception as e:
            record.errors.append(f"{type(action).__name__}: {e}")

    _record_firing(contract)
    return record


# ---------------------------------------------------------------------------
# Persistence — disk-backed contract library
# ---------------------------------------------------------------------------

def save_contract(contract: Contract,
                   contracts_dir: Optional[Path] = None) -> Path:
    """Write the contract document to ``contracts_dir/<id>.json``.
    Validates by round-tripping through ``Contract.from_dict``."""
    d = contract.to_dict()
    Contract.from_dict(d)  # raises if invalid
    cd = Path(contracts_dir or DEFAULT_CONTRACTS_DIR)
    cd.mkdir(parents=True, exist_ok=True)
    p = cd / f"{contract.id}.json"
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False),
                  encoding="utf-8")
    return p


def load_contracts(contracts_dir: Optional[Path] = None
                    ) -> List[Contract]:
    """Load every ``*.json`` file under ``contracts_dir`` as a Contract.
    Invalid files are skipped silently — bad JSON in one contract
    should not break the whole pipeline. Errors are returned via the
    ``list_contracts`` audit channel (when a richer surface lands)."""
    cd = Path(contracts_dir or DEFAULT_CONTRACTS_DIR)
    if not cd.exists() or not cd.is_dir():
        return []
    out: List[Contract] = []
    for f in sorted(cd.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append(Contract.from_dict(d))
        except Exception:
            continue
    return out


def list_contracts(contracts_dir: Optional[Path] = None
                    ) -> List[Dict[str, Any]]:
    """Return a JSON-safe list of contract summaries for the API
    layer to surface."""
    return [c.to_dict() for c in load_contracts(contracts_dir)]

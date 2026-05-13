"""
Tests for Aurora Decision Contracts — predicate evaluation, action
runners, security guards, persistence.

Coverage:
  * Predicate operators (==, >=, in, contains, regex, …)
  * Field-path resolver special cases (crit_count, confidence, peak)
  * Action validation (webhook URL, log level, file traversal)
  * SSRF: localhost / private IP / link-local rejected unless opted in
  * Rate limiting (max_per_minute caps repeated firings)
  * fire_contract returns a FiringRecord even when suppressed
  * Save/load roundtrip + invalid contract rejection
  * No raises across the engine boundary
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from aurora_sdk import Bundle
from fantasyai.aurora.decision_contracts import (
    Contract,
    Trigger,
    Action,
    FiringRecord,
    ContractEvaluationError,
    evaluate_contract,
    fire_contract,
    save_contract,
    load_contracts,
    list_contracts,
    build_action,
    WebhookAction,
    LogAction,
    FileAction,
    InvalidActionError,
    WebhookSecurityError,
)
from fantasyai.aurora.decision_contracts import actions as actions_mod


def _sample_state() -> Dict[str, Any]:
    return {
        "run_id": "dc-test-001",
        "run_meta": {"run_id": "dc-test-001", "state": "complete"},
        "dataset": {"name": "tiny.csv", "rows": 100, "cols": 3, "path": None},
        "structure": {"available": True, "time_axis": True, "cadence": "5s",
                       "columns": []},
        "findings": [
            {"rank": 1, "severity": "crit", "method": "ISO-FOREST",
              "title": "anom", "claim_id": "a0"},
            {"rank": 2, "severity": "crit", "method": "ISO-FOREST",
              "title": "anom2", "claim_id": "a1"},
            {"rank": 3, "severity": "crit", "method": "ISO-FOREST",
              "title": "anom3", "claim_id": "a2"},
            {"rank": 4, "severity": "warn", "method": "Granger",
              "title": "warn1", "claim_id": "w0"},
        ],
        "system_model": {"template_id": "(none)", "confidence": 0.72,
                          "topology": {"entities": [], "relationships": []}},
        "forecast": {
            "available": True, "target": "x", "method": "AR(1)",
            "horizon": 6,
            "points": [
                {"step": 1, "value": 1.0, "ci_low": 0.8, "ci_high": 1.2},
                {"step": 2, "value": 1.49, "ci_low": 1.2, "ci_high": 1.8},
            ],
        },
        "anomalies": [], "regimes": [], "motifs": [],
        "causal": {}, "physics": {},
    }


# ============================================================
# Predicate operators
# ============================================================

class TestOperators:

    def _eval(self, op: str, value: Any, field: str = "findings.crit_count"
              ) -> bool:
        c = Contract(
            id="t", name="t",
            trigger=Trigger(field=field, op=op, value=value),
        )
        bundle = Bundle.from_state(_sample_state()).doc
        ok, _ = evaluate_contract(c, bundle)
        return ok

    def test_eq(self):
        assert self._eval("==", 3) is True
        assert self._eval("==", 0) is False

    def test_gte(self):
        assert self._eval(">=", 3) is True
        assert self._eval(">=", 4) is False

    def test_gt(self):
        assert self._eval(">", 2) is True
        assert self._eval(">", 3) is False

    def test_in(self):
        assert self._eval("in", [1, 2, 3]) is True
        assert self._eval("in", [10, 20]) is False

    def test_contains_on_list(self):
        # findings.methods is a list of strings; contains checks membership
        c = Contract(id="t", name="t",
                      trigger=Trigger(field="findings.methods",
                                       op="contains",
                                       value="ISO-FOREST"))
        bundle = Bundle.from_state(_sample_state()).doc
        ok, _ = evaluate_contract(c, bundle)
        assert ok is True

    def test_regex(self):
        c = Contract(id="t", name="t",
                      trigger=Trigger(field="dataset.name",
                                       op="regex",
                                       value=r"^tiny\..+$"))
        bundle = Bundle.from_state(_sample_state()).doc
        ok, _ = evaluate_contract(c, bundle)
        assert ok is True

    def test_unsupported_op_raises(self):
        c = Contract(id="t", name="t",
                      trigger=Trigger(field="findings.crit_count",
                                       op="approximately",
                                       value=3))
        bundle = Bundle.from_state(_sample_state()).doc
        with pytest.raises(ContractEvaluationError):
            evaluate_contract(c, bundle)

    def test_incompatible_type_returns_false_not_raises(self):
        # Comparing a missing path (None) >= 3 should be False, not crash.
        c = Contract(id="t", name="t",
                      trigger=Trigger(field="nope.nope.nope",
                                       op=">=",
                                       value=3))
        bundle = Bundle.from_state(_sample_state()).doc
        ok, _ = evaluate_contract(c, bundle)
        assert ok is False


# ============================================================
# Field-path resolver — special aggregates
# ============================================================

class TestFieldPaths:

    def test_crit_count(self):
        b = Bundle.from_state(_sample_state()).doc
        c = Contract(id="t", name="t",
                      trigger=Trigger(field="findings.crit_count",
                                       op=">=", value=3))
        ok, val = evaluate_contract(c, b)
        assert val == 3 and ok is True

    def test_confidence_shortcut(self):
        b = Bundle.from_state(_sample_state()).doc
        c = Contract(id="t", name="t",
                      trigger=Trigger(field="confidence",
                                       op=">=", value=0.7))
        ok, val = evaluate_contract(c, b)
        assert val == pytest.approx(0.72)
        assert ok is True

    def test_forecast_peak(self):
        b = Bundle.from_state(_sample_state()).doc
        c = Contract(id="t", name="t",
                      trigger=Trigger(field="forecast.peak",
                                       op=">", value=1.4))
        ok, val = evaluate_contract(c, b)
        assert val == 1.49 and ok is True

    def test_generic_dotted_walk(self):
        b = Bundle.from_state(_sample_state()).doc
        c = Contract(id="t", name="t",
                      trigger=Trigger(field="dataset.rows",
                                       op="==", value=100))
        ok, _ = evaluate_contract(c, b)
        assert ok is True

    def test_empty_path_raises(self):
        c = Contract(id="t", name="t",
                      trigger=Trigger(field="", op="==", value=0))
        b = Bundle.from_state(_sample_state()).doc
        with pytest.raises(ContractEvaluationError):
            evaluate_contract(c, b)


# ============================================================
# Action validation
# ============================================================

class TestActionValidation:

    def test_webhook_requires_url(self):
        with pytest.raises(InvalidActionError):
            build_action({"type": "webhook"})

    def test_webhook_rejects_non_http_scheme(self):
        with pytest.raises(InvalidActionError):
            build_action({"type": "webhook", "url": "ftp://example.com"})

    def test_webhook_rejects_no_hostname(self):
        with pytest.raises(InvalidActionError):
            build_action({"type": "webhook", "url": "https:///nopath"})

    def test_webhook_rejects_bad_header_key(self):
        with pytest.raises(InvalidActionError):
            build_action({
                "type": "webhook",
                "url": "https://example.com/h",
                "headers": {"Bad Key With Spaces!": "x"},
            })

    def test_log_rejects_bad_level(self):
        with pytest.raises(InvalidActionError):
            build_action({"type": "log", "level": "yelling"})

    def test_file_rejects_absolute_path(self):
        with pytest.raises(InvalidActionError):
            build_action({"type": "file", "path": "/etc/passwd"})

    def test_file_rejects_traversal(self):
        with pytest.raises(InvalidActionError):
            build_action({"type": "file", "path": "../../etc/passwd"})

    def test_unknown_action_type_rejected(self):
        with pytest.raises(InvalidActionError):
            build_action({"type": "rm -rf /"})


# ============================================================
# SSRF guard on webhook
# ============================================================

class TestSSRF:

    def test_localhost_rejected_when_local_webhooks_off(self):
        prev = actions_mod.ALLOW_LOCAL_WEBHOOKS
        actions_mod.ALLOW_LOCAL_WEBHOOKS = False
        try:
            wh = WebhookAction(url="http://localhost:9999/hook")
            with pytest.raises(WebhookSecurityError):
                wh.run({"contract_id": "t", "contract_name": "t"})
        finally:
            actions_mod.ALLOW_LOCAL_WEBHOOKS = prev

    def test_127_0_0_1_rejected(self):
        prev = actions_mod.ALLOW_LOCAL_WEBHOOKS
        actions_mod.ALLOW_LOCAL_WEBHOOKS = False
        try:
            wh = WebhookAction(url="http://127.0.0.1/hook")
            with pytest.raises(WebhookSecurityError):
                wh.run({"contract_id": "t", "contract_name": "t"})
        finally:
            actions_mod.ALLOW_LOCAL_WEBHOOKS = prev

    def test_private_ip_rejected(self):
        prev = actions_mod.ALLOW_LOCAL_WEBHOOKS
        actions_mod.ALLOW_LOCAL_WEBHOOKS = False
        try:
            wh = WebhookAction(url="http://10.0.0.5/hook")
            with pytest.raises(WebhookSecurityError):
                wh.run({"contract_id": "t", "contract_name": "t"})
        finally:
            actions_mod.ALLOW_LOCAL_WEBHOOKS = prev


# ============================================================
# Rate limiting
# ============================================================

class TestRateLimit:

    def test_max_per_minute_caps_firings(self, monkeypatch):
        # Stub out the actual webhook send so we can fire fast.
        calls = {"n": 0}
        def _fake_run(self, ctx):
            calls["n"] += 1
        monkeypatch.setattr(WebhookAction, "run", _fake_run)

        contract = Contract(
            id="rl-test",
            name="rate limit test",
            trigger=Trigger(field="findings.crit_count", op=">=", value=1),
            actions=[WebhookAction(url="https://example.com/h")],
            rate_limit={"max_per_minute": 2},
        )
        bundle = Bundle.from_state(_sample_state()).doc
        recs = [fire_contract(contract, bundle) for _ in range(5)]
        # Two firings should run, three should be rate-limited.
        ran = [r for r in recs if r.actions_succeeded > 0]
        suppressed = [r for r in recs if "rate_limited" in r.errors]
        assert len(ran) == 2
        assert len(suppressed) == 3


# ============================================================
# fire_contract behaviour
# ============================================================

class TestFireContract:

    def test_dry_run_does_not_call_actions(self, monkeypatch):
        calls = {"n": 0}
        monkeypatch.setattr(WebhookAction, "run",
                             lambda self, ctx: calls.__setitem__("n", calls["n"] + 1))
        c = Contract(
            id="dr-test", name="dry-run",
            trigger=Trigger(field="findings.crit_count", op=">=", value=1),
            actions=[WebhookAction(url="https://example.com/h")],
        )
        bundle = Bundle.from_state(_sample_state()).doc
        rec = fire_contract(c, bundle, dry_run=True)
        assert "dry_run" in rec.errors
        assert calls["n"] == 0

    def test_not_satisfied_records_reason(self):
        c = Contract(
            id="ns", name="not satisfied",
            trigger=Trigger(field="findings.crit_count", op=">", value=100),
        )
        rec = fire_contract(c, Bundle.from_state(_sample_state()).doc)
        assert "not_satisfied" in rec.errors

    def test_disabled_contract_does_not_fire(self):
        c = Contract(
            id="dis", name="disabled", enabled=False,
            trigger=Trigger(field="findings.crit_count", op=">=", value=1),
        )
        rec = fire_contract(c, Bundle.from_state(_sample_state()).doc)
        assert "not_satisfied" in rec.errors

    def test_record_includes_bundle_identifiers(self):
        c = Contract(
            id="ri", name="record-id",
            trigger=Trigger(field="findings.crit_count", op=">=", value=1),
            actions=[LogAction(level="info", message="x")],
        )
        b = Bundle.from_state(_sample_state()).doc
        rec = fire_contract(c, b)
        assert rec.bundle_run_id == "dc-test-001"
        assert rec.bundle_hash == b["integrity"]["content_hash"]


# ============================================================
# Persistence
# ============================================================

class TestPersistence:

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        c = Contract(
            id="rt.test",
            name="roundtrip",
            description="reload me",
            trigger=Trigger(field="confidence", op="<", value=0.30),
            actions=[LogAction(level="warn", message="low conf")],
            rate_limit={"max_per_hour": 6},
            metadata={"owner": "test@example"},
        )
        save_contract(c, contracts_dir=tmp_path)
        loaded = load_contracts(contracts_dir=tmp_path)
        assert len(loaded) == 1
        rt = loaded[0]
        assert rt.id == "rt.test"
        assert rt.description == "reload me"
        assert rt.rate_limit == {"max_per_hour": 6}
        assert len(rt.actions) == 1
        assert isinstance(rt.actions[0], LogAction)

    def test_load_skips_invalid_files(self, tmp_path: Path):
        # Drop a malformed file alongside a valid one.
        (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
        c = Contract(
            id="valid", name="valid",
            trigger=Trigger(field="findings.crit_count", op=">", value=0),
        )
        save_contract(c, contracts_dir=tmp_path)
        loaded = load_contracts(contracts_dir=tmp_path)
        assert len(loaded) == 1
        assert loaded[0].id == "valid"

    def test_invalid_id_rejected_at_build(self):
        with pytest.raises(ContractEvaluationError):
            Contract.from_dict({
                "id": "../slash/etc",
                "name": "bad id",
                "trigger": {"field": "x", "op": "==", "value": 0},
                "actions": [],
            })


# ============================================================
# Audit / redaction
# ============================================================

class TestAudit:

    def test_authorization_header_redacted_in_to_dict(self):
        wh = WebhookAction(
            url="https://example.com/h",
            headers={"Authorization": "Bearer super-secret-token"},
        )
        d = wh.to_dict()
        assert "super-secret-token" not in d["headers"]["Authorization"]
        assert "redacted" in d["headers"]["Authorization"]

    def test_log_action_run_never_raises_on_logger_failure(self, monkeypatch):
        # Force the logger to blow up; the action must swallow.
        from fantasyai.aurora.decision_contracts import actions as a_mod
        def boom(*args, **kw): raise RuntimeError("logger down")
        monkeypatch.setattr(a_mod._LOGGER, "info", boom)
        la = LogAction(level="info", message="hi")
        la.run({"contract_id": "t", "contract_name": "t",
                "trigger_field": "x", "trigger_value": 1,
                "bundle_run_id": None})

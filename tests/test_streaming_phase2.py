"""
Stream 1.4 Phase 2 tests:
    * Per-finding dedupe (FindingDedupeStore)
    * IncrementalRunner uses dedupe to filter new_finding events
    * Contracts bridge fires only on matching contracts
"""
from __future__ import annotations

import time
from typing import Any, Dict, List
import pytest


# ----------------------------------------------------------------------
# FindingDedupeStore
# ----------------------------------------------------------------------

class TestFindingDedupeStore:

    def test_identity_is_stable(self):
        from fantasyai.aurora.streaming.dedupe import finding_identity
        f = {
            "method": "var", "title": "VAR(2) coupling A→B",
            "severity": "info",
            "evidence": {"status": "fit", "n_obs": 100},
        }
        i1 = finding_identity(f)
        i2 = finding_identity(dict(f))
        assert i1 == i2
        assert len(i1) == 16

    def test_identity_distinguishes_severity(self):
        from fantasyai.aurora.streaming.dedupe import finding_identity
        f1 = {"method": "var", "title": "x", "severity": "info"}
        f2 = {"method": "var", "title": "x", "severity": "crit"}
        assert finding_identity(f1) != finding_identity(f2)

    def test_identity_distinguishes_target_col(self):
        from fantasyai.aurora.streaming.dedupe import finding_identity
        f1 = {"method": "bocpd", "title": "cp", "severity": "warn",
              "evidence": {"target_col": "temperature_c", "status": "fit"}}
        f2 = {"method": "bocpd", "title": "cp", "severity": "warn",
              "evidence": {"target_col": "humidity_pct", "status": "fit"}}
        assert finding_identity(f1) != finding_identity(f2)

    def test_filter_new_dedupes_repeats(self):
        from fantasyai.aurora.streaming.dedupe import FindingDedupeStore
        store = FindingDedupeStore(max_size=100)
        findings = [
            {"method": "var", "title": "A", "severity": "info"},
            {"method": "var", "title": "B", "severity": "info"},
        ]
        new1, _ = store.filter_new(findings)
        assert len(new1) == 2
        new2, _ = store.filter_new(findings)  # same payload
        assert new2 == []
        # A genuinely new finding still gets through.
        more = findings + [{"method": "dtw", "title": "C", "severity": "info"}]
        new3, _ = store.filter_new(more)
        assert len(new3) == 1
        assert new3[0]["method"] == "dtw"

    def test_lru_bounded(self):
        from fantasyai.aurora.streaming.dedupe import FindingDedupeStore
        store = FindingDedupeStore(max_size=3)
        for i in range(10):
            store.filter_new([{"method": "var", "title": str(i),
                                "severity": "info"}])
        assert len(store) == 3

    def test_reset_clears(self):
        from fantasyai.aurora.streaming.dedupe import FindingDedupeStore
        store = FindingDedupeStore()
        store.filter_new([{"method": "var", "title": "A", "severity": "info"}])
        assert len(store) == 1
        store.reset()
        assert len(store) == 0


# ----------------------------------------------------------------------
# IncrementalRunner integration with dedupe
# ----------------------------------------------------------------------

class TestRunnerDedupe:

    def test_runner_only_fires_new_findings(self):
        from fantasyai.aurora.streaming import (
            IncrementalRunner, StreamEventBus, EVENT_NEW_FINDING,
        )
        import pandas as pd

        bus = StreamEventBus()
        captured: List[Any] = []
        sub = bus.subscribe()

        # Stub analysis: always emits the same two findings.
        same_findings = [
            {"method": "var", "title": "A", "severity": "warn",
              "evidence": {"status": "fit", "n_obs": 50}},
            {"method": "dtw", "title": "B", "severity": "info",
              "evidence": {"status": "fit", "n_obs": 50}},
        ]
        def stub(df):
            return {"findings": list(same_findings)}

        runner = IncrementalRunner(
            data_path=".",
            event_bus=bus,
            window=None,
            analysis_fn=stub,
        )
        df = pd.DataFrame({"x": [1, 2, 3]})

        # First ingest → both findings fire as new_finding events.
        runner.ingest(df, source="t1")
        time.sleep(0.05)
        # Drain bus.
        events_round_1 = []
        try:
            while True:
                ev = sub.get(timeout=0.1)
                if ev.kind == EVENT_NEW_FINDING:
                    events_round_1.append(ev)
        except Exception:
            pass
        assert len(events_round_1) == 2, (
            f"first ingest should emit one event per finding, got {len(events_round_1)}"
        )

        # Second ingest with identical findings → 0 new_finding events
        # (dedupe filtered them).
        runner.ingest(df, source="t2")
        time.sleep(0.05)
        events_round_2 = []
        try:
            while True:
                ev = sub.get(timeout=0.1)
                if ev.kind == EVENT_NEW_FINDING:
                    events_round_2.append(ev)
        except Exception:
            pass
        assert events_round_2 == [], (
            f"identical findings shouldn't re-emit, got {len(events_round_2)}"
        )

        # Dedupe store grew to 2 identities.
        assert runner.dedupe_size() == 2

    def test_runner_fires_individual_events_per_finding(self):
        from fantasyai.aurora.streaming import (
            IncrementalRunner, StreamEventBus, EVENT_NEW_FINDING,
        )
        import pandas as pd

        bus = StreamEventBus()
        sub = bus.subscribe()

        def stub(df):
            return {"findings": [
                {"method": "var", "title": "X", "severity": "crit"},
                {"method": "dtw", "title": "Y", "severity": "warn"},
                {"method": "kalman", "title": "Z", "severity": "info"},
            ]}

        runner = IncrementalRunner(
            data_path=".",
            event_bus=bus,
            window=None,
            analysis_fn=stub,
        )
        runner.ingest(pd.DataFrame({"x": [1]}), source="t1")
        time.sleep(0.05)

        # Should get 3 separate new_finding events, one per finding.
        events = []
        try:
            while True:
                ev = sub.get(timeout=0.1)
                if ev.kind == EVENT_NEW_FINDING:
                    events.append(ev)
        except Exception:
            pass
        assert len(events) == 3
        methods = sorted([e.payload.get("method") for e in events])
        assert methods == ["dtw", "kalman", "var"]
        # Each payload carries the identity hash for downstream replay.
        assert all("identity" in e.payload for e in events)


# ----------------------------------------------------------------------
# Contracts bridge
# ----------------------------------------------------------------------

class TestContractsBridge:

    def test_bridge_only_fires_matching_contracts(self, tmp_path):
        from fantasyai.aurora.streaming.contracts_bridge import (
            evaluate_finding_against_contracts,
        )
        from fantasyai.aurora.decision_contracts.engine import (
            Contract, Trigger,
        )
        from fantasyai.aurora.decision_contracts.actions import build_action

        # Use a `log` action so we don't need network or filesystem.
        log_action = build_action({"type": "log", "level": "info"})

        # Two contracts: one fires on findings.crit_count > 0, one on
        # findings.warn_count > 0. Both are passed in directly so the
        # test doesn't depend on disk state.
        c_crit = Contract(
            id="c-crit",
            name="any crit",
            trigger=Trigger(field="findings.crit_count", op=">", value=0),
            actions=[log_action],
            enabled=True,
        )
        c_warn = Contract(
            id="c-warn",
            name="any warn",
            trigger=Trigger(field="findings.warn_count", op=">", value=0),
            actions=[log_action],
            enabled=True,
        )
        # Critical finding → c-crit fires, c-warn doesn't (no warns).
        fired = evaluate_finding_against_contracts(
            {"method": "var", "title": "X", "severity": "crit",
              "evidence": {"status": "fit"}},
            contracts=[c_crit, c_warn],
            dry_run=True,  # don't run actions
        )
        fired_ids = sorted(r["contract_id"] for r in fired)
        assert "c-crit" in fired_ids
        assert "c-warn" not in fired_ids

    def test_bridge_safe_with_no_contracts(self):
        from fantasyai.aurora.streaming.contracts_bridge import (
            evaluate_finding_against_contracts,
        )
        fired = evaluate_finding_against_contracts(
            {"method": "var", "title": "X", "severity": "info"},
            contracts=[],
        )
        assert fired == []

    def test_make_bridge_returns_callable(self):
        from fantasyai.aurora.streaming import make_bridge
        bridge = make_bridge()
        assert callable(bridge)
        # Doesn't crash on a no-op finding (load_contracts returns
        # empty if no contracts dir is set up).
        bridge({"method": "var", "title": "X", "severity": "info"})

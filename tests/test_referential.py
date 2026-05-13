"""
Tests for ``fantasyai.aurora.referential``: 6-level hierarchy + Referent
schema. Phase A of the "Human-Readable Output" brief.

Coverage:
  - Each level fires when its inputs are present.
  - Higher levels preempt lower levels.
  - Fall-through chain is recorded honestly when levels fail.
  - Last-resort row_index always succeeds (and is marked inspect_only).
  - The state-walker integrates with state_builder's finding/anomaly schemas.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ============================================================
# Single-coordinate resolution
# ============================================================

class TestRowIndexFallback:

    def test_bare_row_id_falls_to_l6(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent({"row_id": 1248})
        assert ref.level_used == "row_index"
        assert ref.inspect_only is True
        assert "row 1248" in ref.primary_label
        # The chain shows we tried all earlier levels.
        assert ref.fallback_chain == [
            "named_event", "timestamp", "entity", "group", "position",
        ]

    def test_window_range_falls_to_l6_when_no_position_hint(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent({"window_start": 100, "window_end": 200})
        # Without rows_total context, position can't decide head/tail; falls to L6.
        assert ref.level_used == "row_index"
        assert "100" in ref.primary_label and "200" in ref.primary_label

    def test_no_coords_at_all(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent({})
        # Always succeeds, but is empty + inspect-only.
        assert ref.level_used == "row_index"
        assert ref.inspect_only is True


class TestPosition:

    def test_first_few_rows(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent({"row_id": 2}, {"rows_total": 100_000})
        assert ref.level_used == "position"
        assert "first" in ref.primary_label.lower()

    def test_last_few_rows(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent({"row_id": 99_998}, {"rows_total": 100_000})
        assert ref.level_used == "position"
        assert "last" in ref.primary_label.lower()

    def test_explicit_position_phrase(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent({"position_hint": "the recovery period"})
        assert ref.level_used == "position"
        assert ref.primary_label == "the recovery period"


class TestTimestamp:

    def test_iso_string(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent({"timestamp": "2024-08-12T03:14:00"})
        assert ref.level_used == "timestamp"
        assert "2024-08-12" in ref.primary_label
        assert ref.timestamp.startswith("2024-08-12")

    def test_date_only(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent({"timestamp": "1992-08-24"})
        assert ref.level_used == "timestamp"
        assert ref.primary_label == "1992-08-24"

    def test_unparseable_timestamp_falls_through(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent({"timestamp": "not a date", "row_id": 5},
                                {"rows_total": 1000})
        # Should NOT be a timestamp referent.
        assert ref.level_used != "timestamp"


class TestEntity:

    def test_panel_id_with_explicit_value(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent(
            {"entity_id": "KPHL"},
            {"structure": {"panel_id_col": "station_id"}},
        )
        assert ref.level_used == "entity"
        assert "Station" in ref.primary_label
        assert "KPHL" in ref.primary_label

    def test_panel_id_lookup_from_row_dict(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent(
            {"row": {"patient_id": "P0042", "temperature": 37.2}},
            {"structure": {"panel_id_col": "patient_id"}},
        )
        assert ref.level_used == "entity"
        assert "Patient" in ref.primary_label
        assert "P0042" in ref.primary_label

    def test_no_panel_col_falls_through(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent({"entity_id": "X"}, {})
        assert ref.level_used != "entity"


class TestGroup:

    def test_dominant_group(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent(
            {"group_summary": {"col": "region", "value": "European",
                                "share": 0.85}},
        )
        assert ref.level_used == "group"
        assert "European" in ref.primary_label

    def test_minority_group_does_not_fire(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent(
            {"group_summary": {"col": "region", "value": "European",
                                "share": 0.40},
             "row_id": 50_000},
            {"rows_total": 100_000},
        )
        assert ref.level_used != "group"


class TestNamedEvent:

    def test_registry_match(self):
        from fantasyai.aurora.referential import (
            resolve_referent, NAMED_EVENT_REGISTRY,
        )
        from fantasyai.aurora.referential.resolver import register_named_event
        # Snapshot + restore — tests must be hermetic.
        snapshot = list(NAMED_EVENT_REGISTRY)
        try:
            NAMED_EVENT_REGISTRY.clear()
            register_named_event(
                "Hurricane Andrew", "1992-08-23T00:00:00",
                "1992-08-28T00:00:00", domain="meteorology",
            )
            ref = resolve_referent({"timestamp": "1992-08-24T12:00:00"})
            assert ref.level_used == "named_event"
            assert ref.primary_label == "Hurricane Andrew"
            assert ref.referent_confidence > 0.9
        finally:
            NAMED_EVENT_REGISTRY.clear()
            NAMED_EVENT_REGISTRY.extend(snapshot)

    def test_outside_window_falls_to_timestamp(self):
        from fantasyai.aurora.referential import (
            resolve_referent, NAMED_EVENT_REGISTRY,
        )
        from fantasyai.aurora.referential.resolver import register_named_event
        snapshot = list(NAMED_EVENT_REGISTRY)
        try:
            NAMED_EVENT_REGISTRY.clear()
            register_named_event(
                "Hurricane Andrew", "1992-08-23T00:00:00",
                "1992-08-28T00:00:00",
            )
            ref = resolve_referent({"timestamp": "2001-09-11T12:00:00"})
            # No match → timestamp wins.
            assert ref.level_used == "timestamp"
        finally:
            NAMED_EVENT_REGISTRY.clear()
            NAMED_EVENT_REGISTRY.extend(snapshot)


# ============================================================
# Higher levels preempt lower
# ============================================================

class TestPriority:

    def test_timestamp_preempts_position(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent(
            {"row_id": 2, "timestamp": "2024-08-12 03:14:00"},
            {"rows_total": 100_000},
        )
        assert ref.level_used == "timestamp"

    def test_entity_preempts_position(self):
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent(
            {"row_id": 2, "entity_id": "KPHL"},
            {"structure": {"panel_id_col": "station_id"},
             "rows_total": 100_000},
        )
        assert ref.level_used == "entity"


# ============================================================
# State-level integration
# ============================================================

class TestStateAttachment:

    def test_attach_referents_to_state_anomalies(self):
        from fantasyai.aurora.referential import attach_referents_to_state
        state = {
            "dataset": {"rows": 100_000, "name": "x.csv", "path": ""},
            "structure": {},
            "anomalies": [
                {"when": "row 1248", "column": "temperature"},
            ],
            "regimes": [], "motifs": [], "findings": [
                {"title": "Anomaly in temperature at row 1248"},
            ],
        }
        attach_referents_to_state(state)
        # Every walked dict should grow a referent.
        assert "referent" in state["anomalies"][0]
        assert "referent" in state["findings"][0]
        # No timestamp / panel / event → falls to position (since 1248/100k
        # is neither head nor tail) → falls to row_index.
        assert state["findings"][0]["referent"]["level_used"] in (
            "row_index", "position",
        )

    def test_attach_referents_idempotent(self):
        from fantasyai.aurora.referential import attach_referents_to_state
        state = {
            "anomalies": [{"when": "row 1"}],
            "regimes": [], "motifs": [], "findings": [],
            "dataset": {"rows": 1000, "name": "x.csv", "path": ""},
            "structure": {},
        }
        attach_referents_to_state(state)
        first = dict(state["anomalies"][0]["referent"])
        attach_referents_to_state(state)
        assert state["anomalies"][0]["referent"] == first


class TestSerialisation:

    def test_referent_to_dict_is_jsonable(self):
        import json
        from fantasyai.aurora.referential import resolve_referent
        ref = resolve_referent(
            {"timestamp": "2024-08-12T03:14:00"},
        )
        d = ref.to_dict()
        # Round-trip through JSON must work.
        s = json.dumps(d)
        d2 = json.loads(s)
        assert d2["primary_label"] == d["primary_label"]
        assert d2["level_used"] == d["level_used"]

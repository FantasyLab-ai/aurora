"""
Tests for the Aurora SDK — Bundle Format v1, findings helpers, runner.

Coverage targets:
  * Bundle build → save → load roundtrip preserves content
  * Integrity hash detects tampering (single-byte mutation fails verify)
  * Schema rejection: wrong format, wrong major version
  * Findings filters compose correctly
  * ForecastView / SystemModelView return sensible defaults on empty docs
  * sign/verify roundtrip works when ``cryptography`` is available
    (test is skipped if it isn't)
  * fabricated_count uses the corrected positive-signal logic
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aurora_sdk import (
    Bundle,
    BundleIntegrityError,
    BundleSchemaError,
    BUNDLE_SCHEMA_VERSION,
    BUNDLE_FORMAT_NAME,
    build_bundle_from_state,
)
from aurora_sdk.findings import Findings, ForecastView, SystemModelView


# ============================================================
# Sample state — minimal but realistic shape
# ============================================================

def _sample_state():
    return {
        "run_id": "test-run-001",
        "run_dir": "outputs/test-run-001",
        "run_meta": {
            "run_id": "test-run-001",
            "started_at": "2026-05-12T15:00:00",
            "completed_at": "2026-05-12T15:01:30",
            "state": "complete",
            "tier": "auto",
        },
        "dataset": {
            "name": "factory_bearing_demo.csv",
            "rows": 1000,
            "cols": 5,
            "size_mb": 0.04,
            "path": None,  # don't compute file hash
        },
        "structure": {
            "available": True,
            "time_axis": True,
            "time_col": "timestamp_s",
            "cadence": "5s",
            "columns": [
                {"name": "timestamp_s", "kind": "time"},
                {"name": "vibration_g", "kind": "n"},
                {"name": "motor_temp_c", "kind": "n"},
                {"name": "rpm", "kind": "n"},
                {"name": "bearing_load_kn", "kind": "n"},
            ],
        },
        "findings": [
            {
                "rank": 1, "severity": "crit",
                "method": "ISO-FOREST + ROBUST-Z",
                "title": "Confirmed anomaly in vibration_g at row 205",
                "claim_id": "anom-0000",
                "confidence": 1.0,
            },
            {
                "rank": 2, "severity": "warn",
                "method": "Granger",
                "title": "temperature_c → barometric_pressure",
                "confidence": 0.70,
            },
            {
                "rank": 3, "severity": "info",
                "method": "matrix_profile",
                "title": "Recurring motif at lag 40 min",
                "confidence": 0.92,
            },
        ],
        "system_model": {
            "template_id": "(none)",
            "confidence": 0.0,
            "topology": {
                "entities": [
                    {"id": "vibration_g", "label": "Vibration", "role": "state"},
                    {"id": "motor_temp_c", "label": "Motor temp", "role": "state"},
                ],
                "relationships": [],
            },
        },
        "forecast": {
            "available": True,
            "target": "vibration_g",
            "method": "AR(1)",
            "horizon": 24,
            "points": [
                {"step": 1, "value": 1.20, "ci_low": 1.10, "ci_high": 1.30},
                {"step": 2, "value": 1.49, "ci_low": 1.30, "ci_high": 1.68},
                {"step": 3, "value": 1.35, "ci_low": 1.15, "ci_high": 1.55},
            ],
        },
        "anomalies": [
            {"rank": 1, "row_index": 205, "score": 152.8, "severity": "crit"},
        ],
        "synthesis": {
            "interpretive_summary": "Mock summary",
            "assumptions": [
                "robust z-score uses Hampel 1974 thresholds",
                "AR(1) σ propagates analytically",
            ],
        },
        "regimes": [], "motifs": [], "causal": {}, "physics": {},
    }


# ============================================================
# Build / save / load / verify roundtrip
# ============================================================

class TestBundleRoundtrip:

    def test_build_has_required_top_level_keys(self):
        state = _sample_state()
        b = build_bundle_from_state(state)
        for k in ("bundle_version", "format", "aurora_version",
                  "generated_at", "run", "dataset", "structure",
                  "findings", "system_model", "forecast",
                  "fabricated_count", "integrity"):
            assert k in b, f"bundle missing top-level key: {k}"

    def test_format_marker_and_schema_version(self):
        b = build_bundle_from_state(_sample_state())
        assert b["format"] == BUNDLE_FORMAT_NAME
        assert b["bundle_version"] == BUNDLE_SCHEMA_VERSION
        assert b["bundle_version"].startswith("1.")

    def test_findings_get_synthesized_claim_ids(self):
        state = _sample_state()
        # Strip claim_id from one finding to test synthesis.
        state["findings"][1].pop("claim_id", None)
        b = build_bundle_from_state(state)
        cids = [f["claim_id"] for f in b["findings"]]
        # No duplicates.
        assert len(set(cids)) == len(cids)
        # All non-empty.
        assert all(c for c in cids)

    def test_save_load_roundtrip(self, tmp_path: Path):
        state = _sample_state()
        b = Bundle.from_state(state)
        p = tmp_path / "test.aurora.json"
        b.save(p)
        loaded = Bundle.load(p)
        assert loaded.doc["bundle_version"] == b.doc["bundle_version"]
        assert loaded.run_id == b.run_id
        # findings preserved
        assert len(loaded.findings()) == len(b.findings())

    def test_load_rejects_non_dict_root(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text('"not an object"', encoding="utf-8")
        with pytest.raises(BundleSchemaError):
            Bundle.load(p)

    def test_load_rejects_wrong_format(self, tmp_path: Path):
        p = tmp_path / "wrong.json"
        p.write_text(json.dumps({
            "format": "some.other.format",
            "bundle_version": "1.0.0",
        }), encoding="utf-8")
        with pytest.raises(BundleSchemaError):
            Bundle.load(p)

    def test_load_rejects_unsupported_major_version(self, tmp_path: Path):
        p = tmp_path / "v2.json"
        p.write_text(json.dumps({
            "format": BUNDLE_FORMAT_NAME,
            "bundle_version": "2.0.0",
        }), encoding="utf-8")
        with pytest.raises(BundleSchemaError):
            Bundle.load(p)


# ============================================================
# Integrity (tamper detection)
# ============================================================

class TestIntegrity:

    def test_verify_passes_on_fresh_bundle(self):
        b = Bundle.from_state(_sample_state())
        assert b.verify() is True

    def test_verify_fails_when_findings_tampered(self):
        b = Bundle.from_state(_sample_state())
        # Mutate a finding without updating the integrity hash.
        b.doc["findings"][0]["title"] = "TAMPERED"
        with pytest.raises(BundleIntegrityError):
            b.verify()

    def test_verify_fails_on_missing_integrity_block(self):
        b = Bundle.from_state(_sample_state())
        b.doc.pop("integrity")
        with pytest.raises(BundleIntegrityError):
            b.verify()

    def test_verify_fails_on_wrong_hash_alg(self):
        b = Bundle.from_state(_sample_state())
        b.doc["integrity"]["hash_alg"] = "md5"
        with pytest.raises(BundleIntegrityError):
            b.verify()

    def test_generated_at_does_not_affect_hash(self):
        """Two bundles built from the same state at different times
        should produce the same content_hash (we exclude generated_at)."""
        s = _sample_state()
        b1 = build_bundle_from_state(s)
        b2 = build_bundle_from_state(s)
        # The two bundles may differ in generated_at but their content
        # hashes must be identical.
        assert b1["integrity"]["content_hash"] == b2["integrity"]["content_hash"]


# ============================================================
# Sign / verify (only if cryptography is available)
# ============================================================
# We mark only the signature class as conditionally-skipped so the rest
# of the module still runs in environments without ``cryptography``.

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: F401
        Ed25519PrivateKey,
    )
    _HAVE_CRYPTOGRAPHY = True
except Exception:
    _HAVE_CRYPTOGRAPHY = False


@pytest.mark.skipif(not _HAVE_CRYPTOGRAPHY,
                     reason="cryptography not installed; sign/verify is opt-in")
class TestSignature:

    def test_sign_then_verify_succeeds(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        priv = Ed25519PrivateKey.generate()
        priv_bytes = priv.private_bytes_raw()
        b = Bundle.from_state(_sample_state())
        b.sign(priv_bytes)
        assert b.doc["integrity"]["signature"]
        assert b.doc["integrity"]["public_key"]
        assert b.verify(require_signature=True) is True

    def test_signed_bundle_tamper_detected(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        priv = Ed25519PrivateKey.generate()
        b = Bundle.from_state(_sample_state())
        b.sign(priv.private_bytes_raw())
        b.doc["findings"][0]["title"] = "TAMPERED"
        with pytest.raises(BundleIntegrityError):
            b.verify()


# ============================================================
# Findings helpers
# ============================================================

class TestFindings:

    def test_critical_filter(self):
        b = Bundle.from_state(_sample_state())
        crits = b.findings().critical()
        assert len(crits) == 1
        assert crits[0]["severity"] == "crit"

    def test_by_method_substring_match(self):
        b = Bundle.from_state(_sample_state())
        iso = b.findings().by_method("iso-forest")
        assert len(iso) == 1
        assert "ISO-FOREST" in iso[0]["method"]

    def test_where_with_tuple_values(self):
        b = Bundle.from_state(_sample_state())
        non_info = b.findings().where(severity=("crit", "warn"))
        assert len(non_info) == 2

    def test_chained_filters(self):
        b = Bundle.from_state(_sample_state())
        chained = b.findings().critical().by_method("iso-forest")
        assert len(chained) == 1

    def test_count_by_severity(self):
        b = Bundle.from_state(_sample_state())
        counts = b.findings().count_by_severity()
        assert counts == {"crit": 1, "warn": 1, "info": 1}

    def test_methods_distinct_in_first_appearance_order(self):
        b = Bundle.from_state(_sample_state())
        methods = b.findings().methods()
        assert methods == ["ISO-FOREST + ROBUST-Z", "Granger", "matrix_profile"]


# ============================================================
# Forecast / SystemModel views
# ============================================================

class TestForecastView:

    def test_peak_finds_max_value(self):
        b = Bundle.from_state(_sample_state())
        peak = b.forecast().peak()
        assert peak is not None
        assert peak["value"] == 1.49

    def test_peak_within_horizon(self):
        b = Bundle.from_state(_sample_state())
        # Only step 1 is within horizon=1
        peak = b.forecast().peak(horizon_hours=1)
        assert peak["step"] == 1

    def test_view_on_empty_doc(self):
        v = ForecastView({})
        assert v.available is False
        assert v.points() == []
        assert v.peak() is None


class TestSystemModelView:

    def test_entity_lookup(self):
        b = Bundle.from_state(_sample_state())
        sm = b.system_model()
        ent = sm.entity("vibration_g")
        assert ent is not None
        assert ent["role"] == "state"

    def test_missing_entity_returns_none(self):
        b = Bundle.from_state(_sample_state())
        sm = b.system_model()
        assert sm.entity("nope") is None


# ============================================================
# Fabricated count (per Aurora's contract)
# ============================================================

class TestFabricatedCount:

    def test_no_fabrication_on_normal_findings(self):
        b = Bundle.from_state(_sample_state())
        assert b.fabricated_count == 0

    def test_flag_counts_as_fabricated(self):
        state = _sample_state()
        state["findings"].append({
            "method": "ISO-FOREST",
            "severity": "info",
            "fabricated": True,
        })
        b = Bundle.from_state(state)
        assert b.fabricated_count == 1

    def test_llm_generated_method_counts_as_fabricated(self):
        state = _sample_state()
        state["findings"].append({
            "method": "llm_generated",
            "severity": "info",
        })
        b = Bundle.from_state(state)
        assert b.fabricated_count == 1

    def test_missing_claim_id_does_NOT_count_as_fabricated(self):
        """Regression: the original POLISH W2d logic flagged missing
        optional fields as fabrication; the corrected logic does not."""
        state = _sample_state()
        for f in state["findings"]:
            f.pop("claim_id", None)
            f.pop("rank", None)
        b = Bundle.from_state(state)
        assert b.fabricated_count == 0

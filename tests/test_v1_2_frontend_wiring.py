"""
v1.2 frontend wiring audit — pins the structural invariants that the
v1.2 surfaces rely on so accidental refactors break loudly.

Covers:
    * Data-quality pill (the "7th-lens" preflight chip)
    * The 7 v1.2 extended-method tiles in the ADVANCED METHODS grid
    * The streaming control button + popover wired to /api/stream/*
    * state_builder.extended_methods projection (per-method tile data)

These are intentionally static-grep + import-level checks. Heavier
end-to-end behaviour is exercised by test_streaming.py and the studio
integration tests; this file only catches the "did we break the UI
contract?" class of regression.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_HTML = ROOT / "frontend" / "index.html"


def _read() -> str:
    return FRONTEND_HTML.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# Data-quality pill
# ----------------------------------------------------------------------

class TestDataQualityPill:

    def test_pill_dom_present(self):
        html = _read()
        assert 'id="riDataQuality"' in html, "data-quality pill missing from DOM"
        assert 'id="riDataQualityIcon"' in html
        assert 'id="riDataQualityLbl"' in html

    def test_panel_dom_present(self):
        html = _read()
        assert 'id="dataQualityPanel"' in html
        assert 'id="dqpBody"' in html
        assert 'id="dqpSummary"' in html

    def test_controller_wired(self):
        html = _read()
        assert "window.__refreshDataQuality" in html
        assert "window.__toggleDataQualityPanel" in html
        # Pill uses run_id (not just pathname) for cache key so re-runs
        # re-fetch. Without this fix the pill stales on the first run.
        assert "function currentRunKey()" in html or "currentRunKey" in html

    def test_pending_and_unknown_states(self):
        # The pill must be visible (with a neutral label) even before
        # preflight returns — users were missing it because the old
        # version stayed hidden until ok=true came back.
        html = _read()
        assert "showPending" in html
        assert "showUnknown" in html
        assert "checking data" in html


# ----------------------------------------------------------------------
# v1.2 ADVANCED METHODS tile renderers
# ----------------------------------------------------------------------

class TestExtendedMethodTiles:

    def test_state_extended_methods_consumed(self):
        # The frontend must read state.extended_methods.per_method to
        # render the new tiles.
        html = _read()
        assert "state.extended_methods" in html
        assert "ext.per_method" in html

    def test_all_seven_methods_in_renderer(self):
        # The renderer block has a branch per method label.
        html = _read()
        for name in ("var", "dtw", "bocpd", "robust_pca",
                      "emd", "kalman", "spectral_entropy"):
            assert f"name === '{name}'" in html, (
                f"renderer branch for {name!r} missing"
            )

    def test_method_status_legend(self):
        # ran / skipped / failed / pending are the four states a tile
        # can show. Each must be present in the status mapping.
        html = _read()
        for label in ("'ran'", "'skipped'", "'failed'", "'pending'"):
            assert label in html, f"status legend missing {label}"


# ----------------------------------------------------------------------
# Streaming control surface
# ----------------------------------------------------------------------

class TestStreamingUI:

    def test_toggle_button_present(self):
        html = _read()
        assert 'id="streamToggleBtn"' in html
        assert 'id="streamToggleLbl"' in html
        assert 'id="streamToggleIcon"' in html

    def test_popover_inputs_present(self):
        html = _read()
        for field_id in ("streamPathInput", "streamGlobInput",
                          "streamPollInput", "streamStartBtn",
                          "streamStopBtn", "streamStatusState",
                          "streamEventCount", "streamLastEvent"):
            assert f'id="{field_id}"' in html, (
                f"streaming popover field {field_id!r} missing"
            )

    def test_endpoints_called(self):
        html = _read()
        for endpoint in ("/api/stream/start",
                          "/api/stream/stop",
                          "/api/stream/status",
                          "/api/stream/events"):
            assert endpoint in html, f"streaming UI doesn't call {endpoint}"

    def test_sse_event_kinds_handled(self):
        html = _read()
        for kind in ("window_advanced", "new_finding",
                      "regime_changed", "heartbeat"):
            assert f"'{kind}'" in html, (
                f"streaming controller doesn't subscribe to {kind!r}"
            )


# ----------------------------------------------------------------------
# state_builder.extended_methods projection
# ----------------------------------------------------------------------

class TestStateBuilderExtendedMethods:

    def test_build_extended_methods_handles_missing_doc(self):
        from fantasyai.aurora.state_builder import _build_extended_methods
        out = _build_extended_methods(None)
        assert out["available"] is False

    def test_build_extended_methods_produces_seven_tiles(self):
        from fantasyai.aurora.state_builder import _build_extended_methods
        doc = {
            "findings": [
                {"method": "var",
                  "title": "VAR(1) on 2 vars; strongest A→B",
                  "description": "desc",
                  "severity": "info",
                  "confidence": 0.75,
                  "claim_id": "var-0000",
                  "evidence": {"status": "fit", "chosen_lag": 1,
                                "n_obs": 50, "n_vars": 2}},
            ],
            "per_method": {"var": {"status": "fit", "elapsed_s": 0.1}},
            "n_methods_run": 7, "n_findings": 1,
        }
        out = _build_extended_methods(doc)
        assert out["available"] is True
        assert out["n_methods_run"] == 7
        # All 7 methods always have a tile entry — even when not run
        # (status='missing') — so the UI can show a stable grid.
        assert set(out["per_method"].keys()) == {
            "var", "dtw", "bocpd", "robust_pca",
            "emd", "kalman", "spectral_entropy",
        }
        assert out["per_method"]["var"]["status"] == "fit"
        assert out["per_method"]["dtw"]["status"] == "missing"

    def test_evidence_whitelist_drops_large_blobs(self):
        from fantasyai.aurora.state_builder import _trim_evidence
        ev = {
            "status": "fit",
            "n_obs": 100,
            "huge_array": list(range(10_000)),  # not whitelisted
            "imf_stats": [{"imf_index": 0, "energy": 1.0}] * 200,
        }
        out = _trim_evidence(ev)
        assert "huge_array" not in out
        # imf_stats is whitelisted but should be capped at 64 entries.
        assert len(out["imf_stats"]) == 64
        assert out["n_obs"] == 100

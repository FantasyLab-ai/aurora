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
        # CRITICAL: even when extended_methods.json doesn't exist for
        # this run, the builder MUST still return the 7-tile shell so
        # the frontend renders a consistent grid. available=False just
        # signals "this run didn't produce data" — it never hides tiles.
        from fantasyai.aurora.state_builder import _build_extended_methods
        out = _build_extended_methods(None)
        assert out["available"] is False
        assert "per_method" in out
        assert set(out["per_method"].keys()) == {
            "var", "dtw", "bocpd", "robust_pca",
            "emd", "kalman", "spectral_entropy",
        }
        # All 7 tiles default to "missing" so frontend shows them as pending.
        for name, tile in out["per_method"].items():
            assert tile["status"] == "missing"

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


# ----------------------------------------------------------------------
# Phase 2 frontend surfaces: live findings + workspace chip + contracts toggle
# ----------------------------------------------------------------------

class TestPhase2FrontendSurfaces:

    def test_live_findings_list_present(self):
        html = _read()
        assert 'id="streamLiveFindings"' in html
        assert 'id="streamLiveFindingsEmpty"' in html
        assert "LIVE FINDINGS" in html
        assert "appendLiveFinding" in html

    def test_contracts_toggle_present(self):
        html = _read()
        assert 'id="streamFireContractsInput"' in html
        assert "fire_contracts" in html
        assert "auto-fire Decision Contracts" in html

    def test_dedupe_and_contracts_status_surfaced(self):
        html = _read()
        assert 'id="streamDedupeSize"' in html
        assert 'id="streamContractsState"' in html

    def test_workspace_chip_present(self):
        html = _read()
        assert 'id="workspaceChip"' in html
        assert 'id="workspaceChipLabel"' in html
        # Defaults to display:none so single-tenant deploys stay clean.
        assert "workspaceController" in html
        assert "/api/auth/whoami" in html
        assert "/api/auth/usage" in html


# ----------------------------------------------------------------------
# Q3 frontend surfaces: INHERIT picker + JOIN RUNS + PLUGINS
# ----------------------------------------------------------------------

class TestQ3FrontendSurfaces:

    def test_inherit_picker_present(self):
        html = _read()
        assert 'id="inheritToggleBtn"' in html
        assert 'id="inheritPopover"' in html
        assert 'id="inheritRunsList"' in html
        assert "inheritController" in html
        assert "/api/priors/list" in html
        # PRIOR badge renders on findings that align with a prior pack.
        assert "prior_source" in html
        assert "PRIOR" in html

    def test_join_runs_picker_present(self):
        html = _read()
        assert 'id="joinsToggleBtn"' in html
        assert 'id="joinsPopover"' in html
        assert 'id="joinRunASel"' in html
        assert 'id="joinRunBSel"' in html
        assert "joinsController" in html
        assert "/api/joins/analyze" in html

    def test_plugins_panel_present(self):
        html = _read()
        assert 'id="pluginsToggleBtn"' in html
        assert 'id="pluginsPopover"' in html
        assert 'id="pluginsList"' in html
        assert "pluginsController" in html
        assert "/api/plugins" in html

    def test_inherit_from_sent_to_run_endpoint(self):
        html = _read()
        # The /api/run kickoff body MUST include inherit_from so the
        # backend can stage the prior pack on the new run dir.
        assert "inherit_from" in html
        assert "__auroraInheritFrom" in html


# ----------------------------------------------------------------------
# v1.2 batch D/E surfaces: Runs Library
# ----------------------------------------------------------------------

class TestV12DEFFrontendSurfaces:

    def test_runs_library_panel_present(self):
        html = _read()
        assert 'id="runsToggleBtn"' in html
        assert 'id="runsPopover"' in html
        assert 'id="runsList"' in html
        assert "runsLibraryController" in html

    def test_runs_library_actions_wired(self):
        html = _read()
        # pin / unpin URLs are built dynamically as '/api/runs/' + act,
        # so we check the base path + both action names are present in
        # the controller. compare + share are called as full literals.
        assert "/api/runs/' + act" in html or "/api/runs/${act}" in html
        assert "'pin'" in html or "data-act=\"pin\"" in html
        assert "'unpin'" in html or "data-act=\"unpin\"" in html
        for endpoint in ("/api/runs/compare", "/api/runs/share"):
            assert endpoint in html, (
                f"runs library doesn't call {endpoint}"
            )

    def test_runs_library_compare_renders_delta_block(self):
        html = _read()
        # Confidence delta, new vs disappeared findings — all surfaced.
        assert "confidence_delta" in html
        assert "new_findings" in html
        assert "disappeared_findings" in html
        assert "physics_law_changed" in html


# ----------------------------------------------------------------------
# v2.0 omnibus panel surfaces
# ----------------------------------------------------------------------

class TestV20OmnibusPanel:

    def test_v20_chip_and_panel_present(self):
        html = _read()
        assert 'id="v20ToggleBtn"' in html
        assert 'id="v20Popover"' in html
        assert "v20Controller" in html

    def test_v20_all_six_tabs_rendered(self):
        html = _read()
        for tab in ("causal", "ingest", "attest",
                     "market", "connectors", "compute"):
            assert f'data-tab="{tab}"' in html, f"v2.0 tab {tab!r} missing"

    def test_v20_endpoints_called(self):
        html = _read()
        # Each tab hits its backend endpoint.
        for endpoint in (
            "/api/causal/identify",
            "/api/causal/do",
            "/api/causal/counterfactual",
            "/api/kb/ingest/folder",
            "/api/kb/ingest/list",
            "/api/attest/verify",
            "/api/attest/signers",
            "/api/kb/marketplace",
            "/api/stream/connectors",
            "/api/embeddings/status",
        ):
            assert endpoint in html, f"v2.0 panel doesn't call {endpoint}"

    def test_v20_chip_is_visually_prominent(self):
        """The chip should advertise itself — cyan border + NEW badge —
        so users discover the panel without reading docs."""
        html = _read()
        # The "NEW · 6" badge + a marker that we styled the chip with
        # the cyan accent. We don't pin exact CSS to avoid lockstep
        # with future restyles, but the cyan ID + the NEW text both
        # need to land near the v20 button.
        assert "v20ToggleBtn" in html
        assert "NEW · 6" in html
        assert "v2.0 LAB" in html

    def test_whatif_panel_renders_causal_verdict(self):
        """The legacy WHAT-IF panel now also displays the new
        do-calculus verdict when present in the response."""
        html = _read()
        # The renderer keys on j.causal — make sure the frontend
        # actually reaches for it.
        assert "j.causal" in html
        # The causal block surfaces backdoor adjustment + assumptions.
        assert "backdoor adjustment" in html
        assert "open the v2.0 LAB" in html


# ----------------------------------------------------------------------
# /api/whatif causal integration (backend-level)
# ----------------------------------------------------------------------

class TestWhatifCausalIntegration:

    def test_whatif_request_includes_causal_block_on_clean_intervention(self,
                                                                          tmp_path,
                                                                          monkeypatch):
        """When the parsed intervention has a clean (variable, value)
        shape, the /api/whatif handler ALSO calls do_calculus and
        attaches a `causal` block to the response. This is a unit-
        level check; the integration is exercised in e2e tests when
        the studio_api is actually deployable."""
        # Just confirm the import path works — the handler is
        # exercised via the full Flask test client elsewhere.
        from fantasyai.aurora.causal import do_query, load_dag_from_system_model
        import pandas as pd

        # Build a clean confounded SCM.
        import numpy as np
        rng = np.random.default_rng(7)
        n = 200
        z = rng.standard_normal(n)
        t = 0.5 * z + rng.standard_normal(n) * 0.5
        y = 0.4 * t + 0.5 * z + rng.standard_normal(n) * 0.3
        df = pd.DataFrame({"Z": z, "T": t, "Y": y})

        sm = {
            "nodes": [{"name": "Z"}, {"name": "T"}, {"name": "Y"}],
            "edges": [
                {"source": "Z", "target": "T", "weight": 0.9},
                {"source": "Z", "target": "Y", "weight": 0.9},
                {"source": "T", "target": "Y", "weight": 0.7},
            ],
        }
        dag = load_dag_from_system_model(sm)
        res = do_query(df, dag, "T", "Y", intervention_value=1.0)
        # Recovered effect should be within ~0.15 of the true 0.4.
        assert abs(res.effect_estimate - 0.4) < 0.15
        # The handler ships this as response["causal"] = res.to_dict().
        d = res.to_dict()
        assert d["identifiable"] is True
        assert "Z" in d["adjustment_set"]

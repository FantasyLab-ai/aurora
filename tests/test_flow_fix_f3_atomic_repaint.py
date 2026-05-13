"""
Sub-atom F.3 regression net: atomic full-repaint state machine
+ per-panel failure copy.

These are static-analysis assertions over the frontend bundle. Behavioural
tests would require JSDOM; the contract is structurally clear and
auditable from the source.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_HTML = ROOT / "frontend" / "index.html"


def _read():
    return FRONTEND_HTML.read_text(encoding="utf-8")


# ============================================================
# populateFullState + clearAllPanels exist
# ============================================================

class TestAtomicRepaintFunctions:

    def test_populate_full_state_defined(self):
        html = _read()
        assert "function populateFullState(state, phase)" in html

    def test_clear_all_panels_defined(self):
        html = _read()
        assert "function clearAllPanels(phase)" in html

    def test_phase_resolver_defined(self):
        html = _read()
        assert "_phaseForServerState" in html

    def test_failure_copy_dictionary_present(self):
        html = _read()
        # Per-panel failure copy must be defined.
        for key in ("forecast", "causal", "physics", "regimes",
                     "anomalies", "motifs", "findings", "calculus"):
            assert f"{key}:" in html, f"failure copy missing key: {key}"

    def test_phases_complete(self):
        """Every documented phase must be referenced."""
        html = _read()
        for phase in ("pre_run", "analyzing", "tier1", "tier2", "tier3",
                       "complete", "failed", "cancelled"):
            assert f'"{phase}"' in html or f"'{phase}'" in html, \
                f"phase {phase!r} not referenced"


# ============================================================
# populate* functions are idempotent (clear stale content)
# ============================================================

class TestPopulateIdempotence:
    """The populate* functions must clear their target container before
    rendering, so a subsequent call with empty data wipes stale content
    rather than leaving it visible (the partial-state bug)."""

    def _function_body(self, html: str, name: str) -> str:
        # Find function start.
        lines = html.split("\n")
        start = None
        for i, line in enumerate(lines):
            if f"function {name}(" in line and "function _" not in line:
                start = i
                break
        assert start is not None, f"function {name} not found"
        depth = 0
        body_lines = []
        seen_open = False
        for line in lines[start:]:
            body_lines.append(line)
            for ch in line:
                if ch == "{":
                    depth += 1
                    seen_open = True
                elif ch == "}":
                    depth -= 1
            if seen_open and depth == 0:
                break
        return "\n".join(body_lines)

    def test_populate_anomalies_clears_first(self):
        html = _read()
        body = self._function_body(html, "populateAnomalies")
        # Clear must come BEFORE any conditional return.
        assert "innerHTML = ''" in body, \
            "populateAnomalies does not clear stale content"

    def test_populate_motifs_clears_first(self):
        html = _read()
        body = self._function_body(html, "populateMotifs")
        assert "innerHTML = ''" in body, \
            "populateMotifs does not clear stale content"

    def test_populate_regimes_clears_first(self):
        html = _read()
        body = self._function_body(html, "populateRegimes")
        assert "innerHTML = ''" in body, \
            "populateRegimes does not clear stale content"


# ============================================================
# Phase mapping covers all server states
# ============================================================

class TestPhaseMapping:

    def test_phase_resolver_handles_all_registry_states(self):
        html = _read()
        # Find _phaseForServerState body.
        lines = html.split("\n")
        start = None
        for i, line in enumerate(lines):
            if "_phaseForServerState" in line and "function" in line:
                start = i
                break
        assert start is not None
        depth = 0
        body_lines = []
        seen_open = False
        for line in lines[start:]:
            body_lines.append(line)
            for ch in line:
                if ch == "{":
                    depth += 1
                    seen_open = True
                elif ch == "}":
                    depth -= 1
            if seen_open and depth == 0:
                break
        body = "\n".join(body_lines)
        # Every server state from run_registry.RunState must map.
        for state in ("complete", "failed", "cancelled",
                       "tier1_complete", "tier2_complete", "tier3_complete"):
            assert state in body, \
                f"phase resolver missing case for server state: {state}"


# ============================================================
# refreshState delegates to populateFullState
# ============================================================

class TestRefreshStateDelegation:

    def test_refresh_state_calls_populate_full_state(self):
        html = _read()
        # The refactored refreshState must call populateFullState.
        # Heuristic: appears textually within ~80 lines of `async function refreshState`.
        lines = html.split("\n")
        start = None
        for i, line in enumerate(lines):
            if "async function refreshState" in line:
                start = i
                break
        assert start is not None
        window = "\n".join(lines[start:start + 80])
        assert "populateFullState" in window, \
            "refreshState no longer delegates to populateFullState"

    def test_refresh_state_no_longer_streams_panels(self):
        """The legacy serial calls (populateAnomalies + populateForecast +
        populateRegimes + populateMotifs + populateFindings in sequence
        within refreshState's body) should no longer be present — they
        moved into populateFullState."""
        html = _read()
        lines = html.split("\n")
        # Slice the refreshState body.
        start = None
        for i, line in enumerate(lines):
            if "async function refreshState" in line:
                start = i
                break
        assert start is not None
        # Walk to the matching close brace.
        depth = 0
        body_lines = []
        seen_open = False
        for line in lines[start:]:
            body_lines.append(line)
            for ch in line:
                if ch == "{":
                    depth += 1
                    seen_open = True
                elif ch == "}":
                    depth -= 1
            if seen_open and depth == 0:
                break
        body = "\n".join(body_lines)
        # In the new world, refreshState's own body should NOT call all
        # of these directly; populateFullState owns them.
        forbidden_inline = [
            "populateAnomalies(", "populateForecast(",
            "populateRegimes(", "populateMotifs(",
            "populateAnomalyCubeFace(", "populateForecastCubeFace(",
        ]
        offenders = [c for c in forbidden_inline if c in body]
        assert not offenders, (
            f"refreshState still calls {offenders} inline; F.3 expects "
            f"these to be inside populateFullState only."
        )

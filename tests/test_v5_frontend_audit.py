"""
System Graph v5 frontend audit — pins the structural invariants that the
spacetime + phase-space rendering relies on.

These are static-grep assertions over frontend/index.html: cheap, fast,
and break loudly if the v5 surface gets accidentally refactored away.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_HTML = ROOT / "frontend" / "index.html"


def _read():
    return FRONTEND_HTML.read_text(encoding="utf-8")


# ============================================================
# View-mode toggle
# ============================================================

class TestViewModeToggle:

    def test_three_view_modes_present(self):
        html = _read()
        assert 'data-view="legacy"' in html
        assert 'data-view="spacetime"' in html
        assert 'data-view="phase"' in html

    def test_default_is_legacy(self):
        html = _read()
        # The legacy mode button carries the active class out of the box.
        # Match either attribute order (class before/after data-view).
        m = (re.search(r'class="[^"]*active[^"]*"[^>]*data-view="legacy"', html)
             or re.search(r'data-view="legacy"[^>]*class="[^"]*active', html))
        assert m, "legacy view-mode is not the default active button"

    def test_phase_button_disabled_when_no_data(self):
        # The wiring code must check phase_space presence and toggle disabled.
        html = _read()
        assert "phaseBtn.disabled = !ps" in html


# ============================================================
# Spacetime SVG + present slice + axis labels
# ============================================================

class TestSpacetimeMarkup:

    def test_three_svg_layers(self):
        html = _read()
        assert 'id="systemGraphSvg"' in html               # legacy
        assert 'id="systemGraphSpacetimeSvg"' in html      # spacetime
        assert 'id="systemGraphPhaseSvg"' in html          # phase

    def test_present_slice_and_label(self):
        html = _read()
        assert 'id="sgPresentSlice"' in html
        assert 'id="sgPresentLabel"' in html
        # Pulse animation is defined.
        assert "@keyframes sgPresentPulse" in html

    def test_time_axis_labels(self):
        html = _read()
        assert 'id="sgTimeAxisLabels"' in html
        for lbl in ("−4 HOURS", "−1 HOUR", "+2 HOURS", "+6 HOURS"):
            assert lbl in html


# ============================================================
# Side panels (spacetime readout + context)
# ============================================================

class TestSpacetimeSidePanels:

    def test_readout_panel_exists(self):
        html = _read()
        assert 'id="sgSpacetimeReadout"' in html
        for sect in ("PAST · WORLDLINE", "NOW", "FUTURE · CONE"):
            assert sect in html

    def test_context_panel_exists(self):
        html = _read()
        assert 'id="sgSpacetimeContext"' in html
        assert "SPACETIME CONTEXT" in html


# ============================================================
# Time scrubber + SIMULATE controls
# ============================================================

class TestScrubberAndSimulate:

    def test_scrubber_dom(self):
        html = _read()
        assert 'id="sgTimeScrubber"' in html
        assert 'id="sgTimeTrack"' in html
        assert 'id="sgTimeMarker"' in html

    def test_sim_buttons(self):
        html = _read()
        for action in ("data-sim=\"back\"", "data-sim=\"play\"",
                        "data-sim=\"fwd\"", "data-sim=\"reset\""):
            assert action in html, f"missing scrubber control: {action}"

    def test_simulate_pauses_when_leaving_spacetime(self):
        html = _read()
        assert "_v5SimulatePause" in html
        # Ensure SIMULATE pauses on view-mode change.
        assert "_v5SimulatePause()" in html


# ============================================================
# Causal lag arcs + resonance halos CSS
# ============================================================

class TestLagsAndResonances:

    def test_causal_lag_class_styled(self):
        html = _read()
        assert ".sg-causal-lag" in html
        assert ".sg-causal-lag-label" in html

    def test_resonance_animation_keyframes(self):
        html = _read()
        assert "@keyframes sgResonateRipple" in html
        assert ".sg-resonance-ring" in html


# ============================================================
# Phase space rendering + axis labels
# ============================================================

class TestPhaseSpaceRender:

    def test_phase_axis_labels(self):
        html = _read()
        assert ".sg-phase-axis-label" in html
        assert ".sg-phase-attractor-label" in html

    def test_renderer_function_present(self):
        html = _read()
        assert "_v5RenderPhaseSpace" in html

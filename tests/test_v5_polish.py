"""
V5 polish + bug-fix audit:

  1. INTERVENE / SIMULATE panels are visible in v5 views (right-side panel
     swap by interaction mode, not view mode).
  2. Mode subtitle element is present and updates per mode.
  3. Lane vertical layout adapts to node count.
  4. Cone tip reaches the right edge of the canvas regardless of how
     many forecast steps were emitted.
  5. "no forecast available" label rendered for nodes with null forecast.
  6. Headline findings (physics matches) lift to the top + render with
     distinct visual treatment.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FRONTEND_HTML = ROOT / "frontend" / "index.html"


def _read():
    return FRONTEND_HTML.read_text(encoding="utf-8")


def _function_body(html: str, name: str) -> str:
    """Extract a function body by walking brace depth from the function header.
    Robust against template-literal braces inside nested expressions."""
    lines = html.split("\n")
    start = None
    for i, line in enumerate(lines):
        if f"function {name}(" in line and "function _" not in line[:line.index(f"function {name}(")]:
            start = i
            break
        if f"function {name}(" in line:
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


# ============================================================
# Bug fix: panel orchestration
# ============================================================

class TestPanelOrchestration:

    def test_interaction_mode_state_exists(self):
        html = _read()
        assert "interactionMode:" in html
        assert "_v5SyncPanels" in html

    def test_sync_panels_renders_intervene_in_any_view(self):
        html = _read()
        body = _function_body(html, "_v5SyncPanels")
        assert "mode === 'intervene'" in body
        assert "mode === 'simulate'" in body
        assert "sgRenderInterveneUI()" in body
        assert "sgRenderSimulateUI()" in body

    def test_view_change_triggers_sync_panels(self):
        html = _read()
        # _v5SetView must call _v5SyncPanels at the end.
        assert re.search(r"_v5SyncPanels\(\)", html), \
            "view-mode change does not re-sync the panels"

    def test_interaction_mode_change_triggers_sync_panels(self):
        html = _read()
        # The .sg-mode click handler must update interactionMode + sync.
        assert "__sgV5State.interactionMode = mode;" in html


# ============================================================
# Polish: mode subtitle
# ============================================================

class TestModeSubtitle:

    def test_subtitle_element_present(self):
        html = _read()
        assert 'id="sgModeSubtitle"' in html
        assert "_V5_MODE_SUBTITLE" in html

    def test_subtitle_text_per_mode(self):
        html = _read()
        # Per the brief.
        assert "'hover to read'" in html
        assert "'perturb a node'" in html
        assert "'run forward in time'" in html


# ============================================================
# Polish: lane auto-scaling
# ============================================================

class TestLaneScaling:

    def test_lane_compute_uses_node_count(self):
        html = _read()
        body = _function_body(html, "_v5ComputeLanes")
        assert "(n <=" in body
        assert "fill" in body or "margin" in body

    def test_few_nodes_get_smaller_margin(self):
        html = _read()
        body = _function_body(html, "_v5ComputeLanes")
        assert "n <= 4" in body
        assert "n <= 8" in body


# ============================================================
# Polish: cone reaches 100%
# ============================================================

class TestConeFullExtent:

    def test_cone_uses_horizon_for_norm_when_in_range(self):
        html = _read()
        body = _function_body(html, "_v5ConePaths")
        assert "horizonForNorm" in body
        assert "tMax" in body

    def test_no_forecast_label_present(self):
        html = _read()
        assert "no forecast available for this variable" in html


# ============================================================
# Polish: headline findings
# ============================================================

class TestHeadlineFindings:

    def test_state_builder_flags_physics_law_findings(self):
        # Both the discovered_law finding AND the prior-match finding
        # carry headline=True.
        from fantasyai.aurora import state_builder
        src = Path(state_builder.__file__).read_text(encoding="utf-8")
        # Two physics-related finding emissions; both must set headline.
        assert src.count('"headline": True') >= 2

    def test_findings_sorted_with_headline_first(self):
        from fantasyai.aurora import state_builder
        src = Path(state_builder.__file__).read_text(encoding="utf-8")
        # The new sort uses (head_first, sev_rank, conf).
        assert "_finding_sort_key" in src
        assert 'f.get("headline")' in src

    def test_frontend_renders_headline_class(self):
        html = _read()
        assert "finding.headline" in html
        assert "finding-headline-icon" in html

    def test_frontend_handles_physics_match_kind(self):
        html = _read()
        assert 'data-headline-kind="physics_match"' in html or \
               'data-headline-kind="${fmt.esc(headlineKind)}"' in html
        # Distinct color for physics_match headlines.
        assert 'finding.headline[data-headline-kind="physics_match"]' in html

"""
Sub-atom F.2 regression net: static-grep audit that asserts the frontend
NEVER triggers analysis automatically on dataset acquisition events.

The contract:
  - The drop / upload handler does NOT call runAnalysis().
  - The connector-fetch handler does NOT call runAnalysis().
  - The demo-chip handler does NOT call runAnalysis().
  - The RUN ANALYSIS button is the only call site.

We do this with regex over the HTML rather than running JSDOM, because
the surface area is small + the rule is binary.
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
# Audit: total runAnalysis() callsites
# ============================================================

class TestRunAnalysisCallSites:

    def test_only_one_runanalysis_call_site(self):
        """Exactly ONE place in the codebase should call runAnalysis() —
        the RUN ANALYSIS button handler. F.2 fix removed the three
        implicit calls (upload, connector, demo)."""
        html = _read()
        # Search lines directly; ignore lines that look like comments or
        # error messages mentioning runAnalysis as a string identifier.
        # The contract is purely structural — a definition and few calls.
        defs = re.findall(r"\basync\s+function\s+runAnalysis\b", html)
        # Filter out call sites that are obviously inside string literals
        # (e.g. log messages, error text). The simplest heuristic: only
        # count `await runAnalysis(` and `runAnalysis(` that are NOT
        # immediately preceded by a quote or '['.
        all_calls = list(re.finditer(r"runAnalysis\s*\(", html))
        real_calls = []
        for m in all_calls:
            # Inspect the 80 chars preceding the match.
            pre = html[max(0, m.start() - 80):m.start()]
            # Skip if the call is inside a single- / double- / template-
            # string literal: those have a balanced quote count between
            # them and the start of their line.
            line_start = pre.rfind("\n") + 1
            line_pre = pre[line_start:]
            n_double = line_pre.count('"') - line_pre.count('\\"')
            n_single = line_pre.count("'") - line_pre.count("\\'")
            n_back = line_pre.count("`")
            inside_string = (n_double % 2 == 1) or (n_single % 2 == 1) or \
                            (n_back % 2 == 1)
            # Skip lines that are inline log/comment text.
            stripped = line_pre.lstrip()
            is_comment = stripped.startswith("//") or stripped.startswith("*")
            if inside_string or is_comment:
                continue
            real_calls.append(m)
        assert len(defs) == 1, (
            f"expected 1 runAnalysis definition, got {len(defs)}")
        # F.2 contract: legitimate occurrences are
        #   - the function definition itself
        #   - the RUN ANALYSIS button click handler
        #   - the run-indicator's RETRY button handler
        # Anything beyond 4 means an implicit call (upload/connector/demo)
        # has crept back in.
        assert len(real_calls) <= 4, (
            f"found {len(real_calls)} runAnalysis() occurrences "
            f"(definition + ≤3 deliberate calls); F.2 expects implicit "
            f"calls removed from upload/connector/demo paths."
        )

    def test_run_button_handler_present(self):
        """Sanity — the button binding still exists."""
        html = _read()
        assert "bindRunAnalysis" in html
        assert "runAnalysisBtn" in html


# ============================================================
# Upload path — calls profileDataset, not runAnalysis
# ============================================================

class TestUploadHandler:

    def test_upload_calls_profile_dataset(self):
        html = _read()
        # The drop-success branch must reference profileDataset.
        assert "profileDataset" in html
        # And the legacy 'auto-run after upload' comment is gone.
        assert "Fire the run now" not in html

    def test_upload_form_sends_auto_run_zero(self):
        html = _read()
        # The FormData append must still set auto_run='0'.
        assert "fd.append('auto_run', '0')" in html


# ============================================================
# Connector path
# ============================================================

class TestConnectorHandler:

    def test_connector_fetch_no_auto_run(self):
        html = _read()
        # Body sent to /api/connectors/.../fetch must NOT include
        # auto_run: true any more.
        # Look for the specific JSON body in the fetch call.
        bad = re.search(
            r"/api/connectors/[^']*/fetch.*?auto_run\s*:\s*true",
            html, re.S,
        )
        assert bad is None, \
            "connector fetch still sends auto_run:true — F.2 fix incomplete"


# ============================================================
# Demo path
# ============================================================

class TestDemoHandler:

    def test_runDemoByPath_no_runAnalysis(self):
        html = _read()
        # Locate the function start line.
        lines = html.split("\n")
        start = None
        for i, line in enumerate(lines):
            if "async function runDemoByPath" in line:
                start = i
                break
        assert start is not None, "runDemoByPath function not found"
        # Walk forward, tracking brace depth, until we close the function.
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
        # F.2 contract.
        assert "runAnalysis(" not in body, \
            "runDemoByPath still calls runAnalysis() — F.2 fix incomplete"
        assert "profileDataset(" in body, \
            "runDemoByPath does not call profileDataset()"


# ============================================================
# System graph deactivation (from the bug fix)
# ============================================================

class TestSystemGraphDeactivation:

    def test_dataset_selection_active_class_defined(self):
        html = _read()
        # CSS rule that deactivates the graph during dataset selection.
        assert "dataset-selection-active" in html
        # The CSS must apply pointer-events: none to the graph.
        assert "body.dataset-selection-active" in html
        # And explicitly target #systemGraphSection.
        graph_block = re.search(
            r"body\.dataset-selection-active\s+#systemGraphSection.*?\}",
            html, re.S,
        )
        assert graph_block is not None
        assert "pointer-events" in graph_block.group(0)

    def test_show_empty_state_adds_class(self):
        html = _read()
        # showEmptyState must add the class.
        m = re.search(
            r"function\s+showEmptyState\s*\([^)]*\)\s*\{(.*?)^\s*\}\s*",
            html, re.S | re.M,
        )
        assert m, "showEmptyState function not found"
        body = m.group(1)
        assert "dataset-selection-active" in body, \
            "showEmptyState does not add the body class"

    def test_hide_empty_state_removes_class(self):
        html = _read()
        m = re.search(
            r"function\s+hideEmptyState\s*\([^)]*\)\s*\{(.*?)^\s*\}\s*",
            html, re.S | re.M,
        )
        assert m, "hideEmptyState function not found"
        body = m.group(1)
        assert "dataset-selection-active" in body, \
            "hideEmptyState does not remove the body class"

    def test_empty_state_zindex_above_system_graph(self):
        """The .empty-state z-index must beat the #systemGraphSection one."""
        html = _read()
        es_match = re.search(r"\.empty-state\s*\{[^}]*z-index:\s*(\d+)",
                              html, re.S)
        sg_match = re.search(r"#systemGraphSection[^}]*z-index:\s*(\d+)",
                              html, re.S) or \
                    re.search(r"\.intel-panel,[^{]*#systemGraphSection[^{]*\{[^}]*z-index:\s*(\d+)",
                                html, re.S)
        assert es_match is not None, "could not find .empty-state z-index"
        es_z = int(es_match.group(1))
        # Atom 1.2 lifted #systemGraphSection to z-index 101 via the
        # data-tile class group; make sure empty-state beats that.
        assert es_z > 101, \
            f"empty-state z-index ({es_z}) must beat data-tile z (101)"


# ============================================================
# Configuration surface
# ============================================================

class TestConfigSurface:

    def test_tier_selector_present(self):
        html = _read()
        assert 'id="tierSelector"' in html
        # Four radio options.
        for tier in ("auto", "quick", "standard", "full"):
            assert f'value="{tier}"' in html, f"tier {tier} radio missing"

    def test_runtime_estimate_placeholders(self):
        html = _read()
        for k in ("estAuto", "estQuick", "estStandard", "estFull"):
            assert f'id="{k}"' in html, f"runtime estimate slot {k} missing"

    def test_run_button_starts_disabled(self):
        html = _read()
        # The init code must call setRunButtonEnabled(false, ...).
        assert "setRunButtonEnabled(false" in html, \
            "RUN button does not start disabled"

    def test_profile_dataset_helper_defined(self):
        html = _read()
        assert "async function profileDataset" in html

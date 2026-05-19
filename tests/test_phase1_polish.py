"""
v0.10 Phase 1 — UI polish audit tests.

These are static-grep assertions over frontend/index.html that lock in
the seven Phase 1 deliverables so they don't silently regress when the
next set of features lands:

    1. Typography: Geist Sans + Geist Mono + Departure Mono loaded;
       VT323 + Major Mono Display removed from default production load
       (only re-introduced via the retro easter-egg).
    2. OKLCH design tokens defined under :root.
    3. Quiet cyberpunk: scanlines moved behind data-retro="full".
    4. Cmd-K command palette: overlay markup + controller + Cmd-K hotkey.
    5. Responsive baseline: media queries for 1440 / 1100 / 800 / 600.
    6. A11y: skip-to-content link + universal :focus-visible ring.
    7. Retro mode detector: ?retro=full URL param wires data-retro="full".
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_HTML = ROOT / "frontend" / "index.html"


def _read() -> str:
    return FRONTEND_HTML.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# 1. Typography
# ----------------------------------------------------------------------

class TestPhase1Typography:

    def test_geist_fonts_loaded(self):
        html = _read()
        # Single Google Fonts <link> pulls Geist + Geist Mono + Departure Mono.
        assert "family=Geist:wght" in html
        assert "family=Geist+Mono:wght" in html
        assert "family=Departure+Mono" in html

    def test_vt323_dropped_from_default_load(self):
        """The default Google Fonts <link> MUST NOT pull VT323 or
        Major Mono Display — those are retro easter-egg only."""
        html = _read()
        # Pull every <link href="...googleapis...> tag from <head>.
        import re
        head_end = html.find("<style>")
        head = html[:head_end]
        font_links = re.findall(
            r'<link[^>]+href="(https://fonts\.googleapis\.com[^"]+)"',
            head,
        )
        # There should be exactly ONE default font link.
        assert font_links, "no Google Fonts <link> found in <head>"
        for url in font_links:
            assert "VT323" not in url, (
                f"VT323 still in default font URL: {url}"
            )
            assert "Major+Mono+Display" not in url, (
                f"Major Mono Display still in default font URL: {url}"
            )

    def test_font_role_tokens_defined(self):
        """Four typography roles: body / mono / chrome / display."""
        html = _read()
        for token in ("--font-body", "--font-mono", "--font-chrome", "--font-display"):
            assert token in html, f"font role token {token!r} missing"

    def test_body_uses_geist_sans(self):
        html = _read()
        # html, body { font-family: var(--font-body); } — Geist Sans is body.
        assert "font-family: var(--font-body);" in html


# ----------------------------------------------------------------------
# 2. OKLCH design tokens
# ----------------------------------------------------------------------

class TestPhase1OklchTokens:

    def test_surface_ramp_defined(self):
        html = _read()
        for token in ("--surface-0:", "--surface-1:", "--surface-2:",
                       "--surface-3:", "--surface-4:"):
            assert token in html, f"surface ramp token {token!r} missing"

    def test_text_ramp_defined(self):
        html = _read()
        for token in ("--text-1:", "--text-2:", "--text-3:",
                       "--text-4:", "--text-5:"):
            assert token in html, f"text ramp token {token!r} missing"

    def test_border_ramp_defined(self):
        html = _read()
        for token in ("--border-1:", "--border-2:", "--border-3:"):
            assert token in html, f"border ramp token {token!r} missing"

    def test_accent_tokens_use_oklch(self):
        html = _read()
        # The identity accent should be OKLCH-derived so the whole
        # theme can be re-skinned by changing 3 vars.
        assert "--accent:        oklch(" in html
        assert "--accent-bright: oklch(" in html
        assert "--accent-soft:" in html

    def test_legacy_aliases_resolve_to_new_tokens(self):
        """The 700+ inline styles that reference --mint / --cyan / --violet
        / --crit / --warn still work because the legacy names are now
        aliased to OKLCH-derived values."""
        html = _read()
        # Search for the alias block.
        assert "--mint:          var(--accent);" in html
        assert "--cyan:          var(--info-1);" in html
        assert "--violet:        var(--action-1);" in html
        assert "--crit:          var(--crit-1);" in html


# ----------------------------------------------------------------------
# 3. Quiet cyberpunk
# ----------------------------------------------------------------------

class TestPhase1QuietCyberpunk:

    def test_scanlines_behind_retro_flag(self):
        """Scanlines must only render when data-retro='full' is set,
        not by default."""
        html = _read()
        assert 'html[data-retro="full"] body::before' in html, (
            "scanlines should be gated behind data-retro='full'"
        )

    def test_no_unconditional_scanlines_block(self):
        """The original 'body::before' scanline block must be gone."""
        html = _read()
        # body::before should ONLY appear under the retro selector now.
        # Count: should match the 'html[data-retro="full"] body::before' line.
        body_before_count = html.count("body::before")
        retro_count = html.count('data-retro="full"] body::before')
        assert body_before_count == retro_count, (
            f"unguarded body::before scanline still present "
            f"({body_before_count} body::before, {retro_count} retro-only)"
        )


# ----------------------------------------------------------------------
# 4. Cmd-K command palette
# ----------------------------------------------------------------------

class TestPhase1CmdKPalette:

    def test_overlay_markup_present(self):
        html = _read()
        assert 'id="cmdkOverlay"' in html
        assert 'id="cmdkCard"' in html
        assert 'id="cmdkInput"' in html
        assert 'id="cmdkResults"' in html

    def test_controller_hooks_cmd_k_hotkey(self):
        html = _read()
        assert "cmdkPalette" in html
        # Cmd+K and Ctrl+K bind.
        assert "metaKey || e.ctrlKey" in html
        # Slash also opens (vim-style)
        assert "k === '/'" in html or 'k === "/"' in html
        # ARIA + role on the overlay.
        assert 'role="dialog"' in html

    def test_palette_has_catalog_entries(self):
        """The palette must list lenses + LAB tabs + actions."""
        html = _read()
        for label in ("Overview", "Anomalies", "Causal", "KB Ingest",
                       "Marketplace", "Keyboard shortcuts",
                       "Toggle retro mode"):
            assert label in html, f"Cmd-K catalog entry {label!r} missing"

    def test_palette_supports_arrow_nav_and_escape(self):
        html = _read()
        assert "ArrowDown" in html
        assert "ArrowUp" in html
        assert "Escape" in html


# ----------------------------------------------------------------------
# 5. Responsive baseline
# ----------------------------------------------------------------------

class TestPhase1Responsive:

    def test_four_breakpoints_defined(self):
        html = _read()
        for bp in ("max-width: 1440px", "max-width: 1100px",
                    "max-width: 800px", "max-width: 600px"):
            assert f"@media ({bp})" in html, f"breakpoint {bp!r} missing"

    def test_prefers_reduced_motion_respected(self):
        html = _read()
        assert "@media (prefers-reduced-motion: reduce)" in html


# ----------------------------------------------------------------------
# 6. A11y polish
# ----------------------------------------------------------------------

class TestPhase1A11y:

    def test_skip_link_present(self):
        html = _read()
        assert 'class="skip-link"' in html
        assert 'href="#mainStage"' in html

    def test_universal_focus_visible_ring(self):
        html = _read()
        # The universal focus-visible rule we added.
        assert "*:focus-visible" in html
        assert "outline: 2px solid var(--accent);" in html


# ----------------------------------------------------------------------
# 7. Retro easter-egg
# ----------------------------------------------------------------------

class TestPhase1RetroMode:

    def test_retro_detector_reads_url_param(self):
        html = _read()
        # The retroModeDetector IIFE reads ?retro=full from the URL.
        assert "retroModeDetector" in html
        assert "URLSearchParams" in html
        assert "retro" in html and 'full' in html
        assert 'setAttribute(\'data-retro\', \'full\')' in html

    def test_retro_fonts_lazy_loaded(self):
        """The VT323 + Major Mono Display fonts are only loaded when
        the retro mode actually fires — never on production traffic."""
        html = _read()
        # The detector creates a <link> element only inside its branch.
        retro_block_start = html.find("retroModeDetector")
        assert retro_block_start > 0
        retro_block_end = html.find("})();", retro_block_start)
        assert retro_block_end > retro_block_start
        retro_block = html[retro_block_start:retro_block_end]
        assert "VT323" in retro_block
        assert "Major+Mono+Display" in retro_block

    def test_retro_mode_overrides_chrome_fonts(self):
        """When data-retro='full' is set, --font-chrome falls back to
        VT323 for the loud-cyberpunk look."""
        html = _read()
        assert 'html[data-retro="full"]' in html
        # The override block redefines --font-chrome to VT323 first.
        assert "--font-chrome:  'VT323'" in html

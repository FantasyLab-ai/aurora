"""
v0.10 Phase 2 — Vite + TypeScript scaffolding audit.

These tests verify the build scaffolding is in place WITHOUT requiring
Node / npm to be installed locally. The actual `npm run build` is
exercised in production deploys; here we just lock the shape.

What we test:
    1. package.json + vite.config.ts + tsconfig.json exist + parse.
    2. src/ has the six expected TypeScript modules.
    3. Each module declares the exports/symbols the entry point (main.ts)
       and the legacy index.html graceful loader expect.
    4. The index.html graceful loader exists + HEAD-probes the bundle
       before loading it (no console 404s in pure-legacy mode).
    5. .gitignore excludes node_modules + dist.
    6. Docs page exists.

Everything is static-grep + JSON parsing. No subprocess, no npm.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"
HTML = FRONTEND / "index.html"


# ----------------------------------------------------------------------
# Scaffolding files
# ----------------------------------------------------------------------

class TestScaffolding:

    def test_package_json_exists_and_parses(self):
        f = FRONTEND / "package.json"
        assert f.exists(), "frontend/package.json missing"
        pkg = json.loads(f.read_text(encoding="utf-8"))
        # Must be ESM module + private.
        assert pkg.get("type") == "module"
        assert pkg.get("private") is True
        # Required scripts.
        scripts = pkg.get("scripts") or {}
        for script in ("dev", "build", "typecheck"):
            assert script in scripts, f"missing npm script {script!r}"
        # Required dev deps.
        dev = pkg.get("devDependencies") or {}
        for dep in ("typescript", "vite"):
            assert dep in dev, f"missing devDependency {dep!r}"
        # Nanostores is the chosen state lib.
        deps = pkg.get("dependencies") or {}
        assert "nanostores" in deps

    def test_vite_config_exists(self):
        f = FRONTEND / "vite.config.ts"
        assert f.exists()
        text = f.read_text(encoding="utf-8")
        # Library mode output named aurora.js, proxy /api to Flask.
        assert "lib:" in text
        assert "aurora.js" in text
        assert "/api" in text
        # The output dir is dist/.
        assert "outDir: 'dist'" in text
        # Phase 2 must NOT replace index.html in production — Flask owns it.
        assert "publicDir: false" in text

    def test_vite_config_uses_esm_portable_dirname(self):
        """Regression test: vite.config.ts must NOT rely on the
        CommonJS-only `__dirname` global directly. ESM modules need
        either `fileURLToPath(import.meta.url)` or `import.meta.dirname`.

        Without this guard, `npm run build` fails with TS2304 errors
        ('Cannot find name __dirname') even when @types/node is installed.
        """
        text = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")
        # Either we use the canonical ESM pattern...
        canonical = (
            "fileURLToPath" in text and "import.meta.url" in text
        )
        # ...or the newer Node 22+ helper.
        modern = "import.meta.dirname" in text
        assert canonical or modern, (
            "vite.config.ts must derive __dirname via "
            "fileURLToPath(import.meta.url) or import.meta.dirname — "
            "raw __dirname is CommonJS-only and breaks `tsc --noEmit`"
        )

    def test_package_json_has_types_node(self):
        """vite.config.ts imports 'node:path' + 'node:url'; @types/node
        is required for `tsc --noEmit` to succeed."""
        f = FRONTEND / "package.json"
        pkg = json.loads(f.read_text(encoding="utf-8"))
        dev = pkg.get("devDependencies") or {}
        assert "@types/node" in dev, (
            "package.json missing @types/node — required for the Node "
            "stdlib imports in vite.config.ts"
        )

    def test_tsconfig_includes_node_types(self):
        """tsconfig.json must explicitly include node types so the
        Vite config sees `fileURLToPath`, `import.meta`, etc."""
        text = (FRONTEND / "tsconfig.json").read_text(encoding="utf-8")
        # We accept either `"types": ["node"]` OR an empty types array
        # IF the typeRoots already cover node — but the simpler path
        # (and what we ship) is the explicit "types": ["node"].
        assert '"types": ["node"]' in text, (
            'tsconfig.json should declare `"types": ["node"]` so '
            'vite.config.ts can resolve node:* imports'
        )

    def test_tsconfig_exists_and_strict(self):
        # tsconfig.json is JSONC (JSON-with-comments). Rather than ship
        # a JSONC parser, we verify the load-bearing fields by string
        # presence — that's what TypeScript actually does anyway when
        # it reads the file.
        f = FRONTEND / "tsconfig.json"
        assert f.exists()
        text = f.read_text(encoding="utf-8")
        # Strict flags must be present.
        for key in (
            '"strict": true',
            '"noImplicitAny": true',
            '"strictNullChecks": true',
            '"noEmit": true',
        ):
            assert key in text, f"tsconfig.json missing flag {key!r}"
        # Path aliases mirror vite.config.ts.
        assert '"@/*"' in text
        assert '"@lib/*"' in text
        # Sanity-check that the include block references src/.
        assert '"src/' in text


# ----------------------------------------------------------------------
# src/ modules
# ----------------------------------------------------------------------

class TestSrcModules:

    def test_six_modules_present(self):
        expected = [
            SRC / "main.ts",
            SRC / "types" / "aurora.ts",
            SRC / "lib" / "api.ts",
            SRC / "lib" / "state.ts",
            SRC / "lib" / "events.ts",
            SRC / "lib" / "dom.ts",
        ]
        for f in expected:
            assert f.exists(), f"missing src module: {f.relative_to(ROOT)}"

    def test_main_exports_aurora_namespace(self):
        f = (SRC / "main.ts").read_text(encoding="utf-8")
        # The bundle mounts window.Aurora — that's the public contract.
        assert "window.Aurora" in f
        assert "AuroraNamespace" in f
        # Boot side-effects.
        assert "startGlobalSync" in f
        assert "stream.start" in f

    def test_api_module_has_typed_endpoints(self):
        f = (SRC / "lib" / "api.ts").read_text(encoding="utf-8")
        for name in (
            "fetchState", "healthCheck", "whoami",
            "runDataset", "causalDo", "streamStatus",
        ):
            assert f"export function {name}" in f, (
                f"api.ts missing exported helper {name!r}"
            )
        # Standard return envelope.
        assert "ApiResult" in f

    def test_state_module_uses_nanostores(self):
        f = (SRC / "lib" / "state.ts").read_text(encoding="utf-8")
        assert "from 'nanostores'" in f
        # Mirrors the legacy globals.
        for atom in ("runDir", "runId", "inheritFrom", "workspace"):
            assert f"export const {atom}" in f, (
                f"state.ts missing atom {atom!r}"
            )
        assert "syncFromGlobals" in f
        assert "startGlobalSync" in f

    def test_events_module_hardens_sse(self):
        f = (SRC / "lib" / "events.ts").read_text(encoding="utf-8")
        # Hardened class with backoff, dedupe, visibility-handling.
        assert "class AuroraEventStream" in f
        assert "exponential" in f.lower() or "backoff" in f.lower()
        assert "dedupe" in f.lower() or "seenIdentities" in f
        assert "visibilitychange" in f
        assert "openWhenHidden" in f

    def test_dom_module_has_helpers(self):
        f = (SRC / "lib" / "dom.ts").read_text(encoding="utf-8")
        for helper in ("qs", "qsa", "byId", "on", "escapeHtml",
                        "el", "debounce", "throttle"):
            assert f"export function {helper}" in f, (
                f"dom.ts missing helper {helper!r}"
            )

    def test_types_module_typed_window_augmentation(self):
        f = (SRC / "types" / "aurora.ts").read_text(encoding="utf-8")
        # Typed Finding + AuroraState.
        assert "interface Finding" in f
        assert "interface AuroraState" in f
        # window augmentation for the legacy globals.
        assert "declare global" in f
        assert "__auroraRunDir" in f


# ----------------------------------------------------------------------
# Graceful loader in index.html
# ----------------------------------------------------------------------

class TestGracefulLoader:

    def test_loader_head_probes_bundle(self):
        html = HTML.read_text(encoding="utf-8")
        # Phase 2 loader function exists.
        assert "loadPhase2Bundle" in html
        # HEAD-probes the bundle first so no 404 fires in pure-legacy mode.
        assert "'/static/dist/aurora.js'" in html
        assert "method: 'HEAD'" in html
        # Mounts the module + CSS dynamically.
        assert "type = 'module'" in html
        assert "/static/dist/aurora.css" in html

    def test_loader_never_breaks_page(self):
        """The loader is wrapped in try/catch so a thrown error never
        prevents the rest of the page from rendering."""
        html = HTML.read_text(encoding="utf-8")
        # Find the loader function body.
        start = html.find("function loadPhase2Bundle")
        end = html.find("})();", start)
        assert start > 0 and end > start
        body = html[start:end]
        assert "try {" in body or "try{" in body
        assert ".catch(" in body  # both fetch chains use .catch silent


# ----------------------------------------------------------------------
# Build artifacts ignored
# ----------------------------------------------------------------------

class TestGitignore:

    def test_node_modules_and_dist_ignored(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "frontend/node_modules/" in text
        assert "frontend/dist/" in text


# ----------------------------------------------------------------------
# Docs
# ----------------------------------------------------------------------

class TestDocs:

    def test_frontend_build_md_exists(self):
        f = ROOT / "docs" / "frontend-build.md"
        assert f.exists()
        text = f.read_text(encoding="utf-8")
        # Lists the dev loop, build command, and Phase 3 outlook.
        assert "npm run dev" in text
        assert "npm run build" in text
        assert "window.Aurora" in text
        assert "Phase 3" in text

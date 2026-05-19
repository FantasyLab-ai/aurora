/**
 * Aurora Studio frontend build — Vite + TypeScript.
 *
 * Two responsibilities:
 *   1. Bundle src/main.ts (+ deps) into a single ES module the legacy
 *      index.html can lazy-load via <script type="module">.
 *   2. Proxy /api/* and /mcp/* through to Flask during dev so the
 *      enhanced bundle can talk to the live backend.
 *
 * Production: Flask serves dist/aurora.js + dist/aurora.css from the
 * existing /static/* route (studio_api.py mounts FRONTEND_DIR there).
 * No deployment shape change.
 *
 * Migration model: Phase 2 leaves the legacy inline JS in place.
 * Modules from src/ run alongside it, sharing window.__aurora* state
 * for handoff. Panels migrate one at a time when (or if) you ever
 * choose to do Phase 3.
 */
import { defineConfig } from 'vite';
import { resolve } from 'node:path';

export default defineConfig(({ mode }) => ({
  // The frontend root lives in ./frontend; this config sits there too.
  root: __dirname,

  // We do NOT serve index.html from Vite in production — Flask owns
  // it. Vite only produces JS + CSS assets that the legacy index.html
  // pulls in via <script type="module" src="/static/dist/aurora.js">.
  // Set publicDir to false so Vite doesn't try to copy index.html.
  publicDir: false,

  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Produce an ES module that the legacy page can `<script type="module">`.
    // Library mode lets us name the output file deterministically.
    lib: {
      entry: resolve(__dirname, 'src/main.ts'),
      name: 'AuroraStudio',
      formats: ['es'],
      fileName: () => 'aurora.js',
    },
    // Sourcemaps in dev builds; never in prod-final.
    sourcemap: mode !== 'production',
    // Don't try to inline assets; we want a clean .js + .css pair.
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        // Force a stable CSS filename (default is `style.css`).
        assetFileNames: (info) => {
          if (info.name && info.name.endsWith('.css')) return 'aurora.css';
          return 'assets/[name]-[hash][extname]';
        },
      },
    },
    // The legacy code is ES2015-compatible; keep our output the same
    // so we don't accidentally break older Chromium / Safari users.
    target: 'es2018',
    minify: 'esbuild',
  },

  // Dev server: proxies /api/* through to Flask on port 8000.
  // Run `flask run -p 8000` + `npm run dev` (Vite on 5173).
  server: {
    port: 5173,
    strictPort: false,
    host: '127.0.0.1',
    proxy: {
      '/api':       { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/mcp':       { target: 'http://127.0.0.1:8000', changeOrigin: false },
      // SSE — explicit ws:false so the bundler doesn't try to upgrade.
      '/api/stream/events': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
        ws: false,
      },
      '/static':    { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/knowledge_bank': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },

  resolve: {
    alias: {
      '@':       resolve(__dirname, 'src'),
      '@lib':    resolve(__dirname, 'src/lib'),
      '@types':  resolve(__dirname, 'src/types'),
    },
  },
}));

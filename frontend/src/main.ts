/**
 * Aurora Studio frontend — Vite + TypeScript entry point.
 *
 * Phase 2 of the polish migration. This bundle runs ALONGSIDE the
 * legacy inline scripts in index.html — it doesn't replace them.
 * Its job is to:
 *
 *   1. Expose a typed `window.Aurora` namespace with the new
 *      api/state/events helpers so future panels (and any panel that
 *      migrates today) read clean code instead of hand-rolled fetch.
 *
 *   2. Start the global-sync interval so the Nanostores atoms stay
 *      in lockstep with the legacy `window.__aurora*` globals during
 *      the strangler-fig migration window.
 *
 *   3. Boot a hardened EventSource client (exponential backoff,
 *      tab-hidden pause, dedupe) that legacy controllers can opt
 *      into via `window.Aurora.stream`.
 *
 *   4. Nothing else. Every existing panel keeps working as-is.
 *      The bundle is purely additive.
 *
 * The bundle is loaded by index.html via:
 *   <script type="module" src="/static/dist/aurora.js"></script>
 * — see frontend/dist/ when `npm run build` has run, OR
 * Flask falls back to the legacy-only experience when dist/ is empty.
 */

import * as api from './lib/api';
import * as store from './lib/state';
import { AuroraEventStream } from './lib/events';
import * as dom from './lib/dom';

// ----------------------------------------------------------------------
// Public namespace — bolt onto window so legacy IIFEs can call into us.
// ----------------------------------------------------------------------

export interface AuroraNamespace {
  /** Build identifier — bumped when the bundle changes. */
  version: string;
  /** Typed API client. */
  api: typeof api;
  /** Nanostores-backed atoms. */
  store: typeof store;
  /** DOM helpers. */
  dom: typeof dom;
  /** Live SSE client (started on boot). */
  stream: AuroraEventStream;
  /** Factory for new event streams (tests, panels). */
  EventStream: typeof AuroraEventStream;
  /** Console-friendly diagnostics. */
  health(): Promise<unknown>;
}

const ns: AuroraNamespace = {
  version: '0.10.0+phase2',
  api,
  store,
  dom,
  EventStream: AuroraEventStream,
  stream: new AuroraEventStream({
    url: '/api/stream/events',
    openWhenHidden: false,
    onConnectionChange: (s) => {
      // Cheap dev console signal; no UI yet, the legacy chip is
      // already on the page.
      if (s === 'open') {
        // eslint-disable-next-line no-console
        console.info('[aurora] SSE connected');
      }
    },
  }),
  async health() {
    const h = await api.healthCheck();
    return h;
  },
};

// Expose on window. We deliberately use a single mount point so the
// legacy code can grep `window.Aurora` and confidently extend it.
declare global {
  interface Window {
    Aurora?: AuroraNamespace;
  }
}
window.Aurora = ns;

// ----------------------------------------------------------------------
// Boot side-effects.
// ----------------------------------------------------------------------

// 1. Pull the legacy globals into atoms + start periodic re-sync.
store.syncFromGlobals();
store.startGlobalSync(250);

// 2. Connect the SSE stream lazily — wait one tick so the legacy
//    inline EventSource (if any) doesn't race us. The stream module
//    dedupes per-identity so even if both clients run in parallel
//    during the transition, no event fires twice on the new code path.
setTimeout(() => {
  try { ns.stream.start(); } catch { /* never break boot */ }
}, 100);

// 3. Print a single boot line so power users know the bundle loaded.
//    Production: visible only in DevTools, not user-facing.
// eslint-disable-next-line no-console
console.info(
  '%c⚡ Aurora ' + ns.version + ' loaded',
  'color: #6ee7ff; font-family: monospace; font-weight: 600;',
);

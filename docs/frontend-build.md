# Aurora Studio frontend — Vite + TypeScript build

> v0.10 Phase 2 of the UI polish. Optional, non-breaking, side-by-side
> with the legacy inline `frontend/index.html`. You can ignore the
> entire Phase 2 layer if you want; the Studio works without it.

## What changed

| Before Phase 2 | After Phase 2 |
|---|---|
| Single 17,000-line `frontend/index.html` with inline `<script>` IIFEs | Same file, plus an **optional** Vite + TypeScript bundle that loads on top of it |
| No build step — just open the file | `cd frontend && npm install && npm run build` produces `dist/aurora.js` |
| No types | TypeScript with `strict: true`, type-checked via `tsc --noEmit` |
| `window.__aurora*` globals | Same globals + typed [Nanostores](https://github.com/nanostores/nanostores) atoms that mirror them |
| Hand-rolled `new EventSource(...)` | `AuroraEventStream` class with exponential backoff + dedupe + tab-hidden pause |
| Hand-rolled `fetch('/api/...')` | Typed `Aurora.api.*` helpers that always return `{ok, data, error}` |

The legacy code keeps working unchanged. The Vite bundle is purely
additive — when it's loaded, you get a typed `window.Aurora` namespace
that new panels (and any panel being migrated) can use. When it isn't
loaded (fresh checkout, no `npm install` yet), the Studio runs in
pure-legacy mode exactly as before.

## Quickstart

```bash
cd frontend
npm install          # one-time
npm run build        # produces dist/aurora.js + dist/aurora.css
```

Then start the Flask Studio as usual:

```bash
python studio_api.py
```

Open <http://127.0.0.1:8000>. The page loads the legacy inline code
first, then a tiny probe checks if `dist/aurora.js` exists and
loads it as a module if so. If not, no console warnings — you just
keep the legacy experience.

## Development loop

Two terminals:

```bash
# Terminal 1 — Flask backend on port 8000.
python studio_api.py

# Terminal 2 — Vite dev server with HMR on port 5173,
# proxying /api/* + /static/* + /mcp/* through to Flask.
cd frontend
npm run dev
```

Then open <http://127.0.0.1:5173>. Edits to anything under
`frontend/src/` hot-reload in the browser. The Flask backend is
unchanged — all API calls proxy through.

## Type-checking only (no build)

```bash
cd frontend
npm run typecheck
```

Runs `tsc --noEmit` against the whole `src/` tree. Use this in CI to
catch type errors without producing a build.

## Production deploy

Run `npm run build` as part of your deploy. Flask's existing
`/static/<path>` route serves `dist/aurora.js` + `dist/aurora.css`
from `FRONTEND_DIR/dist/`. No new infra, no Node runtime needed in
production — only at build time.

If you containerise with the existing `Dockerfile`, add this stage
before the Python install:

```dockerfile
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./frontend/
RUN cd frontend && npm install
COPY frontend ./frontend
RUN cd frontend && npm run build
# ... then in the final image:
COPY --from=frontend /build/frontend/dist /app/frontend/dist
```

(The current Dockerfile doesn't ship this yet; add it when Phase 2
becomes the default rather than opt-in.)

## What's in `src/`

```
frontend/src/
├── main.ts                 — entry point, mounts window.Aurora
├── types/
│   └── aurora.ts           — shared interfaces (Finding, AuroraState, …)
└── lib/
    ├── api.ts              — typed fetch wrappers (fetchState, runDataset, …)
    ├── state.ts            — Nanostores atoms mirroring window.__aurora*
    ├── events.ts           — AuroraEventStream — hardened SSE client
    └── dom.ts              — DOM helpers (qs, byId, on, escapeHtml, …)
```

Everything is < 100 lines per file. Browse them — they're meant to
read like documentation.

## Public API (`window.Aurora`)

When the bundle loads, it bolts a typed namespace onto `window`:

```typescript
window.Aurora = {
  version: '0.10.0+phase2',
  api:    { fetchState, runDataset, causalDo, streamStatus, whoami, healthCheck },
  store:  { runDir, runId, inheritFrom, workspace, state, /* atoms */ },
  dom:    { byId, qs, qsa, on, el, escapeHtml, debounce, throttle },
  stream: AuroraEventStream,  // already started
  EventStream: AuroraEventStream,  // factory
  health(): Promise<...>,
};
```

A panel that wants typed API access does:

```typescript
const r = await window.Aurora.api.fetchState();
if (r.ok && r.data) {
  console.log(r.data.findings?.length, 'findings');
}
```

A panel that wants live findings does:

```typescript
const unsub = window.Aurora.stream.on('new_finding', (ev) => {
  console.log('new finding:', ev.payload);
});
// Later:
unsub();
```

## State migration model

During Phase 2, the legacy code keeps using `window.__auroraRunDir`
etc. The TypeScript atoms (`store.runDir`) shadow them and stay in
sync via a 250 ms polling interval (`startGlobalSync()`).

When the time comes to migrate a specific panel:

1. The new panel reads/writes via `store.runDir.set(...)`.
2. The 250 ms sync pushes the change back to `window.__auroraRunDir`.
3. Legacy code sees the updated global and keeps working.

When all panels have migrated, drop `startGlobalSync` and the legacy
globals stop being a source of truth.

## TypeScript strictness

`strict: true` is on. The legacy DOM-mutation code lives in
`frontend/index.html` and is NOT type-checked — only the new code
under `frontend/src/` is. Anything you migrate gets the full
contract.

Migration pragmatics:

- `noImplicitAny: true` — no untyped function parameters.
- `strictNullChecks: true` — null vs undefined matters.
- `noUnusedLocals / noUnusedParameters: false` — too noisy during
  active porting; flip on when a panel is done.

## Risk surface

What CAN'T break:

- The legacy app — Phase 2 loads after legacy code runs.
- Flask — no new dependencies, no new env vars, no new routes.
- Tests — none of the existing tests touch Vite or Node.

What can break (and how to recover):

- `dist/aurora.js` ships with a syntax error → browser silently
  doesn't load it, page works in pure-legacy mode. Fix the bug,
  re-run `npm run build`.
- `npm install` fails in CI → frontend bundle isn't built, page
  works in pure-legacy mode. CI gate is opt-in.
- Vite proxy in dev doesn't reach Flask → check Flask is on port
  8000 and Vite config's `server.proxy.target` matches.

## Phase 3 (later)

Phase 3 is the panel-by-panel rewrite into Svelte 5. The Phase 2
scaffolding is intentionally framework-agnostic — Nanostores and
Observable Plot work in Svelte / React / Solid identically. When
you're ready, add `svelte` to `dependencies`, drop in a
`@sveltejs/vite-plugin-svelte` import, and start porting panels.
Nothing in Phase 2 has to change.

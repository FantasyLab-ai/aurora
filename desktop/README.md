# Aurora desktop shell

Native desktop wrapper for Aurora — a Tauri 2 app that boots a frameless,
transparent window and either renders Aurora's UI natively (Overview /
Findings / Data tabs) or embeds the full legacy Aurora Studio in an
iframe (under the **Aurora Studio** sidebar item).

## What this is

* **Phase 1** — desktop shell with Aurora's identity (cyan + near-black,
  GameCube diamond, mono-caps labels, VT323 numerics), sidebar
  navigation, sliding-underline tab strip, cross-fade view transitions,
  and frameless window chrome.
* **Phase 2** (later) — progressively replace the legacy iframe with
  native views (methods table, dataset browser, bundle library) that
  call Aurora's HTTP API directly.

## Prerequisites

* **Rust** — install via [rustup](https://rustup.rs) (one-time, ~3GB)
* **Node 18+** + npm
* **Aurora backend** — `python studio_api.py` running at
  `http://127.0.0.1:8001` (the shell polls this for live data)

On Windows, Rust also needs the MSVC build tools — the rustup installer
walks you through it.

## Run in dev mode

```powershell
cd desktop
npm install          # once
npm run tauri dev    # hot-reload window
```

First Rust compile is slow (downloads ~200 crates, 5–15 min). Subsequent
runs are seconds.

## Build a release `.exe` / `.dmg` / `.AppImage`

```powershell
npm run tauri build
```

Output binaries land under `src-tauri/target/release/bundle/`.

## How it talks to Aurora

The shell makes plain HTTP calls to Aurora's existing API at
`http://127.0.0.1:8001`:

| Tab / sidebar | Endpoint                | Notes                                  |
|---|---|---|
| Overview      | `GET /api/state`        | Findings count, methods run, regimes   |
| Findings      | `GET /api/state`        | `.findings[]` rendered as cards        |
| Data          | `GET /api/state`        | `.dataset.preview` rendered as a table |
| Run analysis  | `POST /api/run`         | `{ "dataset": "<path>" }`              |
| Aurora Studio | iframe at `8001/`       | The full legacy UI                     |

Aurora Studio must be running for the shell to show live data. The
sidebar has an "aurora: online / offline" status dot driven by polling
`/api/preflight` every 4 s.

## Identity, not skin-deep

Polish techniques applied throughout `styles.css`: one token system, one
motion curve, sliding-underline tabs, cross-fade view transitions with
8 px rise, font smoothing, frameless chrome, hover micro-interactions,
custom thin scrollbars. None of these compromise Aurora's voice — the
cyan, the diamond glyph, and the retro-techno mono labels are still the
brand.

## Project layout

```
desktop/
├── src/                    # Web frontend (vanilla, no bundler)
│   ├── index.html          # Shell layout (sidebar + tabs + views)
│   ├── styles.css          # Aurora tokens + all polish techniques
│   └── main.js             # Tab routing, API client, window controls
├── src-tauri/              # Rust shell
│   ├── Cargo.toml
│   ├── tauri.conf.json     # Frameless + transparent + drag region
│   ├── capabilities/       # Window permissions
│   └── src/                # Rust entry (minimal — no custom commands)
└── package.json
```

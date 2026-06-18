# Aurora desktop shell

Native desktop wrapper for Aurora — a Tauri 2 app that boots a frameless,
transparent, rounded window and renders Aurora's data natively (Overview
/ Findings / Methods / Datasets / Bundles / Data) or embeds the full
legacy Aurora Studio in an iframe under the **Aurora Studio** sidebar
item.

## What this is

* **Phase 1** — desktop shell with Aurora's identity (cyan + near-black,
  GameCube diamond, mono-caps labels, VT323 numerics), sidebar
  navigation, sliding-underline tab strip, cross-fade view transitions,
  frameless window chrome.
* **Phase 2.1** — drag-drop any file (CSV/TSV/JSON/JSONL/Parquet/XLSX)
  onto the window → auto-POST to `/api/run`. Click-to-detail side panel
  on finding cards.
* **Phase 2.2** — native Methods (per-run method tally), Datasets (from
  `/api/demo_datasets`), Bundles (from `/api/runs`). Single-terminal
  launcher script (`launch.ps1`).
* **Phase 3** (later) — Python sidecar bundling (PyInstaller), code
  signing, virtualized data grid (TanStack-style) when finding count >200.

## Quickest start — single command

If you've already built once (`npm run tauri build`), from anywhere:

```powershell
.\desktop\launch.ps1
```

The launcher reloads PATH, finds the venv, starts `studio_api.py` in a
background job, waits for `/api/preflight` to answer, then opens the
Aurora window. When you close the window, the backend is stopped
cleanly. No second terminal required.

For hot-reload UI iteration:

```powershell
.\desktop\launch.ps1 -DevMode
```

Custom port:

```powershell
.\desktop\launch.ps1 -AuroraPort 8002
```

## Prerequisites

* **Rust** — install via [rustup](https://rustup.rs) (one-time, ~3GB)
* **Node 18+** + npm
* **Aurora Python venv** with deps installed — `python -m venv .venv`
  then `pip install -r requirements.txt` from the repo root
* On Windows: Smart App Control must be **off** (one-way switch) for
  Rust's build scripts to execute. See top-level [`README.md`](../README.md#%EF%B8%8F-desktop-app-native-shell)

## Manual two-terminal startup (alternative)

Open Terminal A:
```powershell
.\.venv\Scripts\Activate.ps1
$env:AURORA_PORT = "8001"
python studio_api.py
```

Open Terminal B:
```powershell
cd desktop
npm run tauri dev
```

## Build a distributable

```powershell
cd desktop
npm install                    # once
npm run tauri build            # release build
```

Installers land under `src-tauri/target/release/bundle/` — `.exe`/`.msi`
on Windows, `.dmg` on macOS, `.AppImage`/`.deb` on Linux.

## How it talks to Aurora

The shell makes plain HTTP calls to Aurora's existing API at
`http://127.0.0.1:8001`:

| Tab / sidebar  | Endpoint                | Notes                                            |
|---|---|---|
| Overview       | `GET /api/state`        | Findings count, methods, anomalies, regimes      |
| Findings       | `GET /api/state`        | `.findings[]` rendered as cards (click → detail) |
| Data           | `GET /api/state`        | `.dataset.preview` rendered as a table           |
| Methods        | `GET /api/state`        | Method tally from `findings[].method`            |
| Datasets       | `GET /api/demo_datasets`| Click any card to fire that dataset              |
| Bundles        | `GET /api/runs`         | Past runs from disk (most recent 30)             |
| Run analysis   | `POST /api/run`         | Triggered by Run button OR drop-zone OR dataset card |
| Aurora Studio  | iframe at `8001/`       | Full legacy UI — every feature still reachable   |

Sidebar's `aurora: online/offline` dot polls `/api/preflight` every 4 s.

## Drag-and-drop

Drop any file Aurora reads — **CSV, TSV, JSON, JSONL, Parquet, XLSX** —
anywhere on the window. The drop zone (top of Overview) highlights cyan
during the drag. The dropped file's absolute path is sent to
`/api/run`, the shell switches to Overview, and you watch the stats
populate as Aurora analyzes.

## Identity, not skin-deep

Eight polish techniques applied throughout `styles.css`: one token
system, one motion curve, sliding-underline tabs, cross-fade view
transitions with 8 px rise, font smoothing, frameless chrome, hover
micro-interactions, custom thin scrollbars. None compromise Aurora's
voice — the cyan, diamond glyph, and retro-techno mono labels are still
the brand.

## Project layout

```
desktop/
├── launch.ps1              # Single-terminal launcher (Phase 2.2)
├── README.md               # This file
├── package.json
├── src/                    # Web frontend (vanilla, no bundler)
│   ├── index.html          # Shell layout (sidebar + tabs + views + detail panel)
│   ├── styles.css          # Aurora tokens + all polish techniques
│   └── main.js             # Tab routing, API client, drag-drop, finding detail
└── src-tauri/              # Rust shell
    ├── Cargo.toml
    ├── tauri.conf.json     # Frameless + transparent + drag region
    ├── capabilities/       # Window permissions (drag-drop, min/max/close)
    └── src/                # Rust entry (minimal — no custom commands yet)
```

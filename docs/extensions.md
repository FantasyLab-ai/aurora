# Aurora Editor & Workbook Extensions

Beyond the SDK, MCP server, and Studio, Aurora ships extensions for
the editors and spreadsheets your team already uses. They're all
**thin clients** — the heavy analytical lifting always happens in your
local Aurora Studio (Flask). The extensions just trigger runs and
render results inside the host application.

| Extension | Path | Status |
|---|---|---|
| **VS Code** | `extensions/vscode/` | ✅ Scaffolded (commands, webview, status bar). Ready to compile + side-load locally; marketplace publish needs a maintainer Microsoft account |
| **Excel** (Office Add-in) | `extensions/excel/` | ✅ Scaffolded (manifest + task-pane JS). Ready to side-load; AppSource publish needs the manifest GUID swapped + a public HTTPS URL for the task-pane HTML |
| **Google Sheets** (Apps Script add-on) | `extensions/sheets/` | ✅ Scaffolded. Caveat: Apps Script runs on Google's servers and needs your Aurora Studio at a public URL (use ngrok for dev, host Aurora for prod) |
| **Jupyter** (rich repr + DataFrame input) | `aurora_sdk/jupyter.py` | ✅ Shipped (in-tree, see [docs/jupyter.md](jupyter.md)) |

## What they all share

Every extension follows the same architectural pattern:

```
  Editor / Workbook                        Aurora Studio
  (extension)              HTTP             (Flask server, local)
       │                                            │
       │   POST /api/run                            │
       ├──────────────────────────────────────────►│
       │                                            │  ──→  pipeline.run()
       │                                            │       state_builder.build_state()
       │   GET /api/state                           │
       │◄──────────────────────────────────────────┤
       │                                            │
       │   render findings inline                   │
       └────                                        ┘
```

This means:

- **The extensions are tiny.** A VS Code extension that does everything
  Aurora does would be 100K+ lines. The current scaffold is ~300.
- **Aurora updates don't require extension updates.** Improve the
  pipeline, ship a new Studio release, extensions pick it up
  automatically.
- **The same pattern works for hosted Aurora.** Set the extension's
  `studioUrl` to a remote server and the same UX works.

## VS Code

See [`extensions/vscode/README.md`](../extensions/vscode/README.md).

Quick install for development:

```bash
cd extensions/vscode
npm install
npm run compile
# Then in VS Code: F1 → "Developer: Install Extension from Location..."
# → point at extensions/vscode/
```

Commands added:

- `Aurora: Analyze Current File` (also on right-click for CSV/Parquet)
- `Aurora: Open Studio`
- `Aurora: Run on Selection`

Status bar shows a `$(graph) Aurora` indicator; click it to open the Studio.

## Excel

See [`extensions/excel/README.md`](../extensions/excel/README.md).

The manifest declares an **Analyze Selection** button on the Home
ribbon. Click it, select your range with headers, and Aurora returns
findings in the task pane.

To side-load for development:

1. Run Aurora Studio (`python studio_api.py`).
2. Excel → Insert → My Add-ins → Upload My Add-in → select
   `extensions/excel/manifest.xml`.

For AppSource publish you'll need to:

- Replace the placeholder GUID in `manifest.xml`
- Host the task-pane HTML (currently referenced as
  `http://localhost:8000/excel-taskpane.html`) on a public HTTPS URL
- Submit via Microsoft Partner Center

## Google Sheets

See [`extensions/sheets/README.md`](../extensions/sheets/README.md).

**Caveat first:** Apps Script runs server-side on Google's
infrastructure. To talk to your local Aurora Studio you need a public
URL — either an ngrok tunnel for dev or a hosted Aurora for prod. For
strict local-first use, prefer the VS Code or Excel paths.

To install for development:

1. Create a new Apps Script project at <https://script.google.com/>.
2. Paste `Code.gs` and create a `Sidebar.html` file.
3. Set Script Property `AURORA_STUDIO_URL` to your reachable Aurora URL.
4. Open any spreadsheet → Extensions → Aurora → Open Aurora Sidebar.

## Why this layered approach

Aurora's design tension: most users live in editors / workbooks, but
Aurora's analytical engine is Python. We resolve it by treating Aurora
Studio as the **boundary** — every extension talks to it the same way.
The user picks their environment; Aurora runs the same code regardless.

It also keeps each surface honest:

- **VS Code / Excel** users get a working local-first install with no
  cloud dependencies.
- **Sheets / hosted-Aurora** users get the same engine on a public URL,
  with the same finding shapes + bundle integrity guarantees.
- **The Studio itself** remains the canonical UI for deep analysis.

You're never locked into one surface. The same `audit.aurora.json`
bundle a VS Code user produces is readable by an Excel user, a Sheets
user, a Jupyter user, or a custom MCP agent.

## Roadmap

| Surface | What's next |
|---|---|
| VS Code | Inline severity decorations on flagged rows; tree view of past runs; MCP-aware command palette |
| Excel | Highlight anomalous cells; write findings back into a new sheet; configurable Studio URL UI |
| Sheets | OAuth flow for hosted Aurora; inline severity highlights |
| Cursor / Continue / Aider | Auto-discovery of Aurora MCP when running alongside |
| Slack / Teams (Stream 2.5) | Bot integration ("/aurora analyze [link]") |
| n8n / Zapier (Stream 2.6) | Native nodes for workflow automation |
| Mobile (Stream 2.7) | Read-only viewer for on-call alerts |
| Browser extension (Stream 2.8) | Right-click any data table → analyse |

All of these follow the same thin-client pattern. The expensive part —
designing the Studio's HTTP API to be stable — is already done.

## License

Apache 2.0. Same as Aurora itself.

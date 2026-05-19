# Aurora — VS Code Extension

Right-click any CSV/Parquet/Excel file in VS Code → run an Aurora
analysis. Results render inline as a webview panel with cited findings,
methods, and severity. Zero fabricated, all local.

## What it does

| Command | Trigger | Result |
|---|---|---|
| **Aurora: Analyze Current File** | Right-click any `.csv` / `.tsv` / `.parquet` / `.xlsx` file in the Explorer | Aurora runs on the file via the Studio; findings render in a panel |
| **Aurora: Open Studio** | Command palette / status-bar click | Opens the full Studio UI in your browser |
| **Aurora: Run on Selection** | Select CSV-shaped text in the editor → command palette | Writes the selection to a temp CSV and analyses it |

The extension is a **thin client**. The heavy lifting happens in the
Aurora Studio (Flask) running on your machine. The extension just
triggers analyses and renders results.

## Setup

### 1. Make sure Aurora Studio is running

```bash
cd /path/to/aurora
python studio_api.py
# → Studio binds to http://localhost:8000
```

(See the main Aurora README for the install / first-run steps.)

### 2. Install the extension

**From the VS Code Marketplace** (once published):

```
ext install fantasylab-ai.aurora-vscode
```

**From source** (development / unsigned use):

```bash
cd extensions/vscode
npm install
npm run compile
```

Then in VS Code: `F1` → `Developer: Install Extension from Location...`
→ point at `extensions/vscode/`.

### 3. (Optional) Override the Studio URL

If your Studio runs on a non-default port or host:

```json
// .vscode/settings.json
{
  "aurora.studioUrl": "http://192.168.1.42:8000",
  "aurora.defaultDepth": "standard"
}
```

## Usage

1. Open a folder with CSV data.
2. Right-click a CSV in the Explorer → **Aurora: Analyze Current File**.
3. Aurora's progress bar runs at the top-right; results render in a
   side panel within ~30 seconds.
4. The status bar shows a `$(graph) Aurora` indicator — click it to
   open the full Studio UI any time.

## Building from source

```bash
npm install            # one-time install of devDependencies
npm run compile        # tsc → out/extension.js
npm run watch          # rebuild on save
```

Then `F5` in VS Code to launch an Extension Development Host with the
extension loaded.

## Publishing (maintainer notes)

To publish to the VS Code Marketplace, you need:

1. A Microsoft / Azure DevOps account
2. A `vsce` Personal Access Token: <https://dev.azure.com/>
3. `npm install -g @vscode/vsce`
4. `vsce login fantasylab-ai`
5. `vsce package` → produces `aurora-vscode-X.Y.Z.vsix`
6. `vsce publish` (or upload the `.vsix` manually via the publisher dashboard)

The `media/aurora-mascot-wizard.png` referenced in `package.json` should
match the launch mascot. If you publish before that asset is in this
directory, either copy it here or remove the `"icon"` field from
`package.json`.

## Roadmap

| Feature | Status |
|---|---|
| Command-based analyse | ✅ shipped (this version) |
| Webview results panel | ✅ shipped |
| Status-bar indicator | ✅ shipped |
| Inline `$(...)` decorations on flagged rows in the editor | v0.2 — once MCP is wired |
| Tree view of past Aurora runs in the side panel | v0.2 |
| One-click "Re-run on this file" from a finding | v0.2 |
| Aurora MCP integration (when Cursor / Claude is in the same window) | v0.3 |

## Architecture

```
VS Code (this extension)  ←→  HTTP  ←→  Aurora Studio (Flask)
                                            ↓
                                       state_builder
                                       (the actual analysis)
```

The extension never invokes Aurora's Python code directly. It always
goes through the Studio's HTTP API. This means:

- The extension is tiny (~10 KB packaged).
- Aurora updates don't require an extension update.
- The same protocol works for remote-Studio setups (run Aurora on a
  beefier machine, point your editor at it).

## License

Apache 2.0. Same as the rest of Aurora.

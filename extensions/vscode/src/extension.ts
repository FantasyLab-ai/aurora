/**
 * Aurora VS Code extension — main entry point.
 *
 * Three commands:
 *
 *   - aurora.analyzeCurrentFile   Right-click a CSV in the explorer → run
 *                                 Aurora on it, render findings in a
 *                                 webview panel.
 *   - aurora.openStudio           Open the Studio (Flask UI) in a webview
 *                                 or external browser.
 *   - aurora.runOnSelection       Take the user's current text selection
 *                                 (interpreted as CSV / table) and run a
 *                                 quick analysis.
 *
 * The extension communicates with the user's running Aurora Studio
 * (Flask server) over HTTP. By default it expects `http://localhost:8000`;
 * users can override via `aurora.studioUrl` in settings.
 *
 * Architecture choice: we treat the Studio as the source of truth for
 * analysis. The extension is a *thin client* that triggers runs and
 * renders results — it doesn't re-implement any Aurora logic. This
 * keeps the extension tiny and lets Aurora ship updates without
 * extension republishes.
 */

import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import * as http from "http";
import * as https from "https";


/**
 * Activate the extension. Called by VS Code on first command invocation.
 */
export function activate(context: vscode.ExtensionContext): void {
    context.subscriptions.push(
        vscode.commands.registerCommand("aurora.analyzeCurrentFile",
            (uri?: vscode.Uri) => analyzeCurrentFile(uri)),
        vscode.commands.registerCommand("aurora.openStudio",
            () => openStudio()),
        vscode.commands.registerCommand("aurora.runOnSelection",
            () => runOnSelection()),
    );

    // Subtle status-bar indicator so users know Aurora is loaded.
    const status = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Right, 100);
    status.text = "$(graph) Aurora";
    status.tooltip = "Aurora: Glass-box quantitative intelligence. Click to open Studio.";
    status.command = "aurora.openStudio";
    status.show();
    context.subscriptions.push(status);
}

export function deactivate(): void {
    /* no-op — VS Code disposes subscriptions automatically */
}


// ============================================================================
// Commands
// ============================================================================

/**
 * Run Aurora on the file the user right-clicked (or the active editor's file).
 *
 * Flow:
 *   1. Resolve the file path
 *   2. Check that Aurora Studio is reachable at `aurora.studioUrl`
 *   3. POST to /api/run with the CSV path
 *   4. Open a webview panel that renders the resulting state
 *
 * Long-running. We show a notification with a progress bar while the
 * Studio analyses the file.
 */
async function analyzeCurrentFile(uri?: vscode.Uri): Promise<void> {
    const filePath = await resolveFilePath(uri);
    if (!filePath) {
        return;
    }
    const studioUrl = getStudioUrl();

    // Pre-flight: confirm the Studio is up.
    const studioOk = await pingStudio(studioUrl);
    if (!studioOk) {
        const choice = await vscode.window.showWarningMessage(
            `Aurora Studio isn't reachable at ${studioUrl}. ` +
            `Start it with \`python studio_api.py\` in your Aurora repo.`,
            "Open Studio Setup Docs",
        );
        if (choice === "Open Studio Setup Docs") {
            vscode.env.openExternal(vscode.Uri.parse(
                "https://github.com/FantasyLab-ai/aurora#install"));
        }
        return;
    }

    const depth = vscode.workspace.getConfiguration("aurora")
        .get<string>("defaultDepth", "auto");

    await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: `Aurora: analyzing ${path.basename(filePath)} (depth=${depth})...`,
        cancellable: false,
    }, async (progress) => {
        progress.report({ increment: 0, message: "uploading dataset..." });
        try {
            const runResp = await postJson(`${studioUrl}/api/run`, {
                dataset: filePath,
                depth,
            });
            if (!runResp.ok) {
                throw new Error(runResp.error || "run request failed");
            }
            progress.report({ increment: 50, message: "fetching findings..." });

            // Aurora's /api/state returns the assembled finding view.
            const stateResp = await getJson(`${studioUrl}/api/state`);
            renderResultsWebview(filePath, stateResp);
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            vscode.window.showErrorMessage(`Aurora failed: ${msg}`);
        }
    });
}

/**
 * Open the Aurora Studio in an external browser (default) or webview.
 */
async function openStudio(): Promise<void> {
    const studioUrl = getStudioUrl();
    const studioOk = await pingStudio(studioUrl);
    if (!studioOk) {
        vscode.window.showWarningMessage(
            `Aurora Studio isn't reachable at ${studioUrl}. ` +
            `Start it with \`python studio_api.py\`.`);
        return;
    }
    vscode.env.openExternal(vscode.Uri.parse(studioUrl));
}

/**
 * Save the user's selected text to a temp CSV and run Aurora on it.
 * Useful for "I have this little table inline — what does Aurora say?"
 */
async function runOnSelection(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.selection.isEmpty) {
        vscode.window.showInformationMessage(
            "Aurora: select some CSV-shaped text first.");
        return;
    }
    const text = editor.document.getText(editor.selection);
    const tmpDir = require("os").tmpdir();
    const tmpFile = path.join(tmpDir,
        `aurora_selection_${Date.now()}.csv`);
    fs.writeFileSync(tmpFile, text, { encoding: "utf-8" });
    await analyzeCurrentFile(vscode.Uri.file(tmpFile));
}


// ============================================================================
// Helpers
// ============================================================================

/**
 * Resolve the file path either from a context-menu click (uri arg) or
 * from the active editor. Returns null if nothing usable.
 */
async function resolveFilePath(uri?: vscode.Uri): Promise<string | null> {
    if (uri && uri.fsPath) {
        return uri.fsPath;
    }
    const editor = vscode.window.activeTextEditor;
    if (editor && editor.document.uri.fsPath) {
        return editor.document.uri.fsPath;
    }
    const picked = await vscode.window.showOpenDialog({
        canSelectFiles: true,
        canSelectFolders: false,
        canSelectMany: false,
        filters: { "Datasets": ["csv", "tsv", "parquet", "xlsx"] },
    });
    return picked && picked[0] ? picked[0].fsPath : null;
}

function getStudioUrl(): string {
    return vscode.workspace.getConfiguration("aurora")
        .get<string>("studioUrl", "http://localhost:8000");
}


/**
 * GET ping to the Studio's root. Returns true iff a successful response
 * arrives within 3 seconds.
 */
function pingStudio(baseUrl: string): Promise<boolean> {
    return new Promise((resolve) => {
        try {
            const u = new URL(baseUrl);
            const lib = u.protocol === "https:" ? https : http;
            const req = lib.get(baseUrl, { timeout: 3000 }, (res) => {
                resolve((res.statusCode || 500) < 500);
                res.resume();
            });
            req.on("error", () => resolve(false));
            req.on("timeout", () => { req.destroy(); resolve(false); });
        } catch {
            resolve(false);
        }
    });
}


function getJson(url: string): Promise<any> {
    return new Promise((resolve, reject) => {
        try {
            const u = new URL(url);
            const lib = u.protocol === "https:" ? https : http;
            lib.get(url, (res) => {
                let body = "";
                res.on("data", (chunk) => { body += chunk; });
                res.on("end", () => {
                    try { resolve(JSON.parse(body)); }
                    catch (e) { reject(e); }
                });
                res.on("error", reject);
            }).on("error", reject);
        } catch (e) { reject(e); }
    });
}


function postJson(url: string, payload: Record<string, any>): Promise<any> {
    return new Promise((resolve, reject) => {
        try {
            const u = new URL(url);
            const lib = u.protocol === "https:" ? https : http;
            const data = JSON.stringify(payload);
            const req = lib.request({
                method: "POST",
                hostname: u.hostname,
                port: u.port || (u.protocol === "https:" ? 443 : 80),
                path: u.pathname + u.search,
                headers: {
                    "Content-Type": "application/json",
                    "Content-Length": Buffer.byteLength(data).toString(),
                },
                timeout: 600_000,  // up to 10 minutes — analyses can be long
            }, (res) => {
                let body = "";
                res.on("data", (chunk) => { body += chunk; });
                res.on("end", () => {
                    try { resolve(JSON.parse(body)); }
                    catch (e) { reject(e); }
                });
                res.on("error", reject);
            });
            req.on("error", reject);
            req.on("timeout", () => { req.destroy(new Error("Aurora request timed out")); });
            req.write(data);
            req.end();
        } catch (e) { reject(e); }
    });
}


/**
 * Render the Aurora /api/state response as a webview panel with the
 * top findings. Pure HTML — no JS — so VS Code's webview sandbox
 * doesn't need any special permissions.
 */
function renderResultsWebview(filePath: string, state: any): void {
    const panel = vscode.window.createWebviewPanel(
        "auroraResults",
        `Aurora · ${path.basename(filePath)}`,
        vscode.ViewColumn.Beside,
        { enableScripts: false, retainContextWhenHidden: true },
    );
    panel.webview.html = renderHtml(filePath, state);
}


function renderHtml(filePath: string, state: any): string {
    const s = state?.state || {};
    const findings: any[] = s.findings || [];
    const dataset = s.dataset || {};
    const runMeta = s.run_meta || {};

    const severityCounts: Record<string, number> = {};
    for (const f of findings) {
        const sev = (f.severity || "info").toLowerCase();
        severityCounts[sev] = (severityCounts[sev] || 0) + 1;
    }

    const severityPills = Object.entries(severityCounts)
        .map(([sev, n]) => {
            const colour =
                sev === "crit" ? "#ff4f6b" :
                sev === "warn" ? "#ffb84a" :
                sev === "info" ? "#5aa9ff" : "#888";
            return `<span style="display:inline-block;padding:2px 10px;margin-right:6px;border-radius:12px;background:${colour};color:white;font-size:0.85em;font-weight:600">${sev} ${n}</span>`;
        }).join("");

    const rows = findings.slice(0, 50).map(f => `
        <tr>
            <td style="padding:6px 10px;">${escapeHtml(f.severity || "info")}</td>
            <td style="padding:6px 10px;">${escapeHtml(f.method || "")}</td>
            <td style="padding:6px 10px;">${escapeHtml(f.title || "")}</td>
        </tr>
    `).join("");

    return `<!doctype html>
<html><head><meta charset="utf-8" />
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         padding: 16px; line-height: 1.4; }
  h1 { font-size: 1.2em; margin: 0 0 6px 0; }
  table { border-collapse: collapse; width: 100%; margin-top: 12px; }
  th, td { border-bottom: 1px solid rgba(128,128,128,0.3); text-align: left; }
  th { font-weight: 600; padding: 6px 10px; background: rgba(128,128,128,0.1); }
  code { background: rgba(128,128,128,0.1); padding: 1px 4px; border-radius: 3px; }
  .meta { color: rgba(128,128,128,0.85); margin-bottom: 12px; }
</style></head>
<body>
  <h1>Aurora · <code>${escapeHtml(path.basename(filePath))}</code></h1>
  <div class="meta">
    ${dataset.rows || "?"} rows × ${dataset.cols || "?"} cols ·
    SHA-256 <code>${escapeHtml((dataset.sha256 || "").slice(0, 16))}…</code> ·
    Run <code>${escapeHtml(runMeta.run_id || s.run_id || "?")}</code>
  </div>
  <div>${severityPills || "<i>No findings yet.</i>"}</div>
  <table>
    <thead><tr>
      <th>Severity</th><th>Method</th><th>Title</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <p style="margin-top: 18px; color: rgba(128,128,128,0.75); font-size: 0.85em;">
    Showing ${Math.min(findings.length, 50)} of ${findings.length} findings.
    Open the Studio for the full interactive view.
  </p>
</body></html>`;
}


function escapeHtml(s: any): string {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

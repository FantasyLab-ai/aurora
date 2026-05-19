/**
 * Aurora — Excel task pane client.
 *
 * This file runs INSIDE Excel's task-pane iframe. The task-pane HTML
 * (served by Aurora Studio at /excel-taskpane.html) loads this script
 * via the Office.js runtime.
 *
 * Responsibilities:
 *
 *   1. Read the user's currently-selected range via Office.js.
 *   2. Convert it to a CSV string (in-memory, never written to disk).
 *   3. POST it to Aurora Studio's /api/run endpoint with depth=quick.
 *   4. Poll /api/state until the run completes.
 *   5. Render the findings list inline in the task pane.
 *
 * Once the user sees the findings, they can click "Open Studio" to
 * jump to the full UI in their browser for the rich exploration view.
 *
 * Architecture: same as the VS Code extension — Excel is a thin
 * client, Aurora Studio is the engine.
 */

/* global Office, console, fetch */

Office.onReady(({ host }) => {
    if (host !== Office.HostType.Excel) {
        document.body.innerText =
            "Aurora is only supported in Excel for now. " +
            "We'll ship a Sheets companion soon.";
        return;
    }
    document.getElementById("analyze-btn")
        .addEventListener("click", onAnalyzeClick);
});


async function onAnalyzeClick() {
    const status = document.getElementById("status");
    const findings = document.getElementById("findings");
    status.textContent = "Reading selection…";
    findings.innerHTML = "";

    try {
        const csv = await readSelectionAsCsv();
        if (!csv) {
            status.textContent = "Select a range with headers + numeric data first.";
            return;
        }

        const studio = getStudioUrl();
        status.textContent = "Sending to Aurora…";

        // POST the CSV. The Studio writes it to disk for the audit trail.
        const run = await postJson(`${studio}/api/run/inline-csv`, {
            csv,
            depth: "quick",
        });
        if (!run.ok) {
            throw new Error(run.error || "Aurora run failed");
        }

        status.textContent = "Analysing…";
        // Poll for completion. Most quick-depth runs finish in 30-60s.
        const state = await pollUntilDone(`${studio}/api/state`, 90_000);
        renderFindings(state, findings);
        status.textContent = `${(state.state?.findings || []).length} findings ready.`;
    } catch (err) {
        status.textContent = "Error: " + (err && err.message || err);
    }
}


/**
 * Read the active selection and convert to a CSV string using Office.js.
 */
function readSelectionAsCsv() {
    return new Promise((resolve, reject) => {
        Excel.run(async (context) => {
            const range = context.workbook.getSelectedRange();
            range.load(["values", "rowCount", "columnCount"]);
            await context.sync();
            if (range.rowCount < 2 || range.columnCount < 1) {
                resolve(null);
                return;
            }
            const csv = range.values
                .map(row => row.map(escapeCsvCell).join(","))
                .join("\n");
            resolve(csv);
        }).catch(reject);
    });
}


function escapeCsvCell(v) {
    const s = String(v ?? "");
    if (s.includes(",") || s.includes('"') || s.includes("\n")) {
        return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
}


function getStudioUrl() {
    // Settings are stored in workbook custom xml when the user changes them.
    // For now: hardcode local default. Future v0.2 = settings UI.
    return "http://localhost:8000";
}


async function postJson(url, payload) {
    const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    return await r.json();
}


async function getJson(url) {
    const r = await fetch(url);
    return await r.json();
}


async function pollUntilDone(stateUrl, timeoutMs) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
        await new Promise(r => setTimeout(r, 1500));
        const s = await getJson(stateUrl);
        if (s.ok && s.state && (s.state.findings || []).length > 0) {
            return s;
        }
    }
    throw new Error("Aurora did not return findings within timeout.");
}


function renderFindings(stateResp, container) {
    const findings = stateResp?.state?.findings || [];
    if (!findings.length) {
        container.innerHTML = "<em>No findings yet.</em>";
        return;
    }
    const rows = findings.slice(0, 25).map(f => {
        const sev = (f.severity || "info").toLowerCase();
        const colour =
            sev === "crit" ? "#ff4f6b" :
            sev === "warn" ? "#ffb84a" : "#5aa9ff";
        return `<tr>
            <td style="padding:6px 8px;color:${colour};font-weight:600">${sev}</td>
            <td style="padding:6px 8px;font-size:0.85em">${escapeHtml(f.method || "")}</td>
            <td style="padding:6px 8px">${escapeHtml(f.title || "")}</td>
        </tr>`;
    }).join("");
    container.innerHTML =
        `<table style="border-collapse:collapse;width:100%;font-family:sans-serif;font-size:0.9em">
            <thead><tr style="background:rgba(128,128,128,0.1)">
                <th style="text-align:left;padding:6px 8px">Sev</th>
                <th style="text-align:left;padding:6px 8px">Method</th>
                <th style="text-align:left;padding:6px 8px">Title</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}


function escapeHtml(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

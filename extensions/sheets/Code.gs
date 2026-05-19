/**
 * Aurora — Google Sheets add-on (Apps Script).
 *
 * Companion to extensions/excel/ — same flow, different platform.
 * A user highlights a range, opens the Aurora sidebar from the
 * Extensions menu, clicks "Analyze Selection", and the sidebar shows
 * Aurora's findings.
 *
 * Architecture: Apps Script runs server-side on Google's infrastructure
 * but with access to the user's spreadsheet. The sidebar's HTML/JS
 * calls back to this Code.gs over google.script.run, and Code.gs calls
 * the user's local Aurora Studio via UrlFetchApp.
 *
 * IMPORTANT: Apps Script HTTP fetches are subject to Google's allowlist
 * rules. To talk to localhost:8000 you need:
 *
 *   - Run Aurora Studio on a machine reachable from Google's runners
 *     (i.e., NOT localhost — use an ngrok tunnel or a public IP), OR
 *   - Use a Cloud Functions proxy in front of your Aurora Studio.
 *
 * This is a known limitation. For pure local-first use, the VS Code or
 * Excel paths (which run client-side) are better fits.
 */


/* eslint-disable camelcase */


// ---------------------------------------------------------------------------
// Top-level menu wiring
// ---------------------------------------------------------------------------

/**
 * Adds the "Aurora" menu to the Extensions toolbar on document open.
 */
function onOpen() {
    SpreadsheetApp.getUi()
        .createMenu("Aurora")
        .addItem("Open Aurora Sidebar", "openSidebar")
        .addItem("Analyze Selection (quick)", "analyzeSelectionQuick")
        .addToUi();
}


/**
 * Show the Aurora sidebar built from Sidebar.html.
 */
function openSidebar() {
    const html = HtmlService.createTemplateFromFile("Sidebar")
        .evaluate()
        .setTitle("Aurora")
        .setWidth(380);
    SpreadsheetApp.getUi().showSidebar(html);
}


/**
 * Quick action: skip the sidebar and run Aurora immediately on the
 * active selection. Shows a toast + appends a sheet with findings.
 */
function analyzeSelectionQuick() {
    const ui = SpreadsheetApp.getUi();
    try {
        const findings = runAuroraOnSelection_("quick");
        renderFindingsAsSheet_(findings);
        SpreadsheetApp.getActiveSpreadsheet().toast(
            `Aurora returned ${findings.length} findings.`,
            "Aurora", 5,
        );
    } catch (err) {
        ui.alert("Aurora", String(err && err.message || err), ui.ButtonSet.OK);
    }
}


// ---------------------------------------------------------------------------
// Sidebar callbacks (invoked via google.script.run from Sidebar.html)
// ---------------------------------------------------------------------------

/**
 * Run an analysis and return findings JSON. Called from the sidebar's
 * client JS via google.script.run.withSuccessHandler(...).
 */
function analyzeFromSidebar(depth) {
    return runAuroraOnSelection_(depth || "quick");
}


// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

/**
 * Read the active range, ship it to Aurora Studio, return findings.
 */
function runAuroraOnSelection_(depth) {
    const range = SpreadsheetApp.getActiveRange();
    const values = range.getValues();
    if (!values || values.length < 2) {
        throw new Error("Select a range with at least a header row + one data row.");
    }

    // CSV-ise the range. Apps Script doesn't have a built-in csv encoder
    // we'd like, so do it inline (safe: Aurora ingests user-provided
    // data and we control the encoding).
    const csv = values
        .map(row => row.map(csvCell_).join(","))
        .join("\n");

    const props = PropertiesService.getScriptProperties();
    const studioUrl = props.getProperty("AURORA_STUDIO_URL")
        || "https://aurora.fantasylab.ai";

    const resp = UrlFetchApp.fetch(studioUrl + "/api/run/inline-csv", {
        method: "post",
        contentType: "application/json",
        payload: JSON.stringify({ csv, depth }),
        muteHttpExceptions: true,
        followRedirects: true,
    });
    const code = resp.getResponseCode();
    if (code >= 400) {
        throw new Error("Aurora returned HTTP " + code + ": "
            + resp.getContentText().slice(0, 500));
    }
    const body = JSON.parse(resp.getContentText());
    if (!body.ok) {
        throw new Error("Aurora: " + (body.error || "unknown error"));
    }

    // /api/run/inline-csv is expected to return findings inline OR a run_id
    // we then need to poll. For the initial scaffold we assume inline.
    return (body.findings || []);
}


function csvCell_(v) {
    const s = String(v == null ? "" : v);
    if (s.indexOf(",") >= 0 || s.indexOf('"') >= 0 || s.indexOf("\n") >= 0) {
        return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
}


function renderFindingsAsSheet_(findings) {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheetName = "Aurora Findings (" + new Date().toISOString().slice(0, 16) + ")";
    const sheet = ss.insertSheet(sheetName);
    const header = ["Severity", "Confidence", "Method", "Title", "Description", "Claim ID"];
    sheet.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight("bold");
    if (!findings.length) {
        sheet.getRange(2, 1).setValue("(no findings)");
        return;
    }
    const rows = findings.map(f => [
        f.severity || "info",
        f.confidence || "",
        f.method || "",
        f.title || "",
        f.description || "",
        f.claim_id || "",
    ]);
    sheet.getRange(2, 1, rows.length, header.length).setValues(rows);
    sheet.autoResizeColumns(1, header.length);
}

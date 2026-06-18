// =====================================================================
// Aurora desktop shell — main.js
// ---------------------------------------------------------------------
// Phase 1 wiring:
//   * Tab routing (Overview / Findings / Data) with shared sliding underline
//   * Sidebar routing (workspace + data sections + Aurora Studio)
//   * Frameless window controls (min / max / close) via Tauri global API
//   * Aurora API polling — health, run-state, findings, datasets
//   * Lazy-load Aurora Studio iframe when that view opens; offline fallback
//   * Run-analysis trigger via POST /api/run
//
// No bundler — vanilla ES module loaded directly by Tauri.
// =====================================================================

const TAURI = window.__TAURI__;
const AURORA_BASE = "http://127.0.0.1:8001";

const HEALTH_POLL_MS = 4000;
const STATE_POLL_MS  = 6000;


// ---------------------------------------------------------------------
// 1. Window controls — Tauri v2 global API
// ---------------------------------------------------------------------
function wireWindowControls() {
  if (!TAURI || !TAURI.window) {
    // Running outside Tauri (e.g. just file://) — leave the buttons inert.
    return;
  }
  const win = TAURI.window.getCurrentWindow
    ? TAURI.window.getCurrentWindow()
    : TAURI.window.getCurrent();
  const min = document.getElementById("winMin");
  const max = document.getElementById("winMax");
  const close = document.getElementById("winClose");
  if (min)   min.addEventListener("click",   () => win.minimize());
  if (max)   max.addEventListener("click",   () => win.toggleMaximize());
  if (close) close.addEventListener("click", () => win.close());
}


// ---------------------------------------------------------------------
// 2. Tabs + sidebar routing + sliding underline
// ---------------------------------------------------------------------
// Views the shell knows about. Sidebar entries map to these.
// Tabs map to the first three (overview/findings/data). Sidebar can
// take you to the others (methods/datasets/bundles/studio).
const VIEWS = ["overview", "findings", "data", "methods", "datasets", "bundles", "studio"];
const TABS  = ["overview", "findings", "data"];

let currentView = "overview";

function setActiveView(view) {
  if (!VIEWS.includes(view)) return;
  currentView = view;

  // Toggle .is-active on the view stack.
  document.querySelectorAll(".view").forEach((el) => {
    el.classList.toggle("is-active", el.id === `view-${view}`);
  });

  // Sidebar.
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.classList.toggle("is-active", el.dataset.view === view);
  });

  // Tab strip — only highlights when the view maps to a tab.
  document.querySelectorAll(".tab").forEach((el) => {
    el.classList.toggle("is-active", el.dataset.tab === view);
  });
  moveTabUnderline();

  // Lazy work per view.
  if (view === "studio")   loadStudioIframe();
  if (view === "findings") refreshFindings();
  if (view === "data")     refreshData();
  if (view === "overview") refreshOverview();
}

function moveTabUnderline() {
  const bar = document.getElementById("tabUnderline");
  if (!bar) return;
  const active = document.querySelector(".tab.is-active");
  const tabbar = bar.parentElement;
  if (!active || !tabbar) {
    // No matching tab — hide the underline by zeroing width.
    bar.style.width = "0px";
    return;
  }
  // Position relative to the tabbar's box.
  const barRect    = tabbar.getBoundingClientRect();
  const activeRect = active.getBoundingClientRect();
  const x = activeRect.left - barRect.left;
  bar.style.transform = `translateX(${x}px)`;
  bar.style.width = `${activeRect.width}px`;
}

function wireTabRouting() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => setActiveView(tab.dataset.tab));
  });
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => setActiveView(item.dataset.view));
  });
  window.addEventListener("resize", moveTabUnderline);
}


// ---------------------------------------------------------------------
// 3. Aurora API client — health, state, run, datasets
// ---------------------------------------------------------------------
async function auroraFetch(path, init) {
  const url = `${AURORA_BASE}${path}`;
  try {
    const resp = await fetch(url, {
      cache: "no-store",
      headers: { Accept: "application/json" },
      ...init,
    });
    if (!resp.ok) return { ok: false, status: resp.status };
    return await resp.json();
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

async function pingAurora() {
  const r = await auroraFetch("/api/preflight");
  return r && r.ok !== false;
}


// ---------------------------------------------------------------------
// 4. Health badge in sidebar — polled
// ---------------------------------------------------------------------
let auroraOnline = false;

function setAuroraStatus(online) {
  auroraOnline = online;
  const dot = document.getElementById("auroraDot");
  const lbl = document.getElementById("auroraStatusLabel");
  const runBtn = document.getElementById("runBtn");
  if (dot) {
    dot.classList.toggle("status-dot--on", online);
    dot.classList.toggle("status-dot--off", !online);
  }
  if (lbl) lbl.textContent = online ? "aurora: online" : "aurora: offline";
  if (runBtn) runBtn.disabled = !online;
}

async function pollHealth() {
  setAuroraStatus(await pingAurora());
  setTimeout(pollHealth, HEALTH_POLL_MS);
}


// ---------------------------------------------------------------------
// 5. Overview view — stat cards from /api/state
// ---------------------------------------------------------------------
async function refreshOverview() {
  const state = await auroraFetch("/api/state");
  if (!state || state.ok === false) {
    setStat("statFindings",   "—", "awaiting run");
    setStat("statMethods",    "—", "0 fabricated");
    setStat("statAnomalies",  "—", "crit + warn");
    setStat("statRegimes",    "—", "hmm latent states");
    setText("overviewRunId", auroraOnline ? "no active run" : "aurora offline");
    return;
  }

  const s = state.state || state;
  const findings  = Array.isArray(s.findings)  ? s.findings  : [];
  const anomalies = Array.isArray(s.anomalies) ? s.anomalies : [];
  const methods   = Array.isArray(s.methods)   ? s.methods   : (s.methods_used || []);
  const fabricated = Number(s.fabricated_count || 0);

  const critN = findings.filter((f) => f.severity === "crit").length;
  const warnN = findings.filter((f) => f.severity === "warn").length;

  const regimesObj = s.regimes || s.system_model || {};
  const regimes = regimesObj.n_states || regimesObj.k ||
    (Array.isArray(regimesObj.states) ? regimesObj.states.length : "—");

  setStat("statFindings",  findings.length || 0, `${critN} critical · ${warnN} warnings`);
  setStat("statMethods",   methods.length  || 0, `${fabricated} fabricated`);
  setStat("statAnomalies", anomalies.length || critN, `${critN} crit · ${warnN} warn`);
  setStat("statRegimes",   regimes, regimesObj.method ? `via ${regimesObj.method}` : "hmm latent states");

  const runId = s.run_id || s.run_dir || state.run_dir || "current run";
  setText("overviewRunId", String(runId).split(/[\\/]/).pop().slice(0, 56));
}

function setStat(id, val, sub) {
  setText(id, val == null ? "—" : String(val));
  const subEl = document.getElementById(id + "Sub");
  if (subEl) subEl.textContent = sub;
}
function setText(id, t) {
  const el = document.getElementById(id);
  if (el) el.textContent = t;
}


// ---------------------------------------------------------------------
// 6. Findings view — card grid from /api/state.findings
// ---------------------------------------------------------------------
let _findingsCache = [];
let _findingsFilter = "all";

async function refreshFindings() {
  const state = await auroraFetch("/api/state");
  if (!state || state.ok === false) {
    renderFindings([]);
    return;
  }
  const s = state.state || state;
  _findingsCache = Array.isArray(s.findings) ? s.findings : [];
  const runId = s.run_id || state.run_dir || "current run";
  setText("findingsRunId", String(runId).split(/[\\/]/).pop().slice(0, 56));
  renderFindings(_findingsCache);
}

function renderFindings(list) {
  const grid = document.getElementById("findingsGrid");
  if (!grid) return;
  const filtered = _findingsFilter === "all"
    ? list
    : list.filter((f) => (f.severity || "info") === _findingsFilter);

  if (!filtered.length) {
    grid.innerHTML = `<div class="empty-state">
      No findings ${_findingsFilter === "all" ? "" : `with severity ${_findingsFilter}`} for this run.
    </div>`;
    return;
  }

  grid.innerHTML = filtered.map((f, i) => {
    const sev = (f.severity || "info").toLowerCase();
    const title = esc(f.title || f.name || "(untitled)");
    const sub   = esc(f.description || f.summary || "");
    const meta  = esc(`${(f.method || "—")} · row ${f.row != null ? f.row : "—"}`);
    return `<article class="finding-card" data-finding-idx="${i}">
      <span class="finding-sev finding-sev--${sev}">${sev}</span>
      <div class="finding-title">${title}</div>
      <div class="finding-sub">${sub}</div>
      <div class="finding-meta">${meta}</div>
    </article>`;
  }).join("");

  // Wire click-to-detail. Index is local to the filtered list so the user
  // sees the finding they clicked, not whatever is at that index in the
  // unfiltered cache.
  grid.querySelectorAll(".finding-card").forEach((card) => {
    card.addEventListener("click", () => {
      const idx = parseInt(card.dataset.findingIdx, 10);
      if (!Number.isNaN(idx) && filtered[idx]) openFindingDetail(filtered[idx]);
    });
  });
}

function wireFindingsFilters() {
  document.querySelectorAll(".chip-filter").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip-filter").forEach((c) => c.classList.remove("is-active"));
      chip.classList.add("is-active");
      _findingsFilter = chip.dataset.sev;
      renderFindings(_findingsCache);
    });
  });
}


// ---------------------------------------------------------------------
// 7. Data view — dataset preview table
// ---------------------------------------------------------------------
async function refreshData() {
  const state = await auroraFetch("/api/state");
  const wrap = document.getElementById("dataTableWrap");
  if (!wrap) return;
  if (!state || state.ok === false) {
    wrap.innerHTML = `<div class="empty-state">No dataset loaded.
      Open <b>Aurora Studio</b> to upload a CSV, then come back.</div>`;
    return;
  }

  const s = state.state || state;
  const ds = s.dataset || {};
  const cols = ds.columns || ds.column_names || [];
  const preview = ds.preview || ds.head || [];

  const runId = s.run_id || state.run_dir || "current dataset";
  setText("dataRunId", String(runId).split(/[\\/]/).pop().slice(0, 56));

  if (!cols.length || !preview.length) {
    wrap.innerHTML = `<div class="empty-state">
      Dataset preview unavailable. Open <b>Aurora Studio</b> for full data inspection.
    </div>`;
    return;
  }

  const head = cols.map((c) => `<th>${esc(c.name || c)}</th>`).join("");
  const rows = preview.slice(0, 25).map((row) => {
    const cells = (Array.isArray(row) ? row : cols.map((c) => row[c.name || c]))
      .map((v) => `<td>${esc(v == null ? "—" : String(v))}</td>`).join("");
    return `<tr>${cells}</tr>`;
  }).join("");

  wrap.innerHTML = `<table class="data-table"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
}


// ---------------------------------------------------------------------
// 8. Run trigger — POST /api/run
// ---------------------------------------------------------------------
async function refreshDatasetOptions() {
  // Hardcoded fixture list for Phase 1 — the same files demos/README references.
  const fixtures = [
    { value: "data/fixtures/factory_bearing_demo.csv",        label: "factory_bearing_demo.csv" },
    { value: "demos/datasets/server_metrics/server_metrics.csv", label: "server_metrics.csv" },
    { value: "demos/datasets/falling_ball/falling_ball.csv",     label: "falling_ball.csv" },
    { value: "data/fixtures/climate_buoy_demo.csv",            label: "climate_buoy_demo.csv" },
    { value: "data/fixtures/patient_cohort_demo.csv",          label: "patient_cohort_demo.csv" },
  ];
  const sel = document.getElementById("runDatasetSelect");
  if (!sel) return;
  sel.innerHTML = `<option value="">— pick a dataset —</option>` +
    fixtures.map((f) => `<option value="${esc(f.value)}">${esc(f.label)}</option>`).join("");
}

async function runWithPath(path) {
  // Shared helper: dropdown trigger AND drag-drop trigger both come through
  // here so the run-hint state machine is consistent in both flows.
  const hint = document.getElementById("runHint");
  const filename = String(path).split(/[\\/]/).pop();
  if (hint) hint.textContent = `submitting run for ${filename}…`;
  const r = await auroraFetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset: path }),
  });
  if (r && r.ok !== false) {
    if (hint) hint.innerHTML = `submitted · <code>${esc(r.run_id || "(running)")}</code>`;
    setTimeout(refreshOverview, 1500);
  } else {
    if (hint) hint.innerHTML =
      `<span style="color:var(--crit)">run failed: ${esc(r.error || "see Aurora Studio log")}</span>`;
  }
}

async function triggerRun() {
  const sel = document.getElementById("runDatasetSelect");
  const hint = document.getElementById("runHint");
  if (!sel || !sel.value) {
    if (hint) hint.innerHTML = "<b style=\"color:var(--warn)\">pick a dataset first.</b>";
    return;
  }
  await runWithPath(sel.value);
}


// ---------------------------------------------------------------------
// 8b. Drag-and-drop CSV → /api/run
// ---------------------------------------------------------------------
// Tauri emits four events on the webview during a drag operation. We
// highlight the drop-zone visual while a drag is active and POST the
// dropped file's absolute path to Aurora when it lands. Multiple files
// dropped → take the first that ends in .csv (then any file as fallback).
async function wireDragDrop() {
  if (!TAURI || !TAURI.event) return;
  const zone = document.getElementById("dropZone");
  const setActive = (on) => zone && zone.classList.toggle("is-active", on);

  try {
    await TAURI.event.listen("tauri://drag-enter", () => setActive(true));
    await TAURI.event.listen("tauri://drag-over",  () => setActive(true));
    await TAURI.event.listen("tauri://drag-leave", () => setActive(false));
    await TAURI.event.listen("tauri://drag-drop",  async (event) => {
      setActive(false);
      const paths = (event && event.payload && event.payload.paths) || [];
      if (!paths.length) return;
      const csv = paths.find((p) => /\.csv$/i.test(p)) || paths[0];
      setActiveView("overview");
      await runWithPath(csv);
    });
  } catch (err) {
    console.warn("drag-drop wiring failed:", err);
  }
}


// ---------------------------------------------------------------------
// 8c. Finding detail panel — click a card, side panel slides in
// ---------------------------------------------------------------------
function openFindingDetail(finding) {
  const panel = document.getElementById("detailPanel");
  const backdrop = document.getElementById("detailBackdrop");
  const body = document.getElementById("detailPanelBody");
  const title = document.getElementById("detailPanelTitle");
  if (!panel || !body || !title) return;

  const sev = (finding.severity || "info").toLowerCase();
  title.textContent = `${sev.toUpperCase()} · ${finding.method || "finding"}`;

  // Pull evidence — Aurora puts it under .evidence on the finding, but
  // also exposes a few common fields at the top level.
  const ev = finding.evidence || {};
  const zScore = finding.z_score || finding.z || ev.z || ev.z_score;
  const row    = finding.row != null ? finding.row : (ev.row != null ? ev.row : ev.row_idx);

  const rows = [
    ["severity",   sev],
    ["method",     finding.method || "—"],
    ["title",      finding.title || finding.name || "—"],
    ["description",finding.description || finding.summary || "—"],
    ["row",        row != null ? row : "—"],
    ["z-score",    zScore != null ? Number(zScore).toFixed(3) : "—"],
    ["confidence", finding.confidence != null ? finding.confidence : "—"],
    ["claim_id",   finding.claim_id || "—"],
    ["fabricated", finding.fabricated != null ? finding.fabricated : 0],
  ];

  const rowsHtml = rows.map(([k, v]) => {
    let cls = "detail-row-val";
    if (k === "claim_id" || k === "method") cls += " detail-row-val--mono";
    if (k === "z-score" && v !== "—")       cls += " detail-row-val--num";
    return `<div class="detail-row">
      <span class="detail-row-key">${esc(k)}</span>
      <span class="${cls}">${esc(String(v))}</span>
    </div>`;
  }).join("");

  // If the finding carries a method_spec / citation, surface it at the bottom.
  let citation = "";
  const spec = finding.method_spec || ev.method_spec;
  if (spec) {
    const cite = (spec.citation && spec.citation.text) || spec.source || spec.cite || "";
    if (cite) {
      citation = `<div class="detail-row" style="border-bottom:none; margin-top:var(--sp-4);">
        <span class="detail-row-key">citation</span>
        <span class="detail-row-val" style="color:var(--mint); font-size:11px;">${esc(cite)}</span>
      </div>`;
    }
  }

  body.innerHTML = rowsHtml + citation;
  panel.classList.add("is-visible");
  backdrop.classList.add("is-visible");
  panel.setAttribute("aria-hidden", "false");
}

function closeFindingDetail() {
  const panel = document.getElementById("detailPanel");
  const backdrop = document.getElementById("detailBackdrop");
  if (panel)    { panel.classList.remove("is-visible");
                  panel.setAttribute("aria-hidden", "true"); }
  if (backdrop) { backdrop.classList.remove("is-visible"); }
}

function wireDetailPanel() {
  const close    = document.getElementById("detailPanelClose");
  const backdrop = document.getElementById("detailBackdrop");
  if (close)    close.addEventListener("click", closeFindingDetail);
  if (backdrop) backdrop.addEventListener("click", closeFindingDetail);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeFindingDetail();
  });
}


// ---------------------------------------------------------------------
// 9. Studio iframe — lazy-load + offline fallback
// ---------------------------------------------------------------------
function loadStudioIframe() {
  const frame   = document.getElementById("studioFrame");
  const offline = document.getElementById("studioOffline");
  if (!frame || !offline) return;
  if (auroraOnline) {
    offline.classList.remove("is-visible");
    if (frame.src === "about:blank") frame.src = AURORA_BASE + "/";
  } else {
    offline.classList.add("is-visible");
    frame.src = "about:blank";
  }
  setText("studioUrl", AURORA_BASE);
}

function wireStudioControls() {
  const reload = document.getElementById("studioReload");
  const retry  = document.getElementById("studioRetry");
  const reloadFn = async () => {
    setAuroraStatus(await pingAurora());
    loadStudioIframe();
  };
  if (reload) reload.addEventListener("click", reloadFn);
  if (retry)  retry.addEventListener("click", reloadFn);
}


// ---------------------------------------------------------------------
// 10. Util — minimal HTML escape so user-supplied strings can't break the DOM
// ---------------------------------------------------------------------
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  }[c]));
}


// ---------------------------------------------------------------------
// 11. Boot
// ---------------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
  wireWindowControls();
  wireTabRouting();
  wireFindingsFilters();
  wireStudioControls();
  wireDragDrop();
  wireDetailPanel();

  const runBtn = document.getElementById("runBtn");
  if (runBtn) runBtn.addEventListener("click", triggerRun);

  refreshDatasetOptions();

  // First paint of the underline (waits one frame so layout is settled).
  requestAnimationFrame(() => {
    requestAnimationFrame(moveTabUnderline);
  });

  // Kick off polling. pollHealth() also drives the run-button enable state.
  pollHealth();
  setInterval(() => {
    if (auroraOnline && currentView === "overview")  refreshOverview();
    if (auroraOnline && currentView === "findings")  refreshFindings();
    if (auroraOnline && currentView === "data")      refreshData();
  }, STATE_POLL_MS);
});

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

// Version banner -- bump this whenever main.js changes meaningfully.
// Shows in the WebView2 console so we can verify the right JS loaded
// (WebView2 sometimes caches main.js across builds despite Tauri
// bundling fresh assets every time).
const AURORA_SHELL_VERSION = "phase-2.5-poll-run-status";
console.log(`%c[aurora-shell] ${AURORA_SHELL_VERSION}`,
            "color:#6ee7ff;font-weight:bold;");


// ---------------------------------------------------------------------
// Run state machine -- tracks the in-flight run so the Overview/Findings
// views stay accurate. Aurora's /api/state returns the LAST COMPLETED
// run, so without this the stat cards keep showing the previous run's
// numbers until the new run finishes (which can be confusing -- "I just
// submitted X but I see Y's data"). The machine polls /api/state until
// state.run_id matches the run_id we just submitted, then refreshes
// every view.
// ---------------------------------------------------------------------
const RUN_POLL_MS    = 1500;    // tight cadence while a run is in flight
const RUN_POLL_MAX_S = 600;     // give up after 10 min (sanity cap)

let _activeRun = null;          // { runId, submittedAt, filename } | null
let _elapsedTimer = null;


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
  if (view === "studio")    loadStudioIframe();
  if (view === "findings")  refreshFindings();
  if (view === "data")      refreshData();
  if (view === "overview")  refreshOverview();
  if (view === "methods")   refreshMethods();
  if (view === "datasets")  refreshDatasets();
  if (view === "bundles")   refreshBundles();
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
  // Bulletproof health check: just hit Aurora's root URL with a short
  // timeout. If we get ANY HTTP response back (200/404/500/whatever),
  // Aurora's process is alive and serving. The ONLY thing that should
  // register as offline is a network-level failure -- ECONNREFUSED,
  // ENOTFOUND, or the request timing out.
  //
  // Previous versions read /api/preflight's body.ok field, but that
  // endpoint legitimately returns {ok:false} on an idle backend with no
  // run loaded -- a "I'm up, waiting for you" response, not a death
  // rattle. Even resp.status < 500 wasn't enough in practice because
  // WebView2 sometimes cached the old function. Hitting / with a 2s
  // AbortSignal is the simplest possible probe.
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2000);
    await fetch(`${AURORA_BASE}/`, {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
    });
    clearTimeout(timer);
    return true;
  } catch (err) {
    // ECONNREFUSED, timeout, or any other network-level failure.
    return false;
  }
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
async function refreshOverview(runDir) {
  // Don't overwrite the "running…" state with stale data while we're
  // waiting for a submitted run to complete -- the state machine will
  // call refreshOverview() with the right run_dir once it completes.
  if (_activeRun) return;

  const url = runDir
    ? `/api/state?run_dir=${encodeURIComponent(runDir)}`
    : "/api/state";
  const state = await auroraFetch(url);
  if (!state || state.ok === false) {
    setStat("statFindings",   "—", "awaiting run");
    setStat("statMethods",    "—", "0 fabricated");
    setStat("statAnomalies",  "—", "crit + warn");
    setStat("statRegimes",    "—", "hmm latent states");
    setText("overviewRunId", auroraOnline ? "no active run" : "aurora offline");
    _renderNarrative(null);
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

  _renderNarrative(s, findings);
}

function _renderNarrative(state, findings) {
  const card = document.getElementById("narrativeCard");
  if (!card) return;
  if (!state) { card.hidden = true; return; }

  // Aurora puts the synthesized prose in interpretive_summary; the
  // narrative_engine.summary is a structured fallback.
  const prose = state.interpretive_summary
              || (state.narrative_engine && state.narrative_engine.summary)
              || (state.overview && state.overview.summary)
              || "";
  const confidence = state.confidence || (state.run_meta && state.run_meta.confidence);

  if (!prose && !findings.length) { card.hidden = true; return; }
  card.hidden = false;

  const conf = document.getElementById("narrativeConfidence");
  if (conf) {
    if (confidence != null) {
      const pct = (Number(confidence) * 100).toFixed(0);
      conf.textContent = `confidence ${pct}%`;
      conf.style.color = confidence >= 0.7 ? "var(--mint)" :
                          confidence >= 0.4 ? "var(--amber)" : "var(--crit)";
    } else {
      conf.textContent = "";
    }
  }

  const body = document.getElementById("narrativeBody");
  if (body) {
    body.textContent = prose
      ? String(prose).trim().slice(0, 900)
      : "Aurora has no narrative for this run -- check the Findings tab for raw insights.";
  }

  // Top 3 most-severe / highest-confidence findings as bullet list.
  const insights = document.getElementById("narrativeInsights");
  if (insights) {
    const top = (findings || [])
      .filter(f => f && (f.title || f.description))
      .sort((a, b) => {
        const order = { crit: 0, warn: 1, info: 2 };
        return (order[a.severity] ?? 3) - (order[b.severity] ?? 3);
      })
      .slice(0, 3);
    if (!top.length) { insights.innerHTML = ""; return; }
    insights.innerHTML = "<div class=\"narrative-insights-label\">KEY INSIGHTS</div>" +
      top.map(f => {
        const sev = (f.severity || "info").toLowerCase();
        return `<div class="narrative-insight">
          <span class="narrative-insight-sev finding-sev--${sev}">${esc(sev)}</span>
          <div class="narrative-insight-text">
            <div class="narrative-insight-title">${esc(f.title || f.name || "(untitled)")}</div>
            <div class="narrative-insight-method">${esc(f.method || "—")}</div>
          </div>
        </div>`;
      }).join("");
  }
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

async function refreshFindings(runDir) {
  const url = runDir
    ? `/api/state?run_dir=${encodeURIComponent(runDir)}`
    : "/api/state";
  const state = await auroraFetch(url);
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
async function refreshData(runDir) {
  const url = runDir
    ? `/api/state?run_dir=${encodeURIComponent(runDir)}`
    : "/api/state";
  const state = await auroraFetch(url);
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
// 7b. Methods view — table of analytical methods that ran on this run
// ---------------------------------------------------------------------
async function refreshMethods(runDir) {
  const wrap = document.getElementById("methodsList");
  if (!wrap) return;
  if (!auroraOnline) {
    wrap.innerHTML = renderEmpty("Aurora offline. Start <b>studio_api.py</b> to populate this view.");
    return;
  }
  const url = runDir
    ? `/api/state?run_dir=${encodeURIComponent(runDir)}`
    : "/api/state";
  const state = await auroraFetch(url);
  if (!state || state.ok === false) {
    wrap.innerHTML = renderEmpty("No active run. Run an analysis from <b>Overview</b>.");
    return;
  }
  const s = state.state || state;
  const findings = Array.isArray(s.findings) ? s.findings : [];
  // Build a method tally directly from findings — same source the dominant-
  // method enrichment uses on the backend.
  const tally = new Map();
  for (const f of findings) {
    const m = String((f && f.method) || "").trim();
    if (!m) continue;
    tally.set(m, (tally.get(m) || 0) + 1);
  }
  if (!tally.size) {
    wrap.innerHTML = renderEmpty("No methods recorded for this run yet.");
    return;
  }
  const total = findings.length;
  const rows = [...tally.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([method, count]) => {
      const pct = total ? Math.round((count / total) * 100) : 0;
      return `<tr>
        <td><code>${esc(method)}</code></td>
        <td style="text-align:right;">${count}</td>
        <td style="width:40%;">
          <div class="bar-track">
            <div class="bar-fill" style="width:${pct}%;"></div>
          </div>
        </td>
        <td style="text-align:right;color:var(--ink-faint);">${pct}%</td>
      </tr>`;
    }).join("");
  wrap.innerHTML = `<table class="data-table">
    <thead><tr>
      <th>method</th>
      <th style="text-align:right;">findings</th>
      <th>share</th>
      <th style="text-align:right;">%</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}


// ---------------------------------------------------------------------
// 7c. Datasets view — bundled fixtures + generated demo datasets
// ---------------------------------------------------------------------
async function refreshDatasets() {
  const wrap = document.getElementById("datasetsList");
  if (!wrap) return;
  if (!auroraOnline) {
    wrap.innerHTML = renderEmpty("Aurora offline. Start <b>studio_api.py</b> to list datasets.");
    return;
  }
  const r = await auroraFetch("/api/demo_datasets");
  if (!r || r.ok === false || !Array.isArray(r.datasets || r)) {
    // Fallback: hardcoded fixture list matching the runDatasetSelect dropdown.
    const fallback = [
      { path: "data/fixtures/factory_bearing_demo.csv",           label: "factory_bearing_demo",     domain: "manufacturing" },
      { path: "demos/datasets/server_metrics/server_metrics.csv", label: "server_metrics",           domain: "ops" },
      { path: "demos/datasets/falling_ball/falling_ball.csv",     label: "falling_ball",             domain: "physics" },
      { path: "data/fixtures/climate_buoy_demo.csv",              label: "climate_buoy_demo",        domain: "climate" },
      { path: "data/fixtures/patient_cohort_demo.csv",            label: "patient_cohort_demo",      domain: "biomed" },
    ];
    wrap.innerHTML = renderDatasetGrid(fallback);
    return;
  }
  const list = (r.datasets || r).map((d) => ({
    path:   d.path || d.file || d.dataset || "",
    label:  d.name || d.label || (d.path || "").split(/[\\/]/).pop().replace(/\.[^.]+$/, ""),
    domain: d.domain || d.category || "—",
    rows:   d.rows || d.row_count || null,
    cols:   d.cols || d.column_count || null,
  }));
  wrap.innerHTML = renderDatasetGrid(list);
  // Wire click-to-run on every dataset card so the user can fire an
  // analysis from this view without going back to Overview.
  wrap.querySelectorAll(".finding-card[data-dataset-path]").forEach((card) => {
    card.addEventListener("click", async () => {
      const path = card.dataset.datasetPath;
      if (!path) return;
      setActiveView("overview");
      await runWithPath(path);
    });
  });
}

function renderDatasetGrid(list) {
  if (!list.length) return renderEmpty("No datasets registered with Aurora.");
  return `<div class="findings-grid">
    ${list.map((d) => {
      const meta = [d.domain, d.rows && `${d.rows} rows`, d.cols && `${d.cols} cols`]
        .filter(Boolean).join(" · ");
      return `<article class="finding-card" data-dataset-path="${esc(d.path)}">
        <span class="finding-sev finding-sev--info">dataset</span>
        <div class="finding-title">${esc(d.label)}</div>
        <div class="finding-sub"><code>${esc(d.path)}</code></div>
        <div class="finding-meta">${esc(meta || "—")}</div>
      </article>`;
    }).join("")}
  </div>`;
}


// ---------------------------------------------------------------------
// 7d. Bundles view — past runs from /api/runs (Aurora's run history)
// ---------------------------------------------------------------------
async function refreshBundles() {
  const wrap = document.getElementById("bundlesList");
  if (!wrap) return;
  if (!auroraOnline) {
    wrap.innerHTML = renderEmpty("Aurora offline. Start <b>studio_api.py</b> to list saved runs.");
    return;
  }
  const r = await auroraFetch("/api/runs");
  if (!r || r.ok === false) {
    wrap.innerHTML = renderEmpty("Couldn't list runs. Check Aurora Studio is reachable.");
    return;
  }
  const runs = r.runs || r.list || (Array.isArray(r) ? r : []);
  if (!runs.length) {
    wrap.innerHTML = renderEmpty("No runs on disk yet. Trigger one from <b>Overview</b>.");
    return;
  }
  // Take the 30 most recent (already sorted by Aurora, but be defensive).
  const sorted = [...runs].sort((a, b) =>
    String(b.run_id || "").localeCompare(String(a.run_id || ""))).slice(0, 30);
  wrap.innerHTML = `<div class="findings-grid">
    ${sorted.map((run) => {
      const id     = run.run_id || run.id || "(unknown)";
      const dataset = run.dataset || run.dataset_path || "";
      const dsName = dataset ? String(dataset).split(/[\\/]/).pop() : "—";
      const status = (run.status || run.state || "").toLowerCase();
      const sev    = status === "complete" || status === "completed" ? "info"
                    : status === "failed" || status === "error"       ? "crit"
                    : "warn";
      const findings = run.findings_count != null ? run.findings_count : "—";
      const fab    = run.fabricated_count != null ? run.fabricated_count : 0;
      return `<article class="finding-card" data-run-id="${esc(id)}">
        <span class="finding-sev finding-sev--${sev}">${esc(status || "run")}</span>
        <div class="finding-title">${esc(dsName)}</div>
        <div class="finding-sub"><code>${esc(String(id).slice(0, 36))}</code></div>
        <div class="finding-meta">findings: ${esc(String(findings))} · fabricated: ${esc(String(fab))}</div>
      </article>`;
    }).join("")}
  </div>`;
}


function renderEmpty(html) {
  return `<div class="empty-state">${html}</div>`;
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

  // Stop any previous run we were tracking.
  _stopElapsedTimer();
  _activeRun = null;

  // Immediate visual: clear stale stats, show "submitting" state.
  _showRunningState(filename, "submitting");

  if (hint) hint.textContent = `submitting run for ${filename}…`;
  const r = await auroraFetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset: path }),
  });

  if (!r || r.ok === false) {
    if (hint) hint.innerHTML =
      `<span style="color:var(--crit)">run failed: ${esc((r && r.error) || "see Aurora Studio log")}</span>`;
    _showRunningState(filename, "failed");
    return;
  }

  // Submission accepted. Aurora's /api/state will still return the
  // PREVIOUS run until this one completes, so we now poll until we see
  // the new run_id in /api/state -- that's our completion signal.
  const newRunId = r.run_id || null;
  _activeRun = {
    runId: newRunId,
    submittedAt: Date.now(),
    filename,
  };
  if (hint) hint.innerHTML = newRunId
    ? `running · <code>${esc(newRunId)}</code>`
    : `running · <code>(pending)</code>`;
  _showRunningState(filename, "running");
  _startElapsedTimer();
  pollUntilRunComplete();
}

function _showRunningState(filename, phase, cached) {
  // phase: "submitting" | "running" | "complete" | "failed"
  // cached: optional bool -- when true on completion, surface the
  //         cache-hit so the user understands why a 30-min analysis
  //         "finished" in under a second.
  const banner = document.getElementById("runStatusBanner");
  if (banner) {
    banner.hidden = false;
    banner.className = `run-banner run-banner--${phase}`;
    let label = "";
    if (phase === "submitting") label = `submitting <b>${esc(filename)}</b>…`;
    else if (phase === "running") label = `analyzing <b>${esc(filename)}</b> · <span id="runElapsed">0.0s</span>`;
    else if (phase === "complete") {
      const cacheTag = cached ? ` <span class="run-banner-tag">cache hit</span>` : "";
      label = `complete · <b>${esc(filename)}</b>${cacheTag}`;
    }
    else if (phase === "failed")  label = `failed · <b>${esc(filename)}</b>`;
    banner.innerHTML = `<span class="run-banner-dot"></span><span class="run-banner-text">${label}</span>`;
  }
  // Also blank the stat cards while running so the user doesn't see
  // stale numbers from the previous run.
  if (phase === "submitting" || phase === "running") {
    setStat("statFindings",  "—", "running…");
    setStat("statMethods",   "—", "running…");
    setStat("statAnomalies", "—", "running…");
    setStat("statRegimes",   "—", "running…");
  }
}

function _startElapsedTimer() {
  _stopElapsedTimer();
  _elapsedTimer = setInterval(() => {
    if (!_activeRun) { _stopElapsedTimer(); return; }
    const el = document.getElementById("runElapsed");
    if (el) {
      const s = ((Date.now() - _activeRun.submittedAt) / 1000).toFixed(1);
      el.textContent = `${s}s`;
    }
  }, 100);
}

function _stopElapsedTimer() {
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
}

async function pollUntilRunComplete() {
  if (!_activeRun) return;
  const elapsedS = (Date.now() - _activeRun.submittedAt) / 1000;
  if (elapsedS > RUN_POLL_MAX_S) {
    // Sanity bail-out -- something's wrong.
    _activeRun = null;
    _stopElapsedTimer();
    const hint = document.getElementById("runHint");
    if (hint) hint.innerHTML = `<span style="color:var(--warn)">run timed out at ${RUN_POLL_MAX_S}s -- check Aurora Studio log.</span>`;
    return;
  }

  // Poll the AUTHORITATIVE endpoint: /api/run/status?run_id=X tells us
  // exactly whether OUR submitted run is done. /api/state was wrong
  // because it returns the global "latest" run, which doesn't update
  // for cache hits and may not reflect the run we just submitted.
  const status = await auroraFetch(
    `/api/run/status?run_id=${encodeURIComponent(_activeRun.runId)}`
  );

  if (!status || status.ok === false) {
    // Aurora hasn't registered the run yet (race on submission) -- try again.
    setTimeout(pollUntilRunComplete, RUN_POLL_MS);
    return;
  }

  const runState = (status.state || "").toLowerCase();
  if (runState === "complete" || runState === "completed") {
    const filename = _activeRun.filename;
    const runDir   = status.run_dir;
    const cached   = !!status.cached;
    _activeRun = null;
    _stopElapsedTimer();
    _showRunningState(filename, "complete", cached);
    // Refresh every view using the EXACT run_dir returned by status, so
    // we never pick up some other "latest" run that happens to be on
    // disk. Falls back to default (no run_dir) if Aurora didn't return
    // one for some reason.
    refreshOverview(runDir);
    refreshFindings(runDir);
    refreshData(runDir);
    refreshMethods(runDir);
    return;
  }

  if (runState === "failed" || runState === "error") {
    const filename = _activeRun.filename;
    _activeRun = null;
    _stopElapsedTimer();
    _showRunningState(filename, "failed");
    const hint = document.getElementById("runHint");
    if (hint) hint.innerHTML =
      `<span style="color:var(--crit)">run failed: ${esc(status.error || "see Aurora Studio log")}</span>`;
    return;
  }

  // Still pending/running -- keep polling.
  setTimeout(pollUntilRunComplete, RUN_POLL_MS);
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
// 8a. Click-to-browse — opens a native Tauri file picker
// ---------------------------------------------------------------------
// Clicking the drop zone opens the OS file dialog (native, signed by
// Microsoft on Windows, matches the rest of the desktop UX). The chosen
// file's absolute path is fed through runWithPath() — identical to the
// drag-drop branch, so both flows share the same UX feedback.
async function pickAndRun() {
  if (!TAURI || !TAURI.dialog) {
    console.warn("Tauri dialog plugin not available — click-to-browse disabled.");
    return;
  }
  try {
    const path = await TAURI.dialog.open({
      multiple: false,
      directory: false,
      title: "Pick a dataset for Aurora",
      filters: [
        { name: "Aurora datasets",
          extensions: ["csv", "tsv", "txt", "json", "jsonl", "parquet", "xlsx", "xls", "feather", "arrow"] },
        { name: "All files", extensions: ["*"] },
      ],
    });
    if (!path) return;                       // user cancelled
    const chosen = Array.isArray(path) ? path[0] : path;
    setActiveView("overview");
    await runWithPath(chosen);
  } catch (err) {
    console.warn("file picker failed:", err);
  }
}

function wireDropZoneClick() {
  const zone = document.getElementById("dropZone");
  if (!zone) return;
  zone.style.cursor = "pointer";
  zone.addEventListener("click", pickAndRun);
}


// ---------------------------------------------------------------------
// 8b. Drag-and-drop file → /api/run
// ---------------------------------------------------------------------
// Tauri 2 delivers drag-drop events to the *window*, not via the global
// event bus. The previous implementation used event.listen('tauri://...')
// which doesn't fire under Tauri 2's window-scoped drag-drop model.
//
// Switched to getCurrentWindow().onDragDropEvent(), which is the correct
// Tauri 2 API. Payload variants:
//   { type: 'enter', paths, position }
//   { type: 'over',  position }
//   { type: 'leave' }
//   { type: 'drop',  paths, position }
//
// Accept ANY file extension Aurora's pipeline knows — csv, json, jsonl,
// parquet, xlsx, xls, tsv, txt. Aurora's /api/run does the type detection
// on the server side, so we don't gate at the shell layer.
const AURORA_ACCEPTS = /\.(csv|tsv|txt|json|jsonl|parquet|xlsx|xls|feather|arrow)$/i;

async function wireDragDrop() {
  if (!TAURI || !TAURI.window) {
    console.warn("Tauri window API not available — drag-drop disabled.");
    return;
  }
  const zone = document.getElementById("dropZone");
  const setActive = (on) => zone && zone.classList.toggle("is-active", on);

  const winApi = TAURI.window.getCurrentWindow
    ? TAURI.window.getCurrentWindow()
    : TAURI.window.getCurrent();

  try {
    await winApi.onDragDropEvent(async (event) => {
      const payload = event && event.payload;
      if (!payload) return;
      const kind = payload.type;
      if (kind === "enter" || kind === "over") {
        setActive(true);
      } else if (kind === "leave") {
        setActive(false);
      } else if (kind === "drop") {
        setActive(false);
        const paths = payload.paths || [];
        if (!paths.length) return;
        // Prefer an Aurora-readable file; otherwise just take the first.
        const path = paths.find((p) => AURORA_ACCEPTS.test(p)) || paths[0];
        setActiveView("overview");
        await runWithPath(path);
      }
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
  wireDropZoneClick();
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

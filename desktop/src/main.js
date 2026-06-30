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
const AURORA_SHELL_VERSION = "phase-3.1-onboarding";

// First-run demo launcher. Cards are built from /api/demo_datasets so the
// paths are whatever the backend can actually resolve -- critically, in the
// FROZEN app those are absolute paths into the bundled data/fixtures, NOT
// repo-relative paths to files that aren't shipped. Hardcoding relative
// paths would make the demos fail in the installer.
//
// Per-dataset hook overrides (keyed by the endpoint's `id`) give newcomer-
// friendly copy -- the value, not the method name. Falls back to the
// endpoint's own subtitle/highlights for any dataset without an override.
const DEMO_HOOKS = {
  factory_bearing: {
    glyph: "▲", title: "Catch the anomaly",
    hook: "Vibration sensor data from a factory bearing. Aurora flags the fault with a cited z-score on the exact row — and zero fabricated numbers.",
  },
  climate_buoy: {
    glyph: "≈", title: "Find the regime shift",
    hook: "A year of ocean-buoy readings. Aurora pinpoints the mid-year regime change and fits the cooling law behind it — every claim cited.",
  },
  patient_cohort: {
    glyph: "⚗", title: "Cause vs. correlation",
    hook: "200 patients, no time axis. Aurora surfaces the treatment effect and refuses to mistake correlation for causation.",
  },
};

async function renderDemoLauncher() {
  const wrap = document.getElementById("demoLauncher");
  if (!wrap || wrap.dataset.rendered === "1") return;
  const r = await auroraFetch("/api/demo_datasets");
  const list = (r && Array.isArray(r.datasets)) ? r.datasets : [];
  if (!list.length) return;  // leave hidden; the drop-zone still works

  wrap.innerHTML = list.map((d) => {
    const o = DEMO_HOOKS[d.id] || {};
    const glyph  = o.glyph || "◆";
    const title  = o.title || d.title || d.id || "dataset";
    const hook   = o.hook  ||
      [d.subtitle, d.highlights].filter(Boolean).join(" — ") ||
      "A bundled demo dataset.";
    const domain = d.domain || "demo";
    return `<button class="demo-card" data-demo-path="${esc(d.path)}">
      <div class="demo-card-glyph">${esc(glyph)}</div>
      <div class="demo-card-domain">${esc(domain)}</div>
      <div class="demo-card-title">${esc(title)}</div>
      <div class="demo-card-hook">${esc(hook)}</div>
      <div class="demo-card-cta">▶ Run this demo</div>
    </button>`;
  }).join("");
  wrap.querySelectorAll(".demo-card").forEach((card) => {
    card.addEventListener("click", () => runWithPath(card.dataset.demoPath));
  });
  wrap.dataset.rendered = "1";
}

// Toggle the Overview between its two modes: the onboarding hero (no run
// loaded) and the results view (stats + narrative). Keeps the drop-zone +
// run-row visible in both so the user can always start a run.
function _setOverviewMode(mode) {
  // mode: "onboarding" | "results"
  const onboarding = document.getElementById("onboarding");
  const stats = document.getElementById("statsGrid");
  const showOnboarding = mode === "onboarding";
  if (onboarding) {
    onboarding.hidden = !showOnboarding;
    if (showOnboarding) renderDemoLauncher();
  }
  if (stats) stats.style.display = showOnboarding ? "none" : "";
}
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

// Sticky run_dir for the views. Once a run completes, every subsequent
// refresh (including the 6s background polling loop) targets THIS exact
// run_dir via /api/state?run_dir=X, rather than letting Aurora's default
// "latest" logic drift the view onto a different run.
//
// Why this exists: Aurora's "latest run" pointer doesn't update for
// cache hits, so polling /api/state without a run_dir kept rolling the
// Overview back to the old falling_ball run 6s after a factory_bearing
// completion. Sticky run_dir defeats that drift.
let _currentRunDir = null;


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

  // Lazy work per view. Use the sticky _currentRunDir so tab switches
  // never drift the view onto a different run than the one we last
  // completed.
  if (view === "studio")    loadStudioIframe();
  if (view === "findings")  refreshFindings(_currentRunDir);
  if (view === "data")      refreshData(_currentRunDir);
  if (view === "overview")  refreshOverview(_currentRunDir);
  if (view === "methods")   refreshMethods(_currentRunDir);
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
  const wasOnline = auroraOnline;
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

  // On the offline->online transition, refresh the current view immediately
  // so onboarding (or the latest run) appears the moment Aurora is reachable
  // instead of waiting for the next 6s state poll. The first boot is exactly
  // this transition.
  if (online && !wasOnline && !_activeRun) {
    if (currentView === "overview")  refreshOverview(_currentRunDir);
    if (currentView === "findings")  refreshFindings(_currentRunDir);
    if (currentView === "data")      refreshData(_currentRunDir);
  }
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
    // No run loaded. If Aurora is up, show the onboarding hero (teach by
    // doing); if it's down, keep the neutral empty state.
    setStat("statFindings",   "—", "awaiting run");
    setStat("statMethods",    "—", "0 fabricated");
    setStat("statAnomalies",  "—", "crit + warn");
    setStat("statRegimes",    "—", "hmm latent states");
    setText("overviewRunId", auroraOnline ? "no active run" : "aurora offline");
    _renderNarrative(null);
    _setOverviewMode(auroraOnline ? "onboarding" : "results");
    return;
  }

  // A real run is loaded -> results mode (hide onboarding, show stats).
  _setOverviewMode("results");

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
    wrap.innerHTML = renderEmpty(
      "No dataset state yet. Submit a run from <b>Overview</b> or pick one in <b>Bundles</b>.");
    return;
  }

  const s = state.state || state;
  const ds = s.dataset || {};
  const structure = s.structure || {};
  const cols = structure.columns || ds.columns || [];
  const runId = s.run_id || state.run_dir || "current dataset";
  setText("dataRunId", String(runId).split(/[\\/]/).pop().slice(0, 56));

  if (!cols.length) {
    wrap.innerHTML = renderEmpty(
      "No column profile available. Open <b>Aurora Studio</b> for the full dataset inspector.");
    return;
  }

  // Aurora doesn't ship raw row preview through /api/state -- the file
  // contents live elsewhere. Instead, show the COLUMN PROFILE Aurora
  // built: name, type, fill %, plus the dataset's size + cadence.
  const rowCount   = ds.rows ?? s.dataset_rows_full ?? "—";
  const colCount   = ds.cols ?? cols.length;
  const sizeMb     = ds.size_mb ?? null;
  const hasTime    = !!structure.time_axis;
  const cadence    = structure.cadence || "—";
  const dsName     = ds.name || String(runId).split(/[\\/]/).pop();
  const dsPath     = ds.path || "";

  const summary = `
    <div class="data-summary-grid">
      <div class="data-summary-card"><div class="lbl">DATASET</div><div class="val mono">${esc(dsName)}</div></div>
      <div class="data-summary-card"><div class="lbl">ROWS</div><div class="val">${esc(String(rowCount))}</div></div>
      <div class="data-summary-card"><div class="lbl">COLUMNS</div><div class="val">${esc(String(colCount))}</div></div>
      <div class="data-summary-card"><div class="lbl">TIME AXIS</div><div class="val">${hasTime ? "yes" : "no"}</div></div>
      <div class="data-summary-card"><div class="lbl">CADENCE</div><div class="val">${esc(cadence)}</div></div>
      ${sizeMb != null
        ? `<div class="data-summary-card"><div class="lbl">SIZE</div><div class="val">${(sizeMb).toFixed(2)} MB</div></div>`
        : ""}
    </div>
  `;

  const colTypeLabel = (k) => ({
    n: "numeric", c: "categorical", t: "datetime",
    b: "boolean", i: "id-like", s: "string",
  })[k] || (k || "—");

  const colRows = cols.map((c) => {
    const fill = c.fill_pct != null ? `${c.fill_pct.toFixed(0)}%` : "—";
    const fillCls = c.fill_pct >= 99 ? "ok" : c.fill_pct >= 90 ? "warn" : "low";
    return `<tr>
      <td><code>${esc(c.name)}</code></td>
      <td><span class="col-kind col-kind--${esc(c.kind || "?")}">${esc(colTypeLabel(c.kind))}</span></td>
      <td class="fill-cell"><div class="fill-bar fill-bar--${fillCls}" style="--w:${(c.fill_pct ?? 0).toFixed(0)}%"></div><span class="fill-pct">${esc(fill)}</span></td>
    </tr>`;
  }).join("");

  wrap.innerHTML = summary + `
    <table class="data-table">
      <thead><tr><th>column</th><th>type</th><th>fill</th></tr></thead>
      <tbody>${colRows}</tbody>
    </table>
    ${dsPath ? `<div class="data-foot-hint">source: <code>${esc(dsPath)}</code></div>` : ""}
  `;
}


// ---------------------------------------------------------------------
// 7b. Methods view — table of analytical methods that ran on this run
// ---------------------------------------------------------------------
async function refreshMethods(runDir) {
  const wrap = document.getElementById("methodsList");
  if (!wrap) return;
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
  const r = await auroraFetch("/api/demo_datasets");
  if (!r || r.ok === false || !Array.isArray(r.datasets || r)) {
    // Fallback: hardcoded fixture list matching the runDatasetSelect dropdown.
    const fallback = [
      { path: "data/fixtures/factory_bearing_demo.csv",           title: "Factory bearing",          domain: "industrial" },
      { path: "demos/datasets/server_metrics/server_metrics.csv", title: "Server metrics",           domain: "ops" },
      { path: "demos/datasets/falling_ball/falling_ball.csv",     title: "Falling ball",             domain: "physics" },
      { path: "data/fixtures/climate_buoy_demo.csv",              title: "Climate buoy",             domain: "enviro" },
      { path: "data/fixtures/patient_cohort_demo.csv",            title: "Patient cohort",           domain: "research" },
    ];
    wrap.innerHTML = renderDatasetGrid(fallback);
    _wireDatasetCardClicks(wrap);
    return;
  }
  // Use Aurora's RICH demo_datasets fields when available -- title,
  // subtitle, highlights, domain, size_bytes.
  const list = (r.datasets || r).map((d) => ({
    path:       d.path || d.file || d.dataset || "",
    title:      d.title || d.name || d.label || (d.path || "").split(/[\\/]/).pop().replace(/\.[^.]+$/, ""),
    subtitle:   d.subtitle || "",
    highlights: d.highlights || "",
    domain:     d.domain || d.category || "—",
    size_kb:    d.size_bytes != null ? (d.size_bytes / 1024).toFixed(0) : null,
    available:  d.available !== false,
  }));
  wrap.innerHTML = renderDatasetGrid(list);
  _wireDatasetCardClicks(wrap);
}

function _wireDatasetCardClicks(wrap) {
  // Click a dataset card -> fire that dataset through /api/run.
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
      const meta = [d.domain, d.size_kb && `${d.size_kb} KB`]
        .filter(Boolean).join(" · ");
      return `<article class="finding-card" data-dataset-path="${esc(d.path)}">
        <span class="finding-sev finding-sev--info">${esc(d.domain || "dataset")}</span>
        <div class="finding-title">${esc(d.title)}</div>
        ${d.subtitle ? `<div class="finding-sub">${esc(d.subtitle)}</div>` : ""}
        ${d.highlights ? `<div class="finding-sub" style="color:var(--ink-faint);margin-top:8px;">${esc(d.highlights.slice(0, 120))}</div>` : ""}
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
  const r = await auroraFetch("/api/runs");
  if (!r || r.ok === false) {
    wrap.innerHTML = renderEmpty(
      "Aurora hasn't reported any runs yet. Submit one from <b>Overview</b> or <b>Datasets</b>.");
    return;
  }
  const runs = r.runs || r.list || (Array.isArray(r) ? r : []);
  if (!runs.length) {
    wrap.innerHTML = renderEmpty("No runs on disk yet. Trigger one from <b>Overview</b>.");
    return;
  }
  // Take the 30 most recent (Aurora sorts by mtime desc already, but defensive).
  const sorted = [...runs].sort((a, b) =>
    Number(b.mtime || 0) - Number(a.mtime || 0)).slice(0, 30);

  wrap.innerHTML = `<div class="findings-grid">
    ${sorted.map((run) => {
      const id      = run.run_id || run.id || "(unknown)";
      const runDir  = run.run_dir || "";
      const dataset = run.dataset || run.dataset_path || "";
      const dsName  = dataset ? String(dataset).split(/[\\/]/).pop().replace(/\.csv$/i, "") : "—";
      const findings = run.n_findings != null ? run.n_findings : (run.findings_count != null ? run.findings_count : "—");
      const conf    = run.confidence != null ? `${(run.confidence * 100).toFixed(0)}%` : "—";
      const when    = run.mtime ? _fmtRelative(run.mtime) : "—";
      const pinned  = !!run.pinned;
      const sev     = pinned ? "warn" : "info";   // pinned == amber, otherwise cyan
      return `<article class="finding-card" data-run-id="${esc(id)}" data-run-dir="${esc(runDir)}">
        <span class="finding-sev finding-sev--${sev}">${pinned ? "pinned" : "run"}</span>
        <div class="finding-title">${esc(dsName)}</div>
        <div class="finding-sub"><code>${esc(String(id).split("__").pop().slice(0, 40))}</code></div>
        <div class="finding-meta">${esc(when)} · ${esc(String(findings))} findings · confidence ${esc(conf)}</div>
      </article>`;
    }).join("")}
  </div>`;

  // Click a bundle card -> load that run as the active view.
  wrap.querySelectorAll(".finding-card[data-run-dir]").forEach((card) => {
    card.addEventListener("click", () => {
      const dir = card.dataset.runDir;
      if (!dir) return;
      _currentRunDir = dir;
      // Refresh all per-run views to show this bundle's data.
      refreshOverview(_currentRunDir);
      refreshFindings(_currentRunDir);
      refreshData(_currentRunDir);
      refreshMethods(_currentRunDir);
      // Drop the user onto Overview to see what they just loaded.
      setActiveView("overview");
    });
  });
}

function _fmtRelative(unixSec) {
  // Aurora's mtime is Unix-seconds float. Format as a friendly relative
  // string so the user can eyeball "fresh" vs "yesterday" runs quickly.
  const now = Date.now() / 1000;
  const diff = now - Number(unixSec);
  if (diff < 60)       return `${diff.toFixed(0)}s ago`;
  if (diff < 3600)     return `${(diff / 60).toFixed(0)}m ago`;
  if (diff < 86400)    return `${(diff / 3600).toFixed(0)}h ago`;
  if (diff < 86400 * 7) return `${(diff / 86400).toFixed(0)}d ago`;
  const d = new Date(Number(unixSec) * 1000);
  return d.toLocaleDateString();
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
  // stale numbers from the previous run. Leave onboarding behind us --
  // the moment a run starts, we're in results mode.
  if (phase === "submitting" || phase === "running") {
    _setOverviewMode("results");
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
    // Pin the views to this run's run_dir going forward. Every poll +
    // tab-switch + visibility-change fetch will use this exact path
    // until a new run completes (or the user picks a bundle).
    _currentRunDir = runDir || null;
    refreshOverview(_currentRunDir);
    refreshFindings(_currentRunDir);
    refreshData(_currentRunDir);
    refreshMethods(_currentRunDir);
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
    // Background poll always uses the sticky run_dir so it can't drift
    // a stable view onto a different run after a cache hit.
    if (auroraOnline && currentView === "overview")  refreshOverview(_currentRunDir);
    if (auroraOnline && currentView === "findings")  refreshFindings(_currentRunDir);
    if (auroraOnline && currentView === "data")      refreshData(_currentRunDir);
  }, STATE_POLL_MS);
});

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
const AURORA_SHELL_VERSION = "phase-4.9-data-workbench-decisions";

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
let _bannerHideTimer = null;

// The run the USER just triggered + when it completed (from their POV).
// Used so the run-context bar reads "completed just now" -- even on a cache
// hit, where the bundle's own timestamp is weeks old. { runDir, at, cached }
let _lastCompletion = null;

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
let _currentDatasetName = "";   // best-effort dataset name for the share payload

// Monotonic "which run is on screen" token. Bumped every time the displayed
// run changes (a run is triggered, a run completes and pins, or a bundle is
// loaded). Every async view refresh snapshots this token BEFORE its
// `await auroraFetch(...)` and bails if it changed by the time the fetch
// resolves. This kills the "one run behind" bug: the 6s interval poll and the
// completion refresh both issue `/api/state?run_dir=X` fetches, and whichever
// resolves LAST used to win the paint -- so a slow fetch carrying the PREVIOUS
// run's run_dir would overwrite the new run. The token makes stale paints
// no-ops, so the screen always reflects the most-recently-pinned run.
let _viewToken = 0;
function _bumpViewToken() { _viewToken = (_viewToken + 1) % Number.MAX_SAFE_INTEGER; return _viewToken; }


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
const VIEWS = ["overview", "findings", "data", "methods", "phasespace", "spacetime", "forecast", "causal", "datasets", "bundles", "knowledge", "community", "studio"];
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
  if (view === "methods")    refreshMethods(_currentRunDir);
  if (view === "phasespace") refreshPhaseSpace(_currentRunDir);
  if (view === "spacetime")  refreshSpacetime(_currentRunDir);
  if (view === "forecast")   refreshForecast(_currentRunDir);
  if (view === "causal")     refreshCausal(_currentRunDir);
  if (view === "datasets")   refreshDatasets();
  if (view === "bundles")    refreshBundles();
  if (view === "knowledge")  refreshKnowledgeBank();
  if (view === "community")  refreshCommunity();
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
    const timer = setTimeout(() => controller.abort(), 4000);
    await fetch(`${AURORA_BASE}/api/ping`, {
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
let _everOnline = false;               // has the backend answered even once?
const _shellStartMs = Date.now();

function setAuroraStatus(online) {
  const wasOnline = auroraOnline;
  auroraOnline = online;
  if (online) _everOnline = true;
  const dot = document.getElementById("auroraDot");
  const lbl = document.getElementById("auroraStatusLabel");
  const runBtn = document.getElementById("runBtn");
  // Three honest states, not two: before the backend's FIRST answer we are
  // STARTING (amber), not offline — on a fresh install the engine can take
  // minutes to unpack while Windows inspects the new binaries, and a red
  // "offline" during that window reads as broken when nothing is wrong.
  const starting = !online && !_everOnline;
  if (dot) {
    dot.classList.toggle("status-dot--on", online);
    dot.classList.toggle("status-dot--warm", starting);
    dot.classList.toggle("status-dot--off", !online && !starting);
  }
  if (lbl) {
    if (online) lbl.textContent = "aurora: online";
    else if (starting) {
      const waitedS = (Date.now() - _shellStartMs) / 1000;
      lbl.textContent = waitedS > 75
        ? "aurora: first launch — a few minutes, once"
        : "aurora: starting…";
    } else lbl.textContent = "aurora: offline";
  }
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


// Parse Aurora's run_id timestamp prefix (YYYYMMDD_HHMMSS) into a friendly
// relative string. This is what kills the "is this stale?" question -- the
// user always sees HOW OLD the run on screen is.
function _relativeTimeFromMs(thenMs) {
  const sec = Math.floor((Date.now() - thenMs) / 1000);
  if (sec < 45)   return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  if (sec < 604800) return `${Math.floor(sec / 86400)}d ago`;
  return new Date(thenMs).toLocaleDateString();
}

function _relativeTimeFromRunId(runId) {
  const m = String(runId || "").match(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);
  if (!m) return "";
  const [, Y, Mo, D, H, Mi, S] = m;
  return _relativeTimeFromMs(new Date(+Y, +Mo - 1, +D, +H, +Mi, +S).getTime());
}

// Fade the transient run-banner a few seconds after completion. The
// persistent run-context bar is the lasting signal, so "complete · cache
// hit" doesn't sit on screen forever after the cards have populated.
function _scheduleBannerHide() {
  if (_bannerHideTimer) clearTimeout(_bannerHideTimer);
  _bannerHideTimer = setTimeout(() => {
    const banner = document.getElementById("runStatusBanner");
    if (banner && !_activeRun) banner.hidden = true;
  }, 4500);
}

// Wipe Findings / Data / Methods to an explicit "analyzing" placeholder when
// a run starts, so the previous run's data never sits there looking current
// while the new one computes.
function _renderViewsRunning() {
  const msg = `<div class="empty-state">▶ Analyzing… results appear here the moment the run completes.</div>`;
  const fg = document.getElementById("findingsGrid"); if (fg) fg.innerHTML = msg;
  const dt = document.getElementById("dataTableWrap"); if (dt) dt.innerHTML = msg;
  const ml = document.getElementById("methodsList");  if (ml) ml.innerHTML = msg;
  const pw = document.getElementById("phaseWrap");     if (pw) pw.innerHTML = msg;
  const sw = document.getElementById("spacetimeWrap"); if (sw) sw.innerHTML = msg;
  const fw = document.getElementById("forecastWrap");  if (fw) fw.innerHTML = msg;
}

// Refresh EVERY data view against one run_dir. Used on completion and on
// bundle-click so all tabs agree immediately -- no tab is left showing a
// previous run until the user happens to visit it.
function _refreshAllViews(runDir) {
  refreshOverview(runDir);
  refreshFindings(runDir);
  refreshData(runDir);
  refreshMethods(runDir);
  refreshPhaseSpace(runDir);
  refreshSpacetime(runDir);
  refreshForecast(runDir);
  refreshCausal(runDir);
}

// Populate the persistent run-context bar so every tab agrees on which run
// is on screen. Pass null to hide it (onboarding / no run).
function _renderRunContext(state) {
  const bar = document.getElementById("runContext");
  if (!bar) return;
  if (!state) { bar.hidden = true; return; }
  const s = state.state || state;
  const runId = s.run_id || s.run_dir || state.run_dir || "";
  const dsName = (s.dataset && s.dataset.name) ||
    String(runId).split(/[\\/]/).pop() || "run";
  const findings = Array.isArray(s.findings) ? s.findings.length : 0;
  const fab = Number(s.fabricated_count || 0);
  const conf = s.confidence != null ? s.confidence
             : (s.run_meta && s.run_meta.confidence);

  setText("rcDataset", String(dsName).replace(/\.(csv|tsv|json|jsonl|parquet|xlsx?)$/i, ""));

  // Time label: if this is the run the user just triggered, show it from
  // THEIR point of view ("completed just now") even on a cache hit, where
  // the bundle's own timestamp is weeks old. Otherwise fall back to the
  // bundle's actual age (relevant when browsing old runs in Bundles).
  const runDir = s.run_dir || state.run_dir || "";
  let timeLabel;
  if (_lastCompletion && runDir && runDir === _lastCompletion.runDir) {
    const rel = _relativeTimeFromMs(_lastCompletion.at);
    timeLabel = `completed ${rel}${_lastCompletion.cached ? " · reused cached analysis" : ""}`;
  } else {
    const rel = _relativeTimeFromRunId(runId);
    timeLabel = rel ? `ran ${rel}` : "this run";
  }
  setText("rcTime", timeLabel);
  setText("rcFindings", `${findings} finding${findings === 1 ? "" : "s"}`);
  const fabEl = document.getElementById("rcFabricated");
  if (fabEl) {
    fabEl.textContent = `✓ ${fab} fabricated`;
    fabEl.style.color = fab === 0 ? "var(--mint)" : "var(--crit)";
  }
  const confEl = document.getElementById("rcConfidence");
  if (confEl) confEl.textContent = (conf != null)
    ? `confidence ${(Number(conf) * 100).toFixed(0)}%` : "";
  bar.hidden = false;
}


// ---------------------------------------------------------------------
// 5. Overview view — stat cards from /api/state
// ---------------------------------------------------------------------
async function refreshOverview(runDir) {
  // Don't overwrite the "running…" state with stale data while we're
  // waiting for a submitted run to complete -- the state machine will
  // call refreshOverview() with the right run_dir once it completes.
  if (_activeRun) return;
  // BULLETPROOF PIN: once a run is loaded, ALWAYS target it. Never fall
  // through to /api/state's global "latest", which can be a different
  // (newer-timestamped) run -- that's how falling_ball kept creeping back
  // over server_metrics. Only a fresh boot (no pin yet) hits "latest".
  runDir = runDir || _currentRunDir;

  // No run pinned this session -> ONBOARDING. Deliberately do NOT auto-load
  // the latest run on disk: that "auto-loaded falling_ball" on a fresh boot
  // and fought the user when they went to run something new. The last run
  // lives in Bundles; a fresh launch starts clean.
  if (!runDir) {
    setStat("statFindings",   "—", "awaiting run");
    setStat("statMethods",    "—", "0 fabricated");
    setStat("statAnomalies",  "—", "crit + warn");
    setStat("statRegimes",    "—", "hmm latent states");
    setText("overviewRunId", auroraOnline ? "no active run" : "aurora offline");
    _renderNarrative(null);
    _renderRunContext(null);
    _setOverviewMode(auroraOnline ? "onboarding" : "results");
    return;
  }

  const url = `/api/state?run_dir=${encodeURIComponent(runDir)}`;
  const _tok = _viewToken;
  const state = await auroraFetch(url);
  if (_tok !== _viewToken) return;   // a newer run/pin superseded this fetch — discard stale paint
  if (!state || state.ok === false) {
    setStat("statFindings",   "—", "awaiting run");
    setStat("statMethods",    "—", "0 fabricated");
    setStat("statAnomalies",  "—", "crit + warn");
    setStat("statRegimes",    "—", "hmm latent states");
    setText("overviewRunId", auroraOnline ? "no active run" : "aurora offline");
    _renderNarrative(null);
    _renderRunContext(null);
    _setOverviewMode(auroraOnline ? "onboarding" : "results");
    return;
  }

  // A real run is loaded -> results mode (hide onboarding, show stats).
  _setOverviewMode("results");
  _renderRunContext(state);

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
        const ev = _findingEvidence(f);
        const evHtml = ev.length
          ? `<div class="narrative-insight-ev">${ev.map((e) =>
              `<span class="ev ev--sm"><span class="ev-k">${esc(e.k)}</span>` +
              `<span class="ev-v">${esc(e.v)}</span></span>`).join("")}</div>`
          : "";
        const cited = _citationText(f)
          ? `<span class="finding-cite finding-cite--sm">✓ cited</span>` : "";
        return `<div class="narrative-insight narrative-insight--${sev}">
          <span class="narrative-insight-sev finding-sev--${sev}">${esc(sev)}</span>
          <div class="narrative-insight-text">
            <div class="narrative-insight-title">${esc(f.title || f.name || "(untitled)")}</div>
            <div class="narrative-insight-foot">
              <span class="narrative-insight-method">${esc(f.method || "—")}</span>
              ${cited}
            </div>
          </div>
          ${evHtml}
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
  // A run is in flight: do NOT pull /api/state's global "latest" run, which
  // is the PREVIOUS dataset until ours completes. Without this guard the
  // background poll + tab switches repaint the old run over the new one for
  // the whole analysis duration -- the stale-override bug.
  if (_activeRun) return;
  runDir = runDir || _currentRunDir;   // bulletproof pin (see refreshOverview)
  if (!runDir) { renderFindings([]); return; }   // no run this session -> empty
  const url = `/api/state?run_dir=${encodeURIComponent(runDir)}`;
  const _tok = _viewToken;
  const state = await auroraFetch(url);
  if (_tok !== _viewToken) return;   // a newer run/pin superseded this fetch — discard stale paint
  if (!state || state.ok === false) {
    renderFindings([]);
    return;
  }
  const s = state.state || state;
  _findingsCache = Array.isArray(s.findings) ? s.findings : [];
  _currentDatasetName = (s.dataset && s.dataset.name) || _currentDatasetName;
  const runId = s.run_id || state.run_dir || "current run";
  setText("findingsRunId", String(runId).split(/[\\/]/).pop().slice(0, 56));
  renderFindings(_findingsCache);
}

// Pull the credible numbers out of a finding -- the |z|, p-value, and row
// that make it verifiable. These become inline evidence chips on the card,
// so a finding reads like an insight with receipts, not a bare label.
function _findingEvidence(f) {
  const ev = (f && f.evidence) || {};
  const out = [];
  const z = f.z != null ? f.z : (f.z_score != null ? f.z_score : ev.z);
  if (z != null && !Number.isNaN(Number(z))) {
    out.push({ k: "|z|", v: `${Number(z).toFixed(1)}σ` });
  }
  let p = f.p_value != null ? f.p_value
        : (f.p_bonferroni != null ? f.p_bonferroni : ev.p_value);
  if (p != null && !Number.isNaN(Number(p))) {
    const pn = Number(p);
    out.push({ k: "p", v: pn < 0.001 ? pn.toExponential(1) : pn.toFixed(3) });
  }
  const row = f.row != null ? f.row : (ev.row != null ? ev.row : ev.row_idx);
  if (row != null) out.push({ k: "row", v: String(row) });
  return out;
}

function _citationText(f) {
  const c = f && f.kb_citation;
  if (!c) return "";
  if (typeof c === "string") return c;
  return c.text || c.source || c.title || "cited source";
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
    const sev    = (f.severity || "info").toLowerCase();
    const title  = esc(f.title || f.name || "(untitled)");
    const desc   = esc(String(f.description || f.narrative || f.summary || "")
                        .trim().slice(0, 180));
    const method = esc(f.method || "—");
    const conf   = f.confidence != null
      ? `${Math.round(Number(f.confidence) * 100)}% conf` : "";
    const ev = _findingEvidence(f);
    const evHtml = ev.length
      ? `<div class="finding-evidence">${ev.map((e) =>
          `<span class="ev"><span class="ev-k">${esc(e.k)}</span>` +
          `<span class="ev-v">${esc(e.v)}</span></span>`).join("")}</div>`
      : "";
    const cite = _citationText(f);
    const citeHtml = cite
      ? `<span class="finding-cite" title="${esc(cite)}">✓ cited</span>` : "";
    return `<article class="finding-card finding-card--${sev}" data-finding-idx="${i}">
      <div class="finding-card-head">
        <span class="finding-sev finding-sev--${sev}">${sev}</span>
        ${conf ? `<span class="finding-conf">${conf}</span>` : ""}
      </div>
      <div class="finding-title">${title}</div>
      ${desc ? `<div class="finding-desc">${desc}</div>` : ""}
      ${evHtml}
      <div class="finding-card-foot">
        <span class="finding-method">${method}</span>
        ${citeHtml}
        <button class="finding-share" data-share-idx="${i}" title="Share this finding">⤴</button>
      </div>
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

  // Share button — stopPropagation so it doesn't also open the detail panel.
  grid.querySelectorAll(".finding-share").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = parseInt(btn.dataset.shareIdx, 10);
      if (!Number.isNaN(idx) && filtered[idx]) openShareModal(filtered[idx]);
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
  if (_activeRun) return;  // run in flight: don't paint the stale "latest" run
  runDir = runDir || _currentRunDir;   // bulletproof pin (see refreshOverview)
  const wrap = document.getElementById("dataTableWrap");
  if (!wrap) return;
  if (!runDir) { wrap.innerHTML = renderEmpty("No run loaded — run an analysis from <b>Overview</b>."); return; }
  const url = `/api/state?run_dir=${encodeURIComponent(runDir)}`;
  const _tok = _viewToken;
  const state = await auroraFetch(url);
  if (_tok !== _viewToken) return;   // a newer run/pin superseded this fetch — discard stale paint
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
  if (_activeRun) return;  // run in flight: don't paint the stale "latest" run
  runDir = runDir || _currentRunDir;   // bulletproof pin (see refreshOverview)
  const wrap = document.getElementById("methodsList");
  if (!wrap) return;
  if (!runDir) { wrap.innerHTML = renderEmpty("No run loaded — run an analysis from <b>Overview</b>."); return; }
  const url = `/api/state?run_dir=${encodeURIComponent(runDir)}`;
  const _tok = _viewToken;
  const state = await auroraFetch(url);
  if (_tok !== _viewToken) return;   // a newer run/pin superseded this fetch — discard stale paint
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
// ---------------------------------------------------------------------
// Phase Space — native port of the legacy System Graph phase portrait.
// Reads state.system_model.phase_space and renders the trajectory as SVG.
// ---------------------------------------------------------------------
async function refreshPhaseSpace(runDir) {
  if (_activeRun) return;  // run in flight: don't paint stale data
  runDir = runDir || _currentRunDir;   // bulletproof pin (see refreshOverview)
  const wrap = document.getElementById("phaseWrap");
  if (!wrap) return;
  if (!runDir) { wrap.innerHTML = renderEmpty("No run loaded — run an analysis from <b>Overview</b>."); return; }
  const url = `/api/state?run_dir=${encodeURIComponent(runDir)}`;
  const _tok = _viewToken;
  const state = await auroraFetch(url);
  if (_tok !== _viewToken) return;   // a newer run/pin superseded this fetch — discard stale paint
  if (!state || state.ok === false) {
    wrap.innerHTML = renderEmpty("No active run. Run an analysis from <b>Overview</b>.");
    return;
  }
  const s = state.state || state;
  const runId = s.run_id || state.run_dir || "current run";
  setText("phaseRunId", String(runId).split(/[\\/]/).pop().slice(0, 56));
  const ps = (s.system_model && s.system_model.phase_space) || s.phase_space || null;
  wrap.innerHTML = renderPhaseSpace(ps);
}

function renderPhaseSpace(ps) {
  if (!ps) {
    return renderEmpty(
      "Phase space needs ≥ 2 numeric variables with variance, and isn't " +
      "produced on every run/tier. Try a richer dataset (e.g. the " +
      "<b>falling ball</b> or <b>server metrics</b> demo) at STANDARD tier.");
  }
  const hist = Array.isArray(ps.history) ? ps.history : [];
  const fut  = Array.isArray(ps.future) ? ps.future : [];
  const attr = Array.isArray(ps.attractors) ? ps.attractors : [];
  const cur  = ps.current || (hist.length ? hist[hist.length - 1] : null);

  const xs = [], ys = [];
  hist.forEach((h) => { if (h.x != null) xs.push(h.x); if (h.y != null) ys.push(h.y); });
  fut.forEach((f)  => { if (f.x != null) xs.push(f.x); if (f.y != null) ys.push(f.y); });
  attr.forEach((a) => {
    if (a.x_center != null) xs.push(a.x_center - (a.x_extent || 0), a.x_center + (a.x_extent || 0));
    if (a.y_center != null) ys.push(a.y_center - (a.y_extent || 0), a.y_center + (a.y_extent || 0));
  });
  if (cur && cur.x != null) xs.push(cur.x);
  if (cur && cur.y != null) ys.push(cur.y);
  if (!xs.length || !ys.length) {
    return renderEmpty(`Phase space: ${esc(ps.x_axis || "?")} × ${esc(ps.y_axis || "?")} — insufficient data to plot.`);
  }

  const VB_W = 1400, VB_H = 560;
  const xL = 150, xR = VB_W - 60, yT = 50, yB = VB_H - 80;
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const xPad = (xMax - xMin) * 0.10 || 1, yPad = (yMax - yMin) * 0.10 || 1;
  const xLo = xMin - xPad, xHi = xMax + xPad, yLo = yMin - yPad, yHi = yMax + yPad;
  const mapX = (x) => xL + ((x - xLo) / (xHi - xLo)) * (xR - xL);
  const mapY = (y) => yB - ((y - yLo) / (yHi - yLo)) * (yB - yT);
  const f1 = (n) => Number(n).toFixed(1);

  const P = [];
  P.push(`<defs>
    <linearGradient id="phaseTrail" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#8a7ed8" stop-opacity="0"/>
      <stop offset="60%" stop-color="#8a7ed8" stop-opacity="0.55"/>
      <stop offset="100%" stop-color="#6ee7ff" stop-opacity="1"/>
    </linearGradient>
    <filter id="phaseGlow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="3" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>`);

  // Grid + axes.
  for (let i = 1; i < 4; i++) {
    const gx = xL + i * (xR - xL) / 4, gy = yB - i * (yB - yT) / 4;
    P.push(`<line x1="${gx}" y1="${yT}" x2="${gx}" y2="${yB}" stroke="#1a2438" stroke-width="1"/>`);
    P.push(`<line x1="${xL}" y1="${gy}" x2="${xR}" y2="${gy}" stroke="#1a2438" stroke-width="1"/>`);
  }
  P.push(`<line x1="${xL}" y1="${yB}" x2="${xR}" y2="${yB}" stroke="#2a3550" stroke-width="1.4"/>`);
  P.push(`<line x1="${xL}" y1="${yT}" x2="${xL}" y2="${yB}" stroke="#2a3550" stroke-width="1.4"/>`);
  for (let i = 0; i <= 4; i++) {
    const x = xL + i * (xR - xL) / 4, v = (xLo + i * (xHi - xLo) / 4).toFixed(1);
    P.push(`<text x="${x}" y="${yB + 20}" text-anchor="middle" font-family="VT323" font-size="13" fill="#5a6a8a">${v}</text>`);
    const y = yB - i * (yB - yT) / 4, w = (yLo + i * (yHi - yLo) / 4).toFixed(1);
    P.push(`<text x="${xL - 12}" y="${y + 4}" text-anchor="end" font-family="VT323" font-size="13" fill="#5a6a8a">${w}</text>`);
  }
  P.push(`<text x="${(xL + xR) / 2}" y="${yB + 44}" text-anchor="middle" font-family="JetBrains Mono" font-size="12" fill="#8a96b5" letter-spacing="2">${esc(ps.x_axis || "x")} →</text>`);
  P.push(`<text x="${xL - 38}" y="${(yT + yB) / 2}" text-anchor="middle" transform="rotate(-90 ${xL - 38} ${(yT + yB) / 2})" font-family="JetBrains Mono" font-size="12" fill="#8a96b5" letter-spacing="2">${esc(ps.y_axis || "y")} →</text>`);

  // Attractor regimes.
  const ATTR = ["#88ffd1", "#6ee7ff", "#b794ff", "#ffc857", "#d4b8ff"];
  attr.forEach((a, i) => {
    const cx = mapX(a.x_center), cy = mapY(a.y_center);
    const rx = Math.max(20, Math.abs(mapX(a.x_center + (a.x_extent || 0)) - cx));
    const ry = Math.max(15, Math.abs(mapY(a.y_center + (a.y_extent || 0)) - cy));
    const c = ATTR[i % ATTR.length];
    P.push(`<ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" fill="none" stroke="${c}" stroke-width="1.4" stroke-dasharray="3 5" opacity="0.5"/>`);
    if (a.label) P.push(`<text x="${cx}" y="${cy}" text-anchor="middle" font-family="JetBrains Mono" font-size="9" letter-spacing="1.5" fill="${c}" opacity="0.85">${esc(String(a.label).toUpperCase())}</text>`);
  });

  // Bifurcation manifold.
  if (ps.manifold) {
    const m = ps.manifold;
    P.push(`<line x1="${mapX(m.x0)}" y1="${mapY(m.y0)}" x2="${mapX(m.x1)}" y2="${mapY(m.y1)}" stroke="#ff9a4a" stroke-width="1" stroke-dasharray="2 6" opacity="0.55"/>`);
  }

  // History trail.
  if (hist.length >= 2) {
    let d = `M ${f1(mapX(hist[0].x))} ${f1(mapY(hist[0].y))}`;
    for (let i = 1; i < hist.length; i++) d += ` L ${f1(mapX(hist[i].x))} ${f1(mapY(hist[i].y))}`;
    P.push(`<path d="${d}" stroke="url(#phaseTrail)" stroke-width="2.5" fill="none" filter="url(#phaseGlow)"/>`);
  }

  // Forecast trajectory.
  if (fut.length >= 1 && cur && cur.x != null) {
    let d = `M ${f1(mapX(cur.x))} ${f1(mapY(cur.y))}`;
    fut.forEach((p) => { if (p.x != null && p.y != null) d += ` L ${f1(mapX(p.x))} ${f1(mapY(p.y))}`; });
    P.push(`<path d="${d}" stroke="#ff5577" stroke-width="1.8" stroke-dasharray="4 6" fill="none" opacity="0.6"/>`);
  }

  // NOW marker.
  if (cur && cur.x != null && cur.y != null) {
    const cx = mapX(cur.x), cy = mapY(cur.y);
    P.push(`<g transform="translate(${cx} ${cy})">
      <circle r="20" fill="#6ee7ff" opacity="0.18" filter="url(#phaseGlow)"/>
      <circle r="12" fill="none" stroke="#6ee7ff" stroke-width="1" stroke-dasharray="3 4" opacity="0.7"/>
      <circle r="6" fill="#6ee7ff" filter="url(#phaseGlow)"/>
      <text y="-18" text-anchor="middle" font-family="JetBrains Mono" font-size="10" fill="#fff" font-weight="600" letter-spacing="1">NOW</text>
      <text y="30" text-anchor="middle" font-family="VT323" font-size="14" fill="#6ee7ff">(${(+cur.x).toFixed(2)}, ${(+cur.y).toFixed(2)})</text>
    </g>`);
  }

  return `<svg class="phase-svg" viewBox="0 0 ${VB_W} ${VB_H}" preserveAspectRatio="xMidYMid meet">${P.join("")}</svg>`;
}

// ---------------------------------------------------------------------
// Spacetime — native port of the System Graph spacetime view. Each
// entity from system_model.topology is a horizontal lane: worldline
// (history -> NOW) + probability cone (NOW -> future).
// ---------------------------------------------------------------------
async function refreshSpacetime(runDir) {
  if (_activeRun) return;
  runDir = runDir || _currentRunDir;
  const wrap = document.getElementById("spacetimeWrap");
  if (!wrap) return;
  if (!runDir) { wrap.innerHTML = renderEmpty("No run loaded — run an analysis from <b>Overview</b>."); return; }
  const url = `/api/state?run_dir=${encodeURIComponent(runDir)}`;
  const _tok = _viewToken;
  const state = await auroraFetch(url);
  if (_tok !== _viewToken) return;   // a newer run/pin superseded this fetch — discard stale paint
  if (!state || state.ok === false) {
    wrap.innerHTML = renderEmpty("No active run. Run an analysis from <b>Overview</b>.");
    return;
  }
  const s = state.state || state;
  const runId = s.run_id || state.run_dir || "current run";
  setText("spacetimeRunId", String(runId).split(/[\\/]/).pop().slice(0, 56));
  wrap.innerHTML = renderSpacetime(s.system_model || null);
  populateSimEntities("spacetime", s.system_model || null);
}

function renderSpacetime(model) {
  const nodes = (model && model.topology && model.topology.entities) || [];
  if (!nodes.length) {
    return renderEmpty(
      "Spacetime needs a system model with entities — not produced on every " +
      "run/tier. Try a richer dataset (e.g. <b>server metrics</b>) at STANDARD tier.");
  }
  const VB_W = 1400, VB_H = 560;
  const X_LEFT = 90, X_NOW = VB_W * 0.5, X_RIGHT = VB_W - 60;
  const n = nodes.length;
  const margin = n <= 4 ? 44 : n <= 8 ? 58 : 72;
  const top = margin, bot = VB_H - margin;
  const laneH = (bot - top) / n;
  const fillF = n <= 4 ? 0.42 : n <= 8 ? 0.38 : 0.34;
  const colorOf = (role, sev) =>
    sev === "crit" ? "#ff5577" : sev === "warn" ? "#ff9a4a" :
    role === "forcing" ? "#ff5577" : role === "indicator" ? "#6ee7ff" :
    role === "event" ? "#b794ff" : "#88ffd1";

  const P = [];
  let defs = `<filter id="stGlow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>`;
  nodes.forEach((nd, i) => {
    const c = colorOf(nd.role, nd.severity);
    defs += `<linearGradient id="stTrail${i}" x1="0%" x2="100%"><stop offset="0%" stop-color="${c}" stop-opacity="0"/><stop offset="40%" stop-color="${c}" stop-opacity="0.45"/><stop offset="100%" stop-color="${c}" stop-opacity="1"/></linearGradient>`;
  });
  P.push(`<defs>${defs}</defs>`);

  // NOW line + past/future markers.
  P.push(`<line x1="${X_NOW}" y1="22" x2="${X_NOW}" y2="${VB_H - 28}" stroke="#6ee7ff" stroke-width="1" stroke-dasharray="4 4" opacity="0.5"/>`);
  P.push(`<text x="${X_NOW}" y="16" text-anchor="middle" font-family="JetBrains Mono" font-size="11" fill="#6ee7ff" letter-spacing="2">NOW</text>`);
  P.push(`<text x="${X_LEFT}" y="${VB_H - 8}" font-family="VT323" font-size="14" fill="#5a6a8a">← past</text>`);
  P.push(`<text x="${X_RIGHT}" y="${VB_H - 8}" text-anchor="end" font-family="VT323" font-size="14" fill="#5a6a8a">future →</text>`);

  nodes.forEach((nd, i) => {
    const cy = top + laneH * (i + 0.5);
    const laneTop = cy - laneH * fillF, laneBot = cy + laneH * fillF;
    const c = colorOf(nd.role, nd.severity);
    const hist = (nd.history || []).filter((h) => h.v != null && isFinite(h.v));
    let lo = null, hi = null;
    hist.forEach((h) => { if (lo === null || h.v < lo) lo = h.v; if (hi === null || h.v > hi) hi = h.v; });
    const mapY = (v) => (lo === null || hi === null || hi === lo) ? cy : laneBot - ((v - lo) / (hi - lo)) * (laneBot - laneTop);

    P.push(`<line x1="${X_LEFT}" x2="${X_RIGHT}" y1="${cy.toFixed(1)}" y2="${cy.toFixed(1)}" stroke="#1a2438" stroke-width="0.5" stroke-dasharray="2 6"/>`);

    if (hist.length) {
      let d = "";
      hist.forEach((h, j) => {
        const t = j / Math.max(1, hist.length - 1);
        const x = X_LEFT + t * (X_NOW - X_LEFT);
        d += (j === 0 ? "M" : " L") + ` ${x.toFixed(1)} ${mapY(h.v).toFixed(1)}`;
      });
      P.push(`<path d="${d}" stroke="url(#stTrail${i})" stroke-width="2" fill="none" filter="url(#stGlow)"/>`);
    }
    const curY = hist.length ? mapY(hist[hist.length - 1].v) : cy;

    const fc = (nd.forecast || []).filter((p) => p.t != null && isFinite(p.t));
    if (fc.length) {
      const tMax = Math.max(...fc.map((p) => p.t)) || 1;
      const dx = X_RIGHT - X_NOW;
      const up = [], lw = [];
      fc.forEach((p) => {
        const tn = Math.min(1, p.t / tMax), x = X_NOW + tn * dx;
        const ym = (p.mean != null && isFinite(p.mean)) ? mapY(p.mean) : curY;
        up.push([x, p.ci_hi != null && isFinite(p.ci_hi) ? mapY(p.ci_hi) : ym]);
        lw.push([x, p.ci_lo != null && isFinite(p.ci_lo) ? mapY(p.ci_lo) : ym]);
      });
      let fillD = `M ${X_NOW.toFixed(1)} ${curY.toFixed(1)}`;
      up.forEach((p) => fillD += ` L ${p[0].toFixed(1)} ${p[1].toFixed(1)}`);
      for (let k = lw.length - 1; k >= 0; k--) fillD += ` L ${lw[k][0].toFixed(1)} ${lw[k][1].toFixed(1)}`;
      fillD += " Z";
      P.push(`<path d="${fillD}" fill="${c}" opacity="0.10"/>`);
      let md = `M ${X_NOW.toFixed(1)} ${curY.toFixed(1)}`;
      fc.forEach((p) => { const tn = Math.min(1, p.t / tMax), x = X_NOW + tn * dx; const ym = (p.mean != null && isFinite(p.mean)) ? mapY(p.mean) : curY; md += ` L ${x.toFixed(1)} ${ym.toFixed(1)}`; });
      P.push(`<path d="${md}" stroke="${c}" stroke-width="1.4" stroke-dasharray="4 5" fill="none" opacity="0.6"/>`);
    }

    P.push(`<circle cx="${X_NOW}" cy="${curY.toFixed(1)}" r="5" fill="${c}" filter="url(#stGlow)"/>`);
    P.push(`<text x="${X_LEFT - 10}" y="${(cy + 3).toFixed(1)}" text-anchor="end" font-family="JetBrains Mono" font-size="11" fill="${c}">${esc(nd.id || "")}</text>`);
  });

  return `<svg class="phase-svg" viewBox="0 0 ${VB_W} ${VB_H}" preserveAspectRatio="xMidYMid meet">${P.join("")}</svg>`;
}


// ---------------------------------------------------------------------
// Forecast — native render of state.forecast (headline peak + CI-band chart
// + alternate methods).
// ---------------------------------------------------------------------
async function refreshForecast(runDir) {
  if (_activeRun) return;
  runDir = runDir || _currentRunDir;
  const wrap = document.getElementById("forecastWrap");
  if (!wrap) return;
  if (!runDir) { wrap.innerHTML = renderEmpty("No run loaded — run an analysis from <b>Overview</b>."); return; }
  const url = `/api/state?run_dir=${encodeURIComponent(runDir)}`;
  const _tok = _viewToken;
  const state = await auroraFetch(url);
  if (_tok !== _viewToken) return;   // a newer run/pin superseded this fetch — discard stale paint
  if (!state || state.ok === false) {
    wrap.innerHTML = renderEmpty("No active run. Run an analysis from <b>Overview</b>.");
    return;
  }
  const s = state.state || state;
  const runId = s.run_id || state.run_dir || "current run";
  setText("forecastRunId", String(runId).split(/[\\/]/).pop().slice(0, 56));
  wrap.innerHTML = renderForecast(s.forecast || null);
  populateSimEntities("forecast", s.system_model || null);
}

function renderForecast(fc) {
  if (!fc || fc.available === false) {
    return renderEmpty(
      "No forecast for this run — Aurora forecasts time-series targets. " +
      "Cross-sectional data (no time axis) has no forecast, by design.");
  }
  const pts = Array.isArray(fc.points) ? fc.points.filter((p) => p.value != null) : [];
  const head = (fc.headline_value != null)
    ? `<div class="fc-headline">
         <div class="fc-headline-lbl">PEAK · NEXT ${esc(String(fc.horizon || pts.length))} STEPS</div>
         <div class="fc-headline-val">${(+fc.headline_value).toFixed(2)}</div>
         ${fc.headline_ci_low != null ? `<div class="fc-headline-ci">95% CI ${(+fc.headline_ci_low).toFixed(2)} – ${(+fc.headline_ci_high).toFixed(2)}</div>` : ""}
       </div>`
    : "";
  const meta = `<div class="fc-meta">target <b>${esc(fc.target || "—")}</b> · ${esc(String(fc.method || "ensemble"))}${fc.score != null ? ` · CRPS ${(+fc.score).toFixed(2)}` : ""}</div>`;

  let chart = "";
  if (pts.length >= 2) {
    const VB_W = 1200, VB_H = 320, pad = 40;
    const vals = pts.map((p) => p.value);
    const los = pts.map((p) => (p.ci_low != null ? p.ci_low : p.value));
    const his = pts.map((p) => (p.ci_high != null ? p.ci_high : p.value));
    const lo = Math.min(...vals, ...los), hi = Math.max(...vals, ...his);
    const span = (hi - lo) || 1;
    const mx = (i) => pad + (i / Math.max(1, pts.length - 1)) * (VB_W - 2 * pad);
    const my = (v) => (VB_H - pad) - ((v - lo) / span) * (VB_H - 2 * pad);
    let band = `M ${mx(0).toFixed(1)} ${my(his[0]).toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) band += ` L ${mx(i).toFixed(1)} ${my(his[i]).toFixed(1)}`;
    for (let i = pts.length - 1; i >= 0; i--) band += ` L ${mx(i).toFixed(1)} ${my(los[i]).toFixed(1)}`;
    band += " Z";
    let line = "";
    pts.forEach((p, i) => line += (i === 0 ? "M" : " L") + ` ${mx(i).toFixed(1)} ${my(p.value).toFixed(1)}`);
    chart = `<svg class="fc-svg" viewBox="0 0 ${VB_W} ${VB_H}" preserveAspectRatio="xMidYMid meet">
      <path d="${band}" fill="#6ee7ff" opacity="0.12"/>
      <path d="${line}" stroke="#6ee7ff" stroke-width="2" fill="none" filter=""/>
      <line x1="${pad}" y1="${VB_H - pad}" x2="${VB_W - pad}" y2="${VB_H - pad}" stroke="#2a3550" stroke-width="1"/>
    </svg>`;
  }

  let warn = "";
  if (fc.warning && fc.warning.breach_probability != null) {
    warn = `<div class="fc-warn">⚠ Threshold breach: ${esc(String(fc.warning.breach_value))} exceeds ${esc(String(fc.warning.breach_threshold))} with ${(fc.warning.breach_probability * 100).toFixed(0)}% probability.</div>`;
  }

  return `<div class="fc-wrap">${head}${meta}${chart}${warn}</div>`;
}


// ---------------------------------------------------------------------
// Causal Lab — native port of the v2.0 Lab do-calculus panel.
// Populates treatment/outcome from the run's columns, POSTs to
// /api/causal/do + /api/causal/identify, renders the verdict.
// ---------------------------------------------------------------------
async function refreshCausal(runDir) {
  if (_activeRun) return;
  runDir = runDir || _currentRunDir;
  const tSel = document.getElementById("causalTreatment");
  const oSel = document.getElementById("causalOutcome");
  if (!tSel || !oSel) return;
  if (!runDir) {
    tSel.innerHTML = oSel.innerHTML = `<option value="">— run an analysis first —</option>`;
    setText("causalRunId", "no active run");
    return;
  }
  const url = `/api/state?run_dir=${encodeURIComponent(runDir)}`;
  const _tok = _viewToken;
  const state = await auroraFetch(url);
  if (_tok !== _viewToken) return;   // a newer run/pin superseded this fetch — discard stale paint
  const s = state && (state.state || state);
  if (!s || (state && state.ok === false)) {
    tSel.innerHTML = oSel.innerHTML = `<option value="">— no run —</option>`;
    setText("causalRunId", "no active run");
    return;
  }
  const runId = s.run_id || runDir || "current run";
  setText("causalRunId", String(runId).split(/[\\/]/).pop().slice(0, 40));

  const cols = (s.structure && s.structure.columns) || [];
  const numeric = cols.filter((c) => String(c.kind || "").toLowerCase() === "n").map((c) => c.name);
  const opts = numeric.length ? numeric : cols.map((c) => c.name);
  if (!opts.length) {
    tSel.innerHTML = oSel.innerHTML = `<option value="">— no numeric columns —</option>`;
    return;
  }
  const optHtml = opts.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("");
  const prevT = tSel.value, prevO = oSel.value;
  tSel.innerHTML = optHtml;
  oSel.innerHTML = optHtml;
  if (prevT && opts.includes(prevT)) tSel.value = prevT;
  if (prevO && opts.includes(prevO)) oSel.value = prevO;
  else if (opts.length > 1) oSel.selectedIndex = 1;   // default outcome != treatment
}

async function _runCausal(kind) {
  const t  = (document.getElementById("causalTreatment") || {}).value;
  const o  = (document.getElementById("causalOutcome") || {}).value;
  const iv = ((document.getElementById("causalIv") || {}).value || "").trim();
  const res = document.getElementById("causalResult");
  if (!res) return;
  if (!t || !o) { res.innerHTML = `<div class="empty-state" style="color:var(--warn)">Pick a treatment and an outcome.</div>`; return; }
  if (t === o)  { res.innerHTML = `<div class="empty-state" style="color:var(--warn)">Treatment and outcome must be different columns.</div>`; return; }
  res.innerHTML = `<div class="empty-state">▶ Computing ${kind === "identify" ? "identifiability" : "do() estimate"}…</div>`;

  let j;
  if (kind === "identify") {
    const q = `treatment=${encodeURIComponent(t)}&outcome=${encodeURIComponent(o)}` +
              (_currentRunDir ? `&run_dir=${encodeURIComponent(_currentRunDir)}` : "");
    j = await auroraFetch(`/api/causal/identify?${q}`);
  } else {
    const body = { treatment: t, outcome: o };
    if (iv !== "") body.intervention_value = iv;
    if (_currentRunDir) body.run_dir = _currentRunDir;
    j = await auroraFetch("/api/causal/do", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }
  res.innerHTML = _renderCausalResult(kind, j);
}

function _renderCausalResult(kind, j) {
  if (!j || j.error || j.ok === false || j.unidentifiable_reason) {
    const reason = (j && (j.unidentifiable_reason || j.error)) ||
      "effect not identifiable from this run's DAG";
    return `<div class="causal-verdict causal-verdict--warn">
      <div class="cv-warn-label">⚠ not identifiable</div>
      <div class="cv-warn-text">${esc(reason)}</div>
    </div>`;
  }
  if (kind === "identify") {
    return `<div class="causal-verdict">
      <div class="cv-row"><span>identifiable</span><b style="color:${j.identifiable ? "var(--mint)" : "var(--crit)"}">${j.identifiable ? "✓ yes — via backdoor" : "✗ no"}</b></div>
      <div class="cv-row"><span>adjustment set</span><b>${esc((j.adjustment_set || []).join(", ") || "(empty — none needed)")}</b></div>
    </div>`;
  }
  const beta = j.effect_estimate != null ? (+j.effect_estimate).toFixed(4) : "—";
  const se   = j.standard_error != null ? (+j.standard_error).toFixed(4) : "—";
  const adj  = (j.adjustment_set || []).join(", ") || "(empty — none needed)";
  let html = `<div class="causal-verdict">
    <div class="cv-headline">do(${esc(j.treatment || "T")}) → ${esc(j.outcome || "Y")}</div>
    <div class="cv-effect">${beta}<span class="cv-se">± ${se} SE</span></div>
    <div class="cv-row"><span>adjustment set</span><b>${esc(adj)}</b></div>
    <div class="cv-row"><span>n observations</span><b>${esc(String(j.n_obs || "—"))}</b></div>`;
  if (j.intervention_value != null) {
    html += `<div class="cv-row"><span>do(${esc(j.treatment)} = ${esc(String(j.intervention_value))})</span><b>predicted at sample mean</b></div>`;
  }
  html += `</div>`;
  if ((j.assumptions || []).length) {
    html += `<div class="causal-assumptions"><div class="ca-label">ASSUMPTIONS</div><ul>` +
      j.assumptions.map((a) => `<li>${esc(a)}</li>`).join("") + `</ul></div>`;
  }
  return html;
}

// ---------------------------------------------------------------------
// Knowledge Bank — native view of the cited bank. GLOBAL (not per-run):
// /api/knowledge_bank/summary for totals + per-source, /search for lookup.
// ---------------------------------------------------------------------
async function refreshKnowledgeBank() {
  const wrap = document.getElementById("kbSummary");
  if (!wrap) return;
  const r = await auroraFetch("/api/knowledge_bank/summary");
  if (!r || r.ok === false || !r.summary) {
    setText("kbTotal", "unavailable");
    wrap.innerHTML = renderEmpty(
      "Knowledge bank unavailable. " + esc((r && r.error) || "Start Aurora and retry.") +
      " On a fresh install Aurora seeds ~59 curated entries on first run, then grows nightly.");
    return;
  }
  const s = r.summary;
  const total = Number(s.total_entries || 0);
  setText("kbTotal", `${total.toLocaleString()} entries`);
  const sources = (s.per_source || []).slice().sort((a, b) => b.n - a.n);
  const maxN = Math.max(1, ...sources.map((x) => x.n));
  wrap.innerHTML = `
    <div class="kb-headline">
      <div class="kb-total">${total.toLocaleString()}</div>
      <div class="kb-total-lbl">cited knowledge entries${
        s.n_vectors ? ` · ${Number(s.n_vectors).toLocaleString()} vectors` : ""}${
        s.embedding_model ? ` · ${esc(s.embedding_model)}${s.embedding_dim ? ` (${esc(String(s.embedding_dim))}d)` : ""}` : ""}</div>
    </div>
    <div class="kb-sources">
      ${sources.map((x) => `
        <div class="kb-source">
          <div class="kb-source-head">
            <span class="kb-source-name">${esc(x.source)}</span>
            <span class="kb-source-n">${Number(x.n).toLocaleString()}</span>
          </div>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.round((x.n / maxN) * 100)}%"></div></div>
        </div>`).join("")}
    </div>`;
}

async function _kbSearch() {
  const q = ((document.getElementById("kbSearchInput") || {}).value || "").trim();
  const res = document.getElementById("kbResults");
  if (!res) return;
  if (!q) { res.innerHTML = ""; return; }
  res.innerHTML = `<div class="empty-state">▶ Searching the bank…</div>`;
  const r = await auroraFetch(`/api/knowledge_bank/search?q=${encodeURIComponent(q)}&limit=20`);
  if (!r || r.ok === false) {
    res.innerHTML = renderEmpty("Search failed: " + esc((r && r.error) || "unknown"));
    return;
  }
  const results = r.results || [];
  if (!results.length) { res.innerHTML = renderEmpty(`No entries matched “${esc(q)}”.`); return; }
  res.innerHTML = `<div class="kb-results-label">${results.length} RESULTS</div>` +
    results.map((e) => `
      <div class="kb-result">
        <div class="kb-result-head">
          <span class="kb-result-name">${esc(e.name || e.id)}</span>
          <span class="kb-result-rel">${Math.round((e.relevance || 0) * 100)}%</span>
        </div>
        <div class="kb-result-meta">${esc(e.domain || "—")} · ${esc(e.source || "—")}</div>
        ${e.definition ? `<div class="kb-result-def">${esc(e.definition)}</div>` : ""}
      </div>`).join("");
}

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
      // Loading a bundle is an explicit user choice -> pin to it. _lastCompletion
      // is cleared so the run-context bar shows the bundle's real age, not
      // "completed just now" (this bundle wasn't run just now).
      _currentRunDir = dir;
      _lastCompletion = null;
      _bumpViewToken();   // invalidate any in-flight refresh from the prior pin
      _refreshAllViews(_currentRunDir);
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

  // Stop any previous run we were tracking. Bump the view token FIRST so any
  // in-flight refresh from the previous run (interval poll or a still-pending
  // completion fetch) is invalidated and can't paint over this run.
  _stopElapsedTimer();
  _bumpViewToken();
  // Set _activeRun BEFORE the await. The 6s interval and online-refresh both
  // short-circuit on `if (_activeRun) return`, so claiming the slot now closes
  // the submission window where the PREVIOUS run's data could flash back in
  // while we await POST /api/run. runId is filled in once the POST returns.
  _activeRun = { runId: null, submittedAt: Date.now(), filename };

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
    _activeRun = null;   // release the slot so refreshes can resume
    _showRunningState(filename, "failed");
    return;
  }

  // Submission accepted. Aurora's /api/state will still return the
  // PREVIOUS run until this one completes, so we now poll until we see
  // the new run_id in /api/state -- that's our completion signal. We also
  // capture the POST's run_dir (populated immediately on a sync cache hit)
  // as a fallback in case a later status snapshot omits it.
  const newRunId = r.run_id || null;
  _activeRun.runId = newRunId;
  _activeRun.postRunDir = r.run_dir || null;
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
    if (phase === "submitting") label = `▶ starting analysis · <b>${esc(filename)}</b>…`;
    else if (phase === "running") label = `▶ analyzing <b>${esc(filename)}</b> · <span id="runElapsed">0.0s</span>`;
    else if (phase === "complete") {
      // Soften the cache case: "instant (cached)" reads as a speed win, not
      // an alarming "CACHE HIT" that makes the user think it didn't run.
      const cacheTag = cached ? ` <span class="run-banner-tag">instant · cached</span>` : "";
      label = `✓ analysis complete · <b>${esc(filename)}</b>${cacheTag}`;
    }
    else if (phase === "failed")  label = `✕ analysis failed · <b>${esc(filename)}</b>`;
    banner.innerHTML = `<span class="run-banner-dot"></span><span class="run-banner-text">${label}</span>`;
  }
  // Also blank the stat cards while running so the user doesn't see
  // stale numbers from the previous run. Leave onboarding behind us --
  // the moment a run starts, we're in results mode.
  if (phase === "submitting" || phase === "running") {
    if (_bannerHideTimer) { clearTimeout(_bannerHideTimer); _bannerHideTimer = null; }
    _setOverviewMode("results");
    _renderRunContext(null);   // banner owns the in-flight state; reappears on completion
    _renderNarrative(null);    // clear the PREVIOUS run's narrative during the run
    setStat("statFindings",  "—", "running…");
    setStat("statMethods",   "—", "running…");
    setStat("statAnomalies", "—", "running…");
    setStat("statRegimes",   "—", "running…");
    // Clear Findings / Data / Methods to an explicit "analyzing" placeholder
    // so the OLD run's data isn't sitting there looking current while the
    // new run computes. The _activeRun guards keep the background poll from
    // repainting stale data on top of this.
    _renderViewsRunning();
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
    // Prefer the status snapshot's run_dir; fall back to the run_dir the POST
    // handed us (sync cache hits populate it). This guarantees we never lose
    // THIS run's dir and fall through to the previous pin.
    const runDir   = status.run_dir || _activeRun.postRunDir || null;
    const cached   = !!status.cached;
    _activeRun = null;
    _stopElapsedTimer();
    // Record completion from the USER's POV so the run-context bar can say
    // "completed just now" even on a cache hit (where the bundle's own
    // timestamp is ancient and would otherwise read "ran 6/17").
    _lastCompletion = { runDir: runDir || null, at: Date.now(), cached };
    _showRunningState(filename, "complete", cached);
    // The run hint was stuck on "running · <id>" -- update it to done.
    const hint = document.getElementById("runHint");
    if (hint) hint.innerHTML =
      `<span style="color:var(--mint)">✓ complete</span> · ${esc(filename)}`;
    // Pin the views to this run's run_dir going forward. NEVER null an
    // existing pin -- a cache-hit status occasionally omits run_dir, and
    // nulling the pin would let every later refresh fall through to
    // /api/state's "latest" (the creep bug). Only overwrite with a real dir.
    // Bump the view token AS we pin so the refresh below carries the newest
    // token and any older in-flight fetch (e.g. an interval poll that fired
    // mid-run) is discarded instead of repainting the previous run.
    if (runDir) _currentRunDir = runDir;
    _bumpViewToken();
    _refreshAllViews(_currentRunDir);
    // The transient banner fades out a few seconds after completion; the
    // persistent run-context bar is the lasting "this is what's on screen"
    // signal, so the "complete · cache hit" banner no longer lingers forever.
    _scheduleBannerHide();
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

  // Still pending/running -- show the engine's OWN words while we wait.
  // /api/run/status has always carried message + progress_pct + tier;
  // until now the shell read .state and discarded the rest.
  const pct = typeof status.progress_pct === "number"
    ? Math.max(0, Math.min(100, Math.round(status.progress_pct))) : null;
  const msg = String(status.message || status.state || "running").slice(0, 80);
  const hint = document.getElementById("runHint");
  if (hint && _activeRun) {
    hint.innerHTML = `running · <code>${esc(_activeRun.runId || "…")}</code>`
      + ` · ${esc(msg)}${pct != null ? ` · <b>${pct}%</b>` : ""}`;
  }
  const tileSub = pct != null ? `${msg} · ${pct}%` : `${msg}…`;
  setStat("statFindings",  "—", tileSub);
  setStat("statMethods",   "—", tileSub);
  setStat("statAnomalies", "—", tileSub);
  setStat("statRegimes",   "—", tileSub);
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

  body.innerHTML = rowsHtml + citation + _decisionHTML(finding);
  wireDecisionCard(finding);
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
// 8b. Simulate / Intervene panels (Forecast + Spacetime).
//
// Both views show the same run's system_model, so the panel is identical;
// we build one per view under a scope-prefixed id namespace.
//   Simulate  -> POST /api/system_model/simulate  -> forward trajectory
//   Intervene -> POST /api/system_model/intervene -> propagated deltas
// ---------------------------------------------------------------------
const SIM_SCOPES = ["forecast", "spacetime"];
const _simHostId = (scope) => scope === "forecast" ? "simPanelForecast" : "simPanelSpacetime";
const _simId = (scope, key) => `sim-${scope}-${key}`;

function buildSimPanel(scope) {
  const host = document.getElementById(_simHostId(scope));
  if (!host) return;
  const ph = `<option value="">— run an analysis —</option>`;
  host.innerHTML = `
    <div class="sim-tabs">
      <button class="sim-tab is-active" data-simtab="simulate">▶ Simulate</button>
      <button class="sim-tab" data-simtab="intervene">⤲ Intervene</button>
    </div>
    <div class="sim-pane" data-simpane="simulate">
      <p class="sim-help">Forward-step the run's discovered dynamics from the last observed value.</p>
      <label class="sim-field-lbl">Target variable</label>
      <select class="select sim-select" id="${_simId(scope,'sim-entity')}">${ph}</select>
      <label class="sim-field-lbl">Steps ahead</label>
      <input class="sim-input" id="${_simId(scope,'sim-steps')}" type="number" min="1" max="500" value="50" />
      <button class="btn btn-primary sim-run" id="${_simId(scope,'sim-run')}"><span class="btn-ico">▶</span> Run simulation</button>
      <div class="sim-result" id="${_simId(scope,'sim-result')}">
        <div class="empty-state sim-empty">Pick a variable and run a forward simulation.</div>
      </div>
    </div>
    <div class="sim-pane" data-simpane="intervene" hidden>
      <p class="sim-help">Perturb one variable and propagate the change through the model's relationships.</p>
      <label class="sim-field-lbl">Source variable</label>
      <select class="select sim-select" id="${_simId(scope,'int-entity')}">${ph}</select>
      <label class="sim-field-lbl">Perturbation (Δ)</label>
      <input class="sim-input" id="${_simId(scope,'int-delta')}" type="number" step="any" value="1.0" />
      <label class="sim-field-lbl">Max depth</label>
      <input class="sim-input" id="${_simId(scope,'int-depth')}" type="number" min="1" max="6" value="3" />
      <button class="btn btn-primary sim-run" id="${_simId(scope,'int-run')}"><span class="btn-ico">⤲</span> Propagate</button>
      <div class="sim-result" id="${_simId(scope,'int-result')}">
        <div class="empty-state sim-empty">Pick a variable, set a Δ, and propagate.</div>
      </div>
    </div>`;
}

function wireSimPanel(scope) {
  const host = document.getElementById(_simHostId(scope));
  if (!host) return;
  host.querySelectorAll(".sim-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const which = tab.dataset.simtab;
      host.querySelectorAll(".sim-tab").forEach((t) => t.classList.toggle("is-active", t === tab));
      host.querySelectorAll(".sim-pane").forEach((p) => { p.hidden = (p.dataset.simpane !== which); });
    });
  });
  const sr = document.getElementById(_simId(scope, "sim-run"));
  if (sr) sr.addEventListener("click", () => runSimulate(scope));
  const ir = document.getElementById(_simId(scope, "int-run"));
  if (ir) ir.addEventListener("click", () => runIntervene(scope));
}

// Fill the target/source dropdowns from the run's entities. Called by the
// forecast/spacetime refreshers, which already hold the state.
function populateSimEntities(scope, model) {
  const ents = (model && model.topology && model.topology.entities) || [];
  const sSel = document.getElementById(_simId(scope, "sim-entity"));
  const iSel = document.getElementById(_simId(scope, "int-entity"));
  if (!sSel || !iSel) return;
  if (!ents.length) {
    const none = `<option value="">— no system-model entities —</option>`;
    sSel.innerHTML = none; iSel.innerHTML = none;
    return;
  }
  const opt = ents.map((e) =>
    `<option value="${esc(e.id)}">${esc(e.id)}${e.role ? ` · ${esc(e.role)}` : ""}</option>`).join("");
  const ids = ents.map((e) => e.id);
  const prevS = sSel.value, prevI = iSel.value;
  sSel.innerHTML = opt; iSel.innerHTML = opt;
  if (prevS && ids.includes(prevS)) sSel.value = prevS;
  if (prevI && ids.includes(prevI)) iSel.value = prevI;
}

async function runSimulate(scope) {
  const ent = (document.getElementById(_simId(scope, "sim-entity")) || {}).value;
  const steps = parseInt((document.getElementById(_simId(scope, "sim-steps")) || {}).value, 10) || 50;
  const res = document.getElementById(_simId(scope, "sim-result"));
  if (!res) return;
  if (!_currentRunDir) { res.innerHTML = `<div class="empty-state sim-empty" style="color:var(--warn)">Run an analysis first.</div>`; return; }
  if (!ent) { res.innerHTML = `<div class="empty-state sim-empty" style="color:var(--warn)">Pick a target variable.</div>`; return; }
  res.innerHTML = `<div class="empty-state sim-empty">▶ Simulating ${esc(ent)} · ${steps} steps…</div>`;
  const j = await auroraFetch("/api/system_model/simulate", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_dir: _currentRunDir, target_entity_id: ent, n_steps: steps }),
  });
  res.innerHTML = _renderSimResult(j);
}

function _renderSimResult(j) {
  const r = j && j.result;
  if (!j || j.ok === false || !r) {
    return `<div class="sim-verdict sim-verdict--warn">⚠ ${esc((j && j.error) || "simulation unavailable")}</div>`;
  }
  if (!r.ok) {
    return `<div class="sim-verdict sim-verdict--warn">⚠ ${esc(r.error || "no fitted law to simulate from — try a time-series dataset")}</div>`;
  }
  const traj = Array.isArray(r.trajectory) ? r.trajectory : [];
  if (!traj.length) return `<div class="sim-verdict sim-verdict--warn">No trajectory returned.</div>`;
  const last = traj[traj.length - 1];
  const eq = last.source_equation || (traj[0] && traj[0].source_equation) || "";
  let html = `<div class="sim-verdict">
    <div class="sim-verdict-head">${esc(r.target_entity_id || "target")} · ${esc(r.method || "sim")}</div>
    ${_simMiniChart(traj)}
    <div class="sim-kv"><span>final value</span><b>${last.value != null ? (+last.value).toFixed(3) : "—"}</b></div>
    <div class="sim-kv"><span>final 95% CI</span><b>${last.ci_low != null ? (+last.ci_low).toFixed(2) : "—"} – ${last.ci_high != null ? (+last.ci_high).toFixed(2) : "—"}</b></div>`;
  if (eq) html += `<div class="sim-eq">${esc(eq)}</div>`;
  if (r.paused) html += `<div class="sim-note">⏸ paused: ${esc(r.paused_reason || "CI exceeded threshold")}</div>`;
  html += `</div>`;
  if ((r.assumptions || []).length) {
    html += `<div class="sim-assumptions"><div class="sim-assume-lbl">ASSUMPTIONS</div><ul>` +
      r.assumptions.map((a) => `<li>${esc(a)}</li>`).join("") + `</ul></div>`;
  }
  return html;
}

function _simMiniChart(traj) {
  const pts = traj.filter((s) => s.value != null && isFinite(s.value));
  if (pts.length < 2) return "";
  const W = 280, H = 88, pad = 6;
  const vals = pts.map((s) => s.value);
  const los = pts.map((s) => (s.ci_low != null && isFinite(s.ci_low) ? s.ci_low : s.value));
  const his = pts.map((s) => (s.ci_high != null && isFinite(s.ci_high) ? s.ci_high : s.value));
  const lo = Math.min(...vals, ...los), hi = Math.max(...vals, ...his);
  const span = (hi - lo) || 1;
  const mx = (i) => pad + (i / (pts.length - 1)) * (W - 2 * pad);
  const my = (v) => (H - pad) - ((v - lo) / span) * (H - 2 * pad);
  let band = `M ${mx(0).toFixed(1)} ${my(his[0]).toFixed(1)}`;
  for (let i = 1; i < pts.length; i++) band += ` L ${mx(i).toFixed(1)} ${my(his[i]).toFixed(1)}`;
  for (let i = pts.length - 1; i >= 0; i--) band += ` L ${mx(i).toFixed(1)} ${my(los[i]).toFixed(1)}`;
  band += " Z";
  let line = "";
  pts.forEach((s, i) => line += (i === 0 ? "M" : " L") + ` ${mx(i).toFixed(1)} ${my(s.value).toFixed(1)}`);
  return `<svg class="sim-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path d="${band}" fill="#6ee7ff" opacity="0.13"/>
    <path d="${line}" stroke="#6ee7ff" stroke-width="1.5" fill="none"/>
  </svg>`;
}

async function runIntervene(scope) {
  const ent = (document.getElementById(_simId(scope, "int-entity")) || {}).value;
  const delta = parseFloat((document.getElementById(_simId(scope, "int-delta")) || {}).value);
  const depth = parseInt((document.getElementById(_simId(scope, "int-depth")) || {}).value, 10) || 3;
  const res = document.getElementById(_simId(scope, "int-result"));
  if (!res) return;
  if (!_currentRunDir) { res.innerHTML = `<div class="empty-state sim-empty" style="color:var(--warn)">Run an analysis first.</div>`; return; }
  if (!ent) { res.innerHTML = `<div class="empty-state sim-empty" style="color:var(--warn)">Pick a source variable.</div>`; return; }
  if (Number.isNaN(delta)) { res.innerHTML = `<div class="empty-state sim-empty" style="color:var(--warn)">Enter a numeric Δ.</div>`; return; }
  res.innerHTML = `<div class="empty-state sim-empty">⤲ Propagating Δ${delta} from ${esc(ent)}…</div>`;
  const j = await auroraFetch("/api/system_model/intervene", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_dir: _currentRunDir, source_entity_id: ent, perturbation: delta, max_depth: depth }),
  });
  res.innerHTML = _renderInterveneResult(j, ent, delta);
}

function _renderInterveneResult(j, srcEnt, delta) {
  const r = j && j.result;
  if (!j || j.ok === false || !r) {
    return `<div class="sim-verdict sim-verdict--warn">⚠ ${esc((j && j.error) || "intervention unavailable")}</div>`;
  }
  const deltas = (r.deltas || []).filter((d) => d.entity_id !== r.source_entity_id);
  deltas.sort((a, b) => Math.abs(b.delta || 0) - Math.abs(a.delta || 0));
  let html = `<div class="sim-verdict">
    <div class="sim-verdict-head">do(${esc(r.source_entity_id || srcEnt)} += ${esc(String(r.perturbation != null ? r.perturbation : delta))})</div>`;
  if (r.rationale) html += `<div class="sim-rationale">${esc(r.rationale)}</div>`;
  if (!deltas.length) {
    html += `<div class="sim-note">No downstream effects — this variable has no outgoing relationships in the model.</div>`;
  } else {
    html += `<div class="sim-deltas">` + deltas.slice(0, 10).map((d) => {
      const v = d.delta != null ? ((d.delta >= 0 ? "+" : "") + (+d.delta).toFixed(3)) : "—";
      const cls = (d.delta || 0) >= 0 ? "up" : "down";
      const ci = (d.ci_low != null && d.ci_high != null) ? `${(+d.ci_low).toFixed(2)}–${(+d.ci_high).toFixed(2)}` : "";
      return `<div class="sim-delta-row">
        <span class="sim-delta-ent">${esc(d.entity_id)}</span>
        <span class="sim-delta-val sim-delta-val--${cls}">${v}</span>
        ${ci ? `<span class="sim-delta-ci">95% ${ci}</span>` : ""}
      </div>`;
    }).join("") + `</div>`;
  }
  html += `</div>`;
  if ((r.warnings || []).length) {
    html += `<div class="sim-assumptions"><div class="sim-assume-lbl">WARNINGS</div><ul>` +
      r.warnings.map((w) => `<li>${esc(w)}</li>`).join("") + `</ul></div>`;
  }
  return html;
}


// ---------------------------------------------------------------------
// 8c. Share a finding -> /api/community/share. Copy-to-clipboard always
// works; "Share to community" appends to the local feed and (if a webhook
// is configured) posts to Discord/Slack. Only finding text + cited method
// + dataset NAME are shared — never the raw rows.
// ---------------------------------------------------------------------
let _shareFinding = null;

function _shareText(f) {
  const sev = (f.severity || "info").toUpperCase();
  const title = f.title || f.name || "Aurora finding";
  const desc = String(f.description || f.narrative || f.summary || "").trim();
  const parts = [`[${sev}] ${title}`];
  if (desc) parts.push(desc);
  const meta = [];
  if (f.method) meta.push(`method: ${f.method}`);
  if (_currentDatasetName) meta.push(`dataset: ${_currentDatasetName}`);
  if (meta.length) parts.push(meta.join(" · "));
  parts.push("— found with Aurora (glass-box analysis)");
  return parts.join("\n");
}

function openShareModal(finding) {
  _shareFinding = finding;
  const modal = document.getElementById("shareModal");
  const preview = document.getElementById("sharePreview");
  const text = document.getElementById("shareText");
  const status = document.getElementById("shareStatus");
  if (!modal || !text) return;
  const sev = (finding.severity || "info").toLowerCase();
  if (preview) preview.innerHTML =
    `<span class="finding-sev finding-sev--${sev}">${sev}</span>` +
    `<span class="share-prev-title">${esc(finding.title || finding.name || "finding")}</span>` +
    (finding.method ? `<span class="share-prev-method">${esc(finding.method)}</span>` : "");
  text.value = _shareText(finding);
  if (status) { status.textContent = ""; status.className = "share-status"; }
  modal.classList.add("is-visible");
  modal.setAttribute("aria-hidden", "false");
}

function closeShareModal() {
  const modal = document.getElementById("shareModal");
  if (modal) { modal.classList.remove("is-visible"); modal.setAttribute("aria-hidden", "true"); }
  _shareFinding = null;
}

async function copyShareText() {
  const text = document.getElementById("shareText");
  const status = document.getElementById("shareStatus");
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text.value);
    if (status) { status.textContent = "✓ copied to clipboard"; status.className = "share-status share-status--ok"; }
  } catch (e) {
    text.focus(); text.select();
    if (status) { status.textContent = "press Ctrl+C to copy"; status.className = "share-status"; }
  }
}

async function doCommunityShare() {
  const f = _shareFinding;
  const text = document.getElementById("shareText");
  const status = document.getElementById("shareStatus");
  const btn = document.getElementById("shareSendBtn");
  if (!f) return;
  if (status) { status.textContent = "▶ sharing…"; status.className = "share-status"; }
  if (btn) btn.disabled = true;
  const payload = {
    title: f.title || f.name || "Aurora finding",
    detail: (text && text.value) || String(f.description || ""),
    method: f.method || "",
    dataset: _currentDatasetName || "",
    run_id: _currentRunDir ? String(_currentRunDir).split(/[\\/]/).pop() : "",
    severity: (f.severity || "info").toLowerCase(),
    confidence: f.confidence != null ? f.confidence : null,
  };
  const j = await auroraFetch("/api/community/share", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (btn) btn.disabled = false;
  if (!j || j.ok === false) {
    if (status) { status.textContent = `✕ ${(j && j.error) || "share failed"}`; status.className = "share-status share-status--err"; }
    return;
  }
  // The hosted community feed is the headline result — report it honestly,
  // including the exact error when the post didn't make it.
  const bits = [];
  if (j.hosted_shared) bits.push("✓ live on the community feed");
  else if (j.community_api) bits.push(`✕ community feed unreachable (${j.hosted_error || "network"})`);
  if (j.delivered) bits.push("webhook delivered");
  else if (j.configured) bits.push(`webhook failed (${j.deliver_error || "network"})`);
  bits.push(`#${j.feed_count} in your local feed`);
  const good = j.hosted_shared || j.delivered;
  if (status) {
    status.textContent = (good ? "" : "") + bits.join(" · ");
    status.className = good ? "share-status share-status--ok" : "share-status share-status--err";
  }
}

function wireShareModal() {
  const close = document.getElementById("shareClose");
  const back = document.getElementById("shareBackdrop");
  const copy = document.getElementById("shareCopyBtn");
  const send = document.getElementById("shareSendBtn");
  if (close) close.addEventListener("click", closeShareModal);
  if (back) back.addEventListener("click", closeShareModal);
  if (copy) copy.addEventListener("click", copyShareText);
  if (send) send.addEventListener("click", doCommunityShare);
  document.addEventListener("keydown", (e) => {
    const modal = document.getElementById("shareModal");
    if (e.key === "Escape" && modal && modal.classList.contains("is-visible")) closeShareModal();
  });
}


// ---------------------------------------------------------------------
// 8d. Community — findings shared by Aurora users. Reads /api/community/feed
// (the hosted cross-machine feed, with local fallback).
// ---------------------------------------------------------------------
async function refreshCommunity() {
  const wrap = document.getElementById("communityFeed");
  if (!wrap) return;
  const r = await auroraFetch("/api/community/feed?limit=50");
  if (!r || r.ok === false) {
    setText("communityMeta", "unavailable");
    wrap.innerHTML = renderEmpty(
      "Community feed unavailable. " + esc((r && r.error) || "Start Aurora and retry."));
    return;
  }
  const items = Array.isArray(r.items) ? r.items : [];
  const src = r.source === "community" ? "live · across machines" : "local only";
  setText("communityMeta", `${items.length} shared · ${src}`);
  if (!items.length) {
    wrap.innerHTML = renderEmpty(
      "No shared findings yet. Be the first — open <b>Findings</b>, hit <b>⤴ Share</b> on a " +
      "finding, then <b>Share to community</b>.");
    return;
  }
  wrap.innerHTML = `<div class="community-grid">` + items.map((it) => {
    const sev = (it.severity || "info").toLowerCase();
    const when = _relativeTimeFromIso(it.ts);
    const meta = [];
    if (it.method)  meta.push(esc(it.method));
    if (it.dataset) meta.push(esc(it.dataset));
    return `<article class="comm-card comm-card--${sev}">
      <div class="comm-card-head">
        <span class="finding-sev finding-sev--${sev}">${sev}</span>
        ${when ? `<span class="comm-when">${esc(when)}</span>` : ""}
      </div>
      <div class="comm-title">${esc(it.title || "finding")}</div>
      ${it.detail ? `<div class="comm-detail">${esc(String(it.detail).slice(0, 220))}</div>` : ""}
      ${meta.length ? `<div class="comm-meta">${meta.join(" · ")}</div>` : ""}
    </article>`;
  }).join("") + `</div>`;
}

function _relativeTimeFromIso(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60)    return "just now";
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}


// ---------------------------------------------------------------------
// 8d. Data workbench — Correlations / Distributions / Quality tabs on the
// Data view. One /api/analysis/workbench fetch per run (Pearson + Spearman
// matrices + per-column stats/histograms + a downsampled numeric sample);
// everything else (heatmap, scatter, regression stats, causal check) is
// computed client-side for instant interaction.
// ---------------------------------------------------------------------
let _wbCache = { runDir: null, data: null };
let _wbCorrMethod = "pearson";
let _wbScatter = { xi: 0, yi: 1, mode: "scatter" };

function wireDataTabs() {
  document.querySelectorAll(".wb-tab").forEach((t) => {
    t.addEventListener("click", () => {
      const which = t.dataset.wbtab;
      document.querySelectorAll(".wb-tab").forEach((x) => x.classList.toggle("is-active", x === t));
      document.querySelectorAll(".wb-pane").forEach((p) => { p.hidden = (p.dataset.wbpane !== which); });
      if (which !== "table") loadWorkbench(which);
    });
  });
}

const _wbHostId = (tab) => tab === "correlations" ? "wbCorr" : tab === "distributions" ? "wbDist" : "wbQual";
function _wbMsg(tab, msg, warn) {
  const el = document.getElementById(_wbHostId(tab));
  if (el) el.innerHTML = `<div class="empty-state"${warn ? ' style="color:var(--warn)"' : ""}>${esc(msg)}</div>`;
}

async function loadWorkbench(tab) {
  if (!_currentRunDir) { _wbMsg(tab, "Run an analysis first — the workbench reads that run's dataset.", true); return; }
  if (_wbCache.runDir === _currentRunDir && _wbCache.data) { _renderWbTab(tab, _wbCache.data); return; }
  _wbMsg(tab, "▶ computing correlations from the raw dataset…");
  const j = await auroraFetch(`/api/analysis/workbench?run_dir=${encodeURIComponent(_currentRunDir)}`);
  if (!j || j.ok === false) { _wbMsg(tab, (j && j.error) || "workbench unavailable", true); return; }
  _wbCache = { runDir: _currentRunDir, data: j };
  _wbScatter = { xi: 0, yi: (j.columns && j.columns.length > 1) ? 1 : 0, mode: "scatter" };
  _renderWbTab(tab, j);
}

function _renderWbTab(tab, data) {
  if (tab === "correlations") renderWbCorrelations(data);
  else if (tab === "distributions") renderWbDistributions(data);
  else if (tab === "quality") renderWbQuality(data);
}

const _wbShort = (s) => { s = String(s); return s.length > 11 ? s.slice(0, 10) + "…" : s; };
function _corrColor(v) {
  if (v == null) return "rgba(255,255,255,0.03)";
  const a = Math.min(1, Math.abs(v));
  return v >= 0 ? `rgba(110,231,255,${(0.10 + 0.78 * a).toFixed(3)})`
                : `rgba(255,110,199,${(0.10 + 0.78 * a).toFixed(3)})`;
}

function renderWbCorrelations(data) {
  const host = document.getElementById("wbCorr");
  if (!host) return;
  const cols = data.columns || [];
  if (cols.length < 2) { host.innerHTML = `<div class="empty-state">Need ≥2 numeric columns for a correlation matrix (this dataset has ${cols.length}).</div>`; return; }
  const M = _wbCorrMethod === "spearman" ? data.corr_spearman : data.corr_pearson;
  const n = cols.length;
  let hm = `<div class="wb-heatmap" style="grid-template-columns:100px repeat(${n},minmax(30px,1fr))"><div class="wb-hcorner"></div>`;
  cols.forEach((c) => hm += `<div class="wb-hcol" title="${esc(c)}">${esc(_wbShort(c))}</div>`);
  for (let i = 0; i < n; i++) {
    hm += `<div class="wb-hrow" title="${esc(cols[i])}">${esc(_wbShort(cols[i]))}</div>`;
    for (let j = 0; j < n; j++) {
      const v = (M && M[i]) ? M[i][j] : null;
      const t = v == null ? "" : (Math.abs(v) >= 0.995 ? "1" : v.toFixed(2).replace("0.", ".").replace("-0.", "-."));
      hm += `<div class="wb-cell${i === j ? " wb-cell--diag" : ""}" data-xi="${i}" data-yi="${j}" style="background:${_corrColor(v)}" title="${esc(cols[i])} × ${esc(cols[j])} = ${v == null ? "—" : v.toFixed(3)}">${t}</div>`;
    }
  }
  hm += `</div>`;
  host.innerHTML = `
    <div class="wb-corr-head">
      <div class="wb-corr-title">Correlation matrix · <b>${n}</b> numeric columns · n=${data.n_rows_full}</div>
      <div class="wb-toggle">
        <button class="wb-mini ${_wbCorrMethod === "pearson" ? "is-active" : ""}" data-corrm="pearson">Pearson</button>
        <button class="wb-mini ${_wbCorrMethod === "spearman" ? "is-active" : ""}" data-corrm="spearman">Spearman</button>
      </div>
    </div>
    <div class="wb-corr-body">
      <div class="wb-heatmap-wrap">${hm}${_topPairsHTML(cols, M)}</div>
      <div class="wb-scatter-wrap" id="wbScatterWrap"></div>
    </div>`;
  host.querySelectorAll("[data-corrm]").forEach((b) => b.addEventListener("click", () => { _wbCorrMethod = b.dataset.corrm; renderWbCorrelations(_wbCache.data); }));
  host.querySelectorAll(".wb-cell[data-xi]").forEach((c) => c.addEventListener("click", () => {
    _wbScatter.xi = +c.dataset.xi; _wbScatter.yi = +c.dataset.yi; renderWbScatter(_wbCache.data);
  }));
  host.querySelectorAll(".wb-pair[data-xi]").forEach((c) => c.addEventListener("click", () => {
    _wbScatter.xi = +c.dataset.xi; _wbScatter.yi = +c.dataset.yi; renderWbScatter(_wbCache.data);
  }));
  renderWbScatter(data);
}

function _topPairsHTML(cols, M) {
  const pairs = [];
  for (let i = 0; i < cols.length; i++)
    for (let j = i + 1; j < cols.length; j++) {
      const v = (M && M[i]) ? M[i][j] : null;
      if (v != null) pairs.push({ i, j, v });
    }
  pairs.sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
  const top = pairs.slice(0, 8);
  if (!top.length) return "";
  return `<div class="wb-toppairs"><div class="wb-tp-title">Strongest relationships — click to inspect</div>` +
    top.map((p) => {
      const a = Math.min(1, Math.abs(p.v)), col = p.v >= 0 ? "var(--cyan)" : "var(--magenta)";
      return `<div class="wb-pair" data-xi="${p.i}" data-yi="${p.j}">
        <span class="wb-pair-name">${esc(cols[p.i])} <span class="wb-pair-x">×</span> ${esc(cols[p.j])}</span>
        <span class="wb-pair-bar"><span style="width:${(a * 100).toFixed(0)}%;background:${col}"></span></span>
        <span class="wb-pair-r" style="color:${col}">${p.v >= 0 ? "+" : ""}${p.v.toFixed(2)}</span>
      </div>`;
    }).join("") + `</div>`;
}

function _normCdf(x) {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp(-x * x / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - p : p;
}
function _corrStats(r, n) {
  if (r == null || n == null || n < 4 || Math.abs(r) >= 1) return { p: "—", ci: "—" };
  const z = 0.5 * Math.log((1 + r) / (1 - r)), se = 1 / Math.sqrt(n - 3);
  const lo = Math.tanh(z - 1.96 * se), hi = Math.tanh(z + 1.96 * se);
  const pv = 2 * (1 - _normCdf(Math.abs(z) / se));
  return { p: pv < 1e-4 ? "<0.0001" : pv.toFixed(4), ci: `${lo.toFixed(2)} – ${hi.toFixed(2)}` };
}

function renderWbScatter(data) {
  const wrap = document.getElementById("wbScatterWrap");
  if (!wrap) return;
  const cols = data.columns, xi = _wbScatter.xi, yi = _wbScatter.yi;
  if (xi === yi) { wrap.innerHTML = `<div class="empty-state" style="padding:24px">Click any off-diagonal cell (or a pair on the left) to inspect two variables.</div>`; return; }
  const pts = [];
  (data.sample || []).forEach((row) => { const x = row[xi], y = row[yi]; if (x != null && y != null && isFinite(x) && isFinite(y)) pts.push([x, y]); });
  const rFull = (data.corr_pearson && data.corr_pearson[xi]) ? data.corr_pearson[xi][yi] : null;
  const rSp = (data.corr_spearman && data.corr_spearman[xi]) ? data.corr_spearman[xi][yi] : null;
  const stats = _corrStats(rFull, data.n_rows_full);
  const rCol = rFull == null ? "var(--ink)" : (rFull >= 0 ? "var(--cyan)" : "var(--magenta)");
  const nonlin = (rFull != null && rSp != null && Math.abs(rSp) - Math.abs(rFull) > 0.15);
  wrap.innerHTML = `
    <div class="wb-sc-head">
      <div class="wb-sc-title">${esc(cols[xi])} <span class="wb-pair-x">vs</span> ${esc(cols[yi])}</div>
      <div class="wb-toggle">
        <button class="wb-mini ${_wbScatter.mode === "scatter" ? "is-active" : ""}" data-scm="scatter">Scatter</button>
        <button class="wb-mini ${_wbScatter.mode === "line" ? "is-active" : ""}" data-scm="line">Line</button>
        <button class="wb-mini ${_wbScatter.mode === "residual" ? "is-active" : ""}" data-scm="residual">Residual</button>
      </div>
    </div>
    ${_scatterSVG(pts, _wbScatter.mode)}
    <div class="wb-sc-stats">
      <span class="wb-stat">r <b style="color:${rCol}">${rFull == null ? "—" : (rFull >= 0 ? "+" : "") + rFull.toFixed(3)}</b></span>
      <span class="wb-stat">r² <b>${rFull == null ? "—" : (rFull * rFull).toFixed(3)}</b></span>
      <span class="wb-stat">n <b>${data.n_rows_full}</b></span>
      <span class="wb-stat">p <b>${stats.p}</b></span>
      <span class="wb-stat">95% CI <b>${stats.ci}</b></span>
    </div>
    ${nonlin ? `<div class="wb-sc-note">⚠ Non-linear — Spearman ρ=${rSp.toFixed(2)} is well above Pearson r; the relationship isn't a straight line.</div>` : ""}
    <div class="wb-sc-actions">
      <button class="btn btn-primary" id="wbCausalBtn"><span class="btn-ico">◎</span> Is it real? — run do-calculus</button>
    </div>
    <div class="wb-causal" id="wbCausalResult"></div>`;
  wrap.querySelectorAll("[data-scm]").forEach((b) => b.addEventListener("click", () => { _wbScatter.mode = b.dataset.scm; renderWbScatter(_wbCache.data); }));
  const cb = document.getElementById("wbCausalBtn");
  if (cb) cb.addEventListener("click", () => _wbRunCausal(cols[xi], cols[yi], rFull));
}

function _scatterSVG(pts, mode) {
  if (pts.length < 2) return `<div class="empty-state" style="padding:24px">Not enough overlapping numeric points to plot.</div>`;
  const W = 560, H = 260, pad = 34;
  const n = pts.length;
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  pts.forEach(([x, y]) => { sx += x; sy += y; sxx += x * x; sxy += x * y; });
  const mx = sx / n, my = sy / n;
  const slope = (sxy - n * mx * my) / ((sxx - n * mx * mx) || 1e-9), intercept = my - slope * mx;
  if (mode === "line") return _dualLineSVG(pts, W, H, pad);
  if (mode === "residual") return _cloudSVG(pts.map(([x, y]) => [x, y - (slope * x + intercept)]), W, H, pad, null, null, true);
  return _cloudSVG(pts, W, H, pad, slope, intercept, false);
}
function _extent(a) { let lo = Infinity, hi = -Infinity; a.forEach((v) => { if (v < lo) lo = v; if (v > hi) hi = v; }); if (lo === hi) { lo -= 1; hi += 1; } return [lo, hi]; }
function _cloudSVG(pts, W, H, pad, slope, intercept, isResid) {
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const [xl, xh] = _extent(xs), [yl, yh] = _extent(ys);
  const mX = (x) => pad + ((x - xl) / (xh - xl)) * (W - 2 * pad);
  const mY = (y) => (H - pad) - ((y - yl) / (yh - yl)) * (H - 2 * pad);
  let dots = "";
  pts.forEach((p) => { dots += `<circle cx="${mX(p[0]).toFixed(1)}" cy="${mY(p[1]).toFixed(1)}" r="1.7" fill="#6ee7ff" opacity="0.5"/>`; });
  let line = "";
  if (isResid) { const y0 = mY(0); line = `<line x1="${pad}" y1="${y0.toFixed(1)}" x2="${W - pad}" y2="${y0.toFixed(1)}" stroke="#88ffd1" stroke-width="1.2" stroke-dasharray="5 4"/>`; }
  else if (slope != null) { const x0 = xl, x1 = xh; line = `<line x1="${mX(x0).toFixed(1)}" y1="${mY(slope * x0 + intercept).toFixed(1)}" x2="${mX(x1).toFixed(1)}" y2="${mY(slope * x1 + intercept).toFixed(1)}" stroke="#88ffd1" stroke-width="1.6"/>`; }
  return `<svg class="wb-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    <line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#2a3550"/>
    <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${H - pad}" stroke="#2a3550"/>
    ${dots}${line}</svg>`;
}
function _dualLineSVG(pts, W, H, pad) {
  const norm = (arr) => { const [lo, hi] = _extent(arr); return arr.map((v) => (v - lo) / (hi - lo)); };
  const xN = norm(pts.map((p) => p[0])), yN = norm(pts.map((p) => p[1]));
  const mX = (i) => pad + (i / (pts.length - 1)) * (W - 2 * pad);
  const mY = (t) => (H - pad) - t * (H - 2 * pad);
  const path = (vals) => vals.map((t, i) => (i ? "L" : "M") + ` ${mX(i).toFixed(1)} ${mY(t).toFixed(1)}`).join(" ");
  return `<svg class="wb-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    <line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#2a3550"/>
    <path d="${path(xN)}" stroke="#6ee7ff" stroke-width="1.4" fill="none" opacity="0.9"/>
    <path d="${path(yN)}" stroke="#88ffd1" stroke-width="1.4" fill="none" opacity="0.9"/>
    <text x="${W - pad}" y="16" text-anchor="end" font-family="'JetBrains Mono',monospace" font-size="10" fill="#6ee7ff">X</text>
    <text x="${W - pad}" y="30" text-anchor="end" font-family="'JetBrains Mono',monospace" font-size="10" fill="#88ffd1">Y (both normalized)</text>
  </svg>`;
}

async function _wbRunCausal(x, y, r) {
  const res = document.getElementById("wbCausalResult");
  if (!res) return;
  res.innerHTML = `<div class="wb-causal-box">▶ running do-calculus for do(${esc(x)}) → ${esc(y)}…</div>`;
  const body = { treatment: x, outcome: y };
  if (_currentRunDir) body.run_dir = _currentRunDir;
  const j = await auroraFetch("/api/causal/do", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!j || j.error || j.ok === false || j.unidentifiable_reason) {
    const reason = (j && (j.unidentifiable_reason || j.error)) || "effect not identifiable from this run's DAG";
    res.innerHTML = `<div class="wb-causal-box wb-causal-box--warn">
      <div class="wb-cv-h">⚠ correlation ≠ causation here</div>
      <div class="wb-cv-t">r = ${r == null ? "—" : r.toFixed(2)}, but the causal effect is <b>not identifiable</b>: ${esc(reason)}. Don't act on this as if it were causal.</div></div>`;
    return;
  }
  const beta = j.effect_estimate != null ? (+j.effect_estimate).toFixed(3) : "—";
  const se = j.standard_error != null ? (+j.standard_error).toFixed(3) : "—";
  const adj = (j.adjustment_set || []).join(", ") || "(none needed)";
  const near0 = j.effect_estimate != null && Math.abs(+j.effect_estimate) < (0.05 * (Math.abs(+(j.standard_error || 0)) + 1));
  res.innerHTML = `<div class="wb-causal-box">
    <div class="wb-cv-h">◎ causal verdict — do(${esc(x)}) → ${esc(y)}</div>
    <div class="wb-cv-effect">effect ${beta}<span class="wb-cv-se"> ± ${se} SE</span></div>
    <div class="wb-cv-row">correlation r = <b>${r == null ? "—" : r.toFixed(2)}</b> · adjusted for: <b>${esc(adj)}</b> · n=${esc(String(j.n_obs || "—"))}</div>
    <div class="wb-cv-take">${near0
      ? "The correlation largely <b>collapses</b> once you adjust — likely confounded. Be cautious about acting on it."
      : "The effect <b>survives</b> adjustment — this is a candidate to act on. Validate the magnitude before committing."}</div>
  </div>`;
}

function renderWbDistributions(data) {
  const host = document.getElementById("wbDist");
  if (!host) return;
  const nums = (data.col_stats || []).filter((c) => c.hist && c.hist.counts && c.hist.counts.length);
  if (!nums.length) { host.innerHTML = `<div class="empty-state">No numeric columns to plot distributions for.</div>`; return; }
  host.innerHTML = `<div class="wb-dist-grid">` + nums.map((c) => {
    const mx = Math.max(1, ...c.hist.counts);
    const bars = c.hist.counts.map((n) => `<span class="wb-bar" style="height:${(6 + 94 * n / mx).toFixed(0)}%" title="${n}"></span>`).join("");
    return `<div class="wb-dist-card">
      <div class="wb-dist-name">${esc(c.name)}</div>
      <div class="wb-dist-bars">${bars}</div>
      <div class="wb-dist-foot">${(c.min != null ? c.min.toFixed(1) : "")} <span>μ ${c.mean != null ? c.mean.toFixed(1) : "—"} · σ ${c.std != null ? c.std.toFixed(1) : "—"}</span> ${(c.max != null ? c.max.toFixed(1) : "")}</div>
    </div>`;
  }).join("") + `</div>`;
}

function renderWbQuality(data) {
  const host = document.getElementById("wbQual");
  if (!host) return;
  const rows = data.col_stats || [];
  if (!rows.length) { host.innerHTML = `<div class="empty-state">No columns to summarize.</div>`; return; }
  host.innerHTML = `<div class="wb-qual-head">${data.columns.length} numeric of ${rows.length} columns · ${data.n_rows_full} rows</div>
    <table class="wb-qual"><thead><tr><th>column</th><th>type</th><th>missing</th><th>min</th><th>mean</th><th>max</th></tr></thead><tbody>` +
    rows.map((c) => {
      const miss = c.missing_pct || 0;
      const mc = miss === 0 ? "var(--mint)" : miss < 5 ? "var(--warn)" : "var(--crit)";
      return `<tr>
        <td class="wb-q-name">${esc(c.name)}</td>
        <td class="wb-q-type">${esc(c.dtype)}</td>
        <td><span class="wb-q-miss"><span class="wb-q-missbar" style="width:${Math.min(100, miss).toFixed(0)}%;background:${mc}"></span></span> ${miss}%</td>
        <td class="wb-q-num">${c.min != null ? c.min.toFixed(2) : "—"}</td>
        <td class="wb-q-num">${c.mean != null ? c.mean.toFixed(2) : "—"}</td>
        <td class="wb-q-num">${c.max != null ? c.max.toFixed(2) : "—"}</td>
      </tr>`;
    }).join("") + `</tbody></table>`;
}


// ---------------------------------------------------------------------
// 8e. Decision layer — turn a finding into a recommended decision + a
// one-page brief. The "so what?" that Correlation Studio's analysis never
// reaches: it stops at "investigate further"; Aurora ends at a decision.
// ---------------------------------------------------------------------
function _decisionForFinding(f) {
  const sev = (f.severity || "info").toLowerCase();
  const m = (f.method || "").toLowerCase();
  let rec;
  if (/change|break|cusum|bocpd|ruptur/.test(m))
    rec = "A structural break was detected. Find out what changed at that point and recalibrate thresholds/forecasts to the post-break regime before trusting older baselines.";
  else if (/regime|hmm|markov/.test(m))
    rec = "The system shifted regimes. Confirm which regime you're in now and apply the policy that fits it — a rule tuned for the other regime will misfire.";
  else if (/anom|outlier|iso|z-?score|mad|robust/.test(m))
    rec = "An outlier was flagged. First rule out a data error; if it's real, decide whether it's a one-off to log or a signal to act on now.";
  else if (/causal|granger|do-?calc|backdoor/.test(m))
    rec = "A directional relationship was estimated. If it holds up under do-calculus you can intervene on the driver to move the outcome — validate the effect size first.";
  else if (/forecast|arima|ensemble|prophet|ets|sindy|physics/.test(m))
    rec = "The model projects a notable move. Pre-position for the predicted value and set an alert at the confidence bound so you're warned if reality diverges.";
  else
    rec = "Verify the relationship is causal (not confounded) before acting on it, then take the smallest reversible action that tests it.";
  const risk = sev === "crit" ? "High — flagged critical; ignoring it risks a material miss."
             : sev === "warn" ? "Moderate — worth acting on this cycle."
             : "Low — monitor; no immediate action needed.";
  const conf = f.confidence != null ? `${Math.round(Number(f.confidence) * 100)}% confidence` : "";
  return { rec, risk, conf, sev };
}

function _decisionHTML(f) {
  const d = _decisionForFinding(f);
  return `<div class="decision-card">
    <div class="decision-lbl">▸ DECISION</div>
    <div class="decision-rec">${esc(d.rec)}</div>
    <div class="decision-meta">
      <span class="decision-risk decision-risk--${d.sev}">risk if ignored: ${esc(d.risk)}</span>
      ${d.conf ? `<span class="decision-conf">${esc(d.conf)}</span>` : ""}
    </div>
    <div class="decision-actions">
      <button class="btn btn-secondary" id="decBriefBtn"><span class="btn-ico">⧉</span> Copy decision brief</button>
      <button class="btn btn-secondary" id="decShareBtn"><span class="btn-ico">⤴</span> Share</button>
    </div>
    <div class="decision-status" id="decStatus"></div>
  </div>`;
}

function _briefMarkdown(f) {
  const d = _decisionForFinding(f);
  const ev = _findingEvidence(f).map((e) => `- ${e.k}: ${e.v}`);
  const cite = _citationText(f);
  const out = [
    "# Aurora Decision Brief", "",
    `**Finding:** ${f.title || f.name || "(untitled)"}`,
    `**Severity:** ${(f.severity || "info").toUpperCase()}${d.conf ? " · " + d.conf : ""}`,
    `**Method:** ${f.method || "—"}`,
  ];
  const desc = String(f.description || f.narrative || f.summary || "").trim();
  if (desc) out.push("", desc);
  if (ev.length) out.push("", "## Evidence", ...ev);
  if (cite) out.push("", `**Cited source:** ${cite}`);
  out.push("", "## Recommended decision", d.rec, "", `**Risk if ignored:** ${d.risk}`);
  if (_currentDatasetName) out.push("", `_Dataset: ${_currentDatasetName}_`);
  out.push("_Generated by Aurora — glass-box analysis; every claim is cited._");
  return out.join("\n");
}

function wireDecisionCard(finding) {
  const brief = document.getElementById("decBriefBtn");
  const share = document.getElementById("decShareBtn");
  const status = document.getElementById("decStatus");
  if (brief) brief.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(_briefMarkdown(finding)); if (status) { status.textContent = "✓ decision brief copied — paste into a doc, Slack, or email"; status.className = "decision-status decision-status--ok"; } }
    catch (e) { if (status) status.textContent = "copy failed — select the finding text manually"; }
  });
  if (share) share.addEventListener("click", () => { closeFindingDetail(); openShareModal(finding); });
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

  // Build + wire the Simulate/Intervene panels (Forecast + Spacetime) and
  // the share-finding modal.
  SIM_SCOPES.forEach((sc) => { buildSimPanel(sc); wireSimPanel(sc); });
  wireShareModal();
  wireDataTabs();

  const runBtn = document.getElementById("runBtn");
  if (runBtn) runBtn.addEventListener("click", triggerRun);

  const causalDoBtn = document.getElementById("causalDoBtn");
  if (causalDoBtn) causalDoBtn.addEventListener("click", () => _runCausal("do"));
  const causalIdBtn = document.getElementById("causalIdentifyBtn");
  if (causalIdBtn) causalIdBtn.addEventListener("click", () => _runCausal("identify"));

  const kbBtn = document.getElementById("kbSearchBtn");
  if (kbBtn) kbBtn.addEventListener("click", _kbSearch);

  const commRefresh = document.getElementById("communityRefreshBtn");
  if (commRefresh) commRefresh.addEventListener("click", refreshCommunity);
  const kbInput = document.getElementById("kbSearchInput");
  if (kbInput) kbInput.addEventListener("keydown", (e) => { if (e.key === "Enter") _kbSearch(); });

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
    if (auroraOnline && currentView === "overview")   refreshOverview(_currentRunDir);
    if (auroraOnline && currentView === "findings")   refreshFindings(_currentRunDir);
    if (auroraOnline && currentView === "data")       refreshData(_currentRunDir);
    if (auroraOnline && currentView === "phasespace") refreshPhaseSpace(_currentRunDir);
    if (auroraOnline && currentView === "spacetime")  refreshSpacetime(_currentRunDir);
    if (auroraOnline && currentView === "forecast")   refreshForecast(_currentRunDir);
  }, STATE_POLL_MS);
});

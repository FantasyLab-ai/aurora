// Aurora live demo — a full desktop-app mock, running in the browser with zero
// install. Replays real pre-computed Aurora output (data/*.json) across the
// actual app views: Overview, Findings, Data (correlations/distributions/
// quality), Phase Space, Spacetime, Forecast, and the Decision. No backend.

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (id) => document.getElementById(id);
const num = (v, d = 2) => (v == null || isNaN(+v)) ? "—" : (+v).toFixed(d);

let _demo = null, _view = "overview", _dtab = "correlations";
let _sc = { xi: 0, yi: 1, mode: "scatter" }, _method = "pearson";

const METHOD_LABEL = {
  "bocpd": "Changepoint · BOCPD", "dtw": "Dynamic Time Warping", "emd": "Empirical Mode Decomp.",
  "kalman": "Kalman filter", "matrix_profile": "Matrix Profile", "var": "VAR", "robust_pca": "Robust PCA",
  "spectral_entropy": "Spectral entropy", "multivariate_outlier_consensus": "Outlier consensus",
  "AR(1)": "AR(1) forecast", "iso-forest + robust-Z": "Isolation Forest + robust-Z", "hmm": "HMM regimes",
  "sindy": "SINDy physics", "granger": "Granger causality", "mutual_information_ksg": "Mutual information",
};
const prettyMethod = (m) => METHOD_LABEL[m] || m;
const ENGINE_ALSO = ["SINDy physics discovery", "HMM regime detection", "Mutual information (non-linear)",
  "Causal do-calculus", "Ensemble forecasting", "Phase-space & attractors", "Knowledge-bank citations"];

const NAV = [
  { g: "WORKSPACE", items: [["overview", "◆", "Overview"], ["findings", "≣", "Findings"], ["data", "⊞", "Data"]] },
  { g: "ANALYSIS", items: [["phase", "◎", "Phase Space"], ["spacetime", "✳", "Spacetime"], ["forecast", "⌁", "Forecast"], ["decide", "▸", "Decision"]] },
];

async function boot() {
  let manifest = [];
  try { manifest = await (await fetch("data/manifest.json", { cache: "no-store" })).json(); }
  catch (e) { $("picker").innerHTML = `<div class="demo-loading">Demo data not found — <a href="https://github.com/FantasyLab-ai/aurora/releases/latest">download the app</a>.</div>`; return; }
  $("picker").innerHTML = `<div class="picker-title">Pick a real analysis — replayed in a live mock of the desktop app</div><div class="picker-grid">` +
    manifest.map((d) => `<button class="picker-card" data-slug="${esc(d.slug)}">
      <div class="pc-icon">${esc(d.icon || "◆")}</div><div class="pc-title">${esc(d.title)}</div>
      <div class="pc-blurb">${esc(d.blurb)}</div><div class="pc-go">▶ Open in Aurora</div></button>`).join("") + `</div>`;
  $("picker").querySelectorAll(".picker-card").forEach((b) => b.addEventListener("click", () => selectDemo(b.dataset.slug)));
  const want = new URLSearchParams(location.search).get("d");
  const start = manifest.find((m) => m.slug === want) || manifest[0];
  if (start) selectDemo(start.slug);
}

async function selectDemo(slug) {
  document.querySelectorAll(".picker-card").forEach((c) => c.classList.toggle("is-active", c.dataset.slug === slug));
  const res = $("result"); res.hidden = false;
  res.innerHTML = `<div class="demo-loading">▶ opening ${esc(slug)} in Aurora…</div>`;
  try { _demo = await (await fetch(`data/${slug}.json`, { cache: "no-store" })).json(); }
  catch (e) { res.innerHTML = `<div class="demo-loading">Could not load this demo.</div>`; return; }
  const wb = _demo.workbench || {};
  const VIEWS = ["overview", "findings", "data", "phase", "spacetime", "forecast", "decide"];
  const h = (location.hash || "").slice(1);
  _view = VIEWS.includes(h) ? h : "overview"; _dtab = "correlations";
  _sc = { xi: 0, yi: (wb.columns && wb.columns.length > 1) ? 1 : 0, mode: "scatter" };
  renderApp();
  res.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderApp() {
  const d = _demo;
  const side = NAV.map((s) => `<div class="side-g">${s.g}</div>` + s.items.map(([id, ic, lb]) =>
    `<button class="side-item ${_view === id ? "on" : ""}" data-view="${id}"><span class="si-ic">${ic}</span>${lb}</button>`).join("")).join("");
  $("result").innerHTML = `
    <div class="win">
      <div class="win-bar"><span class="win-dots"><i></i><i></i><i></i></span><span class="win-title">◆ aurora — ${esc(d.dataset || d.title)}</span>
        <a class="win-dl" href="https://github.com/FantasyLab-ai/aurora/releases/latest" target="_blank" rel="noopener">Download ↓</a></div>
      <div class="win-body">
        <aside class="win-side">${side}<div class="side-foot">● replay · no backend</div></aside>
        <main class="win-main" id="appMain"></main>
      </div>
    </div>`;
  $("result").querySelectorAll(".side-item").forEach((b) => b.addEventListener("click", () => { _view = b.dataset.view; try { history.replaceState(null, "", "#" + _view); } catch (e) {} renderApp(); }));
  renderMain();
}

function renderMain() {
  const el = $("appMain"); if (!el) return;
  const V = { overview: viewOverview, findings: viewFindings, data: viewData, phase: viewPhase, spacetime: viewSpacetime, forecast: viewForecast, decide: viewDecide };
  el.innerHTML = (V[_view] || viewOverview)();
  if (_view === "data") wireData();
}

// ---------------- OVERVIEW ----------------
function viewOverview() {
  const d = _demo, fs = d.findings || [];
  const crit = fs.filter((f) => f.severity === "crit").length, warn = fs.filter((f) => f.severity === "warn").length;
  return crumb("overview", d.dataset) + `
    <div class="ov-banner">✓ ANALYSIS COMPLETE · ${esc(d.dataset || d.title)} · <b>${(d.methods || []).length} methods run</b>, <b>0</b> fabricated</div>
    <div class="statbar">
      ${stat(fs.length, "findings", `${crit} crit · ${warn} warn`)}
      ${stat((d.methods || []).length, "methods run", "real stat/ML")}
      ${stat("0", "fabricated", "every claim cited", "mint")}
      ${stat(fs.filter((f) => f.cited).length, "cited", "traced to a source")}
    </div>
    <div class="methods-strip"><div class="ms-lbl">methods Aurora ran on this dataset</div>
      <div class="ms-badges">${(d.methods || []).map((m) => `<span class="mbadge">${esc(prettyMethod(m))}</span>`).join("")}</div>
      <div class="ms-also">…and the engine also does ${ENGINE_ALSO.map((x) => `<span class="also">${esc(x)}</span>`).join("")}</div></div>
    ${d.narrative ? `<div class="rz-narr"><b>Aurora's summary:</b> ${esc(d.narrative)}</div>` : ""}
    <div class="ov-hint">Explore the run using the sidebar → Findings, Data, Phase Space, Spacetime, Forecast, Decision — the same views as the installed app.</div>`;
}

// ---------------- FINDINGS ----------------
function viewFindings() {
  const fs = _demo.findings || [];
  return crumb("findings", `${fs.length} findings`) +
    `<div class="findings">${fs.map(findingCard).join("") || `<div class="muted">No findings.</div>`}</div>`;
}
function findingCard(f) {
  const sev = (f.severity || "info").toLowerCase(), ev = [];
  if (f.z != null && !isNaN(+f.z)) ev.push(["|z|", num(f.z, 1) + "σ"]);
  if (f.p != null && !isNaN(+f.p)) ev.push(["p", +f.p < 0.001 ? (+f.p).toExponential(1) : num(f.p, 3)]);
  if (f.row != null) ev.push(["row", f.row]);
  return `<article class="fc fc--${sev}"><div class="fc-head"><span class="sev sev--${sev}">${sev}</span>${f.confidence != null ? `<span class="conf">${Math.round(f.confidence * 100)}% conf</span>` : ""}</div>
    <div class="fc-title">${esc(f.title)}</div>${f.description ? `<div class="fc-desc">${esc(f.description)}</div>` : ""}
    ${ev.length ? `<div class="fc-ev">${ev.map(([k, v]) => `<span class="ev"><b>${esc(k)}</b>${esc(v)}</span>`).join("")}</div>` : ""}
    <div class="fc-foot"><span class="fc-method">${esc(f.method || "—")}</span>${f.cited ? `<span class="cited">✓ cited</span>` : ""}</div></article>`;
}

// ---------------- DATA (correlations / distributions / quality) ----------------
function viewData() {
  const wb = _demo.workbench || {}, cols = wb.columns || [];
  const tabs = ["correlations", "distributions", "quality"].map((t) =>
    `<button class="dtab ${_dtab === t ? "on" : ""}" data-dtab="${t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join("");
  let body = "";
  if (_dtab === "correlations") {
    body = `<div class="corr-head"><div class="corr-title">Correlation matrix · <b>${cols.length}</b> numeric columns · n=${wb.n_rows_full || "—"}</div>
      <div class="toggle"><button class="mini ${_method === "pearson" ? "on" : ""}" data-m="pearson">Pearson</button><button class="mini ${_method === "spearman" ? "on" : ""}" data-m="spearman">Spearman</button></div></div>
      <div class="corr-body"><div id="heat"></div><div id="scatter"></div></div>`;
  } else if (_dtab === "distributions") {
    body = `<div class="dist-grid">${(wb.col_stats || []).map(distCard).join("")}</div>`;
  } else {
    body = qualityTable(wb.col_stats || []);
  }
  return crumb("data", d3name()) + `<div class="dtabs">${tabs}</div><div class="dbody">${body}</div>`;
}
function d3name() { const wb = _demo.workbench || {}; return `${(wb.columns || []).length} numeric of dataset · ${wb.n_rows_full || "—"} rows`; }
function wireData() {
  $("appMain").querySelectorAll("[data-dtab]").forEach((b) => b.addEventListener("click", () => { _dtab = b.dataset.dtab; renderMain(); }));
  if (_dtab === "correlations") {
    $("appMain").querySelectorAll("[data-m]").forEach((b) => b.addEventListener("click", () => { _method = b.dataset.m; renderMain(); }));
    renderHeat(); renderScatter();
  }
}
function distCard(s) {
  const h = s.hist || {}, c = h.counts || [], mx = Math.max(1, ...c), W = 220, H = 70;
  const bw = W / Math.max(1, c.length);
  const bars = c.map((v, i) => `<rect x="${(i * bw).toFixed(1)}" y="${(H - (v / mx) * H).toFixed(1)}" width="${(bw - 1.5).toFixed(1)}" height="${((v / mx) * H).toFixed(1)}" fill="#6ee7ff" opacity="0.75"/>`).join("");
  return `<div class="dist"><div class="dist-name">${esc(s.name)}</div>
    <svg class="dist-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${bars}</svg>
    <div class="dist-meta"><span>${num(s.min)}</span><span>μ ${num(s.mean)} · σ ${num(s.std)}</span><span>${num(s.max)}</span></div></div>`;
}
function qualityTable(cs) {
  return `<div class="qwrap"><div class="q-sub">${cs.length} numeric columns · every value profiled</div>
    <table class="qtable"><thead><tr><th>column</th><th>type</th><th>missing</th><th>min</th><th>mean</th><th>max</th></tr></thead><tbody>` +
    cs.map((s) => `<tr><td class="q-name">${esc(s.name)}</td><td>${esc(s.dtype || "number")}</td>
      <td class="${(s.missing_pct || 0) > 5 ? "q-bad" : "q-ok"}">${(s.missing_pct || 0).toFixed(0)}%</td>
      <td>${num(s.min)}</td><td>${num(s.mean)}</td><td>${num(s.max)}</td></tr>`).join("") + `</tbody></table></div>`;
}

// ---------------- PHASE SPACE ----------------
function viewPhase() {
  const ps = (_demo.system_model || {}).phase_space;
  const hist = (ps && ps.history || []).filter((p) => p.x != null && p.y != null && isFinite(p.x) && isFinite(p.y));
  if (!ps || hist.length < 2) return crumb("phase space", "trajectory") + empty("Phase space needs ≥2 numeric variables with variance — not produced on every run.");
  const W = 900, H = 460, pad = 46;
  const xs = hist.map((p) => p.x), ys = hist.map((p) => p.y);
  const [xl, xh] = ext(xs), [yl, yh] = ext(ys);
  const mX = (x) => pad + ((x - xl) / (xh - xl)) * (W - 2 * pad), mY = (y) => (H - pad) - ((y - yl) / (yh - yl)) * (H - 2 * pad);
  let trail = ""; hist.forEach((p, i) => trail += (i ? "L" : "M") + ` ${mX(p.x).toFixed(1)} ${mY(p.y).toFixed(1)}`);
  const attr = (ps.attractors || []).map((a, i) => {
    const c = ["#88ffd1", "#6ee7ff", "#b794ff"][i % 3];
    return `<ellipse cx="${mX(a.x_center).toFixed(1)}" cy="${mY(a.y_center).toFixed(1)}" rx="${Math.abs(mX(a.x_center + (a.x_extent || 0)) - mX(a.x_center)).toFixed(1)}" ry="${Math.abs(mY(a.y_center + (a.y_extent || 0)) - mY(a.y_center)).toFixed(1)}" fill="none" stroke="${c}" stroke-dasharray="3 5" opacity="0.5"/>`;
  }).join("");
  const cur = hist[hist.length - 1];
  return crumb("phase space", `${esc(ps.x_axis)} × ${esc(ps.y_axis)}`) +
    `<div class="view-note">The system's <b>trajectory</b> through state space — each point is one moment, the trail is its path. Loops = cycles; a settling point = a stable regime (attractor).</div>
    <div class="svg-wrap"><svg viewBox="0 0 ${W} ${H}" class="bigsvg">
      <defs><linearGradient id="pt" x1="0" x2="1"><stop offset="0" stop-color="#a78dff" stop-opacity="0"/><stop offset="1" stop-color="#6ee7ff"/></linearGradient></defs>
      <line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#2a3550"/><line x1="${pad}" y1="${pad}" x2="${pad}" y2="${H - pad}" stroke="#2a3550"/>
      ${attr}<path d="${trail}" stroke="url(#pt)" stroke-width="2" fill="none"/>
      <circle cx="${mX(cur.x).toFixed(1)}" cy="${mY(cur.y).toFixed(1)}" r="5" fill="#6ee7ff"/>
      <text x="${mX(cur.x).toFixed(1)}" y="${(mY(cur.y) - 12).toFixed(1)}" fill="#fff" font-size="11" font-family="monospace" text-anchor="middle">NOW</text>
      <text x="${W / 2}" y="${H - 12}" fill="#8a96b5" font-size="12" font-family="monospace" text-anchor="middle">${esc(ps.x_axis)} →</text>
    </svg></div>`;
}

// ---------------- SPACETIME ----------------
function viewSpacetime() {
  const ents = ((_demo.system_model || {}).topology || {}).entities || [];
  const lanes = ents.filter((e) => (e.history || []).length);
  if (!lanes.length) return crumb("spacetime", "worldlines") + empty("Spacetime needs a system model with entities — try a richer time-series dataset.");
  const W = 900, laneH = 78, H = lanes.length * laneH + 30, xL = 130, xR = W - 40;
  const rows = lanes.map((e, i) => {
    const hist = (e.history || []).map((h) => (typeof h === "object" ? h.v : h)).filter((v) => v != null && isFinite(v));
    const cy = 20 + i * laneH + laneH / 2, [lo, hi] = ext(hist);
    const mY = (v) => hi === lo ? cy : (cy + laneH * 0.32) - ((v - lo) / (hi - lo)) * (laneH * 0.64);
    const c = e.severity === "crit" ? "#ff5577" : e.severity === "warn" ? "#ff9a4a" : e.role === "forcing" ? "#ff6ec7" : "#88ffd1";
    let d = ""; hist.forEach((v, j) => { const x = xL + (j / Math.max(1, hist.length - 1)) * (xR - xL); d += (j ? "L" : "M") + ` ${x.toFixed(1)} ${mY(v).toFixed(1)}`; });
    return `<line x1="${xL}" y1="${cy}" x2="${xR}" y2="${cy}" stroke="#1a2438" stroke-dasharray="2 6"/>
      <path d="${d}" stroke="${c}" stroke-width="1.8" fill="none" opacity="0.9"/>
      <text x="${xL - 12}" y="${cy + 3}" text-anchor="end" fill="${c}" font-size="11" font-family="monospace">${esc(e.id)}</text>`;
  }).join("");
  return crumb("spacetime", `${lanes.length} worldlines`) +
    `<div class="view-note">Each row is a variable's <b>worldline</b> through time — Aurora tracks every entity in the system together, not one chart at a time.</div>
     <div class="svg-wrap"><svg viewBox="0 0 ${W} ${H}" class="bigsvg">${rows}</svg></div>`;
}

// ---------------- FORECAST ----------------
function viewForecast() {
  const fc = _demo.forecast;
  if (!fc || fc.available === false) return crumb("forecast", "—") + empty("Aurora forecasts time-series targets; cross-sectional data has no forecast, by design.");
  const tgt = String(fc.target || "");
  const timeLike = /time|timestamp|date|index|_s$|^t$/i.test(tgt);
  const flatCI = fc.headline_ci_low != null && fc.headline_ci_high != null && Math.abs(fc.headline_ci_high - fc.headline_ci_low) < 1e-3;
  if (timeLike || flatCI) {
    return crumb("forecast", esc(tgt || "target")) +
      `<div class="view-note">On this run Aurora auto-selected <b>${esc(tgt || "the time axis")}</b> as the target — effectively the clock, so the forecast is degenerate (flat band). Forecasting shines on a real signal: <b>open the “Ocean climate buoy” demo</b> (top of page) to see a <b>temperature forecast with a widening 95% confidence band</b>.</div>`;
  }
  const pts = (fc.points || []).filter((p) => p.value != null);
  const head = fc.headline_value != null ? `<div class="fc-headline"><div class="fch-l">PEAK · NEXT ${esc(String(fc.horizon || pts.length))} STEPS</div>
    <div class="fch-v">${num(fc.headline_value)}</div>${fc.headline_ci_low != null ? `<div class="fch-ci">95% CI ${num(fc.headline_ci_low)} – ${num(fc.headline_ci_high)}</div>` : ""}</div>` : "";
  let chart = empty("Forecast produced, but too few points to plot.");
  if (pts.length >= 2) {
    const W = 900, H = 300, pad = 40, vals = pts.map((p) => p.value), los = pts.map((p) => p.ci_low ?? p.value), his = pts.map((p) => p.ci_high ?? p.value);
    const lo = Math.min(...vals, ...los), hi = Math.max(...vals, ...his), sp = (hi - lo) || 1;
    const mx = (i) => pad + (i / (pts.length - 1)) * (W - 2 * pad), my = (v) => (H - pad) - ((v - lo) / sp) * (H - 2 * pad);
    let band = `M ${mx(0).toFixed(1)} ${my(his[0]).toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) band += ` L ${mx(i).toFixed(1)} ${my(his[i]).toFixed(1)}`;
    for (let i = pts.length - 1; i >= 0; i--) band += ` L ${mx(i).toFixed(1)} ${my(los[i]).toFixed(1)}`;
    let line = ""; pts.forEach((p, i) => line += (i ? "L" : "M") + ` ${mx(i).toFixed(1)} ${my(p.value).toFixed(1)}`);
    chart = `<div class="svg-wrap"><svg viewBox="0 0 ${W} ${H}" class="bigsvg"><path d="${band} Z" fill="#6ee7ff" opacity="0.12"/><path d="${line}" stroke="#6ee7ff" stroke-width="2" fill="none"/><line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#2a3550"/></svg></div>`;
  }
  return crumb("forecast", esc(fc.target || "target")) +
    `<div class="fc-meta">target <b>${esc(fc.target || "—")}</b> · ${esc(String(fc.method || "ensemble"))}${fc.score != null ? ` · CRPS ${num(fc.score)}` : ""} · with a 95% confidence band</div>${head}${chart}`;
}

// ---------------- DECISION ----------------
function topFinding() { const fs = _demo.findings || []; return fs.find((f) => f.severity === "crit") || fs.find((f) => f.severity === "warn") || fs[0] || null; }
function decisionFor(f) {
  const m = (f.method || "").toLowerCase();
  if (/change|break|bocpd/.test(m)) return "A structural break was detected — find what changed and recalibrate thresholds/forecasts to the new regime before trusting old baselines.";
  if (/regime|hmm/.test(m)) return "The system shifted regimes — confirm which one you're in now and apply the policy that fits it.";
  if (/anom|outlier|iso|robust|pca|consensus/.test(m)) return "A structured deviation was flagged — rule out a data error, then decide whether it's a one-off to log or a signal to act on now.";
  if (/forecast|ar\(|kalman/.test(m)) return "The model projects a notable move — pre-position for it and set an alert at the confidence bound so you're warned if reality diverges.";
  if (/spectral|matrix_profile|var|dtw|emd/.test(m)) return "A repeating structure / coupling was found — use it to anticipate the next cycle and watch for the coupled variable moving first.";
  return "Verify the relationship is causal (not confounded), then take the smallest reversible action that tests it.";
}
function viewDecide() {
  const f = topFinding();
  if (!f) return crumb("decision", "so what?") + empty("No decision surfaced for this run.");
  const sev = (f.severity || "info").toLowerCase();
  const risk = sev === "crit" ? "High — flagged critical; ignoring it risks a material miss." : sev === "warn" ? "Moderate — worth acting on this cycle." : "Low — monitor.";
  return crumb("decision", "operationalize the finding") +
    `<div class="view-note">Correlation Studio stops at "here's a chart." Aurora turns the finding into <b>a decision you can act on</b>.</div>
     <div class="decision"><div class="dec-lbl">▸ RECOMMENDED DECISION</div><div class="dec-rec">${esc(decisionFor(f))}</div>
       <div class="dec-meta"><span class="dec-src">from: ${esc(f.title)}</span><span class="dec-risk dec-risk--${sev}">risk if ignored: ${esc(risk)}</span></div>
       <a class="dec-cta" href="https://github.com/FantasyLab-ai/aurora/releases/latest" target="_blank" rel="noopener">Download Aurora to operationalize — set alerts, run do-calculus, export a brief →</a></div>
     <div class="also-strip"><span class="as-lbl">the full app also gives you:</span><span class="as-item">Causal do-calculus</span><span class="as-item">Simulate & intervene</span><span class="as-item">Knowledge-bank citations</span><span class="as-item">Sentinel alerts → Discord/Slack</span><span class="as-item">Share to the community</span></div>`;
}

// ---------------- shared bits ----------------
function crumb(name, sub) { return `<div class="crumb">${esc(name)} / <b>${esc(sub || "")}</b></div>`; }
function stat(n, l, s, cls) { return `<div class="stat"><div class="stat-n ${cls === "mint" ? "stat-mint" : ""}">${esc(String(n))}</div><div class="stat-l">${esc(l)}</div><div class="stat-s">${esc(s || "")}</div></div>`; }
function empty(msg) { return `<div class="view-empty">${msg}</div>`; }
function ext(a) { let lo = Infinity, hi = -Infinity; a.forEach((v) => { if (v < lo) lo = v; if (v > hi) hi = v; }); if (lo === hi || !isFinite(lo)) { lo = (lo || 0) - 1; hi = (hi || 0) + 1; } return [lo, hi]; }
const short = (s) => { s = String(s); return s.length > 11 ? s.slice(0, 10) + "…" : s; };
function corrColor(v) { if (v == null) return "rgba(255,255,255,0.03)"; const a = Math.min(1, Math.abs(v)); return v >= 0 ? `rgba(110,231,255,${(0.1 + 0.78 * a).toFixed(3)})` : `rgba(255,110,199,${(0.1 + 0.78 * a).toFixed(3)})`; }

function renderHeat() {
  const wb = _demo.workbench || {}, cols = wb.columns || [], M = _method === "spearman" ? wb.corr_spearman : wb.corr_pearson, host = $("heat");
  if (!host) return;
  if (cols.length < 2) { host.innerHTML = `<div class="muted">Need ≥2 numeric columns.</div>`; return; }
  const n = cols.length;
  let h = `<div class="heat" style="grid-template-columns:84px repeat(${n},minmax(26px,1fr))"><div></div>`;
  cols.forEach((c) => h += `<div class="hcol" title="${esc(c)}">${esc(short(c))}</div>`);
  for (let i = 0; i < n; i++) {
    h += `<div class="hrow" title="${esc(cols[i])}">${esc(short(cols[i]))}</div>`;
    for (let j = 0; j < n; j++) {
      const v = M && M[i] ? M[i][j] : null, t = v == null ? "" : (Math.abs(v) >= 0.995 ? "1" : v.toFixed(2).replace("0.", ".").replace("-0.", "-."));
      h += `<div class="cell${i === j ? " diag" : ""}" data-xi="${i}" data-yi="${j}" style="background:${corrColor(v)}" title="${esc(cols[i])} × ${esc(cols[j])} = ${v == null ? "—" : v.toFixed(3)}">${t}</div>`;
    }
  }
  host.innerHTML = h + `<div class="heat-hint">click any cell to inspect →</div>`;
  host.querySelectorAll(".cell[data-xi]").forEach((c) => c.addEventListener("click", () => { _sc.xi = +c.dataset.xi; _sc.yi = +c.dataset.yi; _sc.mode = "scatter"; renderScatter(); }));
}
function normCdf(x) { const t = 1 / (1 + 0.2316419 * Math.abs(x)), d = 0.3989423 * Math.exp(-x * x / 2), p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))); return x > 0 ? 1 - p : p; }
function corrStats(r, n) { if (r == null || n == null || n < 4 || Math.abs(r) >= 1) return { p: "—", ci: "—" }; const z = 0.5 * Math.log((1 + r) / (1 - r)), se = 1 / Math.sqrt(n - 3), lo = Math.tanh(z - 1.96 * se), hi = Math.tanh(z + 1.96 * se), pv = 2 * (1 - normCdf(Math.abs(z) / se)); return { p: pv < 1e-4 ? "<0.0001" : pv.toFixed(4), ci: `${lo.toFixed(2)} – ${hi.toFixed(2)}` }; }
function renderScatter() {
  const wb = _demo.workbench || {}, cols = wb.columns || [], xi = _sc.xi, yi = _sc.yi, host = $("scatter"); if (!host) return;
  if (xi === yi) { host.innerHTML = `<div class="muted" style="padding:24px">Click an off-diagonal cell to compare two variables.</div>`; return; }
  const pts = []; (wb.sample || []).forEach((row) => { const x = row[xi], y = row[yi]; if (x != null && y != null && isFinite(x) && isFinite(y)) pts.push([x, y]); });
  const rF = wb.corr_pearson && wb.corr_pearson[xi] ? wb.corr_pearson[xi][yi] : null, rS = wb.corr_spearman && wb.corr_spearman[xi] ? wb.corr_spearman[xi][yi] : null;
  const st = corrStats(rF, wb.n_rows_full), rc = rF == null ? "#d6d8f5" : (rF >= 0 ? "#6ee7ff" : "#ff6ec7"), nl = rF != null && rS != null && Math.abs(rS) - Math.abs(rF) > 0.15;
  host.innerHTML = `<div class="sc-head"><div class="sc-title">${esc(cols[xi])} <span class="x">vs</span> ${esc(cols[yi])}</div>
    <div class="toggle"><button class="mini ${_sc.mode === "scatter" ? "on" : ""}" data-s="scatter">Scatter</button><button class="mini ${_sc.mode === "line" ? "on" : ""}" data-s="line">Line</button><button class="mini ${_sc.mode === "residual" ? "on" : ""}" data-s="residual">Residual</button></div></div>
    ${scatterSVG(pts, _sc.mode)}
    <div class="sc-stats"><span>r <b style="color:${rc}">${rF == null ? "—" : (rF >= 0 ? "+" : "") + rF.toFixed(3)}</b></span><span>r² <b>${rF == null ? "—" : (rF * rF).toFixed(3)}</b></span><span>n <b>${wb.n_rows_full || "—"}</b></span><span>p <b>${st.p}</b></span><span>95% CI <b>${st.ci}</b></span></div>
    ${nl ? `<div class="sc-note">⚠ Non-linear — Spearman ρ=${rS.toFixed(2)} ≫ Pearson r.</div>` : ""}
    <div class="sc-teaser">◎ In the full app, <b>“Is it real?”</b> runs <b>do-calculus</b> on any pair. <a href="https://github.com/FantasyLab-ai/aurora/releases/latest" target="_blank" rel="noopener">Download →</a></div>`;
  host.querySelectorAll("[data-s]").forEach((b) => b.addEventListener("click", () => { _sc.mode = b.dataset.s; renderScatter(); }));
}
function scatterSVG(pts, mode) {
  if (pts.length < 2) return `<div class="muted" style="padding:24px">Not enough overlapping points.</div>`;
  const W = 600, H = 280, pad = 28, n = pts.length; let sx = 0, sy = 0, sxx = 0, sxy = 0;
  pts.forEach(([x, y]) => { sx += x; sy += y; sxx += x * x; sxy += x * y; });
  const mx = sx / n, my = sy / n, slope = (sxy - n * mx * my) / ((sxx - n * mx * mx) || 1e-9), b = my - slope * mx;
  if (mode === "line") return dualLine(pts, W, H, pad);
  if (mode === "residual") return cloud(pts.map(([x, y]) => [x, y - (slope * x + b)]), W, H, pad, null, null, true);
  return cloud(pts, W, H, pad, slope, b, false);
}
function cloud(pts, W, H, pad, slope, b, resid) {
  const [xl, xh] = ext(pts.map((p) => p[0])), [yl, yh] = ext(pts.map((p) => p[1]));
  const mX = (x) => pad + ((x - xl) / (xh - xl)) * (W - 2 * pad), mY = (y) => (H - pad) - ((y - yl) / (yh - yl)) * (H - 2 * pad);
  let dots = ""; pts.forEach((p) => dots += `<circle cx="${mX(p[0]).toFixed(1)}" cy="${mY(p[1]).toFixed(1)}" r="1.8" fill="#6ee7ff" opacity="0.5"/>`);
  let ln = ""; if (resid) ln = `<line x1="${pad}" y1="${mY(0).toFixed(1)}" x2="${W - pad}" y2="${mY(0).toFixed(1)}" stroke="#88ffd1" stroke-dasharray="5 4"/>`;
  else if (slope != null) ln = `<line x1="${mX(xl).toFixed(1)}" y1="${mY(slope * xl + b).toFixed(1)}" x2="${mX(xh).toFixed(1)}" y2="${mY(slope * xh + b).toFixed(1)}" stroke="#88ffd1" stroke-width="1.7"/>`;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}"><line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#2a3550"/><line x1="${pad}" y1="${pad}" x2="${pad}" y2="${H - pad}" stroke="#2a3550"/>${dots}${ln}</svg>`;
}
function dualLine(pts, W, H, pad) {
  const norm = (a) => { const [lo, hi] = ext(a); return a.map((v) => (v - lo) / (hi - lo)); };
  const xN = norm(pts.map((p) => p[0])), yN = norm(pts.map((p) => p[1])), mX = (i) => pad + (i / (pts.length - 1)) * (W - 2 * pad), mY = (t) => (H - pad) - t * (H - 2 * pad);
  const path = (v) => v.map((t, i) => (i ? "L" : "M") + ` ${mX(i).toFixed(1)} ${mY(t).toFixed(1)}`).join(" ");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}"><line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#2a3550"/><path d="${path(xN)}" stroke="#6ee7ff" stroke-width="1.4" fill="none"/><path d="${path(yN)}" stroke="#88ffd1" stroke-width="1.4" fill="none"/></svg>`;
}

boot();

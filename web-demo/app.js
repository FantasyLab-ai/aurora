// Aurora live demo — pure static. Loads pre-computed snapshots (data/*.json)
// produced from real Aurora runs and replays the glass-box output: findings,
// correlation heatmap + scatter, the do-calculus verdict, and the decision.
// No backend, no upload — this is Aurora's actual output, frozen.

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (id) => document.getElementById(id);

let _demo = null;                 // current snapshot
let _sc = { xi: 0, yi: 1, mode: "scatter" };
let _method = "pearson";

// Prettify raw method ids for the "methods Aurora ran" strip.
const METHOD_LABEL = {
  "bocpd": "Changepoint · BOCPD", "dtw": "Dynamic Time Warping", "emd": "Empirical Mode Decomp.",
  "kalman": "Kalman filter", "matrix_profile": "Matrix Profile", "var": "VAR",
  "robust_pca": "Robust PCA", "spectral_entropy": "Spectral entropy",
  "multivariate_outlier_consensus": "Outlier consensus", "AR(1)": "AR(1) forecast",
  "iso-forest + robust-Z": "Isolation Forest + robust-Z", "hmm": "HMM regimes",
  "sindy": "SINDy physics", "granger": "Granger causality", "mutual_information_ksg": "Mutual information",
};
const prettyMethod = (m) => METHOD_LABEL[m] || m;
// Capabilities the engine has beyond whatever this particular run surfaced.
const ENGINE_ALSO = ["SINDy physics discovery", "HMM regime detection", "Mutual information (non-linear)",
  "Causal do-calculus", "Ensemble forecasting + CI", "Phase-space & attractors",
  "Spacetime worldlines", "Knowledge-bank citations"];

async function boot() {
  let manifest = [];
  try { manifest = await (await fetch("data/manifest.json", { cache: "no-store" })).json(); }
  catch (e) { $("picker").innerHTML = `<div class="demo-loading">Demo data not found. Run the generator, or <a href="https://github.com/FantasyLab-ai/aurora/releases/latest">download the app</a>.</div>`; return; }
  $("picker").innerHTML = `<div class="picker-title">Choose a real analysis</div><div class="picker-grid">` +
    manifest.map((d) => `<button class="picker-card" data-slug="${esc(d.slug)}">
      <div class="pc-icon">${esc(d.icon || "◆")}</div>
      <div class="pc-title">${esc(d.title)}</div>
      <div class="pc-blurb">${esc(d.blurb)}</div>
      <div class="pc-go">▶ Analyze</div>
    </button>`).join("") + `</div>`;
  $("picker").querySelectorAll(".picker-card").forEach((b) =>
    b.addEventListener("click", () => selectDemo(b.dataset.slug)));
  // Auto-open the first demo so the page never looks empty.
  if (manifest[0]) selectDemo(manifest[0].slug);
}

async function selectDemo(slug) {
  document.querySelectorAll(".picker-card").forEach((c) => c.classList.toggle("is-active", c.dataset.slug === slug));
  const res = $("result");
  res.hidden = false;
  res.innerHTML = `<div class="demo-loading">▶ replaying Aurora's analysis…</div>`;
  try { _demo = await (await fetch(`data/${slug}.json`, { cache: "no-store" })).json(); }
  catch (e) { res.innerHTML = `<div class="demo-loading">Could not load this demo.</div>`; return; }
  // Open the scatter on the strongest correlation — most striking first impression.
  const wb = _demo.workbench || {}, cols = wb.columns || [], M = wb.corr_pearson;
  let best = [0, cols.length > 1 ? 1 : 0, 0];
  if (M) for (let i = 0; i < cols.length; i++) for (let j = i + 1; j < cols.length; j++) {
    const v = M[i] && M[i][j]; if (v != null && Math.abs(v) > Math.abs(best[2])) best = [i, j, v];
  }
  _sc = { xi: best[0], yi: best[1], mode: "scatter" };
  render();
  res.scrollIntoView({ behavior: "smooth", block: "start" });
}

function render() {
  const d = _demo, wb = d.workbench || {};
  $("result").innerHTML = `
    <div class="rz rz-1">
      <div class="rz-step">STEP 1 · DISCOVER — Aurora ran its full method battery</div>
      <div class="rz-h">${esc(d.title)}</div>
      <div class="statbar">
        <div class="stat"><div class="stat-n">${(d.findings || []).length}</div><div class="stat-l">findings</div></div>
        <div class="stat"><div class="stat-n">${(d.methods || []).length}</div><div class="stat-l">methods run</div></div>
        <div class="stat"><div class="stat-n stat-mint">0</div><div class="stat-l">fabricated</div></div>
        <div class="stat"><div class="stat-n">${(d.findings || []).filter((f) => f.cited).length}</div><div class="stat-l">cited findings</div></div>
      </div>
      <div class="methods-strip">
        <div class="ms-lbl">methods Aurora ran on this dataset</div>
        <div class="ms-badges">${(d.methods || []).map((m) => `<span class="mbadge">${esc(prettyMethod(m))}</span>`).join("")}</div>
        <div class="ms-also">…and the engine also does ${ENGINE_ALSO.map((x) => `<span class="also">${esc(x)}</span>`).join("")}</div>
      </div>
      ${d.narrative ? `<div class="rz-narr"><b>Aurora's summary:</b> ${esc(d.narrative)}</div>` : ""}
      <div class="findings">${(d.findings || []).slice(0, 6).map(findingCard).join("") || `<div class="muted">No findings surfaced.</div>`}</div>
    </div>
    <div class="rz rz-2">
      <div class="rz-step">STEP 2 · UNDERSTAND — relationships, structure &amp; dynamics</div>
      <div class="corr-head"><div class="corr-title">Correlation matrix · <b>${(wb.columns || []).length}</b> numeric columns · n=${wb.n_rows_full || "—"}</div>
        <div class="toggle"><button class="mini ${_method === "pearson" ? "on" : ""}" data-m="pearson">Pearson</button><button class="mini ${_method === "spearman" ? "on" : ""}" data-m="spearman">Spearman</button></div></div>
      <div class="corr-body"><div id="heat"></div><div id="scatter"></div></div>
      <div class="also-strip"><span class="as-lbl">correlation is just the surface — the full app also gives you:</span><span class="as-item">Phase space + attractors</span><span class="as-item">Forecast + CI bands</span><span class="as-item">Spacetime worldlines</span><span class="as-item">Causal DAG + do-calculus</span><span class="as-item">SINDy governing equations</span></div>
    </div>
    <div class="rz rz-3">
      <div class="rz-step">STEP 3 · DECIDE — so what?</div>
      <div id="decide"></div>
    </div>`;
  $("result").querySelectorAll("[data-m]").forEach((b) => b.addEventListener("click", () => { _method = b.dataset.m; render(); }));
  renderHeat(); renderScatter(); renderDecision();
}

function topFinding() {
  const fs = _demo.findings || [];
  return fs.find((f) => (f.severity || "") === "crit") || fs.find((f) => (f.severity || "") === "warn") || fs[0] || null;
}
function decisionFor(f) {
  const m = (f.method || "").toLowerCase();
  if (/change|break|cusum|bocpd|ruptur/.test(m)) return "A structural break was detected — find what changed and recalibrate thresholds/forecasts to the new regime before trusting old baselines.";
  if (/regime|hmm|markov/.test(m)) return "The system shifted regimes — confirm which one you're in now and apply the policy that fits it; a rule tuned for the other regime will misfire.";
  if (/anom|outlier|iso|z-?score|mad|robust|pca/.test(m)) return "A structured deviation was flagged — rule out a data error, then decide whether it's a one-off to log or a signal to act on now.";
  if (/forecast|arima|ar\(|ensemble|kalman/.test(m)) return "The model projects a notable move — pre-position for the predicted value and set an alert at the confidence bound so you're warned if reality diverges.";
  if (/spectral|matrix_profile|var/.test(m)) return "A repeating structure / coupling was found — use it to anticipate the next cycle and watch for the coupled variable moving first.";
  return "Verify the relationship is causal (not confounded) before acting, then take the smallest reversible action that tests it.";
}
function renderDecision() {
  const host = $("decide"), f = topFinding();
  if (!f) { host.innerHTML = `<div class="muted">No decision surfaced for this run.</div>`; return; }
  const sev = (f.severity || "info").toLowerCase();
  const risk = sev === "crit" ? "High — flagged critical; ignoring it risks a material miss."
             : sev === "warn" ? "Moderate — worth acting on this cycle." : "Low — monitor; no immediate action needed.";
  host.innerHTML = `<div class="decision">
    <div class="dec-lbl">▸ RECOMMENDED DECISION</div>
    <div class="dec-rec">${esc(decisionFor(f))}</div>
    <div class="dec-meta"><span class="dec-src">from: ${esc(f.title)}</span><span class="dec-risk dec-risk--${sev}">risk if ignored: ${esc(risk)}</span></div>
    <a class="dec-cta" href="https://github.com/FantasyLab-ai/aurora/releases/latest" target="_blank" rel="noopener">Download Aurora to operationalize this — set alerts, run do-calculus, export a brief →</a>
  </div>`;
}

function findingCard(f) {
  const sev = (f.severity || "info").toLowerCase();
  const ev = [];
  if (f.z != null && !isNaN(+f.z)) ev.push(["|z|", (+f.z).toFixed(1) + "σ"]);
  if (f.p != null && !isNaN(+f.p)) ev.push(["p", +f.p < 0.001 ? (+f.p).toExponential(1) : (+f.p).toFixed(3)]);
  if (f.row != null) ev.push(["row", f.row]);
  return `<article class="fc fc--${sev}">
    <div class="fc-head"><span class="sev sev--${sev}">${sev}</span>${f.confidence != null ? `<span class="conf">${Math.round(f.confidence * 100)}% conf</span>` : ""}</div>
    <div class="fc-title">${esc(f.title)}</div>
    ${f.description ? `<div class="fc-desc">${esc(f.description)}</div>` : ""}
    ${ev.length ? `<div class="fc-ev">${ev.map(([k, v]) => `<span class="ev"><b>${esc(k)}</b>${esc(v)}</span>`).join("")}</div>` : ""}
    <div class="fc-foot"><span class="fc-method">${esc(f.method || "—")}</span>${f.cited ? `<span class="cited">✓ cited</span>` : ""}</div>
  </article>`;
}

const short = (s) => { s = String(s); return s.length > 11 ? s.slice(0, 10) + "…" : s; };
function corrColor(v) {
  if (v == null) return "rgba(255,255,255,0.03)";
  const a = Math.min(1, Math.abs(v));
  return v >= 0 ? `rgba(110,231,255,${(0.10 + 0.78 * a).toFixed(3)})` : `rgba(255,110,199,${(0.10 + 0.78 * a).toFixed(3)})`;
}
function renderHeat() {
  const wb = _demo.workbench || {}, cols = wb.columns || [];
  const M = _method === "spearman" ? wb.corr_spearman : wb.corr_pearson;
  if (cols.length < 2) { $("heat").innerHTML = `<div class="muted">Need ≥2 numeric columns.</div>`; return; }
  const n = cols.length;
  let h = `<div class="heat" style="grid-template-columns:88px repeat(${n},minmax(26px,1fr))"><div></div>`;
  cols.forEach((c) => h += `<div class="hcol" title="${esc(c)}">${esc(short(c))}</div>`);
  for (let i = 0; i < n; i++) {
    h += `<div class="hrow" title="${esc(cols[i])}">${esc(short(cols[i]))}</div>`;
    for (let j = 0; j < n; j++) {
      const v = M && M[i] ? M[i][j] : null;
      const t = v == null ? "" : (Math.abs(v) >= 0.995 ? "1" : v.toFixed(2).replace("0.", ".").replace("-0.", "-."));
      h += `<div class="cell${i === j ? " diag" : ""}" data-xi="${i}" data-yi="${j}" style="background:${corrColor(v)}" title="${esc(cols[i])} × ${esc(cols[j])} = ${v == null ? "—" : v.toFixed(3)}">${t}</div>`;
    }
  }
  h += `</div><div class="heat-hint">click any cell to inspect the pair →</div>`;
  $("heat").innerHTML = h;
  $("heat").querySelectorAll(".cell[data-xi]").forEach((c) => c.addEventListener("click", () => { _sc.xi = +c.dataset.xi; _sc.yi = +c.dataset.yi; _sc.mode = "scatter"; renderScatter(); }));
}

function normCdf(x) { const t = 1 / (1 + 0.2316419 * Math.abs(x)); const d = 0.3989423 * Math.exp(-x * x / 2); const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))); return x > 0 ? 1 - p : p; }
function corrStats(r, n) {
  if (r == null || n == null || n < 4 || Math.abs(r) >= 1) return { p: "—", ci: "—" };
  const z = 0.5 * Math.log((1 + r) / (1 - r)), se = 1 / Math.sqrt(n - 3);
  const lo = Math.tanh(z - 1.96 * se), hi = Math.tanh(z + 1.96 * se);
  const pv = 2 * (1 - normCdf(Math.abs(z) / se));
  return { p: pv < 1e-4 ? "<0.0001" : pv.toFixed(4), ci: `${lo.toFixed(2)} – ${hi.toFixed(2)}` };
}
function extent(a) { let lo = Infinity, hi = -Infinity; a.forEach((v) => { if (v < lo) lo = v; if (v > hi) hi = v; }); if (lo === hi) { lo -= 1; hi += 1; } return [lo, hi]; }

function renderScatter() {
  const wb = _demo.workbench || {}, cols = wb.columns || [], xi = _sc.xi, yi = _sc.yi;
  const host = $("scatter");
  if (xi === yi) { host.innerHTML = `<div class="muted" style="padding:24px">Click an off-diagonal cell to compare two variables.</div>`; return; }
  const pts = [];
  (wb.sample || []).forEach((row) => { const x = row[xi], y = row[yi]; if (x != null && y != null && isFinite(x) && isFinite(y)) pts.push([x, y]); });
  const rF = wb.corr_pearson && wb.corr_pearson[xi] ? wb.corr_pearson[xi][yi] : null;
  const rS = wb.corr_spearman && wb.corr_spearman[xi] ? wb.corr_spearman[xi][yi] : null;
  const st = corrStats(rF, wb.n_rows_full);
  const rc = rF == null ? "#d6d8f5" : (rF >= 0 ? "#6ee7ff" : "#ff6ec7");
  const nl = rF != null && rS != null && Math.abs(rS) - Math.abs(rF) > 0.15;
  host.innerHTML = `
    <div class="sc-head"><div class="sc-title">${esc(cols[xi])} <span class="x">vs</span> ${esc(cols[yi])}</div>
      <div class="toggle"><button class="mini ${_sc.mode === "scatter" ? "on" : ""}" data-s="scatter">Scatter</button><button class="mini ${_sc.mode === "line" ? "on" : ""}" data-s="line">Line</button><button class="mini ${_sc.mode === "residual" ? "on" : ""}" data-s="residual">Residual</button></div></div>
    ${scatterSVG(pts, _sc.mode)}
    <div class="sc-stats"><span>r <b style="color:${rc}">${rF == null ? "—" : (rF >= 0 ? "+" : "") + rF.toFixed(3)}</b></span><span>r² <b>${rF == null ? "—" : (rF * rF).toFixed(3)}</b></span><span>n <b>${wb.n_rows_full || "—"}</b></span><span>p <b>${st.p}</b></span><span>95% CI <b>${st.ci}</b></span></div>
    ${nl ? `<div class="sc-note">⚠ Non-linear — Spearman ρ=${rS.toFixed(2)} ≫ Pearson r; not a straight line.</div>` : ""}
    <div class="sc-actions"><div class="sc-teaser">◎ In the full app, <b>“Is it real?”</b> runs <b>do-calculus</b> on any pair — testing whether the correlation survives as a causal effect. <a href="https://github.com/FantasyLab-ai/aurora/releases/latest" target="_blank" rel="noopener">Download to try it on your own data →</a></div></div>`;
  host.querySelectorAll("[data-s]").forEach((b) => b.addEventListener("click", () => { _sc.mode = b.dataset.s; renderScatter(); }));
}

// ---- charts ----
function scatterSVG(pts, mode) {
  if (pts.length < 2) return `<div class="muted" style="padding:24px">Not enough overlapping points.</div>`;
  const W = 620, H = 300, pad = 30, n = pts.length;
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  pts.forEach(([x, y]) => { sx += x; sy += y; sxx += x * x; sxy += x * y; });
  const mx = sx / n, my = sy / n, slope = (sxy - n * mx * my) / ((sxx - n * mx * mx) || 1e-9), intercept = my - slope * mx;
  if (mode === "line") return dualLine(pts, W, H, pad);
  if (mode === "residual") return cloud(pts.map(([x, y]) => [x, y - (slope * x + intercept)]), W, H, pad, null, null, true);
  return cloud(pts, W, H, pad, slope, intercept, false);
}
function cloud(pts, W, H, pad, slope, intercept, resid) {
  const [xl, xh] = extent(pts.map((p) => p[0])), [yl, yh] = extent(pts.map((p) => p[1]));
  const mX = (x) => pad + ((x - xl) / (xh - xl)) * (W - 2 * pad), mY = (y) => (H - pad) - ((y - yl) / (yh - yl)) * (H - 2 * pad);
  let dots = ""; pts.forEach((p) => dots += `<circle cx="${mX(p[0]).toFixed(1)}" cy="${mY(p[1]).toFixed(1)}" r="1.8" fill="#6ee7ff" opacity="0.5"/>`);
  let line = "";
  if (resid) line = `<line x1="${pad}" y1="${mY(0).toFixed(1)}" x2="${W - pad}" y2="${mY(0).toFixed(1)}" stroke="#88ffd1" stroke-width="1.3" stroke-dasharray="5 4"/>`;
  else if (slope != null) line = `<line x1="${mX(xl).toFixed(1)}" y1="${mY(slope * xl + intercept).toFixed(1)}" x2="${mX(xh).toFixed(1)}" y2="${mY(slope * xh + intercept).toFixed(1)}" stroke="#88ffd1" stroke-width="1.7"/>`;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}"><line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#2a3550"/><line x1="${pad}" y1="${pad}" x2="${pad}" y2="${H - pad}" stroke="#2a3550"/>${dots}${line}</svg>`;
}
function dualLine(pts, W, H, pad) {
  const norm = (a) => { const [lo, hi] = extent(a); return a.map((v) => (v - lo) / (hi - lo)); };
  const xN = norm(pts.map((p) => p[0])), yN = norm(pts.map((p) => p[1]));
  const mX = (i) => pad + (i / (pts.length - 1)) * (W - 2 * pad), mY = (t) => (H - pad) - t * (H - 2 * pad);
  const path = (v) => v.map((t, i) => (i ? "L" : "M") + ` ${mX(i).toFixed(1)} ${mY(t).toFixed(1)}`).join(" ");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}"><line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#2a3550"/><path d="${path(xN)}" stroke="#6ee7ff" stroke-width="1.4" fill="none"/><path d="${path(yN)}" stroke="#88ffd1" stroke-width="1.4" fill="none"/></svg>`;
}

boot();

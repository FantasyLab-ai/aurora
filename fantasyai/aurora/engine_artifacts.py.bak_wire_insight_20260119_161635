
        # Generate modern annotated variants (optional)
        try:
            _try_generate_insight_plots(
                plots_dir=plots_dir,
                debug=debug,
                forecast=debug.get("forecast_payload"),
                correlations=debug.get("correlations_payload"),
                missingness=debug.get("missingness_payload"),
                anomalies=debug.get("anomalies_payload"),
                deep=debug.get("deep_math") if isinstance(debug, dict) else None,
            )
        except Exception as _e:
            debug.setdefault("skipped", []).append({"insight_plots": f"{type(_e).__name__}:{_e}"})
import React, { useEffect, useMemo, useState } from "react"
import auroraClient, {
  type AuroraArtifactsLatest,
  type AuroraRunArgs,
  type AuroraSuggestedAction,
  type AuroraRunActionArgs,
} from "../auroraClient"

type Tile = { label: string; value: React.ReactNode; hint?: string }

function safeNum(v: any): number | null {
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function fmtPct(v: any): string {
  const n = safeNum(v)
  if (n === null) return "—"
  return `${Math.round(n * 100)}%`
}

function fmt(v: any): string {
  if (v === null || v === undefined || v === "") return "—"
  if (typeof v === "number") return Number.isFinite(v) ? v.toString() : "—"
  return String(v)
}

function pickFirst<T = any>(...vals: any[]): T | null {
  for (const v of vals) {
    if (v === null || v === undefined) continue
    const s = String(v).trim()
    if (s === "") continue
    return v as T
  }
  return null
}

export default function preferInsightPlot(files: string[]): string[] {
  if (!Array.isArray(files)) return files as any
  const set = new Set(files)
  const mapOne = (f: string) => {
    if (!f.endsWith(".png")) return f
    if (f.endsWith("_insight.png")) return f
    const ins = f.replace(/\.png$/i, "_insight.png")
    return set.has(ins) ? ins : f
  }
  // Preserve ordering, but swap base->insight where available
  const out = files.map(mapOne)
  // De-dupe if both base and insight existed and we swapped
  return Array.from(new Set(out))
}
function AuroraConsolePanel() {
  const [apiStatus, setApiStatus] = useState<string>("unknown")
  const [datasetPath, setDatasetPath] = useState<string>("")
  const [seed, setSeed] = useState<number>(42)
  const [forecastHorizonDays, setForecastHorizonDays] = useState<number>(14)
  const [anomalyPercentile, setAnomalyPercentile] = useState<number>(95)

  const [alsoMath, setAlsoMath] = useState<boolean>(true)
  const [mathV2, setMathV2] = useState<boolean>(true)
  const [deepMath, setDeepMath] = useState<boolean>(true)
  const [alsoLLM, setAlsoLLM] = useState<boolean>(false)

  const [running, setRunning] = useState<boolean>(false)
  const [log, setLog] = useState<string[]>([])
  const [latest, setLatest] = useState<AuroraArtifactsLatest | null>(null)
  // --- Normalize latestArtifacts() shape (flat vs nested) ---
  // Some backends return: latest.engine_scorecard, latest.plot_files, etc.
  // UI expects: latest.artifacts.engine_scorecard and latest.plots.files.
  const latestAny: any = latest as any
  const artifacts: any = latestAny?.artifacts ?? {
    engine_scorecard: latestAny?.engine_scorecard,
    artifacts_done: latestAny?.artifacts_done,
    run_meta: latestAny?.run_meta,
    deep_math: latestAny?.deep_math,
    math_results: latestAny?.math_results,
  }
  const plots: any = latestAny?.plots ?? {
    files: latestAny?.plot_files ?? [],
    url_prefix: latestAny?.plots_url_prefix ?? "",
  }


  // --- Aurora X: Next actions (LLM/user loop) ---
  const [suggestedActions, setSuggestedActions] = useState<AuroraSuggestedAction[]>([])
  const [selectedActionId, setSelectedActionId] = useState<string>("freeform")
  const [actionText, setActionText] = useState<string>("")
  const [actionBusy, setActionBusy] = useState<boolean>(false)
  const [actionResult, setActionResult] = useState<any | null>(null)
  const [actionOutput, setActionOutput] = useState<string>("")

  async function refreshActions() {
    try {
      const r = await auroraClient.suggestActions()
      const acts = (r?.actions || []) as AuroraSuggestedAction[]
      setSuggestedActions(acts)

      if (acts.length && (selectedActionId === "freeform" || !selectedActionId)) {
        setSelectedActionId(acts[0].id)
        setActionText(acts[0].prompt || "")
      }
    } catch (e: any) {
      addLog("[actions] WARN: " + (e?.message ?? String(e)))
    }
  }

  async function executeAction() {
    const instruction = (actionText || "").trim()
    if (!instruction) {
      addLog("[actions] Provide an instruction first.")
      return
    }

    setActionBusy(true)
    try {
      const payload: AuroraRunActionArgs = {
        run_dir: latest?.run_dir || undefined,
        action_id: selectedActionId || "freeform",
        instruction,
      }
      const res = await auroraClient.runAction(payload)

      // Persist full response (backend may only 'save', or may also return LLM/insight output)
      setActionResult(res ?? null)

      const savedPath =
        (res as any)?.saved_path || (res as any)?.saved || (res as any)?.path || (res as any)?.request_path
      const note = (res as any)?.note || (res as any)?.message || "saved"

      if ((res as any)?.ok) {
        addLog("[actions] ok: " + note + (savedPath ? ` (saved: ${savedPath})` : ""))
      } else {
        addLog("[actions] failed: " + JSON.stringify(res))
      }

      // Try a few common output keys (so if/when backend returns LLM output, UI shows it immediately)
      const out =
        (res as any)?.output_md ||
        (res as any)?.insights_md ||
        (res as any)?.response_md ||
        (res as any)?.output ||
        (res as any)?.response ||
        (res as any)?.text ||
        ""

      if (typeof out === "string" && out.trim().length > 0) {
        setActionOutput(out.trim())
      } else if (savedPath) {
        setActionOutput(
          `Saved request only (executor not wired / safe mode).\n\nRequest path:\n${savedPath}\n\nTip: if LLM execution is enabled on the backend, return output_md/insights_md so the UI can render it here.`
        )
      } else {
        setActionOutput((res as any)?.ok ? "Saved request (no output returned)." : "")
      }

      await refreshLatest()
      await refreshActions()
    } catch (e: any) {
      addLog("[actions] ERROR: " + (e?.message ?? String(e)))
    } finally {
      setActionBusy(false)
    }
  }


    // Clear previous action output when a new run arrives (prevents "stuck" story/LLM panel)
  useEffect(() => {
    if (!latest?.run_dir) return
    setActionResult(null)
    setActionOutput("")
  }, [latest?.run_dir])

function addLog(line: string) {
    setLog((s) => [...s, line].slice(-200))
  }

  async function refreshLatest() {
  try {
    const r = await auroraClient.latestArtifacts()
    if (r?.ok) setLatest(r)
  } catch (e: any) {
    addLog(`[refresh] error: ${e?.message ?? String(e)}`)
  }
}useEffect(() => {
    let alive = true

    async function boot() {
      try {
        const h = await auroraClient.health()
        if (!alive) return
        setApiStatus(h?.ok ? "online" : "offline")
      } catch {
        if (!alive) return
        setApiStatus("offline")
      }

      await refreshLatest()
    }

    boot()

    const t = setInterval(() => {
      refreshLatest()
      refreshActions()
    }, 2500)

    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  // --- Derive tile values from artifacts (engine_scorecard preferred) ---
  const tiles: Tile[] = useMemo(() => {
    const a = artifacts as any
    const sc = a?.engine_scorecard as any
    const dm = a?.deep_math as any
    const mr = a?.math_results as any

    const rows = pickFirst<number>(sc?.shape?.rows, sc?.rows, dm?.rows, mr?.rows)
    const cols = pickFirst<number>(sc?.shape?.cols, sc?.cols, dm?.cols, mr?.cols)

    const engineVersion = pickFirst<string>(sc?.engine_version, sc?.engineVersion, dm?.version)
    const datasetLabel = pickFirst<string>(
      sc?.dataset_key,
      sc?.dataset,
      a?.run_meta?.dataset_key,
      a?.run_meta?.dataset,
      latest?.run_dir
    )

    const engineConfidence = pickFirst<number>(sc?.engine_confidence, sc?.confidence)

    return [
      { label: "Engine confidence", value: fmtPct(engineConfidence), hint: "Composite execution confidence" },
      { label: "Engine version", value: fmt(engineVersion) },
      { label: "Dataset", value: fmt(datasetLabel) },
      { label: "Rows", value: fmt(rows) },
      { label: "Cols", value: fmt(cols) },
      {
        label: "Artifacts",
        value: fmt(
          (a?.artifacts_done && Object.keys(a.artifacts_done).length) ||
            a?.plots_index?.plot_files?.length ||
            "—"
        ),
      },
    ]
  }, [artifacts, latest]);
  // --- Aurora Storycard: deterministic insights derived from artifacts (no LLM needed) ---
  const story = useMemo(() => {
    const a: any = artifacts as any
    const sc: any = a?.engine_scorecard || {}
    const dm: any = a?.deep_math || {}

    const datasetLabel =
      pickFirst<string>(
        sc?.dataset_key,
        sc?.dataset,
        a?.run_meta?.dataset_key,
        a?.run_meta?.dataset,
        latest?.run_dir
      ) || "—"

    const regimes = dm?.regimes?.cluster_summaries
    const regimeCount = Array.isArray(regimes) ? regimes.length : safeNum(dm?.regimes?.k) ?? null

    const q95 = dm?.anomalies?.score_quantiles?.q95
    const qmax = dm?.anomalies?.score_quantiles?.max
    const tailCount = Array.isArray(dm?.anomalies?.top_trips_preview) ? dm.anomalies.top_trips_preview.length : null

    const fc = dm?.forecast || dm?.forecasting || {}
    const horizon = pickFirst<number>(fc?.horizon, fc?.forecast_horizon, a?.run_meta?.forecast_horizon, forecastHorizonDays)
    const mape = pickFirst<number>(fc?.metrics?.mape, fc?.mape, dm?.forecast_mape)
    const rmse = pickFirst<number>(fc?.metrics?.rmse, fc?.rmse)
    const ci = fc?.interval || fc?.ci || {}
    const ciWidthPct = pickFirst<number>(ci?.width_pct, ci?.bandwidth_pct, fc?.bandwidth_pct)

    // Signal selection (simple + stable)
    let signal = "signal: stable run"
    if (safeNum(regimeCount) !== null && (regimeCount as any) >= 2) signal = "signal: regime shift detected"
    if (safeNum(q95) !== null && safeNum(q95)! >= 1.0) signal = "signal: tail risk elevated"
    if (safeNum(ciWidthPct) !== null && safeNum(ciWidthPct)! >= 0.10) signal = "signal: uncertainty widened"

    // Key bullets (keep concise)
    const bullets: { k: string; v: string }[] = []

    bullets.push({ k: "Where", v: `multi-metric time series • ${datasetLabel}` })

    if (safeNum(regimeCount) !== null) bullets.push({ k: "Detected regimes", v: `${regimeCount}` })
    if (safeNum(tailCount) !== null) bullets.push({ k: "Anomalies scored", v: `${tailCount} tail events flagged` })

    const whyParts: string[] = []
    if (safeNum(q95) !== null) whyParts.push(`q95=${Number(q95).toFixed(2)}`)
    if (safeNum(qmax) !== null) whyParts.push(`max=${Number(qmax).toFixed(2)}`)
    if (whyParts.length) bullets.push({ k: "Why", v: `coupled drift + tail risk (${whyParts.join(", ")})` })
    else bullets.push({ k: "Why", v: "coupled drift + tail risk" })

    const proofParts: string[] = []
    if (safeNum(horizon) !== null) proofParts.push(`${horizon} step horizon`)
    if (safeNum(mape) !== null) proofParts.push(`MAPE ${(Number(mape) * 100).toFixed(1)}%`)
    if (safeNum(rmse) !== null) proofParts.push(`RMSE ${Number(rmse).toFixed(3)}`)
    if (safeNum(ciWidthPct) !== null) proofParts.push(`CI ±${(Number(ciWidthPct) * 100).toFixed(1)}%`)
    if (proofParts.length) bullets.push({ k: "Proof", v: proofParts.join(" • ") })
    else bullets.push({ k: "Proof", v: "diagnostic payload + invariant checks" })

    const lastRunHint = latest?.run_dir ? "latest" : "none yet"

    return { signal, bullets, datasetLabel, lastRunHint }
  }, [artifacts, latest, forecastHorizonDays])

  const plotFiles: string[] = useMemo(() => {
    const a = artifacts as any
    const idx = a?.plots_index as any

    const fromIdx = (idx?.plot_files || idx?.files || []) as any[]
    const fromPlots = (plots?.files || []) as any[]

    // Merge all known shapes (idx + normalized plots)
    const merged = [...fromIdx, ...fromPlots] as any[]

    const out = merged
      .map((x) => (typeof x === "string" ? x : x?.file))
      .filter((x) => x && String(x).trim().length > 0)
      .map((x) => String(x))

    return Array.from(new Set(out)).slice(0, 12)
  }, [artifacts, plots, latest]);

  async function runAurora() {
    setRunning(true)
    addLog("[run] starting…")

    const body: AuroraRunArgs = {
      dataset_path: datasetPath,
      seed,
      forecast_horizon: forecastHorizonDays,
      anomalyPercentile,
      also_math: alsoMath,
      math_v2: mathV2,
      deep_math: deepMath,
      also_llm: alsoLLM,
    } as any

    try {
      const resp = await auroraClient.run(body as any)
      if (resp?.ok) {
        addLog("[run] ok rc=0")
        await refreshLatest()
      } else {
        addLog(`[run] failed: ${JSON.stringify(resp)}`)
      }
    } catch (e: any) {
      addLog(`[run] error: ${e?.message ?? String(e)}`)
    } finally {
      setRunning(false)
    }
  }

  async function clearLog() {
    setLog([])
  }

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {/* Controls */}
      <div className="card" style={{ padding: 14 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div style={{ fontWeight: 700 }}>Aurora Console</div>
          <div className="muted" style={{ fontSize: 12 }}>
            API: {apiStatus}
          </div>
        </div>

        <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
          <label className="muted" style={{ fontSize: 12 }}>
            Dataset path
          </label>
          <input value={datasetPath} onChange={(e) => setDatasetPath(e.target.value)} />

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <div>
              <label className="muted" style={{ fontSize: 12 }}>
                Seed
              </label>
              <input type="number" value={seed} onChange={(e) => setSeed(parseInt(e.target.value || "0", 10))} />
            </div>

            <div>
              <label className="muted" style={{ fontSize: 12 }}>
                Forecast horizon (days)
              </label>
              <input
                type="range"
                min={7}
                max={60}
                step={1}
                value={forecastHorizonDays}
                onChange={(e) => setForecastHorizonDays(parseInt(e.target.value, 10))}
              />
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                {forecastHorizonDays} days
              </div>
            </div>
          </div>

          <div>
            <label className="muted" style={{ fontSize: 12 }}>
              Anomaly sensitivity (percentile)
            </label>
            <input
              type="range"
              min={80}
              max={99}
              step={1}
              value={anomalyPercentile}
              onChange={(e) => setAnomalyPercentile(parseInt(e.target.value, 10))}
            />
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              {anomalyPercentile}th percentile
            </div>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={alsoMath} onChange={(e) => setAlsoMath(e.target.checked)} /> Math
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={mathV2} onChange={(e) => setMathV2(e.target.checked)} /> Math v2
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={deepMath} onChange={(e) => setDeepMath(e.target.checked)} /> Deep math
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={alsoLLM} onChange={(e) => setAlsoLLM(e.target.checked)} /> LLM
            </label>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button disabled={running} onClick={runAurora}>
              {running ? "Running…" : "Run Aurora"}
            </button>
            <button onClick={refreshLatest}>Refresh insights</button>
            <button onClick={clearLog}>Clear</button>
          </div>

          <div className="muted" style={{ fontSize: 12 }}>
            Last run: {fmt(latest?.run_dir)}
          </div>
        </div>
      </div>

      {/* Tiles */}
      <div className="card" style={{ padding: 14 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10 }}>
          {tiles.map((t) => (
            <div key={t.label} className="card" style={{ padding: 12 }}>
              <div className="muted" style={{ fontSize: 12 }}>
                {t.label}
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{t.value}</div>
              {t.hint ? (
                <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                  {t.hint}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </div>
      {/* Insights (Storycard + Action/LLM response) */}
      <div className="card" style={{ padding: 14 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div style={{ fontWeight: 800 }}>Aurora Storycard</div>
          <span
            className="muted"
            style={{
              fontSize: 11,
              padding: "6px 10px",
              borderRadius: 999,
              border: "1px solid rgba(255,255,255,0.12)",
              background: "rgba(255,255,255,0.06)",
              whiteSpace: "nowrap",
            }}
          >
            {story.signal}
          </span>
        </div>

        {/* Mini "live" wave (visual only, Scale-ish vibe) */}
        <div
          style={{
            marginTop: 12,
            borderRadius: 16,
            border: "1px solid rgba(255,255,255,0.10)",
            background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02))",
            overflow: "hidden",
          }}
        >
          <svg viewBox="0 0 900 180" width="100%" height="160" preserveAspectRatio="none" style={{ display: "block" }}>
            <defs>
              <linearGradient id="aurWave" x1="0" x2="1" y1="0" y2="0">
                <stop offset="0%" stopColor="rgba(0,0,0,0.0)" />
                <stop offset="40%" stopColor="rgba(0,0,0,0.55)" />
                <stop offset="100%" stopColor="rgba(0,0,0,0.0)" />
              </linearGradient>
            </defs>
            <path
              d="M0,110 C120,40 220,30 320,90 C420,150 520,160 640,120 C760,80 820,80 900,110 L900,180 L0,180 Z"
              fill="url(#aurWave)"
              style={{
                transformOrigin: "center",
                animation: "aurWaveFloat 5.2s ease-in-out infinite",
              }}
            />
          </svg>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1.1fr 1.9fr", gap: 14, marginTop: 12 }}>
          <div style={{ display: "grid", gap: 10 }}>
            {story.bullets.map((b) => (
              <div key={b.k}>
                <div className="muted" style={{ fontSize: 12, fontWeight: 700 }}>{b.k}</div>
                <div style={{ fontSize: 14, marginTop: 2 }}>{b.v}</div>
              </div>
            ))}
          </div>

          <div style={{ display: "grid", gap: 8 }}>
            <div className="muted" style={{ fontSize: 12, fontWeight: 700 }}>
              Insight response (LLM or deterministic)
            </div>

            {actionOutput && actionOutput.trim().length ? (
              <div
                style={{
                  borderRadius: 14,
                  border: "1px solid rgba(255,255,255,0.10)",
                  background: "rgba(0,0,0,0.22)",
                  padding: 12,
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                }}
              >
                {actionOutput}
              </div>
            ) : (
              <div className="muted" style={{ fontSize: 12 }}>
                No response yet. Run an action (and/or enable LLM) to populate this panel.
              </div>
            )}

            <div className="muted" style={{ fontSize: 12 }}>
              Last action: {actionResult?.ok ? "ok" : actionResult ? "failed" : "—"} • run: {story.lastRunHint}
            </div>
          </div>
        </div>

        {/* Inline keyframes (keeps this self-contained; no CSS file needed) */}
        <style>{`
          @keyframes aurWaveFloat {
            0% { transform: translateX(0px) translateY(0px) scaleY(1); opacity: 0.95; }
            50% { transform: translateX(-18px) translateY(6px) scaleY(1.04); opacity: 1; }
            100% { transform: translateX(0px) translateY(0px) scaleY(1); opacity: 0.95; }
          }
        `}</style>
      </div>

      {/* Plots */}
      <div className="card" style={{ padding: 14 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <div style={{ fontWeight: 700 }}>Plots</div>
          <div className="muted" style={{ fontSize: 12 }}>
            {plotFiles.length} files
          </div>
        </div>

        {plotFiles.length === 0 ? (
          <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
            No plots available yet.
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10, marginTop: 10 }}>
            {plotFiles.map((f) => {
              const url = plots?.url_prefix ? `${plots.url_prefix}/${f}` : auroraClient.plotUrlLatest(f)

              const base = String(f || "").split("/").pop() || String(f || "")
              const stem = base.replace(/\.[^.]+$/, "").toLowerCase()

              const titleMap: Record<string, string> = {
                anomalies: "Top anomalies",
                correlations: "Top correlations",
                forecast: "Forecast",
                missingness: "Top missingness",
              }

              const prettyTitle =
                titleMap[stem] ||
                (stem ? stem.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : base)

              const ext = (base.split(".").pop() || "").toUpperCase()

              return (
                <div
                  key={f}
                  className="card"
                  style={{
                    padding: 12,
                    overflow: "hidden",
                    borderRadius: 16,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 10,
                      marginBottom: 10,
                    }}
                  >
                    <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: 14 }}>{prettyTitle}</div>
                      <div
                        className="muted"
                        style={{
                          fontSize: 12,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          maxWidth: 420,
                        }}
                        title={base}
                      >
                        {base}
                      </div>
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span
                        className="muted"
                        style={{
                          fontSize: 11,
                          padding: "4px 8px",
                          borderRadius: 999,
                          border: "1px solid rgba(255,255,255,0.12)",
                          background: "rgba(255,255,255,0.06)",
                        }}
                      >
                        {ext || "FILE"}
                      </span>

                      <button
                        onClick={() => window.open(url, "_blank", "noopener,noreferrer")}
                        style={{ padding: "6px 10px", fontSize: 12 }}
                      >
                        Open
                      </button>
                    </div>
                  </div>

                  <div
                    style={{
                      borderRadius: 14,
                      border: "1px solid rgba(255,255,255,0.10)",
                      background: "rgba(0,0,0,0.22)",
                      overflow: "hidden",
                    }}
                  >
                    <img
                      src={url}
                      alt={base}
                      style={{
                        width: "100%",
                        height: 220,
                        objectFit: "contain",
                        display: "block",
                      }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Aurora X UI: Next actions */}
      <div className="card" style={{ padding: 14 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <div style={{ fontWeight: 700 }}>Next actions</div>
          <div className="muted" style={{ fontSize: 12 }}>{alsoLLM ? "LLM enabled" : "LLM optional"}</div>
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
          <select
            value={selectedActionId}
            onChange={(e) => {
              const id = e.target.value
              setSelectedActionId(id)
              const a = suggestedActions.find((x) => x.id === id)
              if (a && typeof a.prompt !== "undefined") setActionText(a.prompt || "")
            }}
            style={{ minWidth: 260 }}
          >
            {(suggestedActions.length ? suggestedActions : [{ id: "freeform", title: "Custom request", prompt: "" }]).map((a) => (
              <option key={a.id} value={a.id}>
                {a.title}
              </option>
            ))}
          </select>

          <button disabled={actionBusy} onClick={refreshActions}>Refresh</button>
          <button disabled={actionBusy} onClick={executeAction}>{actionBusy ? "Working..." : "Execute"}</button>

          <div className="muted" style={{ fontSize: 12 }}>Run dir: {latest?.run_dir ? "latest" : "none yet"}</div>
        </div>

        <textarea
          value={actionText}
          onChange={(e) => setActionText(e.target.value)}
          placeholder="Accept/edit the suggestion, then click Execute."
          rows={4}
          style={{ marginTop: 10, width: "100%" }}
        />

        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          Saves a request into the run folder (safe, no pipeline changes).
        </div>
      </div>

      {/* Run log */}
      <div className="card" style={{ padding: 14 }}>
        <div style={{ fontWeight: 700 }}>Run log</div>
        <div style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace", fontSize: 12, marginTop: 10, whiteSpace: "pre-wrap" }}>
          {log.length ? log.join("\n") : "Aurora is armed. Pick a demo and press Run Aurora.\n[run] starting…\n[run] ok rc=0"}
        </div>
      </div>
    </div>
  )
}

















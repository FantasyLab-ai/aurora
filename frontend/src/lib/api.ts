/**
 * Typed fetch wrappers for /api/* endpoints.
 *
 * Two design choices:
 *
 *   1. Always return `{ ok, data, error }` shape. The Studio's
 *      endpoints already encode failure as `{ "ok": false, "error": "..." }`
 *      with HTTP 200 — we mirror that so consumers never have to do
 *      `try { res = await fetch... } catch { ... } if (!res.ok)` ceremony.
 *
 *   2. Auth-aware. If the page has authenticated via Stream 2.4
 *      Phase 2, we attach the bearer token automatically. Today this
 *      means reading `window.__auroraWorkspace?.token_id` — when the
 *      page picks up a real bearer flow, the helper grows.
 */

import type { AuroraState, WorkspaceContext } from '../types/aurora';

export interface ApiResult<T> {
  ok: boolean;
  data?: T;
  error?: string;
  status?: number;
}

/** Default fetch options every call inherits. */
function baseInit(extra: RequestInit = {}): RequestInit {
  const headers: Record<string, string> = {
    'Accept': 'application/json',
    ...(extra.headers as Record<string, string> || {}),
  };
  // The full bearer token isn't stored on window in the current build;
  // only the redacted prefix is. If a future build wires a real bearer,
  // it'll set `window.__auroraBearer` and we'll pick it up here.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const bearer = (window as any).__auroraBearer as string | undefined;
  if (bearer) headers['Authorization'] = `Bearer ${bearer}`;
  return {
    credentials: 'same-origin',
    ...extra,
    headers,
  };
}

/** Core helper — GET endpoint that returns JSON. */
async function getJson<T>(path: string): Promise<ApiResult<T>> {
  try {
    const r = await fetch(path, baseInit());
    let body: unknown;
    try { body = await r.json(); } catch { body = null; }
    if (!r.ok) {
      const msg = (body && typeof body === 'object' && 'error' in body)
        ? String((body as { error: unknown }).error) : `HTTP ${r.status}`;
      return { ok: false, status: r.status, error: msg };
    }
    return { ok: true, status: r.status, data: body as T };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/** Core helper — POST JSON to an endpoint. */
async function postJson<T>(
  path: string,
  body: unknown,
): Promise<ApiResult<T>> {
  try {
    const r = await fetch(path, baseInit({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }));
    let resBody: unknown;
    try { resBody = await r.json(); } catch { resBody = null; }
    if (!r.ok) {
      const msg = (resBody && typeof resBody === 'object' && 'error' in resBody)
        ? String((resBody as { error: unknown }).error) : `HTTP ${r.status}`;
      return { ok: false, status: r.status, error: msg };
    }
    return { ok: true, status: r.status, data: resBody as T };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

// ----------------------------------------------------------------------
// Public typed endpoints (a starter set — grow as panels migrate).
// ----------------------------------------------------------------------

/** Fetch the assembled state for the current (or specific) run. */
export function fetchState(runDir?: string): Promise<ApiResult<AuroraState>> {
  const qs = runDir ? `?run_dir=${encodeURIComponent(runDir)}` : '';
  return getJson<AuroraState>(`/api/state${qs}`);
}

/** Health-check the API surface. */
export function healthCheck(): Promise<ApiResult<{ ok: boolean }>> {
  return getJson<{ ok: boolean }>('/api/health');
}

/** Resolve the current workspace (Stream 2.4 Phase 2). */
export function whoami(): Promise<ApiResult<WorkspaceContext>> {
  return getJson<WorkspaceContext>('/api/auth/whoami');
}

/** Kick off a new run. */
export interface RunKickoff {
  ok: boolean;
  run_id?: string;
  run_dir?: string | null;
  async?: boolean;
  cached?: boolean;
  error?: string;
}
export function runDataset(opts: {
  dataset: string;
  seed?: number;
  seed_text?: string;
  inherit_from?: string | null;
}): Promise<ApiResult<RunKickoff>> {
  return postJson<RunKickoff>('/api/run', {
    seed: 42,
    also_llm: true,
    also_motif: true,
    also_orchestrator: true,
    ...opts,
  });
}

/** Causal: do() query. */
export interface DoResult {
  ok: boolean;
  identifiable?: boolean;
  treatment?: string;
  outcome?: string;
  adjustment_set?: string[];
  effect_estimate?: number | null;
  standard_error?: number | null;
  n_obs?: number | null;
  unidentifiable_reason?: string | null;
  assumptions?: string[];
}
export function causalDo(opts: {
  treatment: string;
  outcome: string;
  intervention_value?: number | null;
  run_dir?: string | null;
}): Promise<ApiResult<DoResult>> {
  return postJson<DoResult>('/api/causal/do', opts);
}

/** Streaming status snapshot. */
export interface StreamStatus {
  ok: boolean;
  running?: boolean;
  path?: string;
  window_rows?: number;
  dedupe_size?: number;
  contracts_attached?: boolean;
  subscriber_count?: number;
  last_findings_count?: number;
  errors?: string[];
}
export function streamStatus(): Promise<ApiResult<StreamStatus>> {
  return getJson<StreamStatus>('/api/stream/status');
}

/**
 * Aurora API types — shape mirrors what studio_api.py returns.
 *
 * These are minimal and pragmatic. The full /api/state response is
 * very rich; we type only the fields the TypeScript modules actually
 * touch, leaving the rest as `unknown` so we get real type-safety
 * without committing to a frozen schema.
 *
 * When a panel migrates to Phase 3 (Svelte / framework), its
 * concrete shape moves here. Until then, everything stays minimal.
 */

// ----------------------------------------------------------------------
// Severity scale — used throughout findings + anomalies.
// ----------------------------------------------------------------------

export type Severity = 'crit' | 'warn' | 'info' | 'ok';

// ----------------------------------------------------------------------
// Finding — the core unit of Aurora output. Every analytical method
// emits findings; the Studio renders them as cards.
// ----------------------------------------------------------------------

export interface Finding {
  /** Method name (e.g. "iso-forest", "var", "physics_discovery"). */
  method?: string;
  /** Headline ~80 chars. */
  title?: string;
  /** Body paragraph. */
  description?: string;
  /** Severity tier. */
  severity?: Severity;
  /** 0..1; higher = more confident. */
  confidence?: number;
  /** Glass-box contract — true means the finding lacks provenance. */
  fabricated?: boolean;
  /** Stable id for cross-referencing. */
  claim_id?: string | null;
  /** Structured per-method payload — too varied to type strictly. */
  evidence?: Record<string, unknown>;
  /** Aurora's "headline" tag — physics-law matches, motif IDs. */
  headline?: boolean;
  /** Q3 Stream A — set when a previous run's priors aligned with this finding. */
  prior_source?: {
    run_id?: string;
    kind?: 'physics' | 'regimes' | 'baseline' | 'anomaly_floor';
    alignment?: 'matches' | 'drifts' | 'novel';
    note?: string;
  };
  /** Aurora ref resolver block — e.g. "Hurricane Andrew" instead of "row 1248". */
  referent?: {
    primary_label?: string;
    level_used?: string;
    referent_confidence?: number;
    inspect_only?: boolean;
    fallback_chain?: string[];
  };
  /** KB citation chip attached by state_builder. */
  kb_citation?: {
    domain?: string;
    authors?: string;
    year?: number | string;
    title?: string;
    venue?: string;
    summary?: string;
    url?: string;
  };
  // ... anything we haven't typed yet stays accessible via this index.
  [key: string]: unknown;
}

// ----------------------------------------------------------------------
// State — the /api/state response top level. Only the fields we
// actually consume from TypeScript are typed.
// ----------------------------------------------------------------------

export interface AuroraState {
  run_id?: string;
  run_dir?: string;
  dataset?: Record<string, unknown>;
  structure?: Record<string, unknown>;
  overview?: Record<string, unknown>;
  anomalies?: unknown[];
  forecast?: Record<string, unknown>;
  causal?: Record<string, unknown>;
  physics?: Record<string, unknown>;
  regimes?: unknown[];
  motifs?: unknown[];
  findings?: Finding[];
  copilot?: Record<string, unknown>;
  plots?: unknown[];
  /** Q3 Stream A — composable findings summary. */
  composed_from?: {
    available?: boolean;
    source_run_id?: string;
    n_priors?: number;
    n_tagged?: number;
    priors?: Record<string, unknown>;
  };
  /** v1.2 Stream 1.2 — 7 extended methods per-tile state. */
  extended_methods?: {
    available?: boolean;
    n_methods_run?: number;
    n_findings?: number;
    n_fit?: number;
    per_method?: Record<string, unknown>;
    order?: string[];
  };
  /** Run metadata stamped by build_state. */
  run_meta?: {
    run_id?: string;
    run_dir?: string;
    state?: string;
    started_at?: string;
    dataset_name?: string;
    rows_total?: number;
    seed_text?: string | null;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

// ----------------------------------------------------------------------
// Streaming events (Server-Sent Events on /api/stream/events).
// ----------------------------------------------------------------------

export type StreamEventKind =
  | 'window_advanced'
  | 'new_finding'
  | 'regime_changed'
  | 'heartbeat';

export interface StreamEvent {
  kind: StreamEventKind;
  payload: Record<string, unknown>;
  timestamp: number;
  source?: string;
}

// ----------------------------------------------------------------------
// Workspace context (Stream 2.4 Phase 2).
// ----------------------------------------------------------------------

export interface WorkspaceContext {
  auth_available: boolean;
  workspace_id: string;
  label?: string;
  token_id?: string;
  authenticated: boolean;
  auth_required: boolean;
}

// ----------------------------------------------------------------------
// Augment the global window with the legacy globals so TypeScript
// modules can read/write them without errors during the Phase 2
// strangler-fig handoff. Each global gets a typed accessor in
// src/lib/state.ts.
// ----------------------------------------------------------------------

declare global {
  interface Window {
    __auroraRunDir?: string | null;
    __auroraRunId?: string | null;
    __activeRunId?: string | null;
    __auroraInheritFrom?: {
      run_dir?: string;
      run_id?: string;
      dataset?: string;
      n_priors?: number;
    } | null;
    __auroraWorkspace?: WorkspaceContext | null;
    __pendingSeedText?: string;
    __auroraOpenCmdK?: () => void;
    __toast?: (msg: string) => void;
    refreshState?: (opts?: { run_dir?: string }) => Promise<void> | void;
  }
}

export {};

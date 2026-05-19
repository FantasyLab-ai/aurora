/**
 * State store — Nanostores-backed atoms that mirror the legacy
 * `window.__aurora*` globals.
 *
 * During Phase 2 we keep the globals in place so the inline JS in
 * index.html keeps working unchanged. Every read/write to a global
 * is mirrored to a typed atom so new TypeScript code never has to
 * touch the `window` object — it just `runDir.subscribe(...)` and
 * `runDir.set(...)`.
 *
 * Once Phase 3 (panel migration) starts, the atoms become the
 * source of truth and the globals become thin compatibility
 * shims for any remaining inline code.
 *
 * Nanostores was picked over Zustand/Jotai because it's
 * <1 KB, framework-agnostic, and survives a future Svelte / React /
 * Solid migration without rewriting the store layer.
 */

import { atom, type WritableAtom } from 'nanostores';
import type { AuroraState, WorkspaceContext } from '../types/aurora';

// ----------------------------------------------------------------------
// Core run state — current run directory + id.
// ----------------------------------------------------------------------

export const runDir = atom<string | null>(window.__auroraRunDir ?? null);
export const runId  = atom<string | null>(window.__auroraRunId  ?? null);
export const activeRunId = atom<string | null>(window.__activeRunId ?? null);

// Mirror writes to the legacy globals so the inline code can keep
// reading them. Two-way binding — when the legacy code updates a
// global, we don't see it (no proxy here yet); call `syncFromGlobals()`
// after any legacy operation to pull the latest in.
runDir.subscribe(v => { window.__auroraRunDir = v; });
runId.subscribe(v => { window.__auroraRunId = v; });
activeRunId.subscribe(v => { window.__activeRunId = v; });

// ----------------------------------------------------------------------
// Composable findings — Q3 Stream A.
// ----------------------------------------------------------------------

export interface InheritState {
  run_dir?: string;
  run_id?: string;
  dataset?: string;
  n_priors?: number;
}
export const inheritFrom = atom<InheritState | null>(
  window.__auroraInheritFrom ?? null,
);
inheritFrom.subscribe(v => { window.__auroraInheritFrom = v; });

// ----------------------------------------------------------------------
// Workspace context — Stream 2.4 Phase 2.
// ----------------------------------------------------------------------

export const workspace = atom<WorkspaceContext | null>(
  window.__auroraWorkspace ?? null,
);
workspace.subscribe(v => { window.__auroraWorkspace = v; });

// ----------------------------------------------------------------------
// /api/state cache — populated after every fetchState() call.
// ----------------------------------------------------------------------

export const state = atom<AuroraState | null>(null);

// ----------------------------------------------------------------------
// One-shot sync from window globals back into the atoms. Call this
// after a legacy controller mutates a global directly so the typed
// modules see the change.
// ----------------------------------------------------------------------

export function syncFromGlobals(): void {
  if (window.__auroraRunDir !== runDir.get()) runDir.set(window.__auroraRunDir ?? null);
  if (window.__auroraRunId  !== runId.get())  runId.set(window.__auroraRunId  ?? null);
  if (window.__activeRunId  !== activeRunId.get()) activeRunId.set(window.__activeRunId ?? null);
  if (window.__auroraInheritFrom !== inheritFrom.get())
    inheritFrom.set(window.__auroraInheritFrom ?? null);
  if (window.__auroraWorkspace !== workspace.get())
    workspace.set(window.__auroraWorkspace ?? null);
}

// Re-sync periodically — cheap insurance against missed updates from
// the legacy IIFEs. 4× per second is invisible in any profiler.
let _syncInterval: ReturnType<typeof setInterval> | undefined;
export function startGlobalSync(intervalMs = 250): void {
  if (_syncInterval) return;
  _syncInterval = setInterval(syncFromGlobals, intervalMs);
}
export function stopGlobalSync(): void {
  if (_syncInterval) {
    clearInterval(_syncInterval);
    _syncInterval = undefined;
  }
}

// ----------------------------------------------------------------------
// Helper — convenience for new TypeScript controllers that want a
// "wait for run_dir to be set" promise.
// ----------------------------------------------------------------------

export function whenRunDir(): Promise<string> {
  const v = runDir.get();
  if (v) return Promise.resolve(v);
  return new Promise((resolve) => {
    const unsub = runDir.subscribe((next) => {
      if (next) {
        unsub();
        resolve(next);
      }
    });
  });
}

// Re-export so callers can grab everything from one place.
export type AnyAtom = WritableAtom<unknown>;

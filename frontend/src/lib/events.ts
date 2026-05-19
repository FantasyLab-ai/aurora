/**
 * Hardened Server-Sent Events client.
 *
 * The legacy inline JS does `new EventSource('/api/stream/events')`
 * and trusts it. In practice EventSource has rough edges:
 *
 *   * No exponential backoff when the server bounces.
 *   * No `readyState` gating before reconnecting.
 *   * No way to pause when the tab is hidden.
 *   * No message dedup if the same payload arrives twice on a
 *     network blip.
 *
 * `AuroraEventStream` wraps EventSource and fixes all of the above.
 * Drop-in replacement: instantiate, call `.on(kind, handler)`, call
 * `.close()` when done.
 *
 * This is the recommended SSE pattern for 2026 dashboards — see the
 * oneuptime guide referenced in the migration brief.
 */

import type { StreamEvent, StreamEventKind } from '../types/aurora';

export interface AuroraEventStreamOpts {
  /** Endpoint path. Defaults to /api/stream/events. */
  url?: string;
  /** Pause receiving while the tab is hidden. Default true. */
  openWhenHidden?: boolean;
  /** Cap on retry backoff in ms. Default 30s. */
  maxBackoffMs?: number;
  /** Bound on the dedupe LRU. Default 200 most-recent identities. */
  dedupeSize?: number;
  /** Called whenever the connection state changes ("open" / "closed" / "retrying"). */
  onConnectionChange?: (state: ConnectionState) => void;
}

export type ConnectionState = 'connecting' | 'open' | 'retrying' | 'closed';

type Handler = (ev: StreamEvent) => void;

export class AuroraEventStream {
  private url: string;
  private es: EventSource | null = null;
  private handlers = new Map<StreamEventKind | '*', Set<Handler>>();
  private connState: ConnectionState = 'closed';
  private retryAttempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;
  private closedByCaller = false;
  private opts: Required<AuroraEventStreamOpts>;
  // Bounded LRU of identity hashes for dedupe.
  private seenIdentities = new Set<string>();
  // Visibility tracking.
  private boundVisibilityHandler: () => void;

  constructor(opts: AuroraEventStreamOpts = {}) {
    this.url = opts.url ?? '/api/stream/events';
    this.opts = {
      url: this.url,
      openWhenHidden: opts.openWhenHidden ?? false,
      maxBackoffMs: opts.maxBackoffMs ?? 30_000,
      dedupeSize: opts.dedupeSize ?? 200,
      onConnectionChange: opts.onConnectionChange ?? (() => undefined),
    };
    this.boundVisibilityHandler = () => this.handleVisibilityChange();
  }

  /** Open / re-open the stream. Idempotent. */
  start(): this {
    this.closedByCaller = false;
    this.connect();
    document.addEventListener('visibilitychange', this.boundVisibilityHandler);
    return this;
  }

  /** Subscribe to a specific event kind. Use '*' for every kind. */
  on(kind: StreamEventKind | '*', handler: Handler): () => void {
    let set = this.handlers.get(kind);
    if (!set) { set = new Set(); this.handlers.set(kind, set); }
    set.add(handler);
    return () => set!.delete(handler);
  }

  /** Close the stream + stop retrying. */
  close(): void {
    this.closedByCaller = true;
    this.disconnect();
    document.removeEventListener('visibilitychange', this.boundVisibilityHandler);
  }

  get state(): ConnectionState { return this.connState; }

  // ------------------------------------------------------------------
  // Internals
  // ------------------------------------------------------------------

  private setState(s: ConnectionState): void {
    if (s === this.connState) return;
    this.connState = s;
    try { this.opts.onConnectionChange(s); } catch { /* never let cb break stream */ }
  }

  private connect(): void {
    if (this.es) return;  // already connected
    this.setState('connecting');
    let es: EventSource;
    try {
      es = new EventSource(this.url);
    } catch (e) {
      this.scheduleReconnect();
      return;
    }
    this.es = es;

    es.onopen = () => {
      this.retryAttempt = 0;
      this.setState('open');
    };
    es.onerror = () => {
      // EventSource quietly reconnects on its own when readyState is
      // CONNECTING. We only schedule a retry when it's CLOSED (server
      // told us to go away).
      if (es.readyState === EventSource.CLOSED) {
        this.disconnect();
        this.scheduleReconnect();
      } else {
        this.setState('connecting');
      }
    };

    // Wire every documented event kind.
    const kinds: StreamEventKind[] = [
      'window_advanced', 'new_finding', 'regime_changed', 'heartbeat',
    ];
    for (const k of kinds) {
      es.addEventListener(k, (raw) => this.routeMessage(k, raw as MessageEvent));
    }
  }

  private disconnect(): void {
    if (!this.es) return;
    try { this.es.close(); } catch { /* ignore */ }
    this.es = null;
    if (this.connState !== 'closed') this.setState('closed');
  }

  private scheduleReconnect(): void {
    if (this.closedByCaller) return;
    if (!this.opts.openWhenHidden && document.hidden) return;
    this.setState('retrying');
    // Exponential backoff with jitter, capped at maxBackoffMs.
    const base = Math.min(
      1000 * Math.pow(2, this.retryAttempt),
      this.opts.maxBackoffMs,
    );
    const jitter = Math.random() * 400;
    const wait = base + jitter;
    this.retryAttempt += 1;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = undefined;
      this.connect();
    }, wait);
  }

  private handleVisibilityChange(): void {
    if (this.closedByCaller) return;
    if (document.hidden && !this.opts.openWhenHidden) {
      // Tab hidden — disconnect to save server resources.
      this.disconnect();
    } else if (!document.hidden && !this.es) {
      // Tab visible again — reconnect.
      this.retryAttempt = 0;
      this.connect();
    }
  }

  private routeMessage(kind: StreamEventKind, raw: MessageEvent): void {
    let data: unknown;
    try { data = JSON.parse(raw.data || '{}'); }
    catch { data = { raw: raw.data }; }

    const ev: StreamEvent = {
      kind,
      payload: (data && typeof data === 'object' ? (data as { payload?: Record<string, unknown> }).payload : undefined) || {},
      timestamp: (data && typeof data === 'object' && 'timestamp' in (data as object))
        ? (data as { timestamp: number }).timestamp
        : Date.now() / 1000,
      source: (data && typeof data === 'object' && 'source' in (data as object))
        ? String((data as { source: unknown }).source)
        : undefined,
    };

    // Dedupe — uses the same identity hash the server includes in
    // new_finding events. For other kinds we always deliver.
    if (kind === 'new_finding') {
      const ident = String((ev.payload as Record<string, unknown>).identity || '');
      if (ident && this.seenIdentities.has(ident)) return;
      if (ident) {
        this.seenIdentities.add(ident);
        // Bounded LRU — drop oldest when at cap.
        if (this.seenIdentities.size > this.opts.dedupeSize) {
          const first = this.seenIdentities.values().next().value;
          if (first !== undefined) this.seenIdentities.delete(first);
        }
      }
    }

    this.deliverHandlers(kind, ev);
  }

  private deliverHandlers(kind: StreamEventKind, ev: StreamEvent): void {
    for (const k of [kind, '*' as const]) {
      const set = this.handlers.get(k);
      if (!set) continue;
      for (const h of set) {
        try { h(ev); } catch { /* never let one handler break the rest */ }
      }
    }
  }
}

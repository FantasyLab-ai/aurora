import type { Clock } from '../../lib/clock.js';
import { systemClock } from '../../lib/clock.js';
import type { EventBus } from '../../lib/event-bus.js';
import type { SurplusItemRepository } from './surplus-item.repository.js';

/**
 * The Dynamic Countdown Rotator (Epic 2, Phase 2).
 *
 * Polls for items stuck in SALES_PHASE past their sales window and rolls
 * them into the $0 DONATION_PHASE, emitting `donation.available` so the
 * routing module can dispatch nearby couriers. Items whose cold-chain
 * deadline has already passed are expired instead — spoiled food never
 * reaches the donation pool.
 *
 * The state change uses a compare-and-set transition, so multiple worker
 * instances (or a Redis-keyspace-notification trigger racing the poll) are
 * safe: exactly one transition wins, the rest observe a no-op.
 */
export class PhaseRolloverWorker {
  private timer: ReturnType<typeof setInterval> | undefined;

  constructor(
    private readonly repo: SurplusItemRepository,
    private readonly bus: EventBus,
    private readonly options: { pollIntervalMs?: number } = {},
    private readonly clock: Clock = systemClock,
  ) {}

  start(): void {
    if (this.timer) return;
    const interval = this.options.pollIntervalMs ?? 30_000;
    this.timer = setInterval(() => {
      void this.tick().catch((err) => {
        // A failed sweep must not kill the loop; the next tick retries.
        console.error('[phase-rollover] sweep failed:', err);
      });
    }, interval);
    this.timer.unref?.();
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = undefined;
    }
  }

  /** One sweep. Exposed for tests and for cron-style invocation. */
  async tick(): Promise<{ rolledOver: string[]; expired: string[] }> {
    const now = this.clock.now();
    const due = await this.repo.findExpiredSalesPhase(now);
    const rolledOver: string[] = [];
    const expired: string[] = [];

    for (const item of due) {
      if (item.safeUntil.getTime() <= now.getTime()) {
        const updated = await this.repo.transitionState(item.id, 'SALES_PHASE', 'EXPIRED');
        if (updated) {
          expired.push(item.id);
          await this.bus.emit('item.expired', { itemId: item.id });
        }
        continue;
      }

      const updated = await this.repo.transitionState(item.id, 'SALES_PHASE', 'DONATION_PHASE', {
        rolledOverAt: now,
        salePriceCents: 0,
      });
      if (updated) {
        rolledOver.push(item.id);
        await this.bus.emit('donation.available', {
          itemId: item.id,
          latitude: item.latitude,
          longitude: item.longitude,
        });
      }
    }

    return { rolledOver, expired };
  }
}

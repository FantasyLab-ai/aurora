import type { Clock } from '../../lib/clock.js';
import { systemClock } from '../../lib/clock.js';
import type { EventBus } from '../../lib/event-bus.js';
import { METERS_PER_MILE } from './geo.js';
import type { CourierDispatchService } from './courier-dispatch.service.js';
import type { SurplusItemRepository } from '../inventory/surplus-item.repository.js';

/**
 * The unfilled-rescue escalation ladder — the fix for the death spiral that
 * kills volunteer rescue networks: a pantry that gets stood up stops
 * relying on the platform, a supplier whose box rots by the door stops
 * listing. Established rescue orgs handle this with manual coordination
 * ("volunteer management is the most time-consuming operational burden");
 * we automate the whole ladder:
 *
 *   level 1..maxLevel: unclaimed past the threshold → re-dispatch with a
 *     progressively wider radius and a fresh fan-out (surge karma pricing
 *     reflects the shrinking safety window on its own — urgency is priced
 *     from `minutesUntilUnsafe` at quote time);
 *   final level: `donation.escalated {finalAlert: true}` — a human at the
 *     hub gets pinged; the network never silently drops a rescue;
 *   past `safeUntil`: the item is EXPIRED (never delivered spoiled), and
 *     the expiry is visible in zone health metrics rather than swept away.
 */

export interface EscalationOptions {
  /** Minutes unclaimed in DONATION_PHASE before each escalation step. */
  stepMinutes?: number;
  maxLevel?: number;
  baseRadiusMiles?: number;
  /** Extra radius per escalation level. */
  radiusStepMiles?: number;
  /** Extra couriers offered per escalation level. */
  fanOutStep?: number;
}

export class EscalationWorker {
  private levels = new Map<string, number>();
  private readonly stepMinutes: number;
  private readonly maxLevel: number;
  private readonly baseRadiusMiles: number;
  private readonly radiusStepMiles: number;
  private readonly fanOutStep: number;

  constructor(
    private readonly repo: SurplusItemRepository,
    private readonly dispatch: CourierDispatchService,
    private readonly bus: EventBus,
    options: EscalationOptions = {},
    private readonly clock: Clock = systemClock,
  ) {
    this.stepMinutes = options.stepMinutes ?? 10;
    this.maxLevel = options.maxLevel ?? 2;
    this.baseRadiusMiles = options.baseRadiusMiles ?? 1.5;
    this.radiusStepMiles = options.radiusStepMiles ?? 1;
    this.fanOutStep = options.fanOutStep ?? 2;
  }

  /** One sweep; run alongside the rollover worker's cadence. */
  async tick(): Promise<{ escalated: Array<{ itemId: string; level: number }>; expired: string[] }> {
    const now = this.clock.now();
    const waiting = await this.repo.findInState('DONATION_PHASE');
    const escalated: Array<{ itemId: string; level: number }> = [];
    const expired: string[] = [];

    for (const item of waiting) {
      if (item.safeUntil.getTime() <= now.getTime()) {
        const updated = await this.repo.transitionState(item.id, 'DONATION_PHASE', 'EXPIRED');
        if (updated) {
          this.levels.delete(item.id);
          expired.push(item.id);
          await this.bus.emit('item.expired', { itemId: item.id });
        }
        continue;
      }

      const enteredAt = item.rolledOverAt ?? item.listedAt;
      const level = this.levels.get(item.id) ?? 0;
      if (level >= this.maxLevel) continue;
      const nextDue = enteredAt.getTime() + (level + 1) * this.stepMinutes * 60_000;
      if (now.getTime() < nextDue) continue;

      const newLevel = level + 1;
      this.levels.set(item.id, newLevel);
      await this.dispatch.dispatchForItem(
        item.id,
        { latitude: item.latitude, longitude: item.longitude },
        {
          radiusMeters: (this.baseRadiusMiles + newLevel * this.radiusStepMiles) * METERS_PER_MILE,
          fanOut: 3 + newLevel * this.fanOutStep,
        },
      );
      await this.bus.emit('donation.escalated', {
        itemId: item.id,
        level: newLevel,
        finalAlert: newLevel === this.maxLevel,
      });
      escalated.push({ itemId: item.id, level: newLevel });
    }

    return { escalated, expired };
  }
}

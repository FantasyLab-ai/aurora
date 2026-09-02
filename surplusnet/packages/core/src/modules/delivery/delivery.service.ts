import { randomUUID } from 'node:crypto';
import type { GeoPoint } from '../../domain/types.js';
import type { Clock } from '../../lib/clock.js';
import { systemClock } from '../../lib/clock.js';
import type { EventBus } from '../../lib/event-bus.js';
import {
  InvalidStateTransitionError,
  NotFoundError,
  ValidationError,
} from '../../lib/errors.js';
import type { SurplusItemRepository } from '../inventory/surplus-item.repository.js';

/**
 * Courier delivery lifecycle with cold-chain custody:
 *
 *   accept (wins the item, one courier only) → pickUp → recordTemp* → dropOff
 *
 * Every temperature reading is timestamped custody evidence; an excursion
 * above the safe ceiling flags the delivery (and is surfaced on the
 * compliance certificate — bad handoffs are documented, not hidden).
 * `dropOff` emits `delivery.completed` with the karma amount locked in at
 * accept time, so surge pricing can't be gamed after the fact.
 */

export type DeliveryLifecycleState = 'ACCEPTED' | 'PICKED_UP' | 'DROPPED_OFF' | 'CANCELLED';

export interface TempReading {
  at: Date;
  celsius: number;
}

export interface DeliveryRecord {
  id: string;
  itemId: string;
  courierId: string;
  state: DeliveryLifecycleState;
  acceptedAt: Date;
  pickedUpAt?: Date;
  droppedOffAt?: Date;
  dropoffName: string;
  dropoff: GeoPoint;
  karmaOnCompletion: number;
  tempReadings: TempReading[];
  coldChainCompliant: boolean;
}

export interface DeliveryOptions {
  /** Cold-chain ceiling; readings above it flag the delivery. */
  maxSafeCelsius?: number;
}

export class DeliveryService {
  private deliveries = new Map<string, DeliveryRecord>();
  private readonly maxSafeCelsius: number;

  constructor(
    private readonly items: SurplusItemRepository,
    private readonly bus: EventBus,
    options: DeliveryOptions = {},
    private readonly clock: Clock = systemClock,
  ) {
    this.maxSafeCelsius = options.maxSafeCelsius ?? 5;
  }

  /**
   * Courier accepts a pickup offer. The compare-and-set claim on the item
   * guarantees exactly one courier wins, however many received the offer.
   */
  async accept(input: {
    itemId: string;
    courierId: string;
    dropoffName: string;
    dropoff: GeoPoint;
    karmaOnCompletion: number;
  }): Promise<DeliveryRecord> {
    if (!Number.isSafeInteger(input.karmaOnCompletion) || input.karmaOnCompletion <= 0) {
      throw new ValidationError('karmaOnCompletion must be a positive integer');
    }
    const claimed = await this.items.transitionState(input.itemId, 'DONATION_PHASE', 'CLAIMED', {
      recipientId: input.dropoffName,
    });
    if (!claimed) {
      throw new InvalidStateTransitionError(
        `item ${input.itemId} is not available — another courier accepted first or it left the donation pool`,
      );
    }

    const record: DeliveryRecord = {
      id: randomUUID(),
      itemId: input.itemId,
      courierId: input.courierId,
      state: 'ACCEPTED',
      acceptedAt: this.clock.now(),
      dropoffName: input.dropoffName,
      dropoff: input.dropoff,
      karmaOnCompletion: input.karmaOnCompletion,
      tempReadings: [],
      coldChainCompliant: true,
    };
    this.deliveries.set(record.id, record);
    return { ...record, tempReadings: [] };
  }

  pickUp(deliveryId: string): DeliveryRecord {
    const d = this.require(deliveryId);
    if (d.state !== 'ACCEPTED') {
      throw new InvalidStateTransitionError(`delivery ${deliveryId} is ${d.state}, cannot pick up`);
    }
    d.state = 'PICKED_UP';
    d.pickedUpAt = this.clock.now();
    return this.snapshot(d);
  }

  recordTemp(deliveryId: string, celsius: number): DeliveryRecord {
    const d = this.require(deliveryId);
    if (d.state !== 'PICKED_UP') {
      throw new InvalidStateTransitionError(`delivery ${deliveryId} is ${d.state}, not in transit`);
    }
    d.tempReadings.push({ at: this.clock.now(), celsius });
    if (celsius > this.maxSafeCelsius) d.coldChainCompliant = false;
    return this.snapshot(d);
  }

  async dropOff(deliveryId: string): Promise<DeliveryRecord> {
    const d = this.require(deliveryId);
    if (d.state !== 'PICKED_UP') {
      throw new InvalidStateTransitionError(`delivery ${deliveryId} is ${d.state}, cannot drop off`);
    }
    d.state = 'DROPPED_OFF';
    d.droppedOffAt = this.clock.now();
    await this.items.transitionState(d.itemId, 'CLAIMED', 'DELIVERED');
    await this.bus.emit('delivery.completed', {
      deliveryId: d.id,
      itemId: d.itemId,
      courierId: d.courierId,
      karmaCredits: d.karmaOnCompletion,
    });
    return this.snapshot(d);
  }

  /** Frees the item back to the donation pool if a courier bails. */
  async cancel(deliveryId: string): Promise<void> {
    const d = this.require(deliveryId);
    if (d.state === 'DROPPED_OFF') {
      throw new InvalidStateTransitionError('completed deliveries cannot be cancelled');
    }
    d.state = 'CANCELLED';
    await this.items.transitionState(d.itemId, 'CLAIMED', 'DONATION_PHASE');
  }

  byId(deliveryId: string): DeliveryRecord | undefined {
    const d = this.deliveries.get(deliveryId);
    return d ? this.snapshot(d) : undefined;
  }

  forItem(itemId: string): DeliveryRecord | undefined {
    const d = [...this.deliveries.values()].find(
      (x) => x.itemId === itemId && x.state !== 'CANCELLED',
    );
    return d ? this.snapshot(d) : undefined;
  }

  private require(deliveryId: string): DeliveryRecord {
    const d = this.deliveries.get(deliveryId);
    if (!d) throw new NotFoundError(`no delivery ${deliveryId}`);
    return d;
  }

  private snapshot(d: DeliveryRecord): DeliveryRecord {
    return { ...d, tempReadings: [...d.tempReadings] };
  }
}

import type { CourierPresence, GeoPoint } from '../../domain/types.js';
import type { Clock } from '../../lib/clock.js';
import { systemClock } from '../../lib/clock.js';
import { haversineMeters, METERS_PER_MILE, TRANSPORT_SPEED_MPS } from './geo.js';

/**
 * Hyper-local dispatch (Epic 3): when a surplus item enters DONATION_PHASE,
 * find active couriers inside the dispatch radius, rank them by estimated
 * arrival time, and push a pickup offer to the closest few — the same
 * fan-out pattern rideshare dispatchers use.
 *
 * Live courier positions come from `CourierLocationSource` (Redis geo-set in
 * production, an in-memory map in tests). Straight-line ETA is a stand-in
 * until an OSRM isochrone backend is plugged into `estimateEtaSeconds`.
 */

export interface CourierLocationSource {
  activeCouriersNear(point: GeoPoint, radiusMeters: number): Promise<CourierPresence[]>;
}

export interface DispatchNotifier {
  offerPickup(courierId: string, offer: PickupOffer): Promise<void>;
}

export interface PickupOffer {
  itemId: string;
  pickup: GeoPoint;
  distanceMeters: number;
  etaSeconds: number;
}

export interface DispatchOptions {
  radiusMiles?: number;
  /** How many top-ranked couriers get the offer in the first wave. */
  fanOut?: number;
  /** Heartbeats older than this are treated as offline. */
  staleAfterMs?: number;
}

export interface DispatchResult {
  itemId: string;
  offered: Array<{ courierId: string; distanceMeters: number; etaSeconds: number }>;
}

export class CourierDispatchService {
  private readonly radiusMeters: number;
  private readonly fanOut: number;
  private readonly staleAfterMs: number;

  constructor(
    private readonly locations: CourierLocationSource,
    private readonly notifier: DispatchNotifier,
    options: DispatchOptions = {},
    private readonly clock: Clock = systemClock,
  ) {
    this.radiusMeters = (options.radiusMiles ?? 1.5) * METERS_PER_MILE;
    this.fanOut = options.fanOut ?? 3;
    this.staleAfterMs = options.staleAfterMs ?? 5 * 60_000;
  }

  estimateEtaSeconds(courier: CourierPresence, distanceMeters: number): number {
    return Math.round(distanceMeters / TRANSPORT_SPEED_MPS[courier.transport]);
  }

  async dispatchForItem(
    itemId: string,
    pickup: GeoPoint,
    overrides: { radiusMeters?: number; fanOut?: number } = {},
  ): Promise<DispatchResult> {
    const radiusMeters = overrides.radiusMeters ?? this.radiusMeters;
    const fanOut = overrides.fanOut ?? this.fanOut;
    const now = this.clock.now().getTime();
    const candidates = await this.locations.activeCouriersNear(pickup, radiusMeters);

    const ranked = candidates
      .filter((c) => now - c.lastSeenAt.getTime() <= this.staleAfterMs)
      .map((c) => {
        const distanceMeters = haversineMeters(pickup, c);
        return { courier: c, distanceMeters, etaSeconds: this.estimateEtaSeconds(c, distanceMeters) };
      })
      .filter((r) => r.distanceMeters <= radiusMeters)
      .sort((a, b) => a.etaSeconds - b.etaSeconds)
      .slice(0, fanOut);

    for (const r of ranked) {
      await this.notifier.offerPickup(r.courier.courierId, {
        itemId,
        pickup,
        distanceMeters: r.distanceMeters,
        etaSeconds: r.etaSeconds,
      });
    }

    return {
      itemId,
      offered: ranked.map((r) => ({
        courierId: r.courier.courierId,
        distanceMeters: r.distanceMeters,
        etaSeconds: r.etaSeconds,
      })),
    };
  }
}

/** Test/dev implementation; production uses a Redis GEOSEARCH-backed source. */
export class InMemoryCourierLocationSource implements CourierLocationSource {
  private couriers = new Map<string, CourierPresence>();

  upsert(presence: CourierPresence): void {
    this.couriers.set(presence.courierId, presence);
  }

  async activeCouriersNear(point: GeoPoint, radiusMeters: number): Promise<CourierPresence[]> {
    return [...this.couriers.values()].filter(
      (c) => haversineMeters(point, c) <= radiusMeters,
    );
  }
}

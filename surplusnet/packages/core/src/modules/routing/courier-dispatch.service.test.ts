import { describe, expect, it } from 'vitest';
import type { CourierPresence } from '../../domain/types.js';
import {
  CourierDispatchService,
  InMemoryCourierLocationSource,
  type PickupOffer,
} from './courier-dispatch.service.js';
import { haversineMeters } from './geo.js';

// Pickup point: Union Square, NYC. ~1 degree latitude ≈ 111 km.
const pickup = { latitude: 40.7359, longitude: -73.9911 };

function courier(id: string, latOffset: number, transport: CourierPresence['transport'] = 'EBIKE', lastSeenAt = new Date()): CourierPresence {
  return {
    courierId: id,
    latitude: pickup.latitude + latOffset,
    longitude: pickup.longitude,
    lastSeenAt,
    transport,
  };
}

function makeService(couriers: CourierPresence[], fanOut = 3) {
  const source = new InMemoryCourierLocationSource();
  couriers.forEach((c) => source.upsert(c));
  const offers: Array<{ courierId: string; offer: PickupOffer }> = [];
  const service = new CourierDispatchService(
    source,
    { offerPickup: async (courierId, offer) => void offers.push({ courierId, offer }) },
    { radiusMiles: 1.5, fanOut },
  );
  return { service, offers };
}

describe('haversineMeters', () => {
  it('measures ~111km per degree of latitude', () => {
    const d = haversineMeters(pickup, { ...pickup, latitude: pickup.latitude + 1 });
    expect(d).toBeGreaterThan(110_000);
    expect(d).toBeLessThan(112_000);
  });
});

describe('CourierDispatchService', () => {
  it('only offers to couriers inside the 1.5-mile radius', async () => {
    const near = courier('near', 0.005); // ~0.55 km
    const far = courier('far', 0.05); // ~5.5 km, outside radius
    const { service, offers } = makeService([near, far]);

    const result = await service.dispatchForItem('item-1', pickup);

    expect(result.offered.map((o) => o.courierId)).toEqual(['near']);
    expect(offers.map((o) => o.courierId)).toEqual(['near']);
  });

  it('ranks by ETA, not raw distance: a close walker can lose to a slightly farther e-biker', async () => {
    const walker = courier('walker', 0.008, 'FOOT'); // ~890m at 1.4 m/s ≈ 635s
    const ebiker = courier('ebiker', 0.012, 'EBIKE'); // ~1330m at 6.5 m/s ≈ 205s
    const { service } = makeService([walker, ebiker], 2);

    const result = await service.dispatchForItem('item-1', pickup);

    expect(result.offered[0]?.courierId).toBe('ebiker');
    expect(result.offered[1]?.courierId).toBe('walker');
  });

  it('limits the first wave to the fan-out count', async () => {
    const couriers = [courier('a', 0.001), courier('b', 0.002), courier('c', 0.003), courier('d', 0.004)];
    const { service, offers } = makeService(couriers, 3);

    await service.dispatchForItem('item-1', pickup);
    expect(offers).toHaveLength(3);
    expect(offers.map((o) => o.courierId)).toEqual(['a', 'b', 'c']);
  });

  it('skips couriers with stale heartbeats', async () => {
    const stale = courier('stale', 0.001, 'EBIKE', new Date(Date.now() - 10 * 60_000));
    const fresh = courier('fresh', 0.002);
    const { service, offers } = makeService([stale, fresh]);

    await service.dispatchForItem('item-1', pickup);
    expect(offers.map((o) => o.courierId)).toEqual(['fresh']);
  });
});

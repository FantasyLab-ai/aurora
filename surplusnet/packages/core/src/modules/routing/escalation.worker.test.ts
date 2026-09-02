import { describe, expect, it } from 'vitest';
import { EscalationWorker } from './escalation.worker.js';
import { CourierDispatchService, InMemoryCourierLocationSource } from './courier-dispatch.service.js';
import { METERS_PER_MILE } from './geo.js';
import { EventBus } from '../../lib/event-bus.js';
import { InMemorySurplusItemRepository } from '../inventory/surplus-item.repository.js';
import { SurplusItemService } from '../inventory/surplus-item.service.js';
import { DonationLedger, InMemoryLedgerStore } from '../tax/donation-ledger.js';
import type { Clock } from '../../lib/clock.js';

class FakeClock implements Clock {
  constructor(private current: Date) {}
  now(): Date {
    return new Date(this.current);
  }
  advanceMinutes(minutes: number): void {
    this.current = new Date(this.current.getTime() + minutes * 60_000);
  }
}

const pickup = { latitude: 40.7359, longitude: -73.9911 };

async function makeFixture(safeForMinutes = 240) {
  const clock = new FakeClock(new Date('2026-09-02T10:00:00Z'));
  const repo = new InMemorySurplusItemRepository();
  const items = new SurplusItemService(repo, new DonationLedger(new InMemoryLedgerStore()), clock);
  const bus = new EventBus();
  const locations = new InMemoryCourierLocationSource();
  const offers: Array<{ courierId: string }> = [];
  const dispatch = new CourierDispatchService(
    locations,
    { offerPickup: async (courierId) => void offers.push({ courierId }) },
    { radiusMiles: 1.5 },
    clock,
  );
  const worker = new EscalationWorker(repo, dispatch, bus, { stepMinutes: 10, maxLevel: 2 }, clock);

  const item = await items.listSurplus({
    supplierId: 'grocer-1',
    title: 'Produce Box',
    category: 'produce',
    fmvCents: 2000,
    cogsCents: 800,
    ...pickup,
    safeUntil: new Date(clock.now().getTime() + safeForMinutes * 60_000),
  });
  await repo.transitionState(item.id, 'SALES_PHASE', 'DONATION_PHASE', {
    salePriceCents: 0,
    rolledOverAt: clock.now(),
  });

  // A courier ~2.1 miles out: outside the normal 1.5-mile radius, inside the
  // level-1 escalation radius (2.5 miles).
  const upsertFarCourier = () =>
    locations.upsert({
      courierId: 'far-courier',
      latitude: pickup.latitude + (2.1 * METERS_PER_MILE) / 111_000,
      longitude: pickup.longitude,
      lastSeenAt: clock.now(),
      transport: 'EBIKE',
    });

  return { clock, repo, bus, worker, item, offers, upsertFarCourier };
}

describe('EscalationWorker', () => {
  it('widens the radius on escalation so farther couriers get the offer', async () => {
    const { clock, worker, item, offers, upsertFarCourier } = await makeFixture();

    clock.advanceMinutes(5);
    upsertFarCourier();
    expect((await worker.tick()).escalated).toEqual([]); // not due yet

    clock.advanceMinutes(6); // 11 min unclaimed
    upsertFarCourier();
    const result = await worker.tick();
    expect(result.escalated).toEqual([{ itemId: item.id, level: 1 }]);
    expect(offers.map((o) => o.courierId)).toEqual(['far-courier']);
  });

  it('climbs one level per threshold and fires the final human alert, then stops', async () => {
    const { clock, bus, worker, item } = await makeFixture();
    const events: Array<{ level: number; finalAlert: boolean }> = [];
    bus.on('donation.escalated', (e) => {
      events.push({ level: e.level, finalAlert: e.finalAlert });
    });

    clock.advanceMinutes(11);
    await worker.tick(); // level 1
    clock.advanceMinutes(10);
    await worker.tick(); // level 2 (final)
    clock.advanceMinutes(30);
    const after = await worker.tick(); // capped

    expect(events).toEqual([
      { level: 1, finalAlert: false },
      { level: 2, finalAlert: true },
    ]);
    expect(after.escalated).toEqual([]);
    expect(item.id).toBeTruthy();
  });

  it('expires donation-phase items past their safety deadline instead of escalating', async () => {
    const { clock, repo, bus, worker, item } = await makeFixture(30);
    let expiredEvents = 0;
    bus.on('item.expired', () => {
      expiredEvents += 1;
    });

    clock.advanceMinutes(31);
    const result = await worker.tick();

    expect(result.expired).toEqual([item.id]);
    expect(result.escalated).toEqual([]);
    expect((await repo.findById(item.id))?.currentState).toBe('EXPIRED');
    expect(expiredEvents).toBe(1);
  });

  it('a claimed item stops escalating', async () => {
    const { clock, repo, worker, item } = await makeFixture();
    await repo.transitionState(item.id, 'DONATION_PHASE', 'CLAIMED');
    clock.advanceMinutes(30);
    const result = await worker.tick();
    expect(result.escalated).toEqual([]);
  });
});

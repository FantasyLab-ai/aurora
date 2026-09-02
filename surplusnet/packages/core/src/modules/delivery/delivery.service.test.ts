import { describe, expect, it } from 'vitest';
import { DeliveryService } from './delivery.service.js';
import { EventBus } from '../../lib/event-bus.js';
import { InMemorySurplusItemRepository } from '../inventory/surplus-item.repository.js';
import { SurplusItemService } from '../inventory/surplus-item.service.js';
import { DonationLedger, InMemoryLedgerStore } from '../tax/donation-ledger.js';
import { InvalidStateTransitionError } from '../../lib/errors.js';

async function makeFixture() {
  const repo = new InMemorySurplusItemRepository();
  const itemService = new SurplusItemService(repo, new DonationLedger(new InMemoryLedgerStore()));
  const bus = new EventBus();
  const service = new DeliveryService(repo, bus, { maxSafeCelsius: 5 });

  const item = await itemService.listSurplus({
    supplierId: 'grocer-1',
    title: 'Dairy Box',
    category: 'dairy',
    fmvCents: 2000,
    cogsCents: 800,
    latitude: 40.7,
    longitude: -74.0,
    safeUntil: new Date(Date.now() + 4 * 3_600_000),
  });
  await repo.transitionState(item.id, 'SALES_PHASE', 'DONATION_PHASE', { salePriceCents: 0 });

  const accept = () =>
    service.accept({
      itemId: item.id,
      courierId: 'courier-1',
      dropoffName: 'Hub Fridge #3',
      dropoff: { latitude: 40.71, longitude: -74.0 },
      karmaOnCompletion: 18,
    });

  return { repo, bus, service, item, accept };
}

describe('DeliveryService', () => {
  it('runs accept → pickUp → temps → dropOff and emits the locked-in karma', async () => {
    const { repo, bus, service, item, accept } = await makeFixture();
    const events: Array<{ karmaCredits?: number }> = [];
    bus.on('delivery.completed', (e) => {
      events.push(e);
    });

    const d = await accept();
    service.pickUp(d.id);
    service.recordTemp(d.id, 3.5);
    service.recordTemp(d.id, 4.0);
    const done = await service.dropOff(d.id);

    expect(done.coldChainCompliant).toBe(true);
    expect(done.tempReadings).toHaveLength(2);
    expect((await repo.findById(item.id))?.currentState).toBe('DELIVERED');
    expect(events).toEqual([
      { deliveryId: d.id, itemId: item.id, courierId: 'courier-1', karmaCredits: 18 },
    ]);
  });

  it('only one courier can accept: the CAS claim rejects the second', async () => {
    const { service, item, accept } = await makeFixture();
    await accept();
    await expect(
      service.accept({
        itemId: item.id,
        courierId: 'courier-2',
        dropoffName: 'Hub Fridge #4',
        dropoff: { latitude: 40.72, longitude: -74.0 },
        karmaOnCompletion: 18,
      }),
    ).rejects.toThrow(InvalidStateTransitionError);
  });

  it('flags a cold-chain excursion instead of hiding it', async () => {
    const { service, accept } = await makeFixture();
    const d = await accept();
    service.pickUp(d.id);
    service.recordTemp(d.id, 9.5);
    const done = await service.dropOff(d.id);
    expect(done.coldChainCompliant).toBe(false);
  });

  it('cancel returns the item to the donation pool for re-dispatch', async () => {
    const { repo, service, item, accept } = await makeFixture();
    const d = await accept();
    await service.cancel(d.id);

    expect((await repo.findById(item.id))?.currentState).toBe('DONATION_PHASE');
    expect(service.forItem(item.id)).toBeUndefined(); // cancelled runs don't count as custody

    const retry = await service.accept({
      itemId: item.id,
      courierId: 'courier-2',
      dropoffName: 'Hub Fridge #4',
      dropoff: { latitude: 40.72, longitude: -74.0 },
      karmaOnCompletion: 18,
    });
    expect(retry.courierId).toBe('courier-2');
  });

  it('enforces lifecycle order', async () => {
    const { service, accept } = await makeFixture();
    const d = await accept();
    expect(() => service.recordTemp(d.id, 4)).toThrow(InvalidStateTransitionError);
    await expect(service.dropOff(d.id)).rejects.toThrow(InvalidStateTransitionError);
  });
});

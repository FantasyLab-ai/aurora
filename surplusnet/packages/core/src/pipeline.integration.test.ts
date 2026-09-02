import { describe, expect, it } from 'vitest';
import type { Clock } from './lib/clock.js';
import { createSurplusNet, InMemoryCourierLocationSource, InMemoryWalletStore } from './index.js';
import type { PickupOffer } from './modules/routing/courier-dispatch.service.js';

class FakeClock implements Clock {
  constructor(private current: Date) {}
  now(): Date {
    return new Date(this.current);
  }
  advanceMinutes(minutes: number): void {
    this.current = new Date(this.current.getTime() + minutes * 60_000);
  }
}

describe('SurplusNet end-to-end pipeline', () => {
  it('runs list → rollover → dispatch → delivery → karma mint → audit report', async () => {
    const clock = new FakeClock(new Date('2026-09-02T10:00:00Z'));
    const courierLocations = new InMemoryCourierLocationSource();
    const offers: Array<{ courierId: string; offer: PickupOffer }> = [];

    const net = createSurplusNet({
      clock,
      courierLocations,
      notifier: { offerPickup: async (courierId, offer) => void offers.push({ courierId, offer }) },
    });

    // A courier is online three blocks away
    courierLocations.upsert({
      courierId: 'courier-1',
      latitude: 40.738,
      longitude: -73.9911,
      lastSeenAt: clock.now(),
      transport: 'EBIKE',
    });
    (net.walletStore as InMemoryWalletStore).createWallet('courier-1');

    // 1. Supplier lists surplus — tax engine + ledger fire immediately
    const item = await net.items.listSurplus({
      supplierId: 'bakery-9',
      title: 'Fresh Bakery Assortment Box',
      category: 'bakery',
      fmvCents: 3000,
      cogsCents: 1000,
      latitude: 40.7359,
      longitude: -73.9911,
      safeUntil: new Date(clock.now().getTime() + 6 * 3_600_000),
    });
    expect(item.calculatedTaxDeductionCents).toBe(2000); // 1000 + (3000-1000)/2

    // 2. Unsold for 45 minutes → rollover worker donates it and dispatch fans out
    clock.advanceMinutes(46);
    // The courier app heartbeats continuously; without a fresh lastSeenAt the
    // dispatcher would (correctly) skip them as offline.
    courierLocations.upsert({
      courierId: 'courier-1',
      latitude: 40.738,
      longitude: -73.9911,
      lastSeenAt: clock.now(),
      transport: 'EBIKE',
    });
    const sweep = await net.rolloverWorker.tick();
    expect(sweep.rolledOver).toEqual([item.id]);
    expect(offers.map((o) => o.courierId)).toEqual(['courier-1']);
    expect(offers[0]!.offer.itemId).toBe(item.id);

    // 3. Courier completes the drop-off → karma credits mint exactly once
    await net.bus.emit('delivery.completed', {
      deliveryId: 'delivery-1',
      itemId: item.id,
      courierId: 'courier-1',
    });
    await net.bus.emit('delivery.completed', {
      deliveryId: 'delivery-1',
      itemId: item.id,
      courierId: 'courier-1',
    });
    expect((await net.wallets.balances('courier-1')).karmaCreditBalance).toBe(10);

    // 4. End of month: audit-ready report with an intact hash chain
    const report = await net.auditExport.buildMonthlyReport(2026, 9);
    expect(report.donationCount).toBe(1);
    expect(report.totalDeductionCents).toBe(2000);
    expect(report.brokenSequences).toEqual([]);
  });
});

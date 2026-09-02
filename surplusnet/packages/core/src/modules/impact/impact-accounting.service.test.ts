import { describe, expect, it } from 'vitest';
import { ImpactAccountingService } from './impact-accounting.service.js';
import { DonationLedger, InMemoryLedgerStore } from '../tax/donation-ledger.js';
import type { SurplusItem } from '../../domain/types.js';

function makeItem(overrides: Partial<SurplusItem> = {}): SurplusItem {
  return {
    id: overrides.id ?? 'item-1',
    supplierId: 'grocer-1',
    title: 'Produce Box',
    category: 'produce',
    quantity: 1,
    fmvCents: 2000,
    cogsCents: 800,
    calculatedTaxDeductionCents: 1400,
    currentState: 'DELIVERED',
    listedAt: new Date('2026-09-01T10:00:00Z'),
    salesWindowMinutes: 45,
    safeUntil: new Date('2026-09-01T18:00:00Z'),
    latitude: 40.7,
    longitude: -74.0,
    ...overrides,
  };
}

function makeService() {
  const store = new InMemoryLedgerStore();
  return { store, service: new ImpactAccountingService(new DonationLedger(store)) };
}

describe('ImpactAccountingService', () => {
  it('computes meals, pounds, CO2e, and avoided disposal from actual weight', () => {
    const { service } = makeService();
    // 5 kg of produce: 5000/544 ≈ 9.2 meals, 11.0 lb, 5 * 2.1 kg CO2e, 5 * 18 cents
    const impact = service.computeForItem(makeItem({ weightGrams: 5000 }));
    expect(impact).toEqual({
      itemId: 'item-1',
      weightGrams: 5000,
      mealsRescued: 9.2,
      poundsDiverted: 11,
      co2eGrams: 10_500,
      avoidedDisposalCents: 90,
    });
  });

  it('falls back to per-category unit weights scaled by quantity', () => {
    const { service } = makeService();
    // bakery default 2500 g/unit x 2 units
    const impact = service.computeForItem(makeItem({ category: 'bakery', quantity: 2 }));
    expect(impact.weightGrams).toBe(5000);
  });

  it('uses the default factors for unknown categories', () => {
    const { service } = makeService();
    const impact = service.computeForItem(makeItem({ category: 'mystery', weightGrams: 1000 }));
    expect(impact.co2eGrams).toBe(2500);
  });

  it('books a rescue to the immutable ledger exactly once', async () => {
    const { service, store } = makeService();
    const item = makeItem({ weightGrams: 1000 });

    const first = await service.recordRescue(item, new Date('2026-09-02T12:00:00Z'));
    const replay = await service.recordRescue(item, new Date('2026-09-02T13:00:00Z'));

    expect(first?.weightGrams).toBe(1000);
    expect(replay).toBeUndefined();
    const entries = await store.all();
    expect(entries.filter((e) => e.kind === 'IMPACT_RECORDED')).toHaveLength(1);
  });

  it('aggregates by supplier, zone, and month', async () => {
    const { service } = makeService();
    await service.recordRescue(
      makeItem({ id: 'a', weightGrams: 1000, zoneId: 'downtown' }),
      new Date('2026-09-02T12:00:00Z'),
    );
    await service.recordRescue(
      makeItem({ id: 'b', weightGrams: 2000, zoneId: 'downtown' }),
      new Date('2026-09-10T12:00:00Z'),
    );
    await service.recordRescue(
      makeItem({ id: 'c', weightGrams: 4000, zoneId: 'uptown', supplierId: 'bakery-2' }),
      new Date('2026-10-01T12:00:00Z'),
    );

    expect(service.totals({ zoneId: 'downtown' }).itemCount).toBe(2);
    expect(service.totals({ supplierId: 'bakery-2' }).poundsDiverted).toBe(8.8);
    expect(service.totals({ year: 2026, month: 9 }).itemCount).toBe(2);
    expect(service.totals().co2eKg).toBe(14.7); // (1+2+4)kg * 2.1
  });
});

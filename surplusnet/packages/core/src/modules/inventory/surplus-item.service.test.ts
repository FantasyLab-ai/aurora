import { describe, expect, it } from 'vitest';
import { DonationLedger, InMemoryLedgerStore } from '../tax/donation-ledger.js';
import { InMemorySurplusItemRepository } from './surplus-item.repository.js';
import { SurplusItemService } from './surplus-item.service.js';
import { ValidationError } from '../../lib/errors.js';

function makeService() {
  const repo = new InMemorySurplusItemRepository();
  const store = new InMemoryLedgerStore();
  const ledger = new DonationLedger(store);
  return { repo, store, service: new SurplusItemService(repo, ledger) };
}

const baseInput = {
  supplierId: 'supplier-1',
  title: 'Organic Produce Box',
  category: 'produce',
  fmvCents: 2000,
  cogsCents: 800,
  latitude: 40.7,
  longitude: -74.0,
  safeUntil: new Date(Date.now() + 4 * 3_600_000),
};

describe('SurplusItemService.listSurplus', () => {
  it('creates the item in SALES_PHASE with the calculated deduction and default 70%-off price', async () => {
    const { service } = makeService();
    const item = await service.listSurplus(baseInput);

    expect(item.currentState).toBe('SALES_PHASE');
    // 800 + (2000-800)/2 = 1400
    expect(item.calculatedTaxDeductionCents).toBe(1400);
    // default sale price = 30% of FMV
    expect(item.salePriceCents).toBe(600);
    expect(item.salesWindowMinutes).toBe(45);
  });

  it('writes donation + deduction entries to the immutable ledger', async () => {
    const { service, store } = makeService();
    const item = await service.listSurplus(baseInput);

    const entries = await store.all();
    expect(entries.map((e) => e.kind)).toEqual(['DONATION_RECORDED', 'TAX_DEDUCTION_CALCULATED']);
    expect(entries.every((e) => e.surplusItemId === item.id)).toBe(true);
    expect(entries[1]!.payload['deductionCents']).toBe(1400);
  });

  it('refuses food that is already past its safety deadline', async () => {
    const { service } = makeService();
    await expect(
      service.listSurplus({ ...baseInput, safeUntil: new Date(Date.now() - 1000) }),
    ).rejects.toThrow(ValidationError);
  });

  it('refuses invalid coordinates', async () => {
    const { service } = makeService();
    await expect(service.listSurplus({ ...baseInput, latitude: 123 })).rejects.toThrow(ValidationError);
  });
});

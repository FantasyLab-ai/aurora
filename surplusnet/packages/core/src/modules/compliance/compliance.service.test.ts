import { describe, expect, it } from 'vitest';
import { ComplianceService } from './compliance.service.js';
import { DeliveryService } from '../delivery/delivery.service.js';
import { ImpactAccountingService } from '../impact/impact-accounting.service.js';
import { DonationLedger, InMemoryLedgerStore } from '../tax/donation-ledger.js';
import { InMemorySurplusItemRepository } from '../inventory/surplus-item.repository.js';
import { SurplusItemService } from '../inventory/surplus-item.service.js';
import { EventBus } from '../../lib/event-bus.js';

async function makeFixture() {
  const repo = new InMemorySurplusItemRepository();
  const ledger = new DonationLedger(new InMemoryLedgerStore());
  const itemService = new SurplusItemService(repo, ledger);
  const bus = new EventBus();
  const deliveries = new DeliveryService(repo, bus, { maxSafeCelsius: 5 });
  const impact = new ImpactAccountingService(ledger);
  const compliance = new ComplianceService(repo, deliveries, impact);

  const listAndDeliver = async (title: string, tempC: number) => {
    const item = await itemService.listSurplus({
      supplierId: 'grocer-1',
      title,
      category: 'dairy',
      weightGrams: 3000,
      fmvCents: 2000,
      cogsCents: 800,
      latitude: 40.7,
      longitude: -74.0,
      safeUntil: new Date(Date.now() + 4 * 3_600_000),
    });
    await repo.transitionState(item.id, 'SALES_PHASE', 'DONATION_PHASE', {
      salePriceCents: 0,
      rolledOverAt: new Date(),
    });
    const d = await deliveries.accept({
      itemId: item.id,
      courierId: 'courier-1',
      dropoffName: 'Community Pantry East',
      dropoff: { latitude: 40.71, longitude: -74.0 },
      karmaOnCompletion: 10,
    });
    deliveries.pickUp(d.id);
    deliveries.recordTemp(d.id, tempC);
    await deliveries.dropOff(d.id);
    await impact.recordRescue((await repo.findById(item.id))!, new Date());
    return item;
  };

  return { repo, deliveries, impact, compliance, listAndDeliver };
}

describe('ComplianceService.certificate', () => {
  it('compiles the full custody chain with temp log and a compliant verdict', async () => {
    const { compliance, listAndDeliver } = await makeFixture();
    const item = await listAndDeliver('Dairy Box', 3.5);

    const cert = await compliance.certificate(item.id);
    expect(cert.custodyChain.map((c) => c.event)).toEqual([
      'LISTED_BY_SUPPLIER',
      'ENTERED_DONATION_POOL',
      'ACCEPTED_BY_COURIER',
      'PICKED_UP',
      'DELIVERED_TO_RECIPIENT',
    ]);
    expect(cert.tempLog).toHaveLength(1);
    expect(cert.overallCompliant).toBe(true);
    expect(cert.goodFaithStatement).toContain('Bill Emerson');
  });

  it('surfaces cold-chain excursions honestly', async () => {
    const { compliance, listAndDeliver } = await makeFixture();
    const item = await listAndDeliver('Dairy Box', 9.0);

    const cert = await compliance.certificate(item.id);
    expect(cert.coldChainCompliant).toBe(false);
    expect(cert.overallCompliant).toBe(false);
  });
});

describe('ComplianceService.recoveryReport', () => {
  it('produces the jurisdiction filing with the CFO three numbers', async () => {
    const { compliance, listAndDeliver } = await makeFixture();
    await listAndDeliver('Dairy Box A', 3.0);
    await listAndDeliver('Dairy Box B', 3.0);

    const now = new Date();
    const report = await compliance.recoveryReport(
      'grocer-1',
      now.getUTCFullYear(),
      now.getUTCMonth() + 1,
    );

    expect(report.itemCount).toBe(2);
    expect(report.poundsRecovered).toBeCloseTo(13.2, 1); // 2 x 3kg ≈ 13.2 lb
    expect(report.destinations).toEqual(['Community Pantry East']);
    expect(report.taxDeductionCents).toBe(2 * 1400);
    expect(report.avoidedDisposalCents).toBe(2 * 54); // 3kg x 18c/kg each
    expect(report.co2eKg).toBeCloseTo(19.2, 1); // 6kg x 3.2 (dairy)
    expect(report.jurisdictionNote).toContain('SB 1383');
  });
});
